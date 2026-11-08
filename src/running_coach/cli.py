"""Command line: `running-coach auth strava|whoop`, `running-coach status`, `running-coach serve`,
`running-coach claude-config`."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

from . import config, oauth


def _server_entry() -> dict:
    argv0 = Path(sys.argv[0]).resolve()
    exe = str(argv0) if argv0.name.startswith("running-coach") else (shutil.which("running-coach") or "")
    if exe:
        return {"command": exe, "args": ["serve"]}
    return {"command": sys.executable, "args": ["-m", "running_coach.server"]}


def claude_desktop_config_path() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library/Application Support/Claude/claude_desktop_config.json"
    if sys.platform.startswith("win"):
        return Path(os.environ.get("APPDATA", Path.home() / "AppData/Roaming")) / "Claude/claude_desktop_config.json"
    return Path.home() / ".config/Claude/claude_desktop_config.json"


def install_claude_desktop(path: Path | None = None) -> Path:
    path = path or claude_desktop_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data: dict = {}
    if path.exists() and path.read_text().strip():
        try:
            data = json.loads(path.read_text())
        except json.JSONDecodeError:
            sys.exit(f"Could not read {path} (it isn't valid JSON). Fix or delete it, then retry.")
        shutil.copy(path, path.with_suffix(".json.bak"))
    data.setdefault("mcpServers", {})["running-coach"] = _server_entry()
    path.write_text(json.dumps(data, indent=2))
    print(f"✅ Added running-coach to {path}")
    print("Now fully quit Claude (⌘Q on Mac) and open it again.")
    return path


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="running-coach", description=__doc__)
    sub = ap.add_subparsers(dest="cmd")
    a = sub.add_parser("auth", help="Connect a service via OAuth (opens browser)")
    a.add_argument("provider", choices=["strava", "whoop"])
    a.add_argument("--no-browser", action="store_true", help="Print the URL instead of opening it")
    sub.add_parser("status", help="Show connection status")
    sub.add_parser("serve", help="Run the MCP server on stdio (what Claude launches)")
    sub.add_parser("claude-config", help="Print a Claude Desktop config snippet")
    sub.add_parser("install-claude", help="Add this server to the Claude Desktop app config automatically")
    args = ap.parse_args(argv)

    if args.cmd == "auth":
        oauth.authorize(args.provider, open_browser=not args.no_browser)
        print(f"✅ {args.provider} connected. Tokens saved to {config.home() / 'tokens.json'}")
    elif args.cmd == "status":
        print(json.dumps(oauth.status(), indent=2))
    elif args.cmd == "claude-config":
        print(json.dumps({"mcpServers": {"running-coach": _server_entry()}}, indent=2))
    elif args.cmd == "install-claude":
        install_claude_desktop()
    else:  # default & "serve"
        from .server import main as serve
        serve()


if __name__ == "__main__":
    main()
