import secrets
import hashlib
import base64
from http.server import HTTPServer, BaseHTTPRequestHandler
from threading import Event, Thread
from urllib.parse import parse_qs, urlparse, urlencode
from typing import Callable, Optional, Tuple

import requests
import webbrowser


class AuthManager:
    """Handles Auth0 PKCE authentication flow."""
    
    # Auth0 configuration
    AUTH0_DOMAIN = "dev-4v5a85yv6xnj8jci.ca.auth0.com"
    CLIENT_ID = "Uw6R7qrpxxfP1RdfjVb1bU1Sv6j4lBzT"
    AUDIENCE = "https://api.clipabit.web.app"
    SCOPES = "openid profile email offline_access read:clips write:clips read:profile manage:projects"
    
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
                    self.wfile.write(
                        b"<h1>Login successful! You can close this tab.</h1>"
                    )
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
            event.wait(timeout)
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

        return data


if __name__ == "__main__":
    mgr = AuthManager()
    redirect_uri = "http://127.0.0.1:8765/callback"
    port, verifier, wait = mgr.initiate_login()
    print(f"[Auth] Waiting for callback (port={port}). Log in in the browser...")
    code, ok = wait(timeout=60)
    print(f"[Auth] Result: code={'...' if code else None}, state_valid={ok}")
    if code and ok:
        tokens = mgr.exchange_code_for_tokens(code, verifier, redirect_uri)
        if tokens:
            print(f"[Auth] Success! access_token present: {bool(tokens.get('access_token'))}")
