"""One-time OAuth authorization flow for a tenant's Google Calendar access.

Usage:
    python scripts/authorize_tenant.py --tenant coolbreeze_hvac

Opens a browser for the Google OAuth consent screen and saves the resulting
token to tokens/{tenant_id}.json.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dotenv import load_dotenv
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/calendar"]

ROOT = Path(__file__).resolve().parent.parent
TOKENS_DIR = ROOT / "tokens"

# Default client secrets path — override with --client-secret
DEFAULT_CLIENT_SECRET = Path.home() / ".openclaw/workspace/secrets/google_oauth_client.json"


def main() -> None:
    load_dotenv()

    parser = argparse.ArgumentParser(description="Authorize a tenant for Google Calendar access")
    parser.add_argument("--tenant", required=True, help="Tenant ID (e.g. coolbreeze_hvac)")
    parser.add_argument(
        "--client-secret",
        type=Path,
        default=DEFAULT_CLIENT_SECRET,
        help="Path to Google OAuth client JSON file",
    )
    args = parser.parse_args()

    tenant_id: str = args.tenant
    client_secret: Path = args.client_secret

    if not client_secret.exists():
        print(f"Client secret file not found: {client_secret}", file=sys.stderr)
        sys.exit(1)

    TOKENS_DIR.mkdir(parents=True, exist_ok=True)
    token_path = TOKENS_DIR / f"{tenant_id}.json"

    flow = InstalledAppFlow.from_client_secrets_file(str(client_secret), SCOPES)
    creds = flow.run_local_server(port=0)

    token_path.write_text(creds.to_json(), encoding="utf-8")
    print(f"Token saved to {token_path}")


if __name__ == "__main__":
    main()
