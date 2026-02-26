import secrets
import hashlib
import base64
from http.server import HTTPServer, BaseHTTPRequestHandler
from threading import Event, Thread
from urllib.parse import parse_qs, urlparse
from typing import Callable, Optional, Tuple


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

        server = HTTPServer(("127.0.0.1", 0), CallbackHandler)
        port = server.server_address[1]
        print(f"[Auth] Callback server started on port {port}")

        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()

        def wait_for_callback(timeout: int = 300) -> Tuple[Optional[str], bool]:
            event.wait(timeout)
            print("[Auth] Callback server shutting down")
            server.shutdown()
            return result["code"], result["state_valid"]

        return port, wait_for_callback
