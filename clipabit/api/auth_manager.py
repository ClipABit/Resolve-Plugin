import secrets
import hashlib
import base64
from typing import Tuple


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
