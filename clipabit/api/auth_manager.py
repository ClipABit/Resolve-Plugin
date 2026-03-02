import json
import os
import secrets
import hashlib
import base64
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from threading import Event, Lock, Thread
from urllib.parse import parse_qs, urlparse, urlencode
from typing import Callable, Optional, Tuple

import keyring
import requests
import webbrowser

SERVICE_NAME = "clipabit-plugin"
KEYRING_USERNAME = "tokens"

_RESOURCES_DIR = Path(__file__).parent / "resources"


class AuthManager:
    """Handles Auth0 PKCE authentication flow."""

    SCOPES = "openid profile email offline_access read:clips write:clips read:profile manage:projects"

    def __init__(self):
        self.AUTH0_DOMAIN = os.environ.get("CLIPABIT_AUTH0_DOMAIN", "")
        self.CLIENT_ID = os.environ.get("CLIPABIT_AUTH0_CLIENT_ID", "")
        self.AUDIENCE = os.environ.get("CLIPABIT_AUTH0_AUDIENCE", "")

        missing = [
            name
            for name, value in [
                ("CLIPABIT_AUTH0_DOMAIN", self.AUTH0_DOMAIN),
                ("CLIPABIT_AUTH0_CLIENT_ID", self.CLIENT_ID),
                ("CLIPABIT_AUTH0_AUDIENCE", self.AUDIENCE),
            ]
            if not value
        ]
        if missing:
            raise ValueError(
                f"Missing required environment variable(s): {', '.join(missing)}"
            )

        self.on_reauth_required: Optional[Callable[[], None]] = None
        self._token_lock = Lock()

        print(f"[Auth] Config loaded: domain={self.AUTH0_DOMAIN}")
        print(f"[Auth] Config loaded: client_id present: {bool(self.CLIENT_ID)}")
        print(f"[Auth] Config loaded: audience={self.AUDIENCE}")
    
    def _generate_pkce_pair(self) -> Tuple[str, str]:
        """Generate PKCE code_verifier and code_challenge."""
        code_verifier = secrets.token_urlsafe(64)[:128]
        digest = hashlib.sha256(code_verifier.encode()).digest()
        code_challenge = base64.urlsafe_b64encode(digest).rstrip(b'=').decode()
        return code_verifier, code_challenge
    
    def _generate_state(self) -> str:
        """Generate random state for CSRF protection."""
        return secrets.token_urlsafe(32)

    def _start_callback_server(
        self, expected_state: str
    ) -> Tuple[int, Callable[[int], Tuple[Optional[str], bool]]]:
        """
        Start one-shot callback server. Returns (port, wait_for_callback_fn).
        wait_for_callback(timeout_seconds) blocks until callback received or timeout,
        then returns (code, state_valid).
        """
        result: dict = {"code": None, "state_valid": False}
        event = Event()

        class CallbackHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                parsed = urlparse(self.path)
                if parsed.path in ("/callback", "/callback/"):
                    params = parse_qs(parsed.query)
                    code = params.get("code", [""])[0] or None
                    state = params.get("state", [""])[0] or None
                    error = params.get("error", [""])[0] or None

                    result["code"] = code
                    result["state_valid"] = (
                        state == expected_state if state else False
                    )

                    code_preview = f"{code[:8]}..." if code else "None"
                    state_preview = f"{state[:8]}..." if state else "None"
                    print(f"[Auth] Received callback: code={code_preview}, state={state_preview}")
                    print(
                        f"[Auth] State validation: {'PASS' if result['state_valid'] else 'FAIL'}"
                    )

                    self.send_response(200)
                    self.send_header("Content-type", "text/html")
                    self.end_headers()
                    logo_svg = (_RESOURCES_DIR / "logo.svg").read_text(encoding="utf-8")
                    # Treat state mismatch as an error (CSRF protection: invalid state = possible token theft)
                    if error or not result["state_valid"]:
                        template = (_RESOURCES_DIR / "callback_error.html").read_text(encoding="utf-8")
                    else:
                        template = (_RESOURCES_DIR / "callback_success.html").read_text(encoding="utf-8")
                    html = template.replace("LOGO_SVG_PLACEHOLDER", logo_svg).encode()
                    self.wfile.write(html)
                    event.set()
                else:
                    self.send_response(404)
                    self.end_headers()

            def log_message(self, format, *args):
                pass

        try:
            server = HTTPServer(("127.0.0.1", 8765), CallbackHandler)
        except OSError as e:
            print(f"[Auth] Failed to bind callback server to port 8765: {e}")
            print("[Auth] Falling back to an ephemeral port assigned by the OS")
            try:
                server = HTTPServer(("127.0.0.1", 0), CallbackHandler)
            except OSError as e2:
                print(f"[Auth] Failed to bind callback server to an ephemeral port: {e2}")
                print("[Auth] Unable to start local callback server for login flow")
                raise
        port = server.server_address[1]

        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()

        def wait_for_callback(timeout: int = 300) -> Tuple[Optional[str], bool]:
            print(f"[Auth] Waiting for callback (timeout: {timeout}s)...")
            event.wait(timeout)
            if result["code"] is None:
                print("[Auth] Timeout reached - no callback received")
                print("[Auth] Login cancelled by user")
            print("[Auth] Callback server shutting down")
            server.shutdown()
            server.server_close()
            thread.join()
            return result["code"], result["state_valid"]

        return port, wait_for_callback

    def initiate_login(self) -> Tuple[int, str, Callable[[int], Tuple[Optional[str], bool]]]:
        """
        Start login flow: generate PKCE, start callback server, open browser to Auth0.
        Returns (port, code_verifier, wait_for_callback) for token exchange.
        """
        verifier, challenge = self._generate_pkce_pair()
        state = self._generate_state()

        port, wait_for_callback = self._start_callback_server(expected_state=state)

        print(f"[Auth] Callback server listening on port {port}")

        redirect_uri = f"http://127.0.0.1:{port}/callback"
        params = {
            "response_type": "code",
            "client_id": self.CLIENT_ID,
            "redirect_uri": redirect_uri,
            "scope": self.SCOPES,
            "audience": self.AUDIENCE,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": state,
        }
        authorization_url = (
            f"https://{self.AUTH0_DOMAIN}/authorize?{urlencode(params)}"
        )

        print("[Auth] Opening browser to Auth0...")
        print(f"[Auth] Authorization URL: {authorization_url[:80]}...")

        webbrowser.open(authorization_url)

        return port, verifier, wait_for_callback

    def exchange_code_for_tokens(
        self, code: str, code_verifier: str, redirect_uri: str
    ) -> Optional[dict]:
        """
        Exchange authorization code for tokens.
        Returns dict with access_token, id_token, refresh_token, expires_in or None on failure.
        """
        token_url = f"https://{self.AUTH0_DOMAIN}/oauth/token"
        payload = {
            "grant_type": "authorization_code",
            "client_id": self.CLIENT_ID,
            "code": code,
            "code_verifier": code_verifier,
            "redirect_uri": redirect_uri,
        }

        print("[Auth] Exchanging code for tokens...")

        response = requests.post(
            token_url,
            data=payload,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=10,
        )

        print(f"[Auth] Token exchange response status: {response.status_code}")

        if not response.ok:
            print(f"[Auth] Token exchange failed: {response.text}")
            return None

        data = response.json()
        access_token = data.get("access_token") or ""
        refresh_token = data.get("refresh_token")
        expires_in = data.get("expires_in", 0)

        print(f"[Auth] Received access_token: {'yes' if access_token else 'no'}")
        print(f"[Auth] Received refresh_token: {'yes' if refresh_token else 'no'}")
        print(f"[Auth] Token expires in: {expires_in}s")

        id_token = data.get("id_token", "")
        expires_at = time.time() + expires_in
        self._save_tokens(access_token or "", refresh_token or "", id_token, expires_at)

        return data

    def _save_tokens(self, access_token: str, refresh_token: str, id_token: str, expires_at: float) -> None:
        """Store tokens in keyring."""
        print(f"[Auth] Saving tokens to keyring (service: {SERVICE_NAME})")
        data = {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "id_token": id_token,
            "expires_at": expires_at,
        }
        try:
            keyring.set_password(SERVICE_NAME, KEYRING_USERNAME, json.dumps(data))
            print("[Auth] Tokens saved successfully")
        except Exception as e:
            print(f"[Auth] Failed to save tokens to keyring: {e}")
            print("[Auth] Keyring backend may be unavailable; tokens will not persist")

    def _load_tokens(self) -> Optional[dict]:
        """Retrieve tokens from keyring. Returns dict or None."""
        print("[Auth] Loading tokens from keyring...")
        raw = keyring.get_password(SERVICE_NAME, KEYRING_USERNAME)
        if not raw:
            return None
        try:
            data = json.loads(raw)
            if not isinstance(data, dict):
                print("[Auth] Stored token data is not a JSON object; clearing tokens")
                self.delete_tokens()
                return None
            access = bool(data.get("access_token"))
            refresh = bool(data.get("refresh_token"))
            print(f"[Auth] Tokens loaded: access={'yes' if access else 'no'}, refresh={'yes' if refresh else 'no'}")
            # Require at least one of access_token or refresh_token to consider tokens valid
            if not (access or refresh):
                print("[Auth] Token data missing access_token and refresh_token; clearing tokens")
                self.delete_tokens()
                return None
            return data
        except json.JSONDecodeError:
            print("[Auth] Failed to decode token JSON; clearing tokens")
            self.delete_tokens()
            return None

    def is_logged_in(self) -> bool:
        """Check if valid tokens are stored in keyring (logged in)."""
        try:
            tokens = self._load_tokens()
            if not tokens:
                return False
            has_access = bool(tokens.get("access_token"))
            has_refresh = bool(tokens.get("refresh_token"))
            return has_access or has_refresh
        except Exception:
            return False

    def delete_tokens(self) -> None:
        """Clear tokens from keyring (logout)."""
        print("[Auth] Clearing tokens from keyring...")
        try:
            keyring.delete_password(SERVICE_NAME, KEYRING_USERNAME)
        except Exception:
            pass  # No password stored or backend error

    def _refresh_tokens(self, tokens: Optional[dict] = None) -> Optional[dict]:
        """Refresh access token using refresh_token. Returns new token data or None."""
        if tokens is None:
            tokens = self._load_tokens()
        if not tokens or not tokens.get("refresh_token"):
            return None

        token_url = f"https://{self.AUTH0_DOMAIN}/oauth/token"
        payload = {
            "grant_type": "refresh_token",
            "client_id": self.CLIENT_ID,
            "refresh_token": tokens["refresh_token"],
        }

        print("[Auth] Access token expired, refreshing...")
        print("[Auth] Refresh request sent")

        response = requests.post(
            token_url,
            data=payload,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=10,
        )

        if not response.ok:
            print(f"[Auth] Refresh failed: {response.text}")
            return None

        data = response.json()
        access_token = data.get("access_token") or ""
        expires_in = data.get("expires_in", 0)
        refresh_token = data.get("refresh_token") or tokens.get("refresh_token")
        id_token = data.get("id_token") or tokens.get("id_token", "")

        print(f"[Auth] New access_token received: {'yes' if access_token else 'no'}")

        expires_at = time.time() + expires_in
        self._save_tokens(access_token, refresh_token, id_token, expires_at)
        print("[Auth] Tokens updated in keyring")

        return {"access_token": access_token, "expires_at": expires_at}

    def get_valid_access_token(self) -> Optional[str]:
        """
        Return a valid access token. Refreshes automatically if expired.
        Returns None if no tokens stored or refresh fails.
        Thread-safe: serializes concurrent refresh attempts.
        """
        with self._token_lock:
            tokens = self._load_tokens()
            if not tokens:
                return None

            access_token = tokens.get("access_token")
            expires_at = tokens.get("expires_at", 0)
            # Refresh if expired or within 60s (buffer)
            if not access_token or time.time() >= (expires_at - 60):
                refreshed = self._refresh_tokens(tokens)
                if refreshed:
                    access_token = refreshed.get("access_token")
                else:
                    return None

            print("[Auth] Returning valid access token")
            return access_token

    def execute_with_auth_retry(
        self, endpoint: str, make_request: Callable[[Optional[str]], requests.Response]
    ) -> requests.Response:
        """
        Execute a request with 401 retry. On 401: refresh once, retry. If still 401, clear tokens and call on_reauth_required.
        """
        token = self.get_valid_access_token()
        resp = make_request(token)
        if resp.status_code != 401:
            return resp

        print(f"[Auth] Received 401 from {endpoint}")
        print("[Auth] Attempting token refresh...")
        refreshed = self._refresh_tokens()
        if not refreshed:
            print("[Auth] Refresh failed - prompting re-login")
            self.delete_tokens()
            if self.on_reauth_required:
                self.on_reauth_required()
            return resp

        new_token = refreshed.get("access_token")
        print("[Auth] Retrying request with new token...")
        resp2 = make_request(new_token)
        if resp2.status_code == 401:
            print("[Auth] Refresh failed - prompting re-login")
            self.delete_tokens()
            if self.on_reauth_required:
                self.on_reauth_required()
        return resp2


if __name__ == "__main__":
    import sys
    mgr = AuthManager()

    if len(sys.argv) > 1 and sys.argv[1] == "logout":
        mgr.delete_tokens()
        print("[Auth] Logged out")
    elif len(sys.argv) > 1 and sys.argv[1] == "token":
        token = mgr.get_valid_access_token()
        print(f"[Auth] get_valid_access_token: {'present' if token else None}")
    else:
        port, verifier, wait = mgr.initiate_login()
        redirect_uri = f"http://127.0.0.1:{port}/callback"
        code, ok = wait(timeout=300)  # ~5 min
        print(f"[Auth] Result: code={'...' if code else None}, state_valid={ok}")
        if code and ok:
            tokens = mgr.exchange_code_for_tokens(code, verifier, redirect_uri)
            if tokens:
                print(f"[Auth] Success! access_token present: {bool(tokens.get('access_token'))}")
        elif not code:
            print("[Auth] Login cancelled")
