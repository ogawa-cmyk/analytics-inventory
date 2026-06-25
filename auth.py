"""OAuth flow per Gmail account. Tokens stored in tokens/{email}.json."""
import json
import sys
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

from config import CLIENT_SECRET_PATH, SCOPES, TOKENS_DIR


def _token_path(email: str) -> Path:
    return TOKENS_DIR / f"{email}.json"


def _userinfo(creds: Credentials) -> str:
    import requests
    r = requests.get(
        "https://openidconnect.googleapis.com/v1/userinfo",
        headers={"Authorization": f"Bearer {creds.token}"},
        timeout=10,
    )
    r.raise_for_status()
    return r.json()["email"]


def add_account() -> str:
    """Interactive OAuth — opens a browser, returns the email."""
    if not CLIENT_SECRET_PATH.exists():
        raise FileNotFoundError(
            f"{CLIENT_SECRET_PATH} not found. Download OAuth client (Desktop) "
            "JSON from Google Cloud Console and save it there."
        )
    flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRET_PATH), SCOPES)
    creds = flow.run_local_server(port=0, prompt="consent")
    email = _userinfo(creds)
    _token_path(email).write_text(creds.to_json(), encoding="utf-8")
    return email


def load_credentials(email: str) -> Credentials:
    path = _token_path(email)
    if not path.exists():
        raise FileNotFoundError(f"No token for {email}. Run: python auth.py add")
    creds = Credentials.from_authorized_user_file(str(path), SCOPES)
    if not creds.valid:
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            path.write_text(creds.to_json(), encoding="utf-8")
        else:
            raise RuntimeError(f"Token for {email} is invalid; re-run auth.py add")
    return creds


def list_accounts() -> list[str]:
    return sorted(p.stem for p in TOKENS_DIR.glob("*.json"))


def remove_account(email: str) -> None:
    p = _token_path(email)
    if p.exists():
        p.unlink()


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "list"
    if cmd == "add":
        email = add_account()
        print(f"OK: added {email}")
    elif cmd == "list":
        for e in list_accounts():
            print(e)
    elif cmd == "remove" and len(sys.argv) > 2:
        remove_account(sys.argv[2])
        print(f"OK: removed {sys.argv[2]}")
    else:
        print("Usage: python auth.py [add|list|remove EMAIL]")
        sys.exit(1)
