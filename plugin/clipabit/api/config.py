import os

class Config:
    """Configuration for ClipABit plugin."""
    
    # Environment (can be overridden via environment variable)
    ENVIRONMENT = os.environ.get("CLIPABIT_ENVIRONMENT", "staging")

    # Dev combined mode (single app) toggle for local development.
    DEV_COMBINED = os.environ.get("CLIPABIT_DEV_COMBINED", "true").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }

    # Modal ASGI app base URLs
    # URL format: https://clipabit01--{app-name}-{classname-lowercase}-asgi-app.modal.run
    if ENVIRONMENT == "dev" and DEV_COMBINED:
        # Dev combined mode: both services in dev-server app
        # DevServer class -> devserver-asgi-app
        # DevSearchService wraps SearchService -> searchservice-asgi-app
        SERVER_BASE_URL = "https://clipabit01--dev-server-devserver-asgi-app-dev.modal.run"
        SEARCH_BASE_URL = "https://clipabit01--dev-server-searchservice-asgi-app-dev.modal.run"
    else:
        # Staging/Production: separate apps
        # staging-server app: Server class -> server-asgi-app
        # staging-search app: SearchService class -> searchservice-asgi-app
        SERVER_BASE_URL = f"https://clipabit01--{ENVIRONMENT}-server-server-asgi-app-dev.modal.run"
        SEARCH_BASE_URL = f"https://clipabit01--{ENVIRONMENT}-search-searchservice-asgi-app-dev.modal.run"
    
    # API Endpoints - routes within the ASGI apps
    SEARCH_API_URL = f"{SEARCH_BASE_URL}/search"
    UPLOAD_API_URL = f"{SERVER_BASE_URL}/upload"
    STATUS_API_URL = f"{SERVER_BASE_URL}/status"
    DELETE_API_URL = f"{SERVER_BASE_URL}/videos"
    
    # Device Auth Endpoints
    DEVICE_CODE_URL = f"{SERVER_BASE_URL}/auth/device/code"
    DEVICE_POLL_URL = f"{SERVER_BASE_URL}/auth/device/poll"
    
    # Timeouts and delays
    UPLOAD_TIMEOUT = 300
    STATUS_CHECK_TIMEOUT = 10
    STATUS_CHECK_INTERVAL = 2
    QUEUE_DELAY = 1000  # milliseconds
