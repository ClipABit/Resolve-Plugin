import ctypes
import ctypes.wintypes
import json
import os
import secrets
import hashlib
import base64
import sys
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from threading import Event, Lock
from urllib.parse import parse_qs, urlparse, urlencode
from typing import Callable, Optional, Tuple

import requests

# keyring is optional — it's the preferred credential store on macOS/Linux,
# but on Windows we use DPAPI instead (avoids the 2.5KB size limit that
# causes error 1783 with large Auth0 JWTs).
try:
    import keyring
except ImportError:
    keyring = None

SERVICE_NAME = "clipabit-plugin"
KEYRING_USERNAME = "tokens"
_TOKEN_FILE = Path.home() / ".clipabit" / "tokens.dat"

_RESOURCES_DIR = Path(__file__).parent / "resources"

# --- Windows DPAPI helpers ---
# DPAPI (Data Protection API) encrypts data using the current Windows user's
# login credentials + a machine-specific key. The encrypted blob is only
# decryptable by the same user on the same machine — this is the same
# mechanism Chrome/Edge use for saved passwords. We use it instead of keyring
# on Windows because keyring's Credential Manager backend has a ~2.5KB
# payload limit that Auth0 JWTs routinely exceed (error 1783).
_IS_WINDOWS = sys.platform == "win32"

if _IS_WINDOWS:
    class _DATA_BLOB(ctypes.Structure):
        """Win32 DATA_BLOB structure for CryptProtectData/CryptUnprotectData."""
        _fields_ = [("cbData", ctypes.wintypes.DWORD),
                     ("pbData", ctypes.POINTER(ctypes.c_char))]

    _crypt32 = ctypes.windll.crypt32
    _kernel32 = ctypes.windll.kernel32

    def _dpapi_encrypt(plaintext: bytes) -> bytes:
        """Encrypt bytes using Windows DPAPI (tied to current user)."""
        blob_in = _DATA_BLOB(len(plaintext), ctypes.create_string_buffer(plaintext, len(plaintext)))
        blob_out = _DATA_BLOB()
        if not _crypt32.CryptProtectData(
            ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)
        ):
            raise OSError("CryptProtectData failed")
        encrypted = ctypes.string_at(blob_out.pbData, blob_out.cbData)
        _kernel32.LocalFree(blob_out.pbData)
        return encrypted

    def _dpapi_decrypt(encrypted: bytes) -> bytes:
        """Decrypt bytes using Windows DPAPI."""
        blob_in = _DATA_BLOB(len(encrypted), ctypes.create_string_buffer(encrypted, len(encrypted)))
        blob_out = _DATA_BLOB()
        if not _crypt32.CryptUnprotectData(
            ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)
        ):
            raise OSError("CryptUnprotectData failed")
        decrypted = ctypes.string_at(blob_out.pbData, blob_out.cbData)
        _kernel32.LocalFree(blob_out.pbData)
        return decrypted


def _save_tokens_to_storage(data: dict) -> None:
    """Save token dict to platform-appropriate secure storage."""
    json_bytes = json.dumps(data).encode("utf-8")

    if _IS_WINDOWS:
        # Windows: DPAPI-encrypted file
        try:
            encrypted = _dpapi_encrypt(json_bytes)
            _TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
            _TOKEN_FILE.write_bytes(encrypted)
            print(f"[Auth] Tokens saved (DPAPI-encrypted) to {_TOKEN_FILE}")
            return
        except Exception as e:
            print(f"[Auth] DPAPI save failed: {e}")

    # Mac/Linux: keyring
    if keyring:
        try:
            keyring.set_password(SERVICE_NAME, KEYRING_USERNAME, json_bytes.decode("utf-8"))
            print("[Auth] Tokens saved to keyring")
            return
        except Exception as e:
            print(f"[Auth] Keyring save failed: {e}")

    # Last resort: plaintext file — restrict to owner-only access
    _TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    _TOKEN_FILE.write_bytes(json_bytes)
    try:
        _TOKEN_FILE.chmod(0o600)
    except OSError:
        pass  # Windows may not support POSIX permissions
    print(f"[Auth] WARNING: Tokens saved UNENCRYPTED to {_TOKEN_FILE} "
          f"(DPAPI and keyring both unavailable)")


def _load_tokens_from_storage() -> Optional[str]:
    """Load token JSON string from platform-appropriate secure storage."""
    if _IS_WINDOWS and _TOKEN_FILE.exists():
        try:
            encrypted = _TOKEN_FILE.read_bytes()
            decrypted = _dpapi_decrypt(encrypted)
            return decrypted.decode("utf-8")
        except Exception as e:
            print(f"[Auth] DPAPI load failed: {e}")
            # Fall through to try other methods

    if keyring:
        try:
            raw = keyring.get_password(SERVICE_NAME, KEYRING_USERNAME)
            if raw:
                return raw
        except Exception:
            pass

    # Fallback: try reading file as plaintext (migration or non-Windows)
    if _TOKEN_FILE.exists():
        try:
            raw = _TOKEN_FILE.read_bytes()
            # Try to decode as UTF-8 (plaintext JSON)
            return raw.decode("utf-8")
        except Exception:
            pass

    return None


def _delete_tokens_from_storage() -> None:
    """Clear tokens from all storage locations."""
    if keyring:
        try:
            keyring.delete_password(SERVICE_NAME, KEYRING_USERNAME)
            print("[Auth] Cleared keyring entry")
        except Exception as e:
            print(f"[Auth] Keyring delete skipped: {e}")
    try:
        if _TOKEN_FILE.exists():
            _TOKEN_FILE.unlink()
            print(f"[Auth] Deleted token file: {_TOKEN_FILE}")
    except Exception as e:
        print(f"[Auth] Failed to delete token file: {e}")


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

        self._token_lock = Lock()
        self._debug_auth = os.environ.get("CLIPABIT_AUTH_DEBUG", "").lower() in {
            "1",
            "true",
            "yes",
            "on",
        }

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
    ) -> Tuple[int, Callable[[int], Tuple[Optional[str], bool]], "Event", dict, "HTTPServer"]:
        """
        Start one-shot callback server.

        Returns (port, wait_for_callback_fn, event, result_dict, server).
        wait_for_callback(timeout_seconds) blocks until callback received or timeout,
        then returns (code, state_valid).
        """
        result: dict = {
            "code": None,
            "state_valid": False,
            "error": None,
            "error_description": None,
            "error_uri": None,
        }
        event = Event()

        class CallbackHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                parsed = urlparse(self.path)
                if parsed.path in ("/callback", "/callback/"):
                    params = parse_qs(parsed.query)
                    code = params.get("code", [""])[0] or None
                    state = params.get("state", [""])[0] or None
                    error = params.get("error", [""])[0] or None
                    error_description = params.get("error_description", [""])[0] or None
                    error_uri = params.get("error_uri", [""])[0] or None

                    state_valid = state == expected_state if state else False
                    has_terminal_auth_response = bool(code or error)
                    result["state_valid"] = state_valid

                    # Only persist terminal callback data (code or error).
                    # Ignore partial callbacks that only carry state.
                    if state_valid and has_terminal_auth_response:
                        result["code"] = code
                        result["error"] = error
                        result["error_description"] = error_description
                        result["error_uri"] = error_uri

                    code_preview = f"{code[:8]}..." if code else "None"
                    state_preview = f"{state[:8]}..." if state else "None"
                    print(f"[Auth] Received callback: code={code_preview}, state={state_preview}")
                    print(
                        f"[Auth] State validation: {'PASS' if result['state_valid'] else 'FAIL'}"
                    )
                    if error:
                        print(f"[Auth] Authorization error: {error}")
                    if error_description:
                        print(f"[Auth] Authorization error description: {error_description}")
                    if error_uri:
                        print(f"[Auth] Authorization error uri: {error_uri}")

                    self.send_response(200)
                    self.send_header("Content-type", "text/html")
                    self.end_headers()
                    logo_svg = (_RESOURCES_DIR / "logo.svg").read_text(encoding="utf-8")
                    # Only show success/error when we have a terminal response (code or error).
                    # Otherwise show a neutral waiting page so the user doesn't see "Login Failed"
                    # while the flow may still be in progress.
                    if has_terminal_auth_response:
                        if error or not result["state_valid"] or not code:
                            template = (_RESOURCES_DIR / "callback_error.html").read_text(encoding="utf-8")
                        else:
                            template = (_RESOURCES_DIR / "callback_success.html").read_text(encoding="utf-8")
                    else:
                        template = (_RESOURCES_DIR / "callback_waiting.html").read_text(encoding="utf-8")
                    html = template.replace("LOGO_SVG_PLACEHOLDER", logo_svg).encode()
                    self.wfile.write(html)
                    if has_terminal_auth_response:
                        event.set()
                    else:
                        print("[Auth] Callback missing code/error; waiting for terminal callback")
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
        server.timeout = 0  # Non-blocking for handle_request()

        def wait_for_callback(timeout: int = 0) -> Tuple[Optional[str], bool]:
            print(f"[Auth] Waiting for callback (timeout: {timeout}s)...")
            received = event.wait(timeout)
            if not received and timeout > 0:
                print("[Auth] Timeout reached - no callback received")
                print("[Auth] Login cancelled by user")
            elif result["code"] is None and event.is_set():
                print("[Auth] Callback completed without authorization code")
                if result.get("error"):
                    print(f"[Auth] Authorization error: {result['error']}")
                if result.get("error_description"):
                    print(f"[Auth] Authorization error description: {result['error_description']}")
                if result.get("error_uri"):
                    print(f"[Auth] Authorization error uri: {result['error_uri']}")
            print("[Auth] Callback server shutting down")
            server.server_close()
            return result["code"], result["state_valid"]

        return port, wait_for_callback, event, result, server

    def initiate_login(self):
        """
        Start login flow: generate PKCE, start callback server.

        Returns (port, verifier, wait_for_callback, event, result_dict, server, authorization_url).
        """
        verifier, challenge = self._generate_pkce_pair()
        state = self._generate_state()

        port, wait_for_callback, event, result_dict, server = self._start_callback_server(expected_state=state)

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

        print("[Auth] Authorization URL prepared")
        if self._debug_auth:
            print(f"[Auth] Authorization URL: {authorization_url}")

        return port, verifier, wait_for_callback, event, result_dict, server, authorization_url

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
            print(f"[Auth] Token exchange failed: HTTP {response.status_code}")
            if self._debug_auth:
                print(f"[Auth] Token exchange response body: {response.text}")
            return None

        data = response.json()
        access_token = data.get("access_token") or ""
        refresh_token = data.get("refresh_token")
        expires_in = data.get("expires_in", 0)

        print(f"[Auth] Received access_token: {'yes' if access_token else 'no'}")
        print(f"[Auth] Received refresh_token: {'yes' if refresh_token else 'no'}")
        if not refresh_token:
            print("[Auth] WARNING: No refresh_token received. Plugin will not be able to auto-refresh.")
        print(f"[Auth] Token expires in: {expires_in}s")

        id_token = data.get("id_token", "")
        expires_at = time.time() + expires_in
        self._save_tokens(access_token or "", refresh_token or "", id_token, expires_at)

        return data

    def _save_tokens(self, access_token: str, refresh_token: str, id_token: str, expires_at: float) -> None:
        """Store tokens in secure platform storage."""
        data = {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "id_token": id_token,
            "expires_at": expires_at,
        }
        _save_tokens_to_storage(data)

    def _load_tokens(self) -> Optional[dict]:
        """Retrieve tokens from platform-appropriate storage. Returns dict or None."""
        print("[Auth] Loading tokens...")
        raw = _load_tokens_from_storage()
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
        """Check if valid tokens are available (logged in)."""
        try:
            tokens = self._load_tokens()
            if not tokens:
                return False
            has_access = bool(tokens.get("access_token"))
            has_refresh = bool(tokens.get("refresh_token"))
            return has_access or has_refresh
        except Exception as e:
            print(f"[Auth] is_logged_in check failed: {e}")
            return False

    def delete_tokens(self) -> None:
        """Clear tokens from all storage locations (logout)."""
        print("[Auth] Clearing tokens...")
        _delete_tokens_from_storage()

    def refresh_tokens(self, tokens: Optional[dict] = None) -> Optional[dict]:
        """
        Public entry point for token refresh (used by NetworkClient on 401).
        Thread-safe: prevents multiple simultaneous refresh attempts.
        """
        with self._token_lock:
            return self._refresh_tokens(tokens)

    def _refresh_tokens(self, tokens: Optional[dict] = None) -> Optional[dict]:
        """Refresh access token using refresh_token. Returns new token data or None."""
        if tokens is None:
            tokens = self._load_tokens()
        
        if not tokens:
            print("[Auth] Refresh aborted: No tokens found in storage")
            return None
            
        rt = tokens.get("refresh_token")
        if not rt:
            print("[Auth] Refresh aborted: No refresh_token available")
            return None

        token_url = f"https://{self.AUTH0_DOMAIN}/oauth/token"
        payload = {
            "grant_type": "refresh_token",
            "client_id": self.CLIENT_ID,
            "refresh_token": rt,
        }

        print(f"[Auth] Access token expired or invalid, refreshing (RT: {rt[:8]}...)...")

        try:
            response = requests.post(
                token_url,
                data=payload,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=15,
            )
        except Exception as e:
            print(f"[Auth] Refresh request failed (network error): {e}")
            return None

        if not response.ok:
            print(f"[Auth] Refresh failed: HTTP {response.status_code}")
            try:
                error_data = response.json()
                error_code = error_data.get("error")
                error_desc = error_data.get("error_description")
                print(f"[Auth] Error: {error_code} - {error_desc}")
                
                # If the refresh token is explicitly invalid or revoked, we should clear it.
                # Common Auth0 error codes for this are 'invalid_grant'.
                # TODO: validate this
                if error_code == "invalid_grant":
                    print("[Auth] Refresh token is invalid/revoked. Would normally clear tokens.")
                    # print("[Auth] Refresh token is invalid/revoked. Clearing tokens.")
                    # self.delete_tokens()
            except Exception:
                if self._debug_auth:
                    print(f"[Auth] Refresh error response body: {response.text}")
            return None

        data = response.json()
        access_token = data.get("access_token") or ""
        expires_in = data.get("expires_in", 0)
        # Auth0 might return a new refresh token if rotation is enabled
        refresh_token = data.get("refresh_token") or rt
        id_token = data.get("id_token") or tokens.get("id_token", "")

        print(f"[Auth] New access_token received: {'yes' if access_token else 'no'}")

        expires_at = time.time() + expires_in
        self._save_tokens(access_token, refresh_token, id_token, expires_at)
        print("[Auth] Tokens updated in secure storage")

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

            return access_token


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
        port, verifier, wait, event, result_dict, server, auth_url = mgr.initiate_login()
        print(f"[Auth] Please open this URL in your browser to log in:\n{auth_url}")
        redirect_uri = f"http://127.0.0.1:{port}/callback"
        code, ok = wait(timeout=300)  # ~5 min
        print(f"[Auth] Result: code={'...' if code else None}, state_valid={ok}")
        if code and ok:
            tokens = mgr.exchange_code_for_tokens(code, verifier, redirect_uri)
            if tokens:
                print(f"[Auth] Success! access_token present: {bool(tokens.get('access_token'))}")
        elif not code:
            print("[Auth] Login cancelled")
