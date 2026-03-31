import datetime
import hashlib
import os
import platform
import sys
import time
import traceback
import uuid
import webbrowser
from pathlib import Path
from typing import Dict, List, Optional

try:
    from PyQt6.QtWidgets import (QApplication, QWidget, QVBoxLayout, QHBoxLayout,
                                 QLabel, QPushButton, QMessageBox, QLineEdit,
                                 QScrollArea, QFrame, QListWidget,
                                 QDialog, QCheckBox, QGridLayout, QProgressBar)
    from PyQt6.QtCore import Qt, QTimer
    from PyQt6.QtSvgWidgets import QSvgWidget
    from PyQt6.QtNetwork import QLocalServer, QLocalSocket
except ImportError as e:
    print(f"Error: PyQt6 not found or missing component: {e}")
    # We can't exit here if imported by shim, but we log the issue
    print("Warning: PyQt6 import failed in main_window.py")

# Local imports
from ..api.config import Config
from ..core.job_tracker import JobTracker
from ..core.utils import (
    get_storage_path, load_processed_files, save_processed_files,
    get_file_hash, get_hashed_identifier
)
from ..core.network import NetworkClient
from ..core.uploader import FileUploader
from ..core.version_manager import (
    load_installed_version, save_installed_version,
    is_newer, is_prerelease, get_plugin_install_dir, apply_update,
)
from ..api.auth_manager import AuthManager
from .theme import Theme
from .video_preview import VideoPreviewDialog, extract_thumbnail

# --- Setup Resolve API Globals ---
resolve = None
project = None
media_pool = None
project_manager = None

# Single process window instance
_instance = None

# Single application process server stuff
_SINGLE_INSTANCE_KEY = "ClipABitResolvePluginSingleton"
_single_instance_server = None


def _send_single_instance_message():
    """Notify running instance to activate and then exit."""
    try:
        socket = QLocalSocket()
        socket.connectToServer(_SINGLE_INSTANCE_KEY)
        if not socket.waitForConnected(300):
            return False
        socket.write(b"activate")
        socket.flush()
        socket.waitForBytesWritten(300)
        socket.disconnectFromServer()
        return True
    except Exception as e:
        print(f"[Plugin] Single instance signaling failure: {e}")
        return False


def _setup_single_instance_server():
    """Start local server to accept activation requests from new processes."""
    global _single_instance_server

    try:
        # Remove stale socket from prior crashes.
        QLocalServer.removeServer(_SINGLE_INSTANCE_KEY)
    except Exception:
        pass

    _single_instance_server = QLocalServer()

    def _on_new_connection():
        global _instance
        sock = _single_instance_server.nextPendingConnection()
        if sock:
            try:
                if sock.waitForReadyRead(300):
                    _ = sock.readAll()
            except Exception:
                pass
            finally:
                sock.disconnectFromServer()

        if _instance is not None:
            if _instance.isMinimized():
                _instance.showNormal()
            _instance.show()
            _instance.raise_()
            _instance.activateWindow()

    _single_instance_server.newConnection.connect(_on_new_connection)

    if not _single_instance_server.listen(_SINGLE_INSTANCE_KEY):
        try:
            QLocalServer.removeServer(_SINGLE_INSTANCE_KEY)
        except Exception:
            pass
        _single_instance_server = QLocalServer()
        _single_instance_server.newConnection.connect(_on_new_connection)
        _single_instance_server.listen(_SINGLE_INSTANCE_KEY)

# Try to load standalone API (local dev usage) or check for global 'app'
try:
    import DaVinciResolveScript as dvr_script
    resolve = dvr_script.scriptapp("Resolve")
except ImportError:
    pass

if not resolve:
    # Try global app (if running directly inside Resolve console)
    try:
        # 'app' is often available in the console scope
        resolve = app.GetResolve()
    except NameError:
        pass

# If we found it during import (unlikely in package mode but possible), set up others
if resolve:
    try:
        project_manager = resolve.GetProjectManager()
        project = project_manager.GetCurrentProject()
        media_pool = project.GetMediaPool()
    except Exception:
        print("Warning: Found resolve logic but failed to init project/media_pool")
        resolve = None

class ClipABitApp(QWidget):
    def __init__(self):
        super().__init__()
        
        # Log version on startup
        installed_ver = load_installed_version(Config.RELEASE_TAG)
        print(f"[ClipABit] Version: {installed_ver}")
        
        # Initialize data
        self.clip_map = {}
        self.processed_files = load_processed_files()
        self.current_jobs = {}  # job_id -> job_info
        self._search_generation = 0  # Incremented on clear to cancel pending thumbnail loads
        self.dialog_jobs_list = None
        self.dialog_file_status = None
        
        # Upload queue system
        self.upload_queue = []  # List of files waiting to be uploaded
        self.current_upload = None  # Currently uploading file info
        self.is_uploading = False  # Flag to prevent concurrent uploads
        
        # Auth manager and token getter for API calls
        try:
            self.auth_manager = AuthManager()
        except ValueError as e:
            print(f"[Auth] Configuration error: {e}")
            self.auth_manager = None

        # Shared non-blocking HTTP client (uses QNetworkAccessManager internally)
        self._network = NetworkClient(auth_manager=self.auth_manager, parent=self)
        self._network.reauth_required.connect(self._on_network_reauth)

        # Initialize job tracker (QObject with internal QTimer — no threads)
        self.job_tracker = JobTracker(network=self._network, parent=self)
        self.job_tracker.job_completed.connect(self._on_job_completed)
        self.job_tracker.job_failed.connect(self._on_job_failed)
        self.job_tracker.start()
        
        # Build clip map and check for new files (only if Resolve is available)
        if resolve:
            self.clip_map = self._build_clip_map(debug=False)
            print(f"Found {len(self.clip_map)} clips in media pool")
        else:
            self.clip_map = {}
            print("Running without Resolve API - clip map disabled")
        
        # Detect system theme (macOS/Windows light/dark mode)
        Theme.detect_system_theme()
        
        # Setup UI
        self.setWindowTitle("ClipABit - Search Videos")
        self.resize(1000, 700)
        self.setMinimumSize(800, 600)
        self.init_ui()
        self._apply_theme()

        # Startup auth check
        is_logged_in = self.auth_manager.is_logged_in() if self.auth_manager else False
        print(f"[Auth] Startup check: logged_in={is_logged_in}")
        self._update_auth_button()
        
        # Setup refresh timer (disabled by default; refresh on demand)
        self.refresh_timer = QTimer()
        self.refresh_timer.timeout.connect(lambda: self._refresh_media_pool(debug=False))
        
        # Run consistency check on startup
        self._run_consistency_check("startup")
        
    def init_ui(self):
        """Initialize the UI matching Figma design."""
        # Main layout
        main_layout = QVBoxLayout()
        main_layout.setSpacing(0)
        main_layout.setContentsMargins(0, 0, 0, 0)
        
        # Header bar with buttons (top-right)
        self.header = self._create_header()
        main_layout.addWidget(self.header)
        
        # Content area - we'll use a stacked approach with two content widgets
        # Content for "Get Started" (sign-in) screen
        self.get_started_content = self._create_get_started_content()
        main_layout.addWidget(self.get_started_content, 1)
        
        # Content for upload panel (after login)
        self.upload_content = self._create_upload_content()
        main_layout.addWidget(self.upload_content, 1)
        
        # Status bar at bottom
        self.status_label = QLabel("Ready")
        self.status_label.setObjectName("statusBar")
        self.status_label.setContentsMargins(10, 4, 10, 4)
        main_layout.addWidget(self.status_label)

        self.setLayout(main_layout)
        
        # Show appropriate content based on login state
        self._update_ui_for_auth_state()
    
    def _create_get_started_content(self):
        """Create the 'Get Started' screen shown when not logged in."""
        content = QWidget()
        content.setObjectName("getStartedContent")
        layout = QVBoxLayout()
        layout.setSpacing(0)
        layout.setContentsMargins(0, 0, 0, 0)

        # Center container
        center_container = QWidget()
        center_layout = QVBoxLayout()
        center_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        center_layout.setSpacing(12)

        # Large logo for landing page
        logo_path = Path(__file__).parent.parent.parent / "assets" / "logo-dark.svg"
        if logo_path.exists():
            self.landing_logo = QSvgWidget(str(logo_path))
            self.landing_logo.setFixedSize(260, 120)
        else:
            self.landing_logo = QLabel("ClipABit")
            self.landing_logo.setStyleSheet("font-size: 32px; font-weight: bold;")
            self.landing_logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        center_layout.addWidget(self.landing_logo, alignment=Qt.AlignmentFlag.AlignCenter)

        # Tagline text
        tagline = QLabel("Search by ideas, not timestamps.")
        tagline.setAlignment(Qt.AlignmentFlag.AlignCenter)
        tagline.setObjectName("taglineLabel")
        center_layout.addWidget(tagline)
        
        # Get Started button (yellow, rectangular)
        self.btn_get_started = QPushButton("Get Started")
        self.btn_get_started.setObjectName("getStartedButton")
        self.btn_get_started.setFixedSize(240, 36)
        self.btn_get_started.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_get_started.clicked.connect(self._on_auth_button_clicked)
        center_layout.addWidget(self.btn_get_started, alignment=Qt.AlignmentFlag.AlignCenter)
        
        center_container.setLayout(center_layout)
        
        layout.addStretch()
        layout.addWidget(center_container)
        layout.addStretch()
        
        content.setLayout(layout)
        return content
    
    def _create_upload_content(self):
        """Create the upload panel shown after login with search bar and upload zone."""
        content = QWidget()
        content.setObjectName("uploadContent")
        layout = QVBoxLayout()
        layout.setSpacing(20)
        layout.setContentsMargins(40, 20, 40, 40)

        # Title - "Search Videos"
        self.title_label = QLabel("Search Videos")
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.title_label.setObjectName("mainTitle")
        layout.addWidget(self.title_label)

        # Search bar
        search_container = self._create_search_bar()
        layout.addWidget(search_container, alignment=Qt.AlignmentFlag.AlignCenter)

        # Results area (grid or empty state)
        self.results_container = QWidget()
        self.results_layout = QVBoxLayout()
        self.results_layout.setContentsMargins(0, 0, 0, 0)
        self.results_container.setLayout(self.results_layout)

        # Upload zone frame with dashed border (shown as empty state)
        self.upload_zone = QFrame()
        self.upload_zone.setObjectName("uploadZone")
        self.upload_zone.setFixedSize(420, 280)

        zone_layout = QVBoxLayout()
        zone_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        zone_layout.setSpacing(16)
        zone_layout.setContentsMargins(40, 30, 40, 30)

        # Upload arrow icon
        icon_path = Path(__file__).parent.parent / "assets" / "cloud-upload.svg"
        if icon_path.exists():
            self.upload_icon = QSvgWidget(str(icon_path))
            self.upload_icon.setFixedSize(60, 60)
        else:
            self.upload_icon = QLabel("↑")
            self.upload_icon.setStyleSheet("font-size: 48px; color: #7B8CA0;")
            self.upload_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        zone_layout.addWidget(self.upload_icon, alignment=Qt.AlignmentFlag.AlignCenter)

        # Browse button (pill-shaped, orange)
        self.btn_browse = QPushButton("Browse")
        self.btn_browse.setObjectName("browseButton")
        self.btn_browse.setFixedSize(180, 44)
        self.btn_browse.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_browse.clicked.connect(self._show_upload_dialog)
        zone_layout.addWidget(self.btn_browse, alignment=Qt.AlignmentFlag.AlignCenter)

        # Helper text
        helper_text = QLabel("Add files to your Media Pool to begin")
        helper_text.setObjectName("uploadHelperText")
        helper_text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        zone_layout.addWidget(helper_text)

        self.upload_zone.setLayout(zone_layout)
        self.results_layout.addWidget(self.upload_zone, alignment=Qt.AlignmentFlag.AlignCenter)

        # Empty state label (hidden by default, used after search)
        self.empty_state_label = QLabel("no queries made yet.")
        self.empty_state_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_state_label.setObjectName("emptyState")
        self.empty_state_label.setVisible(False)
        self.results_layout.addWidget(self.empty_state_label)

        # Scroll area for results
        self.results_scroll = QScrollArea()
        self.results_scroll.setWidgetResizable(True)
        self.results_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.results_scroll.setWidget(self.results_container)
        layout.addWidget(self.results_scroll, 1)

        content.setLayout(layout)
        return content
    
    def _update_ui_for_auth_state(self):
        """Show/hide UI elements based on authentication state."""
        is_logged_in = self.auth_manager.is_logged_in() if self.auth_manager else False
        
        # Check for updates when search screen is about to show
        if is_logged_in:
            local_tag = load_installed_version(Config.RELEASE_TAG)
            
            # Use staging track if local version is a pre-release 
            # OR if the environment is explicitly set to 'staging' or 'dev'.
            is_staging_env = Config.ENVIRONMENT in ("staging", "dev")
            use_stable_only = not (is_prerelease(local_tag) or is_staging_env)
            
            if is_staging_env:
                print(f"[Version] Forcing staging track (Env: {Config.ENVIRONMENT})")
            
            self._network.get_github_release_version(
                Config.OWNER, Config.REPO,
                stable_only=use_stable_only,
                on_success=self._on_version_check_success,
                on_error=self._on_version_check_error,
            )
        
        # Toggle visibility of content areas
        self.get_started_content.setVisible(not is_logged_in)
        self.upload_content.setVisible(is_logged_in)
        
        # Ensure header elements remain visible regardless of login state
        # so the 'Sign In' button is accessible.
        self.btn_auth.setVisible(True)
        self.logo_widget.setVisible(True)
        
        # Hide internal features when not logged in
        self.btn_media_pool.setVisible(is_logged_in)
        self.btn_jobs_debug.setVisible(is_logged_in)
        if hasattr(self, 'btn_migrate'):
            self.btn_migrate.setVisible(is_logged_in)
        self.btn_auth.setVisible(is_logged_in)
        self.logo_widget.setVisible(is_logged_in)

    
    def _create_header(self):
        """Create the header bar with logo and actions."""
        header = QWidget()
        header.setFixedHeight(80)
        header.setObjectName("header")
        
        layout = QHBoxLayout()
        layout.setContentsMargins(20, 15, 20, 15)
        layout.setSpacing(10)
        
        # Logo on the left using SVG (maintain aspect ratio: original is 292x135)
        logo_path = Path(__file__).parent.parent / "assets" / "logo-dark.svg"
        if logo_path.exists():
            self.logo_widget = QSvgWidget(str(logo_path))
            self.logo_widget.setObjectName("logoWidget")
            self.logo_widget.setFixedSize(130, 60)  # Maintains ~2.17:1 aspect ratio
        else:
            self.logo_widget = QLabel("ClipABit")
            self.logo_widget.setObjectName("logoWidget")
        layout.addWidget(self.logo_widget)
        
        # Spacer to push buttons to the right
        layout.addStretch()
        
        # Sign In / Sign Out button (only visible when logged in)
        self.btn_auth = QPushButton()
        self.btn_auth.setObjectName("headerButton")
        self.btn_auth.clicked.connect(self._on_auth_button_clicked)
        layout.addWidget(self.btn_auth)
        
        # Media Pool button
        self.btn_media_pool = QPushButton("Media Pool")
        self.btn_media_pool.setObjectName("headerButtonSecondary")
        self.btn_media_pool.clicked.connect(self._show_upload_dialog)
        layout.addWidget(self.btn_media_pool)
        
        # Small info icon button (top-right action)
        self.btn_jobs_debug = QPushButton("i")
        self.btn_jobs_debug.setObjectName("infoIconButton")
        self.btn_jobs_debug.setFixedSize(24, 24)
        self.btn_jobs_debug.setToolTip("Info")
        self.btn_jobs_debug.clicked.connect(self._show_jobs_dialog)
        layout.addWidget(self.btn_jobs_debug)

        if Config.ENVIRONMENT in ("staging", "dev"):
            self.btn_migrate = QPushButton("Migrate")
            self.btn_migrate.setObjectName("headerButtonSecondary")
            self.btn_migrate.setToolTip("Migrate processed files to current project")
            self.btn_migrate.clicked.connect(self._migrate_processed_files)
            layout.addWidget(self.btn_migrate)

        header.setLayout(layout)
        return header
    
    def _update_auth_button(self):
        """Update Sign In/Sign Out button based on login state."""
        if self.auth_manager and self.auth_manager.is_logged_in():
            self.btn_auth.setText("Sign Out")
        else:
            self.btn_auth.setText("Sign In")
        
        # Also update the UI state
        self._update_ui_for_auth_state()

    def _on_auth_button_clicked(self):
        """Handle Sign In or Sign Out button click."""
        if self.auth_manager is None:
            QMessageBox.warning(self, "Configuration Error", "Auth0 environment variables are not set.")
            return
        if self.auth_manager.is_logged_in():
            print("[Auth] Sign Out button clicked")
            self.auth_manager.delete_tokens()
            self._update_auth_button()
            self.status_label.setText("Signed out")
        else:
            print("[Auth] Sign In button clicked")
            if getattr(self, "_login_poll_timer", None) and self._login_poll_timer.isActive():
                return  # Already logging in
            try:
                port, verifier, wait, event, result_dict, server, auth_url = self.auth_manager.initiate_login()
                
                # Open browser automatically
                print(f"[Auth] Opening browser: {auth_url}")
                webbrowser.open(auth_url)

                self.status_label.setText("Complete login in browser...")
                self._login_state = {
                    "port": port,
                    "verifier": verifier,
                    "wait": wait,
                    "event": event,
                    "result": result_dict,
                    "server": server,
                }
                self._login_poll_timer = QTimer()
                self._login_poll_timer.timeout.connect(self._poll_login_callback)
                self._login_poll_timer.start(100)
            except Exception as e:
                print(f"[Auth] Login error: {e}")
                traceback.print_exc()
                QMessageBox.warning(self, "Login Error", str(e))

    def _poll_login_callback(self):
        """Pump the HTTP callback server and check for auth response."""
        state = getattr(self, "_login_state", None)
        if not state:
            self._login_poll_timer.stop()
            return
        try:
            state["server"].handle_request()
        except Exception:
            pass
        if state["event"].is_set():
            self._login_poll_timer.stop()
            code, ok = state["wait"](timeout=0)
            self._login_state = None
            if not code or not ok:
                self._on_login_finished(False, "Login cancelled")
                return
            tokens = self.auth_manager.exchange_code_for_tokens(
                code, state["verifier"], f"http://127.0.0.1:{state['port']}/callback"
            )
            if tokens:
                self._on_login_finished(True, "Login successful!")
            else:
                self._on_login_finished(False, "Token exchange failed")

    def _on_login_finished(self, success: bool, message: str):
        """Handle login flow completion."""
        self._update_auth_button()
        if success:
            if self.upload_queue and not self.is_uploading:
                QTimer.singleShot(100, self._process_upload_queue)
        else:
            QMessageBox.warning(self, "Login Failed", message)

    def _on_network_reauth(self):
        """Handle re-authentication prompt from NetworkClient or AuthManager."""
        self._update_auth_button()
        QMessageBox.warning(self, "Session Expired", "Please sign in again.")

    # ── Auto-update flow ─────────────────────────────────────────────

    def _on_version_check_success(self, status: int, data: dict):
        """Handle successful version check."""
        try:
            remote_tag = data.get("tag_name")
            zipball_url = data.get("zipball_url")
            if not remote_tag:
                print("[Version] No tag_name in release response")
                return

            local_tag = load_installed_version(Config.RELEASE_TAG)
            print(f"[Version] Local: {local_tag}  Remote: {remote_tag}")

            if not is_newer(remote_tag, local_tag):
                print("[Version] Already up to date")
                return

            print(f"[Version] Update available: {local_tag} -> {remote_tag}")
            self._pending_update = {
                "remote_tag": remote_tag,
                "zipball_url": zipball_url,
            }
            self._prompt_update(remote_tag)
        except Exception as e:
            print(f"[Version] Error parsing release data: {e}")
            traceback.print_exc()

    def _on_version_check_error(self, error: str):
        """Handle version check error (non-blocking)."""
        print(f"[Version] Failed to check release version: {error}")

    def _prompt_update(self, remote_tag: str):
        """Ask the user whether to download and install the update."""
        reply = QMessageBox.question(
            self,
            "Update Available",
            f"A new version ({remote_tag}) is available.\n\n"
            "Would you like to download and install it?\n"
            "The plugin will need to close after the update is applied.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._start_update_download()
        else:
            print("[Version] User declined update")

    def _start_update_download(self):
        """Download the zipball to a temp file."""
        info = getattr(self, "_pending_update", None)
        if not info:
            return
        import tempfile
        self._update_zip_path = os.path.join(
            tempfile.gettempdir(), f"clipabit-update-{info['remote_tag']}.zip"
        )
        print(f"[Version] Downloading update to {self._update_zip_path}")
        self._network.download_file(
            info["zipball_url"],
            self._update_zip_path,
            on_success=self._on_update_downloaded,
            on_error=self._on_update_download_error,
        )

    def _on_update_downloaded(self, zip_path: str):
        """Apply the downloaded update and prompt the user to restart."""
        info = getattr(self, "_pending_update", None)
        if not info:
            return
        try:
            install_dir = get_plugin_install_dir()
            apply_update(zip_path, install_dir)
            save_installed_version(info["remote_tag"])

            QMessageBox.information(
                self,
                "Update Installed",
                f"ClipABit has been updated to {info['remote_tag']}.\n\n"
                "The plugin will now close. Please reopen it to use the new version.",
            )
            self.close()
        except Exception as e:
            print(f"[Update] Failed to apply update: {e}")
            traceback.print_exc()
            QMessageBox.warning(
                self, "Update Failed",
                f"Could not apply update:\n{e}\n\nYou can continue using the current version.",
            )
        finally:
            try:
                os.remove(zip_path)
            except OSError:
                pass
            self._pending_update = None

    def _on_update_download_error(self, error: str):
        """Handle download failure (non-blocking)."""
        print(f"[Version] Update download failed: {error}")
        QMessageBox.warning(
            self, "Download Failed",
            f"Could not download the update:\n{error}\n\n"
            "You can continue using the current version.",
        )
        self._pending_update = None

    def _create_search_bar(self):
        """Create the search bar matching Figma design."""
        container = QWidget()
        container.setObjectName("searchContainer")
        container.setFixedHeight(44)
        container.setMinimumWidth(450)
        container.setMaximumWidth(550)
        
        layout = QHBoxLayout()
        layout.setContentsMargins(15, 4, 15, 4)
        layout.setSpacing(10)
        
        # Hidden search button for functionality (triggered by Enter key)
        self.btn_search = QPushButton()
        self.btn_search.setObjectName("searchButton")
        self.btn_search.setFixedSize(0, 0)  # Hidden
        self.btn_search.clicked.connect(self._perform_search)
        layout.addWidget(self.btn_search)
        
        # Search input
        self.search_input = QLineEdit()
        self.search_input.setObjectName("searchInput")
        self.search_input.setPlaceholderText("Enter search query (e.g. 'woman walking', 'car driving')")
        self.search_input.returnPressed.connect(self._perform_search)
        layout.addWidget(self.search_input, 1)
        
        # Clear button (X) - only visible when there's text
        self.btn_clear_search = QPushButton("✕")
        self.btn_clear_search.setObjectName("clearSearchButton")
        self.btn_clear_search.setFixedSize(24, 24)
        self.btn_clear_search.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_clear_search.clicked.connect(self._clear_search)
        self.btn_clear_search.setVisible(False)
        layout.addWidget(self.btn_clear_search)
        
        # Connect text changed to show/hide clear button
        self.search_input.textChanged.connect(self._on_search_text_changed)
        
        container.setLayout(layout)
        return container
    
    def _clear_search(self):
        """Clear the search input and results, returning to initial state with upload zone."""
        print("[Search] Clear button clicked")
        self._search_generation += 1  # Invalidate pending thumbnail loads
        self.search_input.clear()
        # Remove and destroy everything from results layout
        for i in reversed(range(self.results_layout.count())):
            item = self.results_layout.itemAt(i)
            if item.widget():
                item.widget().deleteLater()
            elif item.layout():
                self._clear_layout(item.layout())
            self.results_layout.removeItem(item)
        # Re-create the upload zone as the empty state
        self._rebuild_upload_zone()
        self.status_label.setText("Ready")

    def _rebuild_upload_zone(self):
        """Re-create the upload zone widget in the results layout."""
        self.upload_zone = QFrame()
        self.upload_zone.setObjectName("uploadZone")
        self.upload_zone.setFixedSize(420, 280)

        zone_layout = QVBoxLayout()
        zone_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        zone_layout.setSpacing(16)
        zone_layout.setContentsMargins(40, 30, 40, 30)

        icon_path = Path(__file__).parent.parent / "assets" / "cloud-upload.svg"
        if icon_path.exists():
            self.upload_icon = QSvgWidget(str(icon_path))
            self.upload_icon.setFixedSize(60, 60)
        else:
            self.upload_icon = QLabel("↑")
            self.upload_icon.setStyleSheet("font-size: 48px; color: #7B8CA0;")
            self.upload_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        zone_layout.addWidget(self.upload_icon, alignment=Qt.AlignmentFlag.AlignCenter)

        btn_browse = QPushButton("Browse")
        btn_browse.setObjectName("browseButton")
        btn_browse.setFixedSize(180, 44)
        btn_browse.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_browse.clicked.connect(self._show_upload_dialog)
        zone_layout.addWidget(btn_browse, alignment=Qt.AlignmentFlag.AlignCenter)

        helper_text = QLabel("Add files to your Media Pool to begin")
        helper_text.setObjectName("uploadHelperText")
        helper_text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        zone_layout.addWidget(helper_text)
        self.upload_zone.setLayout(zone_layout)
        self.results_layout.addWidget(self.upload_zone, alignment=Qt.AlignmentFlag.AlignCenter)
        # Re-apply stylesheet so new widgets pick up styles
        self._apply_theme()
    
    def _on_search_text_changed(self, text):
        """Show/hide clear button and upload zone based on search input text."""
        self.btn_clear_search.setVisible(bool(text))
        # Hide upload zone as soon as user starts typing
        if hasattr(self, 'upload_zone') and self.upload_zone:
            self.upload_zone.setVisible(not bool(text))

    def _build_namespace(self) -> str:
        """Build the namespace string from device id and project name."""
        user_id = self.get_or_create_device_id()
        project_name = self.get_project_name() or "default"
        user_id_safe = user_id.lower().replace(" ", "_")
        project_safe = project_name.lower().replace(" ", "_")
        return f"{user_id_safe}-{project_safe}"

    def _save_processed_files(self):
        save_processed_files(self.processed_files)

    def _get_project_processed_files(self) -> dict:
        """Return the processed-files bucket for the current Resolve project."""
        project_id = self.get_project_id()
        if not project_id:
            return {}
        return self.processed_files.get(project_id, {})

    def _apply_theme(self):
        """Apply the current theme stylesheet matching Figma mockups."""
        t = Theme.current
        
        self.setStyleSheet(f"""
            /* Main window */
            QWidget {{
                background-color: {t['background']};
                color: {t['text']};
                font-family: 'Helvetica Neue', 'Segoe UI', Roboto, sans-serif;
            }}
            
            /* Header */
            QWidget#header {{
                background-color: {t['background']};
            }}
            
            /* Logo widget - header size (rectangular) */
            QWidget#logoWidget {{
                background-color: transparent;
                border-radius: 0;
            }}
            
            /* Large logo for Get Started screen */
            QWidget#largeLogoContainer {{
                background-color: transparent;
                border-radius: 0;
            }}
            
            /* Tagline label (smaller) */
            QLabel#taglineLabel {{
                font-size: 18px;
                font-weight: 400;
                color: {t['text']};
                padding: 15px;
            }}
            
            /* Get Started button (yellow, rectangular) */
            QPushButton#getStartedButton {{
                background-color: #FAAF04;
                color: {t['button_text']};
                border: none;
                border-radius: 0;
                font-size: 14px;
                font-weight: 500;
            }}
            QPushButton#getStartedButton:hover {{
                background-color: #E09E04;
            }}
            
            /* Header button (primary - Sign In/Out, rectangular) */
            QPushButton#headerButton {{
                background-color: {t['accent']};
                color: {t['button_text']};
                border: none;
                border-radius: 0;
                padding: 8px 16px;
                font-size: 13px;
                font-weight: 500;
            }}
            QPushButton#headerButton:hover {{
                background-color: {t['accent_hover']};
            }}
            
            /* Header button secondary (Media Pool) */
            QPushButton#headerButtonSecondary {{
                background-color: #9CA3AF;
                color: #FFFFFF;
                border: none;
                border-radius: 0;
                padding: 8px 16px;
                font-size: 13px;
                font-weight: 500;
            }}
            QPushButton#headerButtonSecondary:hover {{
                background-color: #7B8794;
            }}

            /* Compact info icon button */
            QPushButton#infoIconButton {{
                background-color: transparent;
                color: {t['text_secondary']};
                border: 1px solid {t['text_secondary']};
                border-radius: 12px;
                font-size: 12px;
                font-weight: 700;
                padding: 0;
            }}
            QPushButton#infoIconButton:hover {{
                color: {t['text']};
                border-color: {t['text']};
                background-color: rgba(255, 255, 255, 0.08);
            }}
            
            /* Main title */
            QLabel#mainTitle {{
                font-size: 32px;
                font-weight: bold;
                color: {t['text']};
                padding: 20px;
            }}
            
            /* Search container (rectangular) */
            QWidget#searchContainer {{
                background-color: #F2F8FF;
                border-radius: 0;
            }}
            
            /* Search button (hidden) */
            QPushButton#searchButton {{
                background-color: {t['accent']};
                color: #000000;
                border: none;
                border-radius: 0;
                font-size: 14px;
                font-weight: 600;
            }}
            QPushButton#searchButton:hover {{
                background-color: {t['accent_hover']};
            }}
            
            /* Search input - darker text for light background */
            QLineEdit#searchInput {{
                background-color: transparent;
                border: none;
                color: #0F1729;
                font-size: 14px;
                padding: 0 10px;
            }}
            
            /* Clear search button */
            QPushButton#clearSearchButton {{
                background-color: transparent;
                color: #979797;
                border: none;
                font-size: 14px;
                font-weight: bold;
            }}
            QPushButton#clearSearchButton:hover {{
                color: #666666;
            }}
            
            /* Empty state */
            QLabel#emptyState {{
                color: {t['text_secondary']};
                font-size: 16px;
                padding: 100px;
            }}
            
            /* Status bar */
            QLabel#statusBar {{
                color: {t['text_secondary']};
                font-size: 11px;
                padding: 8px 20px;
                background-color: {t['background']};
            }}
            
            /* Result card (rectangular) */
            QFrame#resultCard {{
                background-color: {t['card_bg']};
                border-radius: 0;
                border: 1px solid {t['border']};
            }}
            
            /* Thumbnail placeholder (rectangular) */
            QLabel#thumbnail {{
                background-color: {t['card_thumbnail']};
                border-radius: 0;
            }}
            
            /* Progress bar (rectangular) */
            QProgressBar {{
                background-color: rgba(217, 217, 217, 0.3);
                border: none;
                border-radius: 0;
                height: 4px;
                text-align: center;
            }}
            QProgressBar::chunk {{
                background-color: {t['progress_bg']};
                border-radius: 0;
            }}
            
            /* Add to timeline button (rectangular) */
            QPushButton#addToTimelineBtn {{
                background-color: {t['accent']};
                color: #000000;
                border: none;
                border-radius: 0;
                padding: 8px 16px;
                font-size: 12px;
                font-weight: 500;
            }}
            QPushButton#addToTimelineBtn:hover {{
                background-color: {t['accent_hover']};
            }}
            
            /* Scroll area */
            QScrollArea {{
                border: none;
                background-color: transparent;
            }}
            QScrollBar:vertical {{
                background-color: {t['background']};
                width: 8px;
                border-radius: 0;
            }}
            QScrollBar::handle:vertical {{
                background-color: {t['border']};
                border-radius: 0;
                min-height: 30px;
            }}
            QScrollBar::handle:vertical:hover {{
                background-color: {t['text_secondary']};
            }}
            
            /* Dialog styling */
            QDialog {{
                background-color: {t['background']};
            }}
            QDialog QLabel {{
                color: {t['text']};
            }}
            QDialog QPushButton {{
                background-color: {t['accent']};
                color: #000000;
                border: none;
                border-radius: 0;
                padding: 10px 20px;
                font-weight: 500;
            }}
            QDialog QPushButton:hover {{
                background-color: {t['accent_hover']};
            }}
            
            /* Get Started content container */
            QWidget#getStartedContent {{
                background-color: {t['background']};
            }}
            
            /* Search content container */
            QWidget#searchContent {{
                background-color: {t['background']};
            }}
            
            /* Upload content container */
            QWidget#uploadContent {{
                background-color: {t['background']};
            }}
            
            /* Upload zone with dashed border */
            QFrame#uploadZone {{
                background-color: {t['upload_zone_bg']};
                border: 2px dashed {t['upload_zone_border']};
                border-radius: 2px;
            }}
            
            /* Browse button (pill-shaped) */
            QPushButton#browseButton {{
                background-color: {t['browse_btn_bg']};
                color: {t['browse_btn_text']};
                border: none;
                border-radius: 25px;
                font-size: 14px;
                font-weight: 500;
            }}
            QPushButton#browseButton:hover {{
                background-color: {t['accent_hover']};
            }}

            /* Upload helper text */
            QLabel#uploadHelperText {{
                color: #979797;
                font-size: 13px;
            }}

            /* Help bubble button */
            QPushButton#helpBubble {{
                background-color: #4A4B52;
                color: #FFFFFF;
                border: none;
                border-radius: 10px;
                font-size: 12px;
                font-weight: bold;
            }}
            QPushButton#helpBubble:hover {{
                background-color: #5A5B62;
            }}
        """)
    
    def _show_jobs_dialog(self):
        """Show the active jobs/info dialog."""
        dialog = QDialog(self)
        dialog.setWindowTitle("Info")
        dialog.setMinimumSize(500, 400)
        
        layout = QVBoxLayout()
        layout.setSpacing(15)
        layout.setContentsMargins(20, 20, 20, 20)
        
        # Jobs section
        jobs_title = QLabel("Active Jobs")
        jobs_title.setStyleSheet("font-size: 18px; font-weight: bold;")
        layout.addWidget(jobs_title)
        
        # Jobs list
        self.dialog_jobs_list = QListWidget()
        self.dialog_jobs_list.setMinimumHeight(150)
        self._update_jobs_list_widget(self.dialog_jobs_list)
        layout.addWidget(self.dialog_jobs_list)
        
        # Info section
        info_title = QLabel("Info")
        info_title.setStyleSheet("font-size: 18px; font-weight: bold;")
        layout.addWidget(info_title)
        
        # Storage path
        storage_path = get_storage_path()
        storage_label = QLabel(f"Storage: {storage_path}")
        storage_label.setWordWrap(True)
        storage_label.setStyleSheet("color: #8E8E93; font-size: 11px;")
        layout.addWidget(storage_label)
        
        # Processed files count
        processed_label = QLabel(f"Processed: {len(self._get_project_processed_files())} files")
        processed_label.setStyleSheet("color: #8E8E93; font-size: 11px;")
        layout.addWidget(processed_label)
        
        # Queue info
        queue_label = QLabel(f"Upload Queue: {len(self.upload_queue)} files")
        queue_label.setStyleSheet("color: #8E8E93; font-size: 11px;")
        layout.addWidget(queue_label)
        
        # Close button
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(dialog.accept)
        layout.addWidget(close_btn)
        
        dialog.setLayout(layout)
        try:
            dialog.exec()
        finally:
            self.dialog_jobs_list = None
    
    def _show_upload_dialog(self):
        """Show the upload dialog with media pool actions."""
        dialog = QDialog(self)
        dialog.setWindowTitle("Media Pool")
        dialog.setMinimumSize(450, 320)
        
        layout = QVBoxLayout()
        layout.setSpacing(15)
        layout.setContentsMargins(20, 20, 20, 20)
        
        # Upload section
        upload_title = QLabel("Upload Media")
        upload_title.setStyleSheet("font-size: 18px; font-weight: bold;")
        layout.addWidget(upload_title)
        
        # File status
        file_status = self._get_file_status_text()
        self.dialog_file_status = QLabel(file_status)
        self.dialog_file_status.setStyleSheet("color: #8E8E93;")
        layout.addWidget(self.dialog_file_status)
        
        # Select files button
        btn_select = QPushButton("Select Files to Upload")
        btn_select.setStyleSheet("background-color: #FFFFFF; color: #000000;")
        btn_select.clicked.connect(lambda: self._select_files_to_upload_dialog(dialog))
        layout.addWidget(btn_select)
        
        # Spacer
        layout.addStretch()
        
        # Close button
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(dialog.accept)
        layout.addWidget(close_btn)
        
        dialog.setLayout(layout)
        try:
            dialog.exec()
        finally:
            self.dialog_file_status = None
    
    def _get_file_status_text(self):
        """Get the file status text for display."""
        total = len(self.clip_map)
        processed = 0
        project_files = self._get_project_processed_files()
        for clip_info in self.clip_map.values():
            if isinstance(clip_info, list):
                for clip in clip_info:
                    filepath = clip.get('filepath')
                    if filepath and get_file_hash(filepath) in project_files:
                        processed += 1
            else:
                filepath = clip_info.get('filepath')
                if filepath and get_file_hash(filepath) in project_files:
                    processed += 1
        new_files = total - processed
        return f"Total: {total} files | Processed: {processed} | New: {new_files}"
    
    def _update_jobs_list_widget(self, list_widget):
        """Update a jobs list widget with current jobs."""
        list_widget.clear()
        if not self.current_jobs and not self.upload_queue:
            list_widget.addItem("No active jobs")
        else:
            for _, job_info in self.current_jobs.items():
                filename = job_info.get('filename', 'Unknown')
                list_widget.addItem(f"Processing: {filename}")
            for file_info in self.upload_queue:
                filename = file_info.get('filename', 'Unknown')
                list_widget.addItem(f"Queued: {filename}")
    
    def _select_files_to_upload_dialog(self, dialog):
        """Handle file selection from the settings dialog."""
        self._select_files_to_upload()
        # Update the dialog's file status
        if self.dialog_file_status:
            self.dialog_file_status.setText(self._get_file_status_text())

    def _update_file_status(self):
        """Update the file status display."""
        total_files = len(self.clip_map)
        processed_count = 0
        new_files = []
        
        for filename, clip_info in self.clip_map.items():
            project_files = self._get_project_processed_files()
            if isinstance(clip_info, list):
                # Handle multiple clips with same filename
                for clip in clip_info:
                    filepath = clip.get('filepath')
                    if filepath:
                        file_hash = get_file_hash(filepath)
                        if file_hash in project_files:
                            processed_count += 1
                        else:
                            new_files.append(filename)
            else:
                filepath = clip_info.get('filepath')
                if filepath:
                    file_hash = get_file_hash(filepath)
                    if file_hash in project_files:
                        processed_count += 1
                    else:
                        new_files.append(filename)
        
        new_count = len(set(new_files))
        queued_count = len(self.upload_queue)
        
        # Update status text to include queue info
        status_parts = [f"Files: {total_files} total, {processed_count} processed, {new_count} new"]
        if queued_count > 0:
            status_parts.append(f"{queued_count} queued")
        if self.is_uploading:
            status_parts.append("uploading...")
            
        status_text = ", ".join(status_parts)
        
        self.status_label.setText(status_text)

        # Update dialog status label if open
        try:
            if self.dialog_file_status is not None:
                # Check isVisible() might throw RuntimeError if underlying object is deleted
                if self.dialog_file_status.isVisible():
                    self.dialog_file_status.setText(status_text)
        except RuntimeError:
            # The widget gave up the ghost (was deleted C++ side)
            self.dialog_file_status = None
        except Exception as e:
            print(f"[UI] Error updating file status: {e}")
        
    def _select_files_to_upload(self):
        """Select media pool clips and add them to the upload queue."""
        if not resolve:
            QMessageBox.warning(self, "Resolve Not Available", "Resolve API is not available. Media pool selection is disabled.")
            return

        # Refresh media pool so we show the latest clips
        self._refresh_media_pool(debug=False)

        files_to_upload = []
        skipped_processed = 0
        skipped_queued = 0
        skipped_missing = 0
        seen_paths = set()

        def collect_candidate(entry):
            nonlocal skipped_processed, skipped_queued, skipped_missing
            filepath = entry.get('filepath')
            if not filepath:
                skipped_missing += 1
                return
            if filepath in seen_paths:
                return
            seen_paths.add(filepath)

            if not os.path.exists(filepath):
                skipped_missing += 1
                return

            filename = os.path.basename(filepath)
            file_hash = get_file_hash(filepath)

            if file_hash in self._get_project_processed_files():
                skipped_processed += 1
                return

            if self._is_file_being_processed(file_hash):
                skipped_queued += 1
                return

            files_to_upload.append({
                'filepath': filepath,
                'filename': filename,
                'hash': file_hash
            })

        for _, clip_info in self.clip_map.items():
            if isinstance(clip_info, list):
                for entry in clip_info:
                    collect_candidate(entry)
            else:
                collect_candidate(clip_info)

        if not files_to_upload:
            QMessageBox.information(
                self,
                "Info",
                f"No eligible media pool clips to upload.\nProcessed: {skipped_processed}, In queue: {skipped_queued}, Missing path: {skipped_missing}"
            )
            return

        # Build selection dialog from media pool candidates
        dialog = QDialog(self)
        dialog.setWindowTitle("Select Media Pool Clips")
        dialog.resize(700, 500)
        dialog.setModal(True)

        layout = QVBoxLayout()
        header = QLabel(f"<b>Select clips to upload ({len(files_to_upload)} available)</b>")
        layout.addWidget(header)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll_widget = QWidget()
        scroll_layout = QVBoxLayout()

        checkboxes = []
        for entry in files_to_upload:
            filename = entry.get('filename', 'Unknown')
            filepath = entry.get('filepath', '')
            checkbox = QCheckBox(f"{filename}\n{filepath}")
            checkbox.setChecked(False)
            checkbox.setToolTip(filepath)
            checkbox.setStyleSheet("font-size: 11px;")
            scroll_layout.addWidget(checkbox)
            checkboxes.append((checkbox, entry))

        scroll_layout.addStretch()
        scroll_widget.setLayout(scroll_layout)
        scroll.setWidget(scroll_widget)
        layout.addWidget(scroll)

        # Buttons
        button_row = QHBoxLayout()
        btn_select_all = QPushButton("Select All")
        btn_select_none = QPushButton("Select None")
        btn_cancel = QPushButton("Cancel")
        btn_add = QPushButton("Add Selected")

        def set_all(state: bool):
            for cb, _ in checkboxes:
                cb.setChecked(state)

        btn_select_all.clicked.connect(lambda: set_all(True))
        btn_select_none.clicked.connect(lambda: set_all(False))
        btn_cancel.clicked.connect(dialog.reject)
        btn_add.clicked.connect(dialog.accept)

        button_row.addWidget(btn_select_all)
        button_row.addWidget(btn_select_none)
        button_row.addStretch()
        button_row.addWidget(btn_cancel)
        button_row.addWidget(btn_add)

        layout.addLayout(button_row)
        dialog.setLayout(layout)

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        selected_files = [entry for cb, entry in checkboxes if cb.isChecked()]

        if not selected_files:
            QMessageBox.information(self, "Info", "No clips selected for upload.")
            return

        # Add files to upload queue
        self.upload_queue.extend(selected_files)

        # Create temporary job entries for UI
        for file_info in selected_files:
            temp_job_id = f"queued_{file_info['hash'][:8]}"
            self.current_jobs[temp_job_id] = {
                'filename': file_info['filename'],
                'filepath': file_info['filepath'],
                'file_hash': file_info['hash'],
                'status': 'queued',
                'temp_job': True
            }

        self._update_jobs_display()
        self._update_file_status()

        # Start processing queue
        if not self.is_uploading:
            self._process_upload_queue()

        QMessageBox.information(
            self,
            "Queued",
            f"Added {len(selected_files)} file(s) to upload queue.\nSkipped processed: {skipped_processed}, in queue: {skipped_queued}, missing: {skipped_missing}"
        )
            
    def _is_file_being_processed(self, file_hash: str) -> bool:
        """Check if file is currently being processed."""
        # Check if file is in current jobs (being processed)
        for job_info in self.current_jobs.values():
            if job_info.get('file_hash') == file_hash:
                return True
        
        # Check if file is in upload queue
        for file_info in self.upload_queue:
            if file_info.get('hash') == file_hash:
                return True
                
        return False
        
    def _delete_backend_entry(self, filename: str, hashed_identifier: str, namespace: str):
        """Backend delete is deactivated — will be reimplemented as a separate feature."""
        print(f"[Delete] DEACTIVATED — skipping backend delete for {filename}")
            
    def _process_upload_queue(self):
        """Process the next file in the upload queue."""
        if not self.upload_queue or self.is_uploading:
            return
        if not self.auth_manager or not self.auth_manager.get_valid_access_token():
            QMessageBox.warning(self, "Sign In Required", "Please sign in to upload.")
            return
            
        # Get next file from queue
        file_info = self.upload_queue.pop(0)
        self.current_upload = file_info
        self.is_uploading = True
        
        # Find and update the queued job to "processing" status
        temp_job_id = f"queued_{file_info['hash'][:8]}"
        if temp_job_id in self.current_jobs:
            self.current_jobs[temp_job_id]['status'] = 'processing'
            self._update_jobs_display()
        
        # Update UI
        remaining = len(self.upload_queue)
        filename = file_info['filename']
        self.status_label.setText(f"Starting upload: {filename} ({remaining} remaining)")
        
        namespace = self._build_namespace()
        project_id = self.get_project_id() or ""
        hashed_id = get_hashed_identifier(file_info['filepath'])
        file_info['hashed_identifier'] = hashed_id
        file_info['project_id'] = project_id

        # Start uploader (non-blocking — uses QNetworkAccessManager internally)
        self.current_uploader = FileUploader(
            file_info, namespace,
            hashed_identifier=hashed_id, project_id=project_id,
            network=self._network, parent=self,
        )
        self.current_uploader.upload_started.connect(self._on_upload_started)
        self.current_uploader.upload_progress.connect(self._on_upload_progress)
        self.current_uploader.upload_success.connect(self._on_upload_success)
        self.current_uploader.upload_failed.connect(self._on_upload_error)
        self.current_uploader.start()

    def _on_upload_started(self, filename: str):
        """Handle upload start."""
        self.status_label.setText(f"Uploading: {filename}...")

    def _on_upload_progress(self, filename: str, msg: str):
        """Handle upload progress message."""
        self.status_label.setText(msg)

    def _on_upload_success(self, filename: str, file_hash: str, result: dict):
        """Handle successful upload."""
        try:
            job_id = result.get("job_id")
            print(f"[Upload] Upload successful, job_id: {job_id}")
            
            # Cleanup temp job
            temp_job_id = f"queued_{file_hash[:8]}"
            if temp_job_id in self.current_jobs:
                del self.current_jobs[temp_job_id]
                
            # Create real job entry — read from the FileUploader instance
            # (not shared state) so the data is always correct for this upload.
            uploader = self.current_uploader
            job_info = {
                'filename': filename,
                'filepath': uploader.filepath if uploader else "",
                'file_hash': file_hash,
                'status': 'processing',
                'namespace': uploader.namespace if uploader else "",
                'hashed_identifier': uploader.hashed_identifier if uploader else "",
                'project_id': uploader.project_id if uploader else "",
            }

            self.current_jobs[job_id] = job_info
            self.job_tracker.add_job(job_id, job_info)
            self._update_jobs_display()
            self._update_file_status()

            # Show quota info if available
            vector_count = result.get("vector_count")
            vector_quota = result.get("vector_quota")
            if vector_count is not None and vector_quota is not None:
                self.status_label.setText(
                    f"Upload started: {filename} (vectors: {vector_count}/{vector_quota})"
                )
            else:
                self.status_label.setText(f"Upload started: {filename}")
            
            # Continue with next upload
            self._on_upload_completed(True)
        except Exception as e:
            err_msg = f"Critical error in upload success handler: {e}\n{traceback.format_exc()}"
            print(err_msg)
            QMessageBox.critical(self, "Plugin Error", f"An error occurred after upload:\n{e}")
            self._on_upload_completed(False)

    def _on_upload_error(self, filename: str, file_hash: str, error_msg: str):
        """Handle upload failure."""
        print(f"[Upload] Error: {error_msg}")
        
        temp_job_id = f"queued_{file_hash[:8]}"
        if temp_job_id in self.current_jobs:
            del self.current_jobs[temp_job_id]
            
        QMessageBox.warning(self, "Upload Error", f"Failed to upload {filename}:\n{error_msg}")
        
        # Continue with next upload even if failed
        self._on_upload_completed(False)
            
    def _on_job_completed(self, job_id: str, result: dict):
        """Handle job completion."""
        try:
            if job_id in self.current_jobs:
                job_info = self.current_jobs[job_id]
                filename = job_info['filename']
                filepath = job_info['filepath']
                file_hash = job_info['file_hash']
                namespace = job_info['namespace']
                hashed_identifier = job_info.get('hashed_identifier') or get_hashed_identifier(filepath)
                vector_count = None
                try:
                    if isinstance(result, dict) and result.get("chunks") is not None:
                        vector_count = int(result.get("chunks"))
                except (TypeError, ValueError):
                    pass

                project_id = self.get_project_id()
                if project_id:
                    self.processed_files.setdefault(project_id, {})[file_hash] = {
                        'filename': filename,
                        'filepath': filepath,
                        'namespace': namespace,
                        'hashed_identifier': hashed_identifier,
                        'vector_count': vector_count,
                        'processed_at': time.time(),
                    }
                self._save_processed_files()
                
                # Remove from current jobs
                del self.current_jobs[job_id]
                self._update_jobs_display()
                self._update_file_status()
                
                self.status_label.setText(f"Completed: {filename}")
                print(f"[Job] Job {job_id} completed for {filename}")
                # Skip immediate consistency check; backend counts can lag right after upload
        except Exception as e:
            err_msg = f"Critical error in job completion: {e}\n{traceback.format_exc()}"
            print(err_msg)
            QMessageBox.critical(self, "Plugin Error", f"An error occurred while finishing the job:\n{e}")
            
    def _on_job_failed(self, job_id: str, error: str):
        """Handle job failure."""
        if job_id in self.current_jobs:
            job_info = self.current_jobs[job_id]
            filename = job_info['filename']

            print(f"[Job] Job {job_id} failed for {filename}: {error}")

            del self.current_jobs[job_id]
            self._update_jobs_display()

            self.status_label.setText(f"Failed: {filename}")
            QMessageBox.warning(self, "Upload Failed", f"Processing failed for {filename}:\n{error}")
            
    def _on_upload_completed(self, success: bool):
        """Handle completion of a single upload."""
        self.is_uploading = False
        self.current_upload = None
        
        # Process next file in queue
        if self.upload_queue:
            QTimer.singleShot(Config.QUEUE_DELAY, self._process_upload_queue)
        else:
            self.status_label.setText("All uploads completed!")
            self._update_file_status()
            
    def _update_jobs_display(self):
        """Update the jobs list dialog if open."""
        try:
            if self.dialog_jobs_list is not None and self.dialog_jobs_list.isVisible():
                self._update_jobs_list_widget(self.dialog_jobs_list)
        except RuntimeError:
            # Dialog closed and underlying C++ widget has been destroyed.
            self.dialog_jobs_list = None

    def _perform_search(self):
        """Perform semantic search (non-blocking)."""
        query = self.search_input.text().strip()
        if not query:
            return
        self._search_generation += 1  # Cancel any pending thumbnail loads from previous search
        gen = self._search_generation

        namespace = self._build_namespace()

        if not self.auth_manager or not self.auth_manager.is_logged_in():
            QMessageBox.warning(self, "Sign In Required", "Please sign in to search.")
            return

        self.status_label.setText(f"Searching for: {query}")
        self.btn_search.setEnabled(False)
        print(f"[Search] Sending async search: query={query!r}, namespace={namespace}")

        def on_success(status, data):
            if gen != self._search_generation:
                print("[Search] Stale result discarded")
                return
            results = data.get("results", []) if isinstance(data, dict) else []
            self._display_search_results(results, query)
            self.status_label.setText(f"Found {len(results)} results for: {query}")
            self.btn_search.setEnabled(True)

        def on_error(msg):
            if gen != self._search_generation:
                return
            print(f"[Search] Error: {msg}")
            QMessageBox.warning(self, "Search Error", f"Search failed: {msg}")
            self.status_label.setText("Search failed")
            self.btn_search.setEnabled(True)

        project_id = self.get_project_id() or ""
        self._network.get(
            Config.SEARCH_API_URL,
            params={"query": query, "namespace": namespace, "project_id": project_id},
            timeout=30,
            on_success=on_success,
            on_error=on_error,
        )
            
    def _display_search_results(self, results: List[Dict], query: str):
        """Display search results in a grid layout matching Figma design."""
        # Clear previous results
        for i in reversed(range(self.results_layout.count())):
            item = self.results_layout.itemAt(i)
            if item.widget():
                item.widget().setParent(None)
            elif item.layout():
                # Clear nested layouts
                self._clear_layout(item.layout())
                
        # Hide empty state / upload zone if still present
        if self.empty_state_label and self.empty_state_label.parent():
            self.empty_state_label.hide()
        if hasattr(self, 'upload_zone') and self.upload_zone and self.upload_zone.parent():
            self.upload_zone.hide()
                
        if not results:
            no_results = QLabel("No results found for your query.")
            no_results.setObjectName("emptyState")
            no_results.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.results_layout.addWidget(no_results)
            return

        # Debug: log what metadata the search returned
        for i, r in enumerate(results[:3]):
            meta = r.get('metadata', {})
            print(f"[Search] Result {i}: filename={meta.get('file_filename')}, "
                  f"file_path={meta.get('file_path')}, "
                  f"start={meta.get('start_time_s')}, end={meta.get('end_time_s')}, "
                  f"exists={os.path.exists(meta.get('file_path', ''))}")
        
        # Create grid layout for results
        grid_container = QWidget()
        grid_layout = QGridLayout()
        grid_layout.setSpacing(20)
        grid_layout.setContentsMargins(20, 20, 20, 20)
        
        # Display results in 3-column grid
        num_columns = 3
        for i, result in enumerate(results[:9]):  # Limit to 9 for 3x3 grid
            result_widget = self._create_result_card(result, i)
            row = i // num_columns
            col = i % num_columns
            grid_layout.addWidget(result_widget, row, col)
        
        grid_container.setLayout(grid_layout)
        self.results_layout.addWidget(grid_container)
        
        # Add stretch to push results to top
        self.results_layout.addStretch()
    
    def _clear_layout(self, layout):
        """Recursively clear and destroy all widgets in a layout."""
        if layout is None:
            return
        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
            elif item.layout():
                self._clear_layout(item.layout())
        
    def _resolve_local_path(self, filename: str) -> Optional[str]:
        """Look up a filename in the media pool clip_map and return its local file path."""
        clip_info = self.clip_map.get(filename)
        if clip_info:
            if isinstance(clip_info, list):
                clip_info = clip_info[0]
            path = clip_info.get('filepath')
            if path and os.path.exists(path):
                return path
        # Fuzzy fallback: partial filename match
        filename_lower = filename.lower()
        for clip_name, info in self.clip_map.items():
            if filename_lower in clip_name.lower() or clip_name.lower() in filename_lower:
                if isinstance(info, list):
                    info = info[0]
                path = info.get('filepath')
                if path and os.path.exists(path):
                    return path
        return None

    def _load_thumbnail(self, label: QLabel, file_path: str, time_s: float, generation: int):
        """Load a thumbnail into a label (called via QTimer after cards are shown)."""
        if generation != self._search_generation:
            return  # Results were cleared, skip

        def on_ready(pixmap):
            if generation != self._search_generation:
                return  # Cleared while extracting
            if pixmap and label and label.parent():
                label.setPixmap(pixmap)
                label.setScaledContents(True)

        try:
            extract_thumbnail(
                file_path, time_s, size=(280, 140),
                on_ready=on_ready, parent=self,
            )
        except Exception as e:
            print(f"[Search] Thumbnail load failed: {e}")

    def _create_result_card(self, result: Dict, index: int) -> QWidget:
        """Create a card widget for a single search result with video thumbnail."""
        t = Theme.current

        card = QFrame()
        card.setObjectName("resultCard")
        card.setFixedSize(280, 240)
        card.setCursor(Qt.CursorShape.PointingHandCursor)

        layout = QVBoxLayout()
        layout.setSpacing(0)
        layout.setContentsMargins(0, 0, 0, 0)

        metadata = result.get('metadata', {})
        filename = metadata.get('file_filename', 'Unknown')
        file_path = metadata.get('file_path', '') or ''
        start_time = float(metadata.get('start_time_s', 0))
        end_time = float(metadata.get('end_time_s', 0))
        score = result.get('score', 0)
        display_name = f"{filename[:20]}..." if len(filename) > 20 else filename

        # Resolve local path: use metadata file_path if it exists, otherwise match from media pool
        if not file_path or not os.path.exists(file_path):
            file_path = self._resolve_local_path(filename) or ''
            if file_path:
                # Inject resolved path into result so preview dialog can use it
                result.setdefault('metadata', {})['file_path'] = file_path
                print(f"[Search] Resolved {filename} -> {file_path}")

        # Thumbnail placeholder — show text immediately, load real thumbnail after
        thumbnail = QLabel()
        thumbnail.setObjectName("thumbnail")
        thumbnail.setFixedHeight(140)
        thumbnail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        thumbnail.setTextFormat(Qt.TextFormat.PlainText)
        thumbnail.setText(f"{display_name}\n{start_time:.1f}s - {end_time:.1f}s")

        # Schedule lazy thumbnail load so search results appear instantly
        if file_path and os.path.exists(file_path):
            mid_time = (start_time + end_time) / 2.0
            gen = self._search_generation
            QTimer.singleShot(100 * index, lambda t=thumbnail, fp=file_path, mt=mid_time, g=gen: self._load_thumbnail(t, fp, mt, g))

        thumbnail.setStyleSheet(f"""
            background-color: {t['card_thumbnail']};
            color: #666666;
            font-size: 11px;
            border-top-left-radius: 8px;
            border-top-right-radius: 8px;
        """)
        layout.addWidget(thumbnail)

        # Relevance score bar
        progress = QProgressBar()
        progress.setFixedHeight(6)
        progress.setTextVisible(False)
        progress.setMinimum(0)
        progress.setMaximum(100)
        progress.setValue(int(min(score * 100, 100)))
        progress.setStyleSheet(f"""
            QProgressBar {{
                background-color: rgba(217, 217, 217, 0.5);
                border: none;
                border-radius: 0px;
            }}
            QProgressBar::chunk {{
                background-color: {t['progress_bg']};
            }}
        """)
        layout.addWidget(progress)

        # Info and button container
        btn_container = QWidget()
        btn_container.setFixedHeight(70)
        btn_container.setStyleSheet(f"background-color: {t['card_bg']}; border-bottom-left-radius: 8px; border-bottom-right-radius: 8px;")
        btn_layout = QVBoxLayout()
        btn_layout.setContentsMargins(10, 8, 10, 10)
        btn_layout.setSpacing(6)

        name_label = QLabel(display_name)
        name_label.setTextFormat(Qt.TextFormat.PlainText)
        name_label.setStyleSheet(f"color: {t['text']}; font-size: 12px; font-weight: 500; background: transparent;")
        btn_layout.addWidget(name_label)

        # Preview button opens the video preview/trimmer dialog
        btn_preview = QPushButton("Preview & Trim")
        btn_preview.setObjectName("addToTimelineBtn")
        btn_preview.setFixedHeight(28)
        btn_preview.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_preview.clicked.connect(lambda checked, r=result: self._open_video_preview(r))
        btn_layout.addWidget(btn_preview)

        btn_container.setLayout(btn_layout)
        layout.addWidget(btn_container)

        card.setLayout(layout)
        return card

    def _open_video_preview(self, result: Dict):
        """Open the video preview/trimmer dialog for a search result."""
        dialog = VideoPreviewDialog(result, parent=self)
        dialog.insert_requested.connect(self._add_result_to_timeline)
        dialog.exec()

    def _add_result_to_timeline(self, result: Dict):
        """Add a search result to the timeline."""
        if not resolve:
            QMessageBox.warning(self, "Error", "Resolve API not available.")
            return
        
        # Refresh media pool to ensure we have latest clips
        self._refresh_media_pool(debug=True)
        print(f"[Timeline] clip_map entries: {len(self.clip_map)}")
            
        def _normalize_path(path_value: Optional[str]) -> Optional[str]:
            if not path_value:
                return None
            try:
                return os.path.normcase(os.path.normpath(path_value))
            except Exception:
                return path_value

        metadata = result.get('metadata', {})
        filename = metadata.get('file_filename', 'Unknown')
        file_path = metadata.get('file_path')
        normalized_file_path = _normalize_path(file_path)
        print(f"[Timeline] Target filename: {filename}")
        print(f"[Timeline] Target file_path: {file_path}")
        start_time = metadata.get('start_time_s', 0)
        end_time = metadata.get('end_time_s', 0)

        # Validate time range
        try:
            if float(end_time) <= float(start_time):
                QMessageBox.warning(self, "Error", f"Invalid time range for {filename}: {start_time} - {end_time}")
                return
        except Exception:
            QMessageBox.warning(self, "Error", f"Invalid time range metadata for {filename}.")
            return
        
        # Find the clip in media pool
        matching_clip = None
        matching_clip_info = None
        if normalized_file_path:
            for _, clip_info in self.clip_map.items():
                if isinstance(clip_info, list):
                    for entry in clip_info:
                        if _normalize_path(entry.get('filepath')) == normalized_file_path:
                            matching_clip_info = entry
                            matching_clip = entry.get('media_pool_item')
                            break
                else:
                    if _normalize_path(clip_info.get('filepath')) == normalized_file_path:
                        matching_clip_info = clip_info
                        matching_clip = clip_info.get('media_pool_item')
                if matching_clip:
                    break

        if not matching_clip:
            filename_lower = filename.lower()
            for clip_filename, clip_info in self.clip_map.items():
                clip_filename_lower = clip_filename.lower()
                if filename_lower in clip_filename_lower or clip_filename_lower in filename_lower:
                    if isinstance(clip_info, list):
                        matching_clip_info = clip_info[0]
                    else:
                        matching_clip_info = clip_info
                    matching_clip = matching_clip_info['media_pool_item']
                    break
                
        if not matching_clip:
            # Debug logging to diagnose mismatches
            print("[Timeline] Failed to match clip in media pool")
            print(f"[Timeline] Result filename: {filename}")
            print(f"[Timeline] Result file_path: {file_path}")
            sample_paths = []
            total_paths = 0
            for _, clip_info in self.clip_map.items():
                if isinstance(clip_info, list):
                    for entry in clip_info:
                        path = entry.get('filepath')
                        if path:
                            total_paths += 1
                            if len(sample_paths) < 5:
                                sample_paths.append(path)
                else:
                    path = clip_info.get('filepath')
                    if path:
                        total_paths += 1
                        if len(sample_paths) < 5:
                            sample_paths.append(path)
            print(f"[Timeline] Media pool file paths: {total_paths} total")
            for p in sample_paths:
                print(f"[Timeline] Sample path: {p}")
            QMessageBox.warning(self, "Error", f"Could not find {filename} in media pool")
            return

        try:
            clip_name = matching_clip.GetName()
        except Exception:
            clip_name = None
        print(f"[Timeline] Matched clip: {clip_name or '<unnamed>'}")
            
        # Ensure timeline exists
        if not self._ensure_timeline():
            return
        try:
            timeline = project.GetCurrentTimeline()
            if timeline:
                project.SetCurrentTimeline(timeline)
            resolve.OpenPage("edit")
        except Exception:
            pass
            
        def _count_timeline_items(tl) -> Optional[int]:
            try:
                total = 0
                track_count = tl.GetTrackCount("video")
                for i in range(1, int(track_count) + 1):
                    items = tl.GetItemListInTrack("video", i) or []
                    total += len(items)
                return total
            except Exception:
                return None

        # Add to timeline
        try:
            # Set current folder to the clip's parent folder for subfolder support
            try:
                clip_folder = matching_clip_info.get('folder') if matching_clip_info else None
                if clip_folder:
                    media_pool.SetCurrentFolder(clip_folder)
                else:
                    root_folder = media_pool.GetRootFolder()
                    if root_folder:
                        media_pool.SetCurrentFolder(root_folder)
            except Exception:
                pass

            # Determine clip length (frames) and fps
            clip_frames = None
            if matching_clip_info:
                for key in ("Frames", "Frame Count", "Duration"):
                    try:
                        val = matching_clip_info["media_pool_item"].GetClipProperty(key)
                        if val:
                            clip_frames = int(float(val))
                            break
                    except Exception:
                        pass
            if clip_frames is None:
                clip_frames = 1000

            fps = (matching_clip_info.get("fps") or 24.0) if matching_clip_info else 24.0

            start_frame = int(float(start_time) * float(fps))
            end_frame = int(float(end_time) * float(fps))
            start_frame = max(0, min(start_frame, clip_frames))
            end_frame = max(0, min(end_frame, clip_frames))
            if end_frame <= start_frame:
                end_frame = start_frame + 1

            print(f"[Timeline] Using fps={fps}, start_frame={start_frame}, end_frame={end_frame}, clip_frames={clip_frames}")

            # Ensure at least one video/audio track exists (AppendToTimeline can fail on empty timelines)
            try:
                if timeline and int(timeline.GetTrackCount("video") or 0) == 0:
                    timeline.AddTrack("video")
                    print("[Timeline] Added missing video track 1.")
                if timeline and int(timeline.GetTrackCount("audio") or 0) == 0:
                    timeline.AddTrack("audio")
                    print("[Timeline] Added missing audio track 1.")
            except Exception as e:
                print(f"[Timeline] Failed to ensure video track: {e}")

            # Best-effort track selection (blue source + red target) with diagnostics only.
            if timeline:
                enable_fn = getattr(timeline, "SetTrackEnable", None)
                autoselect_fn = getattr(timeline, "SetTrackAutoSelect", None)
                lock_fn = getattr(timeline, "SetTrackLock", None)
                print(
                    "[Timeline] Track methods:",
                    f"Enable={callable(enable_fn)}, AutoSelect={callable(autoselect_fn)}, Lock={callable(lock_fn)}"
                )
                for track_type in ("video", "audio"):
                    for name, fn, args in (
                        ("Enable", enable_fn, (track_type, 1, True)),
                        ("AutoSelect", autoselect_fn, (track_type, 1, True)),
                        ("Lock", lock_fn, (track_type, 1, False)),
                    ):
                        if not callable(fn):
                            continue
                        try:
                            result = fn(*args)
                            print(f"[Timeline] {name} {track_type}1 -> {result}")
                        except Exception as e:
                            print(f"[Timeline] {name} {track_type}1 failed: {e}")

            before_count = _count_timeline_items(timeline) if timeline else None
            print(f"[Timeline] Items before append: {before_count}")

            clip_info = {
                "mediaPoolItem": matching_clip,
                "startFrame": start_frame,
                "endFrame": end_frame,
            }
            result = media_pool.AppendToTimeline([clip_info])
            print(f"[Timeline] AppendToTimeline result: {result!r} (type={type(result).__name__})")

            def _is_append_success(value) -> bool:
                if value is True:
                    return True
                if isinstance(value, list):
                    return any(item is not None for item in value)
                return bool(value)

            success = _is_append_success(result)
            if not success:
                print("[Timeline] Timed append failed, trying full clip fallback.")
                result_fallback = media_pool.AppendToTimeline([matching_clip])
                print(f"[Timeline] Fallback AppendToTimeline result: {result_fallback!r} (type={type(result_fallback).__name__})")
                success = _is_append_success(result_fallback)

            after_count = _count_timeline_items(timeline) if timeline else None
            print(f"[Timeline] Items after append: {after_count}")

            if success:
                self.status_label.setText(f"Added {filename} ({start_time:.1f}s-{end_time:.1f}s) to timeline")
            else:
                print("[Timeline] AppendToTimeline returned False")
                QMessageBox.warning(
                    self,
                    "Failed to Add Clip",
                    "Resolve could not insert the clip. If this is an empty timeline, "
                    "please enable Edit -> Edit Options -> Automatically Create Tracks on Edit, "
                    "or drag any clip into the timeline once to initialize track patching."
                )
                
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to add clip: {str(e)}")
    
    def _ensure_timeline(self):
        """Ensure a timeline exists, create one if needed."""
        if not resolve:
            QMessageBox.warning(self, "Error", "Resolve API not available. Cannot perform action.")
            return False
        
        # Ask the project for the current timeline at runtime (don't rely on module-level cache)
        timeline = project.GetCurrentTimeline()
        if timeline:
            return True

        # No timeline: try to create an empty one and re-query
        create_ok = media_pool.CreateEmptyTimeline("New Timeline")
        if not create_ok:
            QMessageBox.warning(self, "Error", "Failed to create a new timeline.")
            return False

        # Re-query the project's current timeline
        timeline = project.GetCurrentTimeline()
        if not timeline:
            QMessageBox.warning(self, "Error", "Timeline creation reported success but no timeline found.")
            return False

        print("Created new empty timeline.")
        return True

    def _extract_clip_fps(self, clip):
        """Try to read a clip's frame rate from common clip properties.

        Returns a float fps if found, otherwise None.
        """
        # Common property keys that Resolve may expose for frame rate
        keys = ("FPS", "Frame Rate", "FrameRate", "Video FPS", "Video Frame Rate")
        for k in keys:
            try:
                val = clip.GetClipProperty(k)
            except Exception:
                val = None
            if val:
                # Sometimes Resolve returns strings like '23.976' or '24'
                try:
                    return float(val)
                except Exception:
                    # Try to extract numeric portion
                    try:
                        num = ''.join(ch for ch in str(val) if (ch.isdigit() or ch == '.' or ch == ','))
                        num = num.replace(',', '.')
                        return float(num)
                    except Exception:
                        continue
        return None

    def _build_clip_map(self, debug: bool = False):
        """Scan media pool clips that match the current filter and build a mapping.

        The filter used is the same as in `_action_append_chunk`: keep clips
        where `"Video"` appears in `GetClipProperty("Type")`.

        Returns a dict mapping `filename` -> { 'media_pool_item': <item>, 'fps': <float|None> }
        If multiple items share the same filename, the value becomes a list of such dicts.
        """
        if not resolve:
            return {}  # Return empty dict instead of raising error

        def _get_subfolders(folder):
            if not folder:
                return []
            for method in ("GetSubFolderList", "GetSubFolders"):
                try:
                    result = getattr(folder, method)()
                    if result is not None:
                        return result
                except Exception:
                    continue
            return []

        def _collect_clips(folder):
            if not folder:
                return []
            collected = []
            try:
                for clip in (folder.GetClipList() or []):
                    collected.append((clip, folder))
            except Exception:
                pass
            for sub in _get_subfolders(folder):
                collected.extend(_collect_clips(sub))
            return collected

        root_folder = media_pool.GetRootFolder()
        clips_with_folders = _collect_clips(root_folder)
        if debug:
            print(f"[MediaPool] Total clips found (pre-filter): {len(clips_with_folders)}")

        mapping = {}
        for clip, folder in clips_with_folders:
            if not clip:
                continue

            # Set folder context before any GetClipProperty calls
            try:
                media_pool.SetCurrentFolder(folder)
            except Exception:
                pass

            # Skip clips that are definitely not video (audio-only, stills).
            # Allow clips with unknown/None type through — some containers
            # (e.g. .MOV/QuickTime) may report unexpected type strings.
            try:
                clip_type = clip.GetClipProperty("Type")
            except Exception:
                clip_type = None
            if clip_type and clip_type.strip() in ("Audio", "Still"):
                if debug:
                    print(f"[MediaPool] Skipping non-video clip: type={clip_type!r}")
                continue

            try:
                file_path = clip.GetClipProperty("File Path") or clip.GetClipProperty("FilePath")
            except Exception:
                file_path = None

            if file_path:
                filename = os.path.basename(file_path)
            else:
                try:
                    filename = clip.GetName() or "<unnamed>"
                except Exception:
                    filename = "<unnamed>"

            fps = self._extract_clip_fps(clip)

            entry = {"media_pool_item": clip, "fps": fps, "filepath": file_path, "folder": folder}

            # Handle duplicate filenames by collecting into a list
            if filename in mapping:
                if isinstance(mapping[filename], list):
                    mapping[filename].append(entry)
                else:
                    mapping[filename] = [mapping[filename], entry]
            else:
                mapping[filename] = entry

        # Restore media pool folder to root
        try:
            media_pool.SetCurrentFolder(root_folder)
        except Exception:
            pass

        return mapping

    def _refresh_media_pool(self, debug: bool = False):
        """Refresh media pool and update file status."""
        if not resolve:
            return
            
        self.clip_map = self._build_clip_map(debug=debug)
        if debug:
            print(f"[MediaPool] Refreshed clip map: {len(self.clip_map)} unique filenames")
        self._update_file_status()
        
    def _clear_upload_queue(self):
        """Clear the upload queue."""
        if not self.upload_queue and not self.is_uploading:
            QMessageBox.information(self, "Info", "No uploads to cancel.")
            return
            
        # Ask for confirmation
        reply = QMessageBox.question(
            self, 
            "Clear Upload Queue", 
            f"Cancel {len(self.upload_queue)} queued uploads?\n\nCurrently uploading file will continue.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        
        if reply == QMessageBox.StandardButton.Yes:
            cleared_count = len(self.upload_queue)
            
            # Remove queued jobs from display
            jobs_to_remove = []
            for job_id, job_info in self.current_jobs.items():
                if job_info.get('status') == 'queued':
                    jobs_to_remove.append(job_id)
            
            for job_id in jobs_to_remove:
                del self.current_jobs[job_id]
            
            # Clear the queue
            self.upload_queue.clear()
            
            # Update displays
            self._update_jobs_display()
            self.status_label.setText(f"Cleared {cleared_count} uploads from queue")
            self._update_file_status()

    def _get_device_id_path(self) -> Path:
        """Return a platform-appropriate path to persist the device id."""
        system = platform.system()
        if system == "Windows":
            base = os.getenv("APPDATA") or str(Path.home())
            return Path(base) / "ClipABit" / "device_id.txt"
        elif system == "Darwin":
            return Path.home() / "Library" / "Application Support" / "ClipABit" / "device_id.txt"
        else:
            xdg = os.getenv("XDG_CONFIG_HOME")
            base = xdg if xdg else str(Path.home() / ".config")
            return Path(base) / "clipabit" / "device_id.txt"

    def get_or_create_device_id(self, persist: bool = True) -> str:
        """Get a persistent device id, create and store one if missing.

        If `persist` is False returns a generated id without saving.
        """
        path = self._get_device_id_path()

        try:
            if path.exists():
                text = path.read_text(encoding="utf-8").strip()
                if text:
                    return text
        except Exception:
            # ignore read errors and regenerate
            pass

        # Create a new id. Use uuid4 for randomness but prefix with a short host hash
        host = platform.node() or "unknown-host"
        host_hash = hashlib.sha1(host.encode("utf-8")).hexdigest()[:8]
        new_id = f"{host_hash}-{uuid.uuid4().hex}"

        if persist:
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(new_id, encoding="utf-8")
            except Exception:
                # If persisting fails, still return the generated id
                pass

        return new_id

    def get_project_name(self) -> Optional[str]:
        """Return the current Resolve project name, or None if unavailable."""
        if not resolve or not project:
            return None

        # Try common method names; Resolve API varies by version
        for method in ("GetName", "GetProjectName", "GetTitle"):
            try:
                fn = getattr(project, method, None)
                if callable(fn):
                    name = fn()
                    if name:
                        return str(name)
            except Exception:
                continue

        # As a last resort try project manager lookup
        try:
            pm = resolve.GetProjectManager()
            cur = pm.GetCurrentProject()
            if cur:
                return getattr(cur, "GetName", lambda: None)()
        except Exception:
            pass

        return None

    def get_project_id(self) -> Optional[str]:
        """Return the Resolve project's unique ID, or None if unavailable.

        Uses GetUniqueId() (Resolve 18.0b3+) which is immutable across renames.
        Falls back to a SHA-256 hash of the project name if unavailable.
        """
        if not project:
            return None
        try:
            fn = getattr(project, "GetUniqueId", None)
            if callable(fn):
                uid = fn()
                if uid:
                    return str(uid)
        except Exception:
            pass
        # Fallback: hash the project name (changes if user renames the project)
        name = self.get_project_name()
        if name:
            return hashlib.sha256(name.encode()).hexdigest()[:36]
        return None

    def _migrate_processed_files(self):
        """Migrate flat processed-file entries into the current project's bucket."""
        project_id = self.get_project_id()
        if not project_id:
            QMessageBox.warning(self, "Migration", "No Resolve project detected.")
            return

        # Identify flat entries (top-level values that have a "filename" key)
        flat_hashes = {
            h: info for h, info in self.processed_files.items()
            if isinstance(info, dict) and "filename" in info
        }

        if not flat_hashes:
            QMessageBox.information(self, "Migration", "No flat entries to migrate.")
            return

        # Build a lookup of file hashes present in the current clip map
        clip_hashes = set()
        for clip_info in self.clip_map.values():
            entries = clip_info if isinstance(clip_info, list) else [clip_info]
            for entry in entries:
                fp = entry.get("filepath")
                if fp:
                    clip_hashes.add(get_file_hash(fp))

        migrated = 0
        unmatched = 0
        bucket = self.processed_files.setdefault(project_id, {})

        for file_hash, info in flat_hashes.items():
            if file_hash in clip_hashes:
                bucket[file_hash] = info
                migrated += 1
            else:
                unmatched += 1

        self._save_processed_files()
        self._update_file_status()
        QMessageBox.information(
            self,
            "Migration Complete",
            f"Migrated: {migrated}\nUnmatched: {unmatched}\n\n"
            "Flat entries were NOT deleted — remove them manually if desired.",
        )
        print(f"[Migration] project={project_id}: migrated={migrated}, unmatched={unmatched}")

    def _show_processed_files(self):
        """Show processed files in a dialog window."""
        project_files = self._get_project_processed_files()
        if not project_files:
            QMessageBox.information(self, "Processed Files", "No files have been processed yet.")
            return

        try:
            # Create dialog window (modal to prevent it from closing)
            dialog = QDialog(self)
            dialog.setWindowTitle("Processed Files")
            dialog.resize(600, 400)
            dialog.setModal(True)  # Make it modal so it stays open

            layout = QVBoxLayout()

            # Header
            header = QLabel(f"<b>Processed Files ({len(project_files)} total)</b>")
            layout.addWidget(header)

            # Scrollable list
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll_widget = QWidget()
            scroll_layout = QVBoxLayout()

            # Display each processed file
            for _, file_info in sorted(project_files.items(),
                                          key=lambda x: x[1].get('processed_at', 0),
                                          reverse=True):
                file_frame = QFrame()
                file_frame.setFrameStyle(QFrame.Shape.Box)
                file_layout = QVBoxLayout()
                
                filename = file_info.get('filename', 'Unknown')
                job_id = file_info.get('job_id', 'Unknown')
                processed_at = file_info.get('processed_at', 0)
                result = file_info.get('result', {})
                
                # Format timestamp
                if processed_at:
                    dt = datetime.datetime.fromtimestamp(processed_at)
                    time_str = dt.strftime("%Y-%m-%d %H:%M:%S")
                else:
                    time_str = "Unknown"
                
                # File info
                file_label = QLabel(f"<b>{filename}</b>")
                file_layout.addWidget(file_label)
                
                info_label = QLabel(f"Job ID: {job_id} | Processed: {time_str}")
                info_label.setStyleSheet("color: gray; font-size: 9px;")
                file_layout.addWidget(info_label)
                
                # Status
                status = result.get('status', 'unknown')
                status_label = QLabel(f"Status: {status}")
                status_label.setStyleSheet("color: blue; font-size: 9px;")
                file_layout.addWidget(status_label)

                vector_count = file_info.get('vector_count')
                vector_text = f"Vectors: {vector_count}" if vector_count is not None else "Vectors: unknown"
                vector_label = QLabel(vector_text)
                vector_label.setStyleSheet("color: gray; font-size: 9px;")
                file_layout.addWidget(vector_label)
                
                file_frame.setLayout(file_layout)
                scroll_layout.addWidget(file_frame)
            
            scroll_layout.addStretch()
            scroll_widget.setLayout(scroll_layout)
            scroll.setWidget(scroll_widget)
            layout.addWidget(scroll)
            
            # Close button
            btn_close = QPushButton("Close")
            btn_close.clicked.connect(dialog.accept)
            layout.addWidget(btn_close)
            
            dialog.setLayout(layout)
            dialog.exec()  # Use exec() instead of show() to make it modal
        except Exception as e:
            error_msg = f"Error showing processed files dialog: {e}\n{traceback.format_exc()}"
            print(error_msg)
            QMessageBox.critical(self, "Error", f"Failed to show processed files:\n{str(e)}")
    
    def _clear_processed_files(self):
        """Clear local processed files tracking for the current project."""
        project_id = self.get_project_id()
        project_files = self._get_project_processed_files()
        if not project_files:
            QMessageBox.information(self, "Clear Processed Files", "No processed files to clear.")
            return

        reply = QMessageBox.question(
            self,
            "Clear Processed Files",
            f"Clear tracking for {len(project_files)} processed files?\n\n"
            "This only clears local tracking — backend data is retained.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )

        if reply == QMessageBox.StandardButton.Yes:
            self.processed_files.pop(project_id, None)
            self._save_processed_files()
            self._update_file_status()
            QMessageBox.information(self, "Cleared", "Processed files tracking has been cleared.")
            print("[Files] Processed files tracking cleared")
            
    def _run_consistency_check(self, reason: str):
        """Sync local tracking with backend and remove dangling entries."""
        project_id = self.get_project_id()
        project_files = self._get_project_processed_files()
        if not project_files:
            return {"checked": 0, "removed": 0}

        removed_count = 0
        checked_count = 0

        for file_hash, info in list(project_files.items()):
            filepath = info.get("filepath", "")
            checked_count += 1

            if filepath and not os.path.exists(filepath):
                print(f"[Consistency] Missing local file: {info.get('filename', '')}. Removing local record.")
                del self.processed_files[project_id][file_hash]
                removed_count += 1

        if removed_count > 0:
            self._save_processed_files()
            self._update_file_status()

        if checked_count > 0:
            print(f"[Consistency] {reason}: checked {checked_count}, removed {removed_count}")
        return {"checked": checked_count, "removed": removed_count}

    def closeEvent(self, event):
        """Handle window close event."""
        global _instance
        if hasattr(self, 'job_tracker'):
            self.job_tracker.stop()
        if hasattr(self, 'refresh_timer'):
            self.refresh_timer.stop()
        _instance = None
        event.accept()

def main(resolve_api=None):
    """Main entry point.
    
    Args:
        resolve_api: Optional Resolve object injected from the shim.
    """
    global resolve, project, media_pool, project_manager, _instance

    # If API object is injected, use it to setup globals
    if resolve_api:
        resolve = resolve_api
        try:
            project_manager = resolve.GetProjectManager()
            project = project_manager.GetCurrentProject()
            media_pool = project.GetMediaPool()
            print("[Plugin] Resolve API injected successfully.")
        except Exception as e:
            print(f"[Plugin] Failed to initialize Resolve objects from injected API: {e}")
            resolve = None

    # Check if we have a valid Resolve connection
    if not resolve:
        print("[Plugin] Warning: Running without Resolve API (Simulation Mode)")

    app_qt = QApplication.instance()
    if not app_qt:
        app_qt = QApplication(sys.argv)

    # Another process already has server and window
    if _send_single_instance_message():
        print("ClipABit Plugin already running in another process: requesting existing instance to activate.")
        return

    # This process becomes the primary instance
    _setup_single_instance_server()

    _instance = ClipABitApp()
    _instance.show()
    _instance.raise_()
    _instance.activateWindow()

    print("ClipABit Plugin started.")
    app_qt.exec()
