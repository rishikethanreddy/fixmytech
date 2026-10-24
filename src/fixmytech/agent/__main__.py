"""Start the local diagnostic agent.

    python -m fixmytech.agent --web-url http://127.0.0.1:8000

Prints a pairing link (and opens it in the browser) that hands the per-run token to the FixMyTech
web page through the URL fragment, which browsers never send to any server.
"""

from __future__ import annotations

import argparse
import logging
import os
import secrets
import sys
import webbrowser
from pathlib import Path
from urllib.parse import quote, urlsplit

import uvicorn

from fixmytech.agent import AGENT_VERSION
from fixmytech.agent.adb import AdbClient, locate_adb
from fixmytech.agent.server import create_agent_app


def _salt_file() -> Path:
    base = Path(os.environ.get("APPDATA") or Path.home() / ".config") / "FixMyTech"
    base.mkdir(parents=True, exist_ok=True)
    return base / "agent_device_salt"


def load_salt() -> bytes:
    """Per-installation salt so the same phone maps to the same device_ref on this computer only."""
    path = _salt_file()
    if path.is_file() and len(path.read_bytes()) == 16:
        return path.read_bytes()
    salt = secrets.token_bytes(16)
    path.write_bytes(salt)
    return salt


def origin_of(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="fixmytech-agent", description="FixMyTech local diagnostic agent")
    ap.add_argument("--web-url", default=os.environ.get("FIXMYTECH_WEB_URL", "http://127.0.0.1:8000"),
                    help="URL of the FixMyTech web app that may talk to this agent")
    ap.add_argument("--port", type=int, default=int(os.environ.get("FIXMYTECH_AGENT_PORT", "8765")))
    ap.add_argument("--adb-path", default=os.environ.get("FIXMYTECH_ADB_PATH"))
    ap.add_argument("--no-browser", action="store_true", help="Do not open the pairing link automatically")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    origins = {origin_of(args.web_url)}
    # Treat localhost and 127.0.0.1 as the same local web app during development.
    for o in list(origins):
        origins.add(o.replace("://localhost", "://127.0.0.1"))
        origins.add(o.replace("://127.0.0.1", "://localhost"))
    extra = os.environ.get("FIXMYTECH_AGENT_EXTRA_ORIGINS", "")
    origins.update(o.strip() for o in extra.split(",") if o.strip())

    token = secrets.token_urlsafe(24)
    adb = AdbClient(args.adb_path)
    app = create_agent_app(token, sorted(origins), adb=adb, salt=load_salt(), port=args.port)

    agent_url = f"http://127.0.0.1:{args.port}"
    pair_url = f"{args.web_url.rstrip('/')}/#/pair?agent={quote(agent_url, safe='')}&token={token}"
    adb_path = locate_adb(args.adb_path)
    print(f"\nFixMyTech local diagnostic agent v{AGENT_VERSION}")
    print(f"  Listening on   {agent_url} (this computer only)")
    print(f"  ADB            {adb_path or 'NOT FOUND - install Android platform-tools'}")
    print(f"  Allowed web    {', '.join(sorted(origins))}")
    print("\nOpen this pairing link in your browser (keep it private; it is valid until the agent stops):")
    print(f"  {pair_url}\n", flush=True)
    if not args.no_browser:
        webbrowser.open(pair_url)
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    sys.exit(main())
