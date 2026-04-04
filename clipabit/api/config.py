import os


class Config:
    """Configuration for ClipABit plugin."""

    # Environment (can be overridden via environment variable)
    ENVIRONMENT = os.environ.get("CLIPABIT_ENVIRONMENT", "dev")

    # Modal ASGI app base URLs
    # URL format: https://clipabit01--{app-name}-{classname-lowercase}-asgi-app.modal.run
    # Base URLs
    # set CLIPABIT_DEV_REMOTE=true to use modal dev URLs
    DEV_REMOTE = os.environ.get(
        "CLIPABIT_DEV_REMOTE", "").lower() in {"1", "true"}

    if ENVIRONMENT == "dev" and not DEV_REMOTE:
        SERVER_BASE_URL = "http://127.0.0.1:8000"
        SEARCH_BASE_URL = "http://127.0.0.1:8000"
    elif ENVIRONMENT == "dev":
        # Dev combined mode: both services in dev-server app
        # App name is {DEV_NAME}-dev-server (matches monorepo dev_combined.py)
        DEV_NAME = os.environ.get("CLIPABIT_DEV_NAME", "dev")
        SERVER_BASE_URL = f"https://clipabit01--{DEV_NAME}-dev-server-devserver-asgi-app-dev.modal.run"
        SEARCH_BASE_URL = f"https://clipabit01--{DEV_NAME}-dev-server-searchservice-asgi-app-dev.modal.run"
    else:
        # Staging/Production: separate apps
        # staging-server app: Server class -> server-asgi-app
        # staging-search app: SearchService class -> searchservice-asgi-app
        SERVER_BASE_URL = f"https://clipabit01--{ENVIRONMENT}-server-server-asgi-app.modal.run"
        SEARCH_BASE_URL = f"https://clipabit01--{ENVIRONMENT}-search-searchservice-asgi-app.modal.run"

    # Allow env var overrides for custom dev servers
    SERVER_BASE_URL = os.environ.get("CLIPABIT_SERVER_URL", SERVER_BASE_URL)
    SEARCH_BASE_URL = os.environ.get("CLIPABIT_SEARCH_URL", SEARCH_BASE_URL)

    # API Endpoints - routes within the ASGI apps
    SEARCH_API_URL = f"{SEARCH_BASE_URL}/search"
    UPLOAD_API_URL = f"{SERVER_BASE_URL}/upload"
    STATUS_API_URL = f"{SERVER_BASE_URL}/status"
    # Timeouts and delays
    UPLOAD_TIMEOUT = 300
    STATUS_CHECK_TIMEOUT = 10
    STATUS_CHECK_INTERVAL = 2
    QUEUE_DELAY = 1000  # milliseconds

    # Repo information (for auto-update)
    OWNER = "ClipABit"
    REPO = "Resolve-Plugin"
    RELEASE_TAG = "1.4.1rc1"
