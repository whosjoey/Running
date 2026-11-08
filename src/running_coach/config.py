"""Paths, credentials, token storage and athlete profile."""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


def home() -> Path:
    p = Path(os.environ.get("RUNNING_COACH_HOME", "~/.running-coach")).expanduser()
    p.mkdir(parents=True, exist_ok=True)
    return p


def _load_dotenv() -> None:
    """Minimal .env loader (cwd/.env then ~/.running-coach/.env). Never overrides real env vars."""
    for path in (Path.cwd() / ".env", home() / ".env"):
        if not path.is_file():
            continue
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip().strip('"').strip("'")
            if k and v and k not in os.environ:
                os.environ[k] = v


_load_dotenv()


def env(name: str, default: str | None = None) -> str | None:
    return os.environ.get(name, default)


def oauth_port() -> int:
    return int(env("RUNNING_COACH_OAUTH_PORT", "8723"))


# ---------------------------------------------------------------- tokens

def _tokens_path() -> Path:
    return home() / "tokens.json"


def load_tokens(provider: str) -> dict[str, Any] | None:
    p = _tokens_path()
    if not p.exists():
        return None
    return json.loads(p.read_text()).get(provider)


def save_tokens(provider: str, tokens: dict[str, Any]) -> None:
    p = _tokens_path()
    data = json.loads(p.read_text()) if p.exists() else {}
    data[provider] = tokens
    p.write_text(json.dumps(data, indent=2))
    try:
        p.chmod(0o600)
    except OSError:
        pass


# ---------------------------------------------------------------- athlete profile

@dataclass
class AthleteProfile:
    max_hr: int | None = None          # bpm; if unknown we estimate (Tanaka) or use observed max
    resting_hr: int | None = None      # bpm; falls back to WHOOP resting HR if connected
    lthr: int | None = None            # lactate threshold HR (e.g. avg HR of last 20 min of a 30-min TT)
    birth_year: int | None = None
    sex: str | None = None             # "male" / "female" (affects Banister TRIMP weighting)
    units: str = "km"                  # "km" or "mi"
    weight_kg: float | None = None
    goal_race: str | None = None       # free text, e.g. "Sub-3:30 marathon, Chicago, 2027-10-10"
    notes: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def load(cls) -> "AthleteProfile":
        p = home() / "profile.json"
        if not p.exists():
            return cls()
        raw = json.loads(p.read_text())
        known = {k: raw[k] for k in cls.__dataclass_fields__ if k in raw}
        return cls(**known)

    def save(self) -> None:
        (home() / "profile.json").write_text(json.dumps(asdict(self), indent=2))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
