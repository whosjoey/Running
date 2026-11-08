"""Command line: `running-coach auth strava|whoop`, `running-coach status`, `running-coach serve`,
`running-coach claude-config`."""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from . import config, oauth


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="running-coach", description=__doc__)
    sub = ap.add_subparsers(dest="cmd")
    a = sub.add_parser("auth", help="Connect a service via OAuth (opens browser)")
    a.add_argument("provider", choices=["strava", "whoop"])
    a.add_argument("--no-browser", action="store_true", help="Print the URL instead of opening it")
    sub.add_parser("status", help="Show connection status")
    sub.add_parser("serve", help="Run the MCP server on stdio (what Claude launches)")
    sub.add_parser("claude-config", help="Print a Claude Desktop config snippet")
    args = ap.parse_args(argv)

    if args.cmd == "auth":
        oauth.authorize(args.provider, open_browser=not args.no_browser)
        print(f"✅ {args.provider} connected. Tokens saved to {config.home() / 'tokens.json'}")
    elif args.cmd == "status":
        print(json.dumps(oauth.status(), indent=2))
    elif args.cmd == "claude-config":
        argv0 = Path(sys.argv[0]).resolve()
        exe = str(argv0) if argv0.name == "running-coach" else (shutil.which("running-coach") or sys.executable)
        cmd = {"command": exe, "args": ["serve"]} if exe.endswith("running-coach") else \
              {"command": exe, "args": ["-m", "running_coach.server"]}
        print(json.dumps({"mcpServers": {"running-coach": cmd}}, indent=2))
    else:  # default & "serve"
        from .server import main as serve
        serve()


if __name__ == "__main__":
    main()
