import json
import os
import secrets
import hashlib
import base64
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from threading import Event, Thread
from urllib.parse import parse_qs, urlparse, urlencode
from typing import Callable, Optional, Tuple

import keyring
import requests
import webbrowser

SERVICE_NAME = "clipabit-plugin"
KEYRING_USERNAME = "tokens"

_DEFAULT_AUTH0_DOMAIN = "dev-4v5a85yv6xnj8jci.ca.auth0.com"
_DEFAULT_CLIENT_ID = "Uw6R7qrpxxfP1RdfjVb1bU1Sv6j4lBzT"
_DEFAULT_AUDIENCE = "https://api.clipabit.web.app"


class AuthManager:
    """Handles Auth0 PKCE authentication flow."""

    SCOPES = "openid profile email offline_access read:clips write:clips read:profile manage:projects"

    def __init__(self):
        self.AUTH0_DOMAIN = os.environ.get("CLIPABIT_AUTH0_DOMAIN", _DEFAULT_AUTH0_DOMAIN)
        self.CLIENT_ID = os.environ.get("CLIPABIT_AUTH0_CLIENT_ID", _DEFAULT_CLIENT_ID)
        self.AUDIENCE = os.environ.get("CLIPABIT_AUTH0_AUDIENCE", _DEFAULT_AUDIENCE)
        self.on_reauth_required: Optional[Callable[[], None]] = None

        print(f"[Auth] Config loaded: domain={self.AUTH0_DOMAIN}")
        print(f"[Auth] Config loaded: client_id={self.CLIENT_ID[:8]}...")
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
                    logo_svg = '''<svg width="180" height="84" viewBox="0 0 292 135" fill="none" xmlns="http://www.w3.org/2000/svg">
<rect x="57" y="66" width="3" height="30" fill="white"/>
<circle cx="58.6812" cy="38.6812" r="38.6812" fill="white"/>
<ellipse cx="58.5" cy="39" rx="36.5" ry="36" fill="#979797"/>
<path d="M77 37.4615C77 31.9693 80.103 26.9485 85.0154 24.4923C94.6565 19.6718 106 26.6824 106 37.4615V41.0673C106 52.1888 93.9958 59.17 84.3294 53.6702C79.7984 51.0922 77 46.2804 77 41.0673V37.4615Z" fill="#1a1a2e"/>
<path d="M79 38.4998L45.75 60.5836V16.4163L79 38.4998Z" fill="#1a1a2e"/>
<path d="M94.0801 53H91.0981V24.86H94.0801V53ZM102.078 29.606H99.0962V24.86H102.078V29.606ZM102.078 53H99.0962V32.252H102.078V53ZM110.076 60.14H107.094V32.252H109.782V38.09H110.118C111.042 34.352 114.15 31.832 119.064 31.832C125.532 31.832 129.228 36.326 129.228 42.626C129.228 48.926 125.574 53.42 118.98 53.42C114.402 53.42 111.084 50.9 110.118 46.91H110.076V60.14ZM110.076 43.004C110.076 47.918 113.31 50.69 118.182 50.69C123.012 50.69 126.204 48.632 126.204 42.626C126.204 36.62 122.97 34.604 118.266 34.604C113.1 34.604 110.076 37.502 110.076 42.626V43.004ZM138.647 53.42C134.573 53.42 131.801 51.488 131.801 48.128C131.801 44.726 134.615 43.256 138.479 42.836L148.349 41.744V40.148C148.349 36.116 146.585 34.52 142.049 34.52C137.597 34.52 135.245 36.116 135.245 39.77V39.938H132.263V39.77C132.263 35.402 135.875 31.832 142.259 31.832C148.559 31.832 151.247 35.444 151.247 40.022V53H148.559V47.414H148.349C147.131 51.236 143.477 53.42 138.647 53.42ZM134.783 47.918C134.783 50.018 136.169 51.11 139.319 51.11C144.359 51.11 148.349 48.884 148.349 43.886V43.718L139.403 44.726C136.295 45.02 134.783 45.776 134.783 47.918ZM158.837 53H156.149V24.86H159.131V38.048H159.341C160.223 34.478 163.205 31.832 168.203 31.832C174.713 31.832 178.283 36.326 178.283 42.626C178.283 48.926 174.713 53.42 167.951 53.42C163.331 53.42 160.013 51.026 159.047 46.868H158.837V53ZM159.131 42.962C159.131 47.876 162.239 50.69 167.237 50.69C172.193 50.69 175.259 48.632 175.259 42.626C175.259 36.62 172.109 34.604 167.321 34.604C162.071 34.604 159.131 37.46 159.131 42.584V42.962ZM184.93 29.606H181.948V24.86H184.93V29.606ZM184.93 53H181.948V32.252H184.93V53ZM202.672 53H198.178C194.062 53 191.416 51.278 191.416 46.406V34.814H187.72V32.252H191.416V27.296H194.44V32.252H202.672V34.814H194.44V46.574C194.44 49.472 195.868 50.27 198.892 50.27H202.672V53Z" fill="white"/>
</svg>'''
                    if error:
                        html = f'''<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>ClipABit - Login Failed</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: linear-gradient(135deg, #0f0f1a 0%, #1a1a2e 50%, #16213e 100%);
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            color: #fff;
        }}
        .container {{
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            text-align: center;
            padding: 28px 56px 52px;
            min-height: 200px;
            background: rgba(255,255,255,0.03);
            border-radius: 20px;
            backdrop-filter: blur(20px);
            border: 1px solid rgba(255,255,255,0.08);
            box-shadow: 0 8px 32px rgba(0,0,0,0.3);
            max-width: 420px;
        }}
        .logo {{
            display: flex;
            justify-content: center;
            align-items: center;
            margin-bottom: 28px;
        }}
        .logo svg {{
            display: block;
            transform: translateX(12px);
        }}
        h1 {{
            font-size: 22px;
            font-weight: 600;
            margin-bottom: 12px;
            color: #ff6b6b;
            letter-spacing: -0.3px;
        }}
        p {{
            color: #9ca3af;
            font-size: 15px;
            line-height: 1.5;
            margin-bottom: 8px;
        }}
        .hint {{
            font-size: 13px;
            color: #6b7280;
            margin-top: 24px;
            margin-bottom: 0;
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="logo">{logo_svg}</div>
        <h1>Login Failed</h1>
        <p>Authentication was cancelled or an error occurred.</p>
        <p class="hint">You can close this tab and try again.</p>
    </div>
</body>
</html>'''.encode()
                    else:
                        html = f'''<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>ClipABit - Login Successful</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: linear-gradient(135deg, #0f0f1a 0%, #1a1a2e 50%, #16213e 100%);
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            color: #fff;
        }}
        .container {{
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            text-align: center;
            padding: 28px 56px 52px;
            min-height: 200px;
            background: rgba(255,255,255,0.03);
            border-radius: 20px;
            backdrop-filter: blur(20px);
            border: 1px solid rgba(255,255,255,0.08);
            box-shadow: 0 8px 32px rgba(0,0,0,0.3);
            max-width: 420px;
        }}
        .logo {{
            display: flex;
            justify-content: center;
            align-items: center;
            margin-bottom: 28px;
        }}
        .logo svg {{
            display: block;
            transform: translateX(12px);
        }}
        h1 {{
            font-size: 22px;
            font-weight: 600;
            margin-bottom: 12px;
            color: #4ade80;
            letter-spacing: -0.3px;
        }}
        p {{
            color: #9ca3af;
            font-size: 15px;
            line-height: 1.5;
            margin-bottom: 8px;
        }}
        .hint {{
            font-size: 13px;
            color: #6b7280;
            margin-top: 24px;
            margin-bottom: 0;
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="logo">{logo_svg}</div>
        <h1>Login Successful!</h1>
        <p>You're now signed in to ClipABit.</p>
        <p class="hint">You can close this tab and return to the plugin.</p>
    </div>
</body>
</html>'''.encode()
                    self.wfile.write(html)
                else:
                    self.send_response(404)
                    self.end_headers()

                event.set()

            def log_message(self, format, *args):
                pass

        server = HTTPServer(("127.0.0.1", 8765), CallbackHandler)
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
            return result["code"], result["state_valid"]

        return port, wait_for_callback

    def initiate_login(self) -> Tuple[int, str, Callable[[int], Tuple[Optional[str], bool]]]:
        """
        Start login flow: generate PKCE, start callback server, open browser to Auth0.
        Returns (port, code_verifier, wait_for_callback) for token exchange.
        """
        verifier, challenge = self._generate_pkce_pair()
        state = self._generate_state()

        print(f"[Auth] Generated PKCE verifier: {verifier[:16]}...")
        print(f"[Auth] Generated state: {state[:16]}...")

        port, wait_for_callback = self._start_callback_server(expected_state=state)

        print(f"[Auth] Callback server listening on port {port}")

        #temporary fixed port for callback
        redirect_uri = f"http://127.0.0.1:8765/callback"
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

        print(f"[Auth] Opening browser to Auth0...")
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

        print(f"[Auth] Exchanging code for tokens...")

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

        print(f"[Auth] Received access_token: {access_token[:20]}...")
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
        keyring.set_password(SERVICE_NAME, KEYRING_USERNAME, json.dumps(data))
        print(f"[Auth] Tokens saved successfully")

    def _load_tokens(self) -> Optional[dict]:
        """Retrieve tokens from keyring. Returns dict or None."""
        print(f"[Auth] Loading tokens from keyring...")
        raw = keyring.get_password(SERVICE_NAME, KEYRING_USERNAME)
        if not raw:
            return None
        try:
            data = json.loads(raw)
            access = bool(data.get("access_token"))
            refresh = bool(data.get("refresh_token"))
            print(f"[Auth] Tokens loaded: access={'yes' if access else 'no'}, refresh={'yes' if refresh else 'no'}")
            return data
        except json.JSONDecodeError:
            return None

    def is_logged_in(self) -> bool:
        """Check if tokens are stored in keyring (logged in)."""
        raw = keyring.get_password(SERVICE_NAME, KEYRING_USERNAME)
        return raw is not None and len(raw) > 0

    def delete_tokens(self) -> None:
        """Clear tokens from keyring (logout)."""
        print(f"[Auth] Clearing tokens from keyring...")
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

        print(f"[Auth] Access token expired, refreshing...")
        print(f"[Auth] Refresh request sent")

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

        print(f"[Auth] New access_token received: {access_token[:20]}...")

        expires_at = time.time() + expires_in
        self._save_tokens(access_token, refresh_token, id_token, expires_at)
        print(f"[Auth] Tokens updated in keyring")

        return {"access_token": access_token, "expires_at": expires_at}

    def get_valid_access_token(self) -> Optional[str]:
        """
        Return a valid access token. Refreshes automatically if expired.
        Returns None if no tokens stored or refresh fails.
        """
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

        print(f"[Auth] Returning valid access token")
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
    redirect_uri = "http://127.0.0.1:8765/callback"

    if len(sys.argv) > 1 and sys.argv[1] == "logout":
        mgr.delete_tokens()
        print("[Auth] Logged out")
    elif len(sys.argv) > 1 and sys.argv[1] == "token":
        token = mgr.get_valid_access_token()
        print(f"[Auth] get_valid_access_token: {'...' + token[-8:] if token else None}")
    else:
        port, verifier, wait = mgr.initiate_login()
        code, ok = wait(timeout=300)  # ~5 min
        print(f"[Auth] Result: code={'...' if code else None}, state_valid={ok}")
        if code and ok:
            tokens = mgr.exchange_code_for_tokens(code, verifier, redirect_uri)
            if tokens:
                print(f"[Auth] Success! access_token present: {bool(tokens.get('access_token'))}")
        elif not code:
            print("[Auth] Login cancelled")
