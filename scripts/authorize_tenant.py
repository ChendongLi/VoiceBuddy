"""One-time OAuth authorization flow for a tenant's Google Calendar access.

Usage:
    python scripts/authorize_tenant.py --tenant coolbreeze_hvac

Opens a browser for the Google OAuth consent screen and saves the resulting
token to tokens/{tenant_id}.json.
"""

from __future__ import annotations

import argparse
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from dotenv import load_dotenv
from google_auth_oauthlib.flow import Flow

SCOPES = ["https://www.googleapis.com/auth/calendar"]
REDIRECT_URI = "http://localhost:8080/oauth/callback"

ROOT = Path(__file__).resolve().parent.parent
TOKENS_DIR = ROOT / "tokens"
DEFAULT_CLIENT_SECRET = Path.home() / ".openclaw/workspace/secrets/google_oauth_client.json"

_auth_code: str | None = None
_auth_error: str | None = None


class _CallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        global _auth_code, _auth_error
        parsed = urlparse(self.path)
        if parsed.path == "/oauth/callback":
            params = parse_qs(parsed.query)
            if "code" in params:
                _auth_code = params["code"][0]
                body = b"<h2>Authorization successful! You can close this tab.</h2>"
            elif "error" in params:
                _auth_error = params["error"][0]
                body = f"<h2>Error: {_auth_error}</h2>".encode()
            else:
                body = b"<h2>Unknown response.</h2>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *args):
        pass  # suppress request logs


def main() -> None:
    global _auth_code, _auth_error
    load_dotenv()

    parser = argparse.ArgumentParser(description="Authorize a tenant for Google Calendar access")
    parser.add_argument("--tenant", required=True, help="Tenant ID (e.g. coolbreeze_hvac)")
    parser.add_argument("--client-secret", type=Path, default=DEFAULT_CLIENT_SECRET)
    args = parser.parse_args()

    if not args.client_secret.exists():
        print(f"Client secret file not found: {args.client_secret}", file=sys.stderr)
        sys.exit(1)

    TOKENS_DIR.mkdir(parents=True, exist_ok=True)
    token_path = TOKENS_DIR / f"{args.tenant}.json"

    # Build the auth flow with exact registered redirect URI
    flow = Flow.from_client_secrets_file(
        str(args.client_secret),
        scopes=SCOPES,
        redirect_uri=REDIRECT_URI,
    )

    auth_url, _ = flow.authorization_url(
        access_type="offline",
        prompt="consent",
        include_granted_scopes="true",
    )

    # Start local callback server in background thread
    server = HTTPServer(("localhost", 8080), _CallbackHandler)
    thread = threading.Thread(target=server.handle_request)
    thread.daemon = True
    thread.start()

    print(f"\nOpening browser for tenant: {args.tenant}")
    print(f"If browser doesn't open, visit:\n  {auth_url}\n")
    webbrowser.open(auth_url)

    # Wait for callback (up to 120s)
    thread.join(timeout=120)

    if _auth_error:
        print(f"Authorization error: {_auth_error}", file=sys.stderr)
        sys.exit(1)

    if not _auth_code:
        print("Timed out waiting for authorization.", file=sys.stderr)
        sys.exit(1)

    # Exchange code for token
    flow.fetch_token(code=_auth_code)
    creds = flow.credentials

    token_path.write_text(creds.to_json(), encoding="utf-8")
    print(f"Token saved to {token_path}")
    print(f"Tenant {args.tenant!r} is now authorized for Google Calendar.")


if __name__ == "__main__":
    main()
