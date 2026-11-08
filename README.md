# Running Coach: Strava + WHOOP → Claude

An [MCP](https://modelcontextprotocol.io) server that gives Claude live, read-only access to your
**Strava** runs and **WHOOP** recovery/sleep/strain data, plus a built-in sports-science toolkit.
Once it's connected you can ask Claude things like:

- *"Analyze my run this morning."*
- *"Am I overtraining?"* / *"Should I do intervals today?"*
- *"What can I run for a half marathon right now? Give me training paces."*
- *"Is my aerobic fitness improving?"*
- *"Does my HRV or sleep actually affect how my runs go?"*
- *"Do a weekly review and plan next week."*

## What it computes

| Area | Metrics |
|---|---|
| **Pacing** | km/mile (or custom) splits, **grade-adjusted pace** (Minetti 2002 energy-cost model), half-vs-half negative/positive split, last-quarter fade, pace variability (CV), fastest/slowest split, best efforts (400 m → marathon) found anywhere inside a run |
| **Heart rate** | 5 zones (Friel %LTHR, Karvonen %HRR, or %HRmax), time-in-zone, Seiler 3-zone, **aerobic decoupling (Pa:HR)**, efficiency factor, cardiac drift (bpm/h), Banister **TRIMP**, **hrTSS** |
| **Training load** | Fitness / fatigue / form (**CTL / ATL / TSB** performance-management chart), **acute:chronic workload ratio** (rolling + EWMA), Foster monotony & strain, weekly mileage, **ramp rate** (10% rule), long-run share |
| **Intensity distribution** | Polarized (80/20) vs pyramidal vs "grey zone" check, from WHOOP zone times or Strava HR streams |
| **Fitness trend** | Efficiency factor at easy HR over time, with a linear trend |
| **Racing** | **VDOT** (Daniels & Gilbert), **Riegel** predictions, Daniels E/M/T/I/R **training paces** (per km/mi and per 400 m) |
| **WHOOP recovery** | Recovery score, **HRV baseline & normal range** (7-day rolling ln rMSSD vs baseline ± 0.5 SD, per Plews/Kiviniemi), HRV CV, resting-HR elevation, SpO₂, skin temp |
| **Sleep** | Time asleep vs need, 7-day sleep debt, deep/REM share, efficiency, bedtime consistency, respiratory-rate rise (early illness flag) |
| **Readiness** | A traffic light combining recovery, HRV, sleep, TSB and ACWR → *Go / Moderate / Easy / Rest* |
| **Recovery ↔ performance** | Correlations between morning WHOOP metrics and that day's run efficiency |
| **Escape hatches** | `strava_get` / `whoop_get` reach **any** read-only API endpoint; `activity_streams` gives raw per-second data for custom analysis |

Built-in prompts: **weekly_review**, **analyze_latest_run**, **race_plan**.

## Setup (about 10 minutes)

### 1. Install

Easiest (no Python needed; [uv](https://docs.astral.sh/uv/) installs it for you):

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh    # then close & reopen Terminal
git clone https://github.com/whosjoey/Running.git
cd Running
uv tool install --python 3.12 -e .
running-coach status                               # should print connection info
```

If `running-coach` isn't found, run `uv tool update-shell` and reopen Terminal.
Already have Python 3.10+ and pip? `pip install -e .` works too.

### 2. Create API apps (free)

**Strava:** go to <https://www.strava.com/settings/api> and create an app.
- *Authorization Callback Domain*: `localhost`
- Copy the **Client ID** and **Client Secret**.

**WHOOP:** go to <https://developer-dashboard.whoop.com>, create a team, then an app.
- *Redirect URL*: `http://localhost:8723/callback`
- Scopes: tick all the `read:*` scopes and `offline`.
- Copy the **Client ID** and **Client Secret**.

### 3. Save credentials

```bash
mkdir -p ~/.running-coach
cp .env.example ~/.running-coach/.env
# then edit ~/.running-coach/.env and paste in the 4 values
```

### 4. Connect your accounts

```bash
running-coach auth strava
running-coach auth whoop
running-coach status      # both should say "connected": true
```

A browser tab opens for each one. Click approve, and the tokens are saved to `~/.running-coach/tokens.json`
(readable only by you) and refreshed automatically after that.

### 5. Add it to Claude

**Claude Code:**
```bash
claude mcp add running-coach -- running-coach serve
```

**Claude Desktop:** run `running-coach claude-config` and merge the output into
`~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) or
`%APPDATA%\Claude\claude_desktop_config.json` (Windows), then restart Claude. It looks like:
```json
{
  "mcpServers": {
    "running-coach": { "command": "/full/path/to/running-coach", "args": ["serve"] }
  }
}
```

### 6. Tell Claude about yourself (recommended)

Ask Claude to *"update my profile: max HR 192, resting HR 48, LTHR 172, born 1995, male, units miles,
goal sub-1:40 half on 2027-04-12"*. Zones, TRIMP/hrTSS and load numbers get much more accurate with
real values. If you leave these out, it estimates them: resting HR from WHOOP, max HR from your highest
recorded HR or the Tanaka formula.

## Tools

| Tool | What it does |
|---|---|
| `connection_status`, `update_profile`, `heart_rate_zones` | Setup and physiology |
| `list_runs` | Recent activities with pace/HR/cadence |
| `analyze_run` | Full single-run breakdown (`activity_id="latest"` works) |
| `compare_runs` | Side-by-side runs |
| `personal_bests` | Fastest efforts across the past N days, from GPS streams |
| `activity_streams` | Raw downsampled time series |
| `training_load` | CTL/ATL/TSB, ACWR, monotony/strain, weekly volume and ramp |
| `intensity_distribution` | Polarization check |
| `aerobic_fitness_trend` | EF trend on easy runs |
| `race_predictor`, `training_paces` | VDOT / Riegel / Daniels paces |
| `whoop_recovery`, `whoop_sleep`, `whoop_strain`, `hrv_status` | WHOOP data and analysis |
| `readiness_check` | Today's go/easy/rest call |
| `recovery_vs_performance` | Does recovery predict run quality? |
| `weekly_report` | Everything for a weekly review |
| `strava_get`, `whoop_get` | Any read-only API endpoint |

## Notes and limits

- **Read-only**: it never writes to Strava or WHOOP.
- **Strava rate limits** (default 200 requests/15 min, 2,000/day): activity details and streams are cached
  forever in `~/.running-coach/cache/`, so each run is downloaded once. Tools that scan many runs
  (`personal_bests`, `intensity_distribution` with Strava) have a `max_activities` cap.
- Load metrics without a chest strap rely on wrist HR, which can be noisy during intervals.
- ACWR, VDOT and Riegel are well-known heuristics, not certainties. Claude is told to say so.
- Everything (tokens, profile, cache) lives in `~/.running-coach` (override with `RUNNING_COACH_HOME`).

## Development

```bash
pip install -e ".[dev]"
pytest
```

The analysis code in `src/running_coach/analysis/` is pure Python with no network access, and is tested
with synthetic runs. `tests/test_server.py` runs every MCP tool against mocked APIs.
