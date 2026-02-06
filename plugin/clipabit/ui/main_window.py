import sys
import os
import requests
import uuid
import json
import time
import traceback
import platform
import webbrowser
from pathlib import Path
from typing import Dict, List, Optional

try:
    from PyQt6.QtWidgets import (QApplication, QWidget, QVBoxLayout, QHBoxLayout,
                                 QLabel, QPushButton, QMessageBox, QLineEdit,
                                 QScrollArea, QFrame, QListWidget, QListWidgetItem,
                                 QDialog, QCheckBox, QGridLayout, QProgressBar,
                                 QStackedWidget, QGraphicsOpacityEffect)
    from PyQt6.QtCore import Qt, QTimer, QThread, pyqtSignal
    from PyQt6.QtSvgWidgets import QSvgWidget
except ImportError as e:
    print(f"Error: PyQt6 not found or missing component: {e}")
    # We can't exit here if imported by shim, but let's assume environment is good
    pass

import hashlib

# Local imports
from ..api.config import Config
from ..core.job_tracker import JobTracker
from ..core.utils import (
    get_storage_path, load_processed_files, save_processed_files,
    get_file_hash, get_hashed_identifier
)
from .theme import Theme

# --- Setup Resolve API Globals ---
resolve = None
project = None
media_pool = None
project_manager = None

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
        
        # Initialize data
        self.clip_map = {}
        self.processed_files = self._load_processed_files()
        self.current_jobs = {}  # job_id -> job_info
        
        # Upload queue system
        self.upload_queue = []  # List of files waiting to be uploaded
        self.current_upload = None  # Currently uploading file info
        self.is_uploading = False  # Flag to prevent concurrent uploads
        
        # Initialize job tracker
        self.job_tracker = JobTracker()
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
        
        # Auth state management
        self.auth_token = None
        self.is_authenticated = False
        self.device_code = None
        self.user_code = None
        self.auth_polling_timer = None
        
        # Load stored auth token
        self._load_auth_token()
        
        # Setup UI
        self.setWindowTitle("ClipABit")
        self.resize(1000, 700)
        self.setMinimumSize(800, 600)
        self.init_ui()
        self._apply_theme()
        
        # Setup refresh timer (disabled by default; refresh on demand)
        self.refresh_timer = QTimer()
        self.refresh_timer.timeout.connect(lambda: self._refresh_media_pool(debug=False))
        
        # Run consistency check on startup
        self._run_consistency_check("startup")
        
    def init_ui(self):
        # Main layout
        main_layout = QVBoxLayout()
        main_layout.setSpacing(0)
        main_layout.setContentsMargins(0, 0, 0, 0)
        
        # Header bar with buttons (top-right) - shown on all screens
        self.header = self._create_header()
        main_layout.addWidget(self.header)
        
        # Stacked widget for different screens
        self.stacked_widget = QStackedWidget()
        
        # Page 0: Welcome screen (auth flow start)
        self.welcome_page = self._create_welcome_screen()
        self.stacked_widget.addWidget(self.welcome_page)
        
        # Page 1: Device code screen
        self.device_code_page = self._create_device_code_screen()
        self.stacked_widget.addWidget(self.device_code_page)
        
        # Page 2: Main search/results screen
        self.search_page = self._create_search_screen()
        self.stacked_widget.addWidget(self.search_page)
        
        main_layout.addWidget(self.stacked_widget, 1)
        
        # Status bar (minimal) - shown on all screens
        self.status_label = QLabel("Ready")
        self.status_label.setObjectName("statusBar")
        main_layout.addWidget(self.status_label)
        
        self.setLayout(main_layout)
        
        # Set initial page based on auth state
        if self.is_authenticated:
            self.stacked_widget.setCurrentIndex(2)  # Show search screen
            self.status_label.setText("Ready")
        else:
            self.stacked_widget.setCurrentIndex(0)  # Show welcome screen
            self.status_label.setText("Welcome to ClipABit")

    def _create_search_screen(self):
        """Create the main search/results screen (original UI)."""
        page = QWidget()
        content_layout = QVBoxLayout()
        content_layout.setSpacing(20)
        content_layout.setContentsMargins(40, 20, 40, 40)
        
        # Logo - ClipABit SVG (smaller)
        self.logo_widget = QSvgWidget()
        self.logo_widget.setFixedSize(140, 56)
        self._update_logo()
        
        # Center the logo
        logo_container = QWidget()
        logo_layout = QHBoxLayout()
        logo_layout.setContentsMargins(0, 0, 0, 0)
        logo_layout.addStretch()
        logo_layout.addWidget(self.logo_widget)
        logo_layout.addStretch()
        logo_container.setLayout(logo_layout)
        content_layout.addWidget(logo_container)
        
        # Search bar (rectangular)
        search_container = self._create_search_bar()
        content_layout.addWidget(search_container, alignment=Qt.AlignmentFlag.AlignCenter)
        
        # Results area (grid or empty state)
        self.results_container = QWidget()
        self.results_layout = QVBoxLayout()
        self.results_layout.setContentsMargins(0, 0, 0, 0)
        self.results_container.setLayout(self.results_layout)
        
        # Scroll area for results
        self.results_scroll = QScrollArea()
        self.results_scroll.setWidgetResizable(True)
        self.results_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.results_scroll.setWidget(self.results_container)
        content_layout.addWidget(self.results_scroll, 1)
        
        page.setLayout(content_layout)
        return page

    def _create_welcome_screen(self):
        """Create the welcome screen (Screen 1 in Figma) - auth flow start."""
        t = Theme.current
        page = QWidget()
        
        layout = QVBoxLayout()
        layout.setSpacing(0)
        layout.setContentsMargins(40, 40, 40, 40)
        
        # Add stretch to center content vertically
        layout.addStretch(2)
        
        # Logo - ClipABit SVG (larger for welcome)
        self.welcome_logo_widget = QSvgWidget()
        self.welcome_logo_widget.setFixedSize(200, 80)
        self._update_welcome_logo()
        
        # Center the logo
        logo_container = QWidget()
        logo_layout = QHBoxLayout()
        logo_layout.setContentsMargins(0, 0, 0, 0)
        logo_layout.addStretch()
        logo_layout.addWidget(self.welcome_logo_widget)
        logo_layout.addStretch()
        logo_container.setLayout(logo_layout)
        layout.addWidget(logo_container)
        
        # Spacer
        layout.addSpacing(20)
        
        # Tagline
        tagline = QLabel("Search by ideas, not timestamps.")
        tagline.setObjectName("welcomeTagline")
        tagline.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(tagline)
        
        # Spacer
        layout.addSpacing(40)
        
        # Get Started button
        btn_container = QWidget()
        btn_layout = QHBoxLayout()
        btn_layout.setContentsMargins(0, 0, 0, 0)
        btn_layout.addStretch()
        
        self.btn_get_started = QPushButton("Get Started")
        self.btn_get_started.setObjectName("getStartedButton")
        self.btn_get_started.setFixedSize(200, 50)
        self.btn_get_started.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_get_started.clicked.connect(self._on_get_started_clicked)
        btn_layout.addWidget(self.btn_get_started)
        
        btn_layout.addStretch()
        btn_container.setLayout(btn_layout)
        layout.addWidget(btn_container)
        
        # Add stretch to center content vertically
        layout.addStretch(3)
        
        page.setLayout(layout)
        return page

    def _create_device_code_screen(self):
        t = Theme.current
        page = QWidget()
        
        layout = QVBoxLayout()
        layout.setSpacing(0)
        layout.setContentsMargins(40, 40, 40, 40)
        
        # Add stretch to center content vertically
        layout.addStretch(2)
        
        # Header with URL
        header_label = QLabel('Follow these steps on <a href="https://clipabit.web.app">https://clipabit.web.app</a>')
        header_label.setObjectName("deviceCodeHeader")
        header_label.setOpenExternalLinks(True)  # Make the link clickable
        header_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(header_label)
        
        # Spacer
        layout.addSpacing(40)
        
        # Steps container - centered
        steps_container = QWidget()
        steps_container.setFixedWidth(400)
        steps_layout = QVBoxLayout()
        steps_layout.setSpacing(25)
        steps_layout.setContentsMargins(0, 0, 0, 0)
        
        # Step 1: Sign up
        step1_row = QHBoxLayout()
        step1_row.setSpacing(15)
        
        step1_badge = QLabel("1")
        step1_badge.setObjectName("stepBadge")
        step1_badge.setFixedSize(32, 32)
        step1_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        step1_row.addWidget(step1_badge)
        
        step1_text_layout = QVBoxLayout()
        step1_text_layout.setSpacing(4)
        step1_label = QLabel("Sign up OR sign in to your account.")
        step1_label.setObjectName("stepLabel")
        step1_text_layout.addWidget(step1_label)
        step1_row.addLayout(step1_text_layout)
        step1_row.addStretch()
        
        steps_layout.addLayout(step1_row)
        
        # Step 2: Confirm code
        step2_row = QHBoxLayout()
        step2_row.setSpacing(15)
        
        step2_badge = QLabel("2")
        step2_badge.setObjectName("stepBadge")
        step2_badge.setFixedSize(32, 32)
        step2_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        step2_row.addWidget(step2_badge)
        
        step2_text_layout = QVBoxLayout()
        step2_text_layout.setSpacing(4)
        step2_label = QLabel("Confirm this code in the dashboard to access the plugin.")
        step2_label.setObjectName("stepLabel")
        step2_text_layout.addWidget(step2_label)
        step2_row.addLayout(step2_text_layout)
        step2_row.addStretch()
        
        steps_layout.addLayout(step2_row)
        
        # Code display box
        self.code_display = QLabel("----")
        self.code_display.setObjectName("codeDisplay")
        self.code_display.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.code_display.setFixedHeight(80)
        steps_layout.addWidget(self.code_display)
        
        # Waiting message
        self.waiting_label = QLabel("Waiting for authorization...")
        self.waiting_label.setObjectName("waitingLabel")
        self.waiting_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        steps_layout.addWidget(self.waiting_label)
        
        steps_container.setLayout(steps_layout)
        
        # Center the steps container
        center_layout = QHBoxLayout()
        center_layout.addStretch()
        center_layout.addWidget(steps_container)
        center_layout.addStretch()
        layout.addLayout(center_layout)
        
        # Add stretch to center content vertically
        layout.addStretch(3)
        
        page.setLayout(layout)
        return page

    def _update_welcome_logo(self):
        """Update the welcome screen logo based on current theme."""
        t = Theme.current
        if t == Theme.DARK:
            logo_path = os.path.join(os.path.dirname(__file__), '..', '..', 'assets', 'logo-dark.svg')
        else:
            logo_path = os.path.join(os.path.dirname(__file__), '..', '..', 'assets', 'logo-light.svg')
        
        if hasattr(self, 'welcome_logo_widget') and os.path.exists(logo_path):
            self.welcome_logo_widget.load(logo_path)

    def _on_get_started_clicked(self):
        """Handle 'Get Started' button click - request device code."""
        self.btn_get_started.setEnabled(False)
        self.btn_get_started.setText("Loading...")
        self.status_label.setText("Requesting device code...")
        
        try:
            # Request device code from backend
            response = requests.post(Config.DEVICE_CODE_URL, timeout=30)
            
            if response.status_code == 200:
                result = response.json()
                self.device_code = result.get("device_code")
                self.user_code = result.get("user_code", "----")
                
                if self.device_code and self.user_code:
                    # Update the code display
                    self.code_display.setText(self.user_code)
                    
                    # Switch to device code screen
                    self.stacked_widget.setCurrentIndex(1)
                    self.status_label.setText(f"Enter code {self.user_code} at clipabit.app")
                    
                    # Start polling for authorization
                    self._start_auth_polling()
                    
                    # Auto-open browser to sign-in page
                    webbrowser.open("https://clipabit.web.app/sign-in")
                else:
                    QMessageBox.warning(self, "Error", "Invalid response from server. Please try again.")
                    self.btn_get_started.setEnabled(True)
                    self.btn_get_started.setText("Get Started")
            else:
                QMessageBox.warning(self, "Error", f"Failed to get device code: {response.status_code}\n{response.text}")
                self.btn_get_started.setEnabled(True)
                self.btn_get_started.setText("Get Started")
                
        except requests.exceptions.RequestException as e:
            QMessageBox.critical(self, "Network Error", f"Failed to connect to server:\n{str(e)}")
            self.btn_get_started.setEnabled(True)
            self.btn_get_started.setText("Get Started")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"An error occurred:\n{str(e)}")
            self.btn_get_started.setEnabled(True)
            self.btn_get_started.setText("Get Started")
        
        self.status_label.setText("Ready")

    def _start_auth_polling(self):
        """Start polling for auth token."""
        if self.auth_polling_timer:
            self.auth_polling_timer.stop()
        
        self.auth_polling_timer = QTimer()
        self.auth_polling_timer.timeout.connect(self._poll_for_auth_token)
        self.auth_polling_timer.start(5000)  # Poll every 5 seconds
        print("[Auth] Started polling for authorization")

    def _stop_auth_polling(self):
        """Stop polling for auth token."""
        if self.auth_polling_timer:
            self.auth_polling_timer.stop()
            self.auth_polling_timer = None
        print("[Auth] Stopped polling")

    def _poll_for_auth_token(self):
        """Poll the backend to check if user has authorized."""
        if not self.device_code:
            self._stop_auth_polling()
            return
        
        try:
            response = requests.post(
                Config.DEVICE_POLL_URL,
                json={"device_code": self.device_code},
                timeout=10
            )
            
            if response.status_code == 200:
                result = response.json()
                status = result.get("status", "pending")
                
                if status == "authorized" or status == "success":
                    # User has authorized - get the token
                    token = result.get("access_token") or result.get("token")
                    if token:
                        self._stop_auth_polling()
                        self._save_auth_token(token)
                        
                        # Clear device code state
                        self.device_code = None
                        self.user_code = None
                        
                        # Transition to search screen
                        self.stacked_widget.setCurrentIndex(2)
                        self.status_label.setText("Authenticated! Ready to search.")
                        print("[Auth] Successfully authorized")
                    else:
                        print("[Auth] Authorized but no token in response")
                        
                elif status == "pending":
                    # Still waiting - update UI
                    self.waiting_label.setText("Waiting for authorization...")
                    print("[Auth] Still pending authorization")
                    
                elif status == "expired":
                    # Device code expired
                    self._stop_auth_polling()
                    QMessageBox.warning(
                        self,
                        "Code Expired",
                        "The device code has expired. Please try again."
                    )
                    # Go back to welcome screen
                    self.stacked_widget.setCurrentIndex(0)
                    self.btn_get_started.setEnabled(True)
                    self.btn_get_started.setText("Get Started")
                    self.device_code = None
                    self.user_code = None
                    
            elif response.status_code == 400:
                # Bad request - might be expired or invalid
                result = response.json() if response.text else {}
                error = result.get("error", "")
                if "expired" in error.lower():
                    self._stop_auth_polling()
                    QMessageBox.warning(self, "Code Expired", "The device code has expired. Please try again.")
                    self.stacked_widget.setCurrentIndex(0)
                    self.btn_get_started.setEnabled(True)
                    self.btn_get_started.setText("Get Started")
                    self.device_code = None
                    self.user_code = None
                    
        except requests.exceptions.RequestException as e:
            print(f"[Auth] Poll request failed: {e}")
            # Don't stop polling on network error, will retry next interval
            self.waiting_label.setText("Connection error, retrying...")
        except Exception as e:
            print(f"[Auth] Poll error: {e}")

    def _create_header(self):
        """Create the header bar with Media Pool, Debug buttons and settings."""
        header = QWidget()
        header.setFixedHeight(60)
        header.setObjectName("header")
        
        layout = QHBoxLayout()
        layout.setContentsMargins(20, 10, 20, 10)
        
        # Spacer to push buttons to the right
        layout.addStretch()
        
        # Media Pool button
        self.btn_media_pool = QPushButton("Media Pool")
        self.btn_media_pool.setObjectName("headerButton")
        self.btn_media_pool.clicked.connect(self._show_media_pool_dialog)
        layout.addWidget(self.btn_media_pool)
        
        # Debug button
        self.btn_debug = QPushButton("Debug")
        self.btn_debug.setObjectName("headerButton")
        self.btn_debug.clicked.connect(self._show_jobs_dialog)
        layout.addWidget(self.btn_debug)
        
        # Settings button (gear icon)
        self.btn_settings = QPushButton("⚙")
        self.btn_settings.setObjectName("settingsButton")
        self.btn_settings.setFixedSize(40, 40)
        self.btn_settings.clicked.connect(self._show_settings_dialog)
        layout.addWidget(self.btn_settings)
        
        header.setLayout(layout)
        return header
    
    def _create_search_bar(self):
        """Create the search bar matching Figma design."""
        container = QWidget()
        container.setObjectName("searchContainer")
        container.setFixedHeight(52)
        container.setMinimumWidth(500)
        container.setMaximumWidth(600)
        
        layout = QHBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)  # No padding - button flush with edge
        layout.setSpacing(0)
        
        # Search button (left side) - flush with container edge
        self.btn_search = QPushButton("Search")
        self.btn_search.setObjectName("searchButton")
        self.btn_search.setFixedSize(90, 52)  # Full height of container
        self.btn_search.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_search.clicked.connect(self._perform_search)
        layout.addWidget(self.btn_search)
        
        # Search input
        self.search_input = QLineEdit()
        self.search_input.setObjectName("searchInput")
        self.search_input.setPlaceholderText("Enter search query (e.g., 'woman walking', 'car driving')")
        self.search_input.returnPressed.connect(self._perform_search)
        self.search_input.textChanged.connect(self._on_search_text_changed)
        layout.addWidget(self.search_input, 1)
        
        # Clear (X) button - hidden by default
        self.btn_clear_search = QPushButton("✕")
        self.btn_clear_search.setObjectName("clearSearchButton")
        self.btn_clear_search.setFixedSize(30, 30)
        self.btn_clear_search.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_clear_search.clicked.connect(self._clear_search)
        self.btn_clear_search.hide()
        layout.addWidget(self.btn_clear_search)
        
        container.setLayout(layout)
        return container
    
    def _on_search_text_changed(self, text):
        """Show/hide clear button based on search text."""
        if text:
            self.btn_clear_search.show()
        else:
            self.btn_clear_search.hide()
    
    def _clear_search(self):
        """Clear the search input and reset results."""
        self.search_input.clear()
        self._show_empty_state()
    
    def _show_empty_state(self):
        """Clear results and show empty state (just clears the area)."""
        # Clear previous results
        for i in reversed(range(self.results_layout.count())):
            item = self.results_layout.itemAt(i)
            if item.widget():
                item.widget().setParent(None)
    
    def _update_logo(self):
        """Update the logo based on current theme."""
        t = Theme.current
        # Determine which logo to use based on theme
        if t == Theme.DARK:
            logo_path = os.path.join(os.path.dirname(__file__), '..', '..', 'assets', 'logo-dark.svg')
        else:
            logo_path = os.path.join(os.path.dirname(__file__), '..', '..', 'assets', 'logo-light.svg')
        
        if os.path.exists(logo_path):
            self.logo_widget.load(logo_path)
    
    def _show_loading_state(self):
        """Show the loading/searching state."""
        from PyQt6.QtWidgets import QApplication
        t = Theme.current
        
        # Clear previous results
        for i in reversed(range(self.results_layout.count())):
            item = self.results_layout.itemAt(i)
            if item.widget():
                item.widget().setParent(None)
        
        # Hide empty state
        if hasattr(self, 'empty_state_label'):
            self.empty_state_label.hide()
        
        # Show loading indicator
        from PyQt6.QtWidgets import QVBoxLayout
        loading_container = QWidget()
        loading_layout = QVBoxLayout()
        loading_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        # Loading spinner (text dots)
        loading_label = QLabel("●  ●  ●")
        loading_label.setObjectName("loadingSpinner")
        loading_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        loading_layout.addWidget(loading_label)
        
        # Loading text
        searching_label = QLabel("searching...")
        searching_label.setObjectName("loadingText")
        searching_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        loading_layout.addWidget(searching_label)
        
        loading_container.setLayout(loading_layout)
        self.results_layout.addWidget(loading_container)
        self.results_layout.addStretch()
        
        QApplication.processEvents()

    # --- Utility Wrappers (Delegated to core.utils) ---
    def _get_storage_path(self) -> Path:
        return get_storage_path()
        
    def _load_processed_files(self) -> Dict[str, Dict]:
        return load_processed_files()
        
    def _save_processed_files(self):
        save_processed_files(self.processed_files)
            
    def _get_file_hash(self, filepath: str) -> str:
        return get_file_hash(filepath)

    def _get_hashed_identifier(self, filepath: str, namespace: str, filename: str) -> str:
        return get_hashed_identifier(filepath, namespace, filename)

    # --- Methods to FILL IN ---
    def _apply_theme(self):
        """Apply the current theme stylesheet."""
        t = Theme.current
        
        self.setStyleSheet(f"""
            /* Main window */
            QWidget {{
                background-color: {t['background']};
                color: {t['text']};
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                font-weight: 300;
            }}
            
            /* Header */
            QWidget#header {{
                background-color: {t['background']};
            }}
            
            /* Header button - rectangular, gray */
            QPushButton#headerButton {{
                background-color: #9CA3AF;
                color: #FFFFFF;
                border: none;
                border-radius: 0px;
                padding: 10px 20px;
                font-size: 13px;
            }}
            QPushButton#headerButton:hover {{
                background-color: #6B7280;
            }}
            
            /* Settings button */
            QPushButton#settingsButton {{
                background-color: transparent;
                color: {t['text_secondary']};
                border: none;
                font-size: 20px;
            }}
            QPushButton#settingsButton:hover {{
                color: {t['text']};
            }}
            
            
            /* Search container - rectangular */
            QWidget#searchContainer {{
                background-color: {t['search_bg']};
                border-radius: 0px;
            }}
            
            /* Search button - rectangular */
            QPushButton#searchButton {{
                background-color: {t['accent']};
                color: {t['button_text']};
                border: none;
                border-radius: 0px;
                font-size: 14px;
                font-weight: 200;
            }}
            QPushButton#searchButton:hover {{
                background-color: {t['accent_hover']};
            }}
            
            /* Clear search button */
            QPushButton#clearSearchButton {{
                background-color: transparent;
                color: {t['search_text']};
                border: none;
                font-size: 14px;
            }}
            QPushButton#clearSearchButton:hover {{
                color: #000000;
            }}
            
            /* Search input */
            QLineEdit#searchInput {{
                background-color: transparent;
                border: none;
                color: {t['search_text']};
                font-size: 14px;
                padding: 0 15px;
            }}
            QLineEdit#searchInput::placeholder {{
                color: {t['search_placeholder']};
            }}
            
            
            /* Loading spinner */
            QLabel#loadingSpinner {{
                color: {t['text']};
                font-size: 32px;
                padding: 50px;
            }}
            
            /* Loading text */
            QLabel#loadingText {{
                color: {t['text_secondary']};
                font-size: 16px;
            }}
            
            /* Status bar */
            QLabel#statusBar {{
                color: {t['text_secondary']};
                font-size: 11px;
                padding: 8px 20px;
                background-color: {t['background']};
            }}
            
            /* Result card - rectangular */
            QFrame#resultCard {{
                background-color: {t['card_bg']};
                border-radius: 0px;
                border: 1px solid {t['border']};
            }}
            
            /* Thumbnail placeholder - rectangular */
            QLabel#thumbnail {{
                background-color: #D9D9D9;
                border-radius: 0px;
            }}
            
            /* Add to timeline button - light orange, rectangular */
            QPushButton#addToTimelineBtn {{
                background-color: #FFD89E;
                color: #000000;
                border: none;
                border-radius: 0px;
                padding: 10px 20px;
                font-size: 12px;
                font-weight: 200;
            }}
            QPushButton#addToTimelineBtn:hover {{
                background-color: #F5A623;
            }}
            
            /* Scroll area */
            QScrollArea {{
                border: none;
                background-color: transparent;
            }}
            QScrollBar:vertical {{
                background-color: {t['background']};
                width: 8px;
                border-radius: 0px;
            }}
            QScrollBar::handle:vertical {{
                background-color: {t['border']};
                border-radius: 0px;
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
                color: {t['button_text']};
                border: none;
                border-radius: 0px;
                padding: 10px 20px;
            }}
            QDialog QPushButton:hover {{
                background-color: {t['accent_hover']};
            }}
            
            /* Auth screen styles */
            QLabel#welcomeTagline {{
                color: {t['welcome_text']};
                font-size: 18px;
                font-weight: 300;
            }}
            
            QPushButton#getStartedButton {{
                background-color: {t['accent']};
                color: {t['button_text']};
                border: none;
                border-radius: 0px;
                font-size: 16px;
                font-weight: 400;
            }}
            QPushButton#getStartedButton:hover {{
                background-color: {t['accent_hover']};
            }}
            QPushButton#getStartedButton:disabled {{
                background-color: #CCCCCC;
                color: #666666;
            }}
            
            QLabel#stepBadge {{
                background-color: {t['step_badge_bg']};
                color: {t['step_badge_text']};
                font-size: 16px;
                font-weight: 400;
                border-radius: 16px;
            }}
            
            QLabel#stepLabel {{
                color: {t['text']};
                font-size: 16px;
                font-weight: 300;
            }}
            
            QLabel#stepUrl {{
                color: {t['light_border']};
                font-size: 14px;
                font-weight: 300;
            }}
            
            QLabel#codeDisplay {{
                background-color: {t['code_bg']};
                color: #000000;
                font-size: 36px;
                font-weight: 400;
                font-family: 'Courier New', monospace;
                border-radius: 0px;
                padding: 20px;
            }}
            
            QLabel#waitingLabel {{
                color: {t['text_secondary']};
                font-size: 14px;
                font-weight: 300;
            }}
            
            QLabel#deviceCodeHeader {{
                color: {t['text']};
                font-size: 18px;
                font-weight: 400;
            }}
        """)
    
    def _show_jobs_dialog(self):
        """Show the Active Jobs/Debug dialog."""
        dialog = QDialog(self)
        dialog.setWindowTitle("Active Jobs / Debug")
        dialog.setMinimumSize(500, 400)
        
        layout = QVBoxLayout()
        layout.setSpacing(15)
        layout.setContentsMargins(20, 20, 20, 20)
        
        # Jobs section
        jobs_title = QLabel("Active Jobs")
        jobs_title.setStyleSheet("font-size: 18px; font-weight: 400;")
        layout.addWidget(jobs_title)
        
        # Jobs list
        self.dialog_jobs_list = QListWidget()
        self.dialog_jobs_list.setMinimumHeight(150)
        self._update_jobs_list_widget(self.dialog_jobs_list)
        layout.addWidget(self.dialog_jobs_list)
        
        # Debug section
        debug_title = QLabel("Debug Info")
        debug_title.setStyleSheet("font-size: 18px; font-weight: 400;")
        layout.addWidget(debug_title)
        
        # Storage path
        storage_path = self._get_storage_path()
        storage_label = QLabel(f"Storage: {storage_path}")
        storage_label.setWordWrap(True)
        storage_label.setStyleSheet("color: #8E8E93; font-size: 11px;")
        layout.addWidget(storage_label)
        
        # Processed files count
        processed_label = QLabel(f"Processed: {len(self.processed_files)} files")
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
        dialog.exec()
    
    def _show_media_pool_dialog(self):
        """Show the Media Pool dialog with file processing queue."""
        from PyQt6.QtWidgets import QProgressBar
        dialog = QDialog(self)
        dialog.setWindowTitle("Media Pool")
        dialog.setMinimumSize(600, 500)
        self._media_pool_dialog = dialog
        
        t = Theme.current
        
        layout = QVBoxLayout()
        layout.setSpacing(10)
        layout.setContentsMargins(20, 20, 20, 20)
        
        # Scrollable area for file list
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(f"QScrollArea {{ border: none; background-color: {t['background']}; }}")
        
        scroll_content = QWidget()
        self.media_pool_layout = QVBoxLayout()
        self.media_pool_layout.setSpacing(15)
        self.media_pool_layout.setContentsMargins(0, 0, 0, 0)
        
        # Populate file list
        self._populate_media_pool_list()
        
        self.media_pool_layout.addStretch()
        scroll_content.setLayout(self.media_pool_layout)
        scroll.setWidget(scroll_content)
        layout.addWidget(scroll)
        
        # Close button
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(dialog.accept)
        layout.addWidget(close_btn)
        
        dialog.setLayout(layout)
        dialog.exec()
    
    def _populate_media_pool_list(self):
        """Populate the media pool with file items."""
        from PyQt6.QtWidgets import QProgressBar
        t = Theme.current
        
        # Clear existing items
        for i in reversed(range(self.media_pool_layout.count())):
            item = self.media_pool_layout.itemAt(i)
            if item.widget():
                item.widget().setParent(None)
        
        # Get files from upload queue and processed files
        files_to_show = []
        
        for file_info in self.upload_queue:
            files_to_show.append({
                'path': file_info.get('filepath', ''),
                'name': file_info.get('filename', 'Unknown'),
                'status': 'queued',
                'progress': 0
            })
        
        for file_path, file_info in self.processed_files.items():
            files_to_show.append({
                'path': file_info.get('filepath', ''),
                'name': file_info.get('filename', 'Unknown'),
                'status': 'complete',
                'progress': 100
            })
        
        if not files_to_show:
            # Empty state with Process Videos button
            empty_container = QWidget()
            empty_layout = QVBoxLayout()
            empty_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty_layout.setSpacing(20)
            
            empty_label = QLabel("No files in media pool.")
            empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty_label.setStyleSheet(f"color: {t['text_secondary']}; font-size: 14px;")
            empty_layout.addWidget(empty_label)
            
            # Process Videos button
            process_btn = QPushButton("Process Videos")
            process_btn.setFixedWidth(200)
            process_btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: {t['accent']};
                    color: {t['button_text']};
                    border: none;
                    border-radius: 0px;
                    padding: 12px 24px;
                    font-size: 14px;
                    font-weight: 400;
                }}
                QPushButton:hover {{
                    background-color: {t['accent_hover']};
                }}
            """)
            process_btn.clicked.connect(self._process_videos_from_resolve)
            empty_layout.addWidget(process_btn, alignment=Qt.AlignmentFlag.AlignCenter)
            
            empty_container.setLayout(empty_layout)
            self.media_pool_layout.addWidget(empty_container)
            return
        
        for file_info in files_to_show:
            item = self._create_media_pool_item(file_info)
            self.media_pool_layout.addWidget(item)
    
    def _create_media_pool_item(self, file_info):
        """Create a media pool item widget."""
        from PyQt6.QtWidgets import QProgressBar
        t = Theme.current
        
        container = QWidget()
        layout = QHBoxLayout()
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(15)
        
        # Video file icon
        icon_label = QLabel("🎬")
        icon_label.setFixedSize(40, 40)
        icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_label.setStyleSheet("font-size: 24px;")
        layout.addWidget(icon_label)
        
        # File info
        info_layout = QVBoxLayout()
        info_layout.setSpacing(5)
        
        name_row = QHBoxLayout()
        name_label = QLabel(file_info['name'])
        name_label.setStyleSheet(f"font-size: 14px; font-weight: 400; color: {t['text']};")
        name_row.addWidget(name_label)
        name_row.addStretch()
        
        status = file_info['status']
        progress = file_info['progress']
        if status == 'complete':
            status_text = "100%"
            status_color = "#F5A623"
        elif status == 'error':
            status_text = "error"
            status_color = "#E53935"
        else:
            status_text = f"{progress}%"
            status_color = "#F5A623"
        
        status_label = QLabel(status_text)
        status_label.setStyleSheet(f"font-size: 14px; color: {status_color};")
        name_row.addWidget(status_label)
        
        info_layout.addLayout(name_row)
        
        # Progress bar
        progress_bar = QProgressBar()
        progress_bar.setFixedHeight(6)
        progress_bar.setTextVisible(False)
        progress_bar.setValue(progress)
        bar_color = "#E53935" if status == 'error' else "#F5A623"
        progress_bar.setStyleSheet(f"""
            QProgressBar {{ background-color: #E0E0E0; border: none; }}
            QProgressBar::chunk {{ background-color: {bar_color}; }}
        """)
        info_layout.addWidget(progress_bar)
        
        layout.addLayout(info_layout, 1)
        
        # Action button
        if status == 'complete':
            action_btn = QPushButton("🗑")
            action_btn.setToolTip("Remove")
        elif status == 'error':
            action_btn = QPushButton("↻")
            action_btn.setToolTip("Retry")
        else:
            action_btn = QPushButton("✕")
            action_btn.setToolTip("Cancel")
        
        action_btn.setFixedSize(30, 30)
        action_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: transparent;
                border: none;
                font-size: 18px;
                color: {t['text_secondary']};
            }}
            QPushButton:hover {{ color: {t['text']}; }}
        """)
        layout.addWidget(action_btn)
        
        container.setLayout(layout)
        return container
    
    def _process_videos_from_resolve(self):
        """Process videos from Resolve's media pool."""
        if not resolve:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "Not Available", "Resolve API not available.")
            return
        
        self._refresh_media_pool(debug=False)
        
        files_added = 0
        for _, clip_info in self.clip_map.items():
            if isinstance(clip_info, list):
                for entry in clip_info:
                    file_path = entry.get('filepath', '')
                    if file_path and os.path.exists(file_path):
                        file_hash = self._get_file_hash(file_path)
                        if file_hash not in self.processed_files and not self._is_file_being_processed(file_hash):
                            self.upload_queue.append({
                                'filepath': file_path,
                                'filename': os.path.basename(file_path),
                                'hash': file_hash
                            })
                            files_added += 1
            else:
                file_path = clip_info.get('filepath', '')
                if file_path and os.path.exists(file_path):
                    file_hash = self._get_file_hash(file_path)
                    if file_hash not in self.processed_files and not self._is_file_being_processed(file_hash):
                        self.upload_queue.append({
                            'filepath': file_path,
                            'filename': os.path.basename(file_path),
                            'hash': file_hash
                        })
                        files_added += 1
        
        if files_added > 0:
            self._update_file_status()
            self._populate_media_pool_list()
            if not self.is_uploading:
                self._process_upload_queue()
        else:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.information(self, "No New Files", "No new video files found in Resolve's media pool.")
    
    def _show_settings_dialog(self):
        """Show the Settings dialog."""
        dialog = QDialog(self)
        dialog.setWindowTitle("Settings")
        dialog.setMinimumSize(400, 300)
        
        layout = QVBoxLayout()
        layout.setSpacing(15)
        layout.setContentsMargins(20, 20, 20, 20)
        
        # Data Management section
        data_title = QLabel("Data Management")
        data_title.setStyleSheet("font-size: 18px; font-weight: 400;")
        layout.addWidget(data_title)
        
        btn_clear = QPushButton("Clear Processed Files")
        btn_clear.setStyleSheet("background-color: #FFFFFF; color: #000000;")
        btn_clear.clicked.connect(self._clear_processed_files)
        layout.addWidget(btn_clear)
        
        # Divider
        divider = QFrame()
        divider.setFrameShape(QFrame.Shape.HLine)
        divider.setStyleSheet("background-color: #4A4B52;")
        layout.addWidget(divider)
        
        # Appearance section
        theme_title = QLabel("Appearance")
        theme_title.setStyleSheet("font-size: 18px; font-weight: 400;")
        layout.addWidget(theme_title)
        
        theme_btn = QPushButton("Toggle Dark/Light Mode")
        theme_btn.setStyleSheet("background-color: #FFFFFF; color: #000000;")
        theme_btn.clicked.connect(self._toggle_theme)
        layout.addWidget(theme_btn)
        
        layout.addStretch()
        
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(dialog.accept)
        layout.addWidget(close_btn)
        
        dialog.setLayout(layout)
        dialog.exec()
    
    def _toggle_theme(self):
        """Toggle between dark and light themes."""
        if Theme.current == Theme.DARK:
            Theme.current = Theme.LIGHT
        else:
            Theme.current = Theme.DARK
        self._apply_theme()
        self._update_logo()
        self._update_welcome_logo()
    
    def _get_file_status_text(self):
        """Get the file status text for display."""
        total = len(self.clip_map)
        processed = len(self.processed_files)
        new_files = total - processed
        return f"Total: {total} files | Processed: {processed} | New: {new_files}"
    
    def _update_jobs_list_widget(self, list_widget):
        """Update a jobs list widget with current jobs."""
        list_widget.clear()
        if not self.current_jobs and not self.upload_queue:
            list_widget.addItem("No active jobs")
        else:
            for job_id, job_info in self.current_jobs.items():
                filename = job_info.get('filename', 'Unknown')
                list_widget.addItem(f"Processing: {filename}")
            for file_info in self.upload_queue:
                filename = file_info.get('filename', 'Unknown')
                list_widget.addItem(f"Queued: {filename}")
    
    def _select_files_to_upload_dialog(self, dialog):
        """Handle file selection from the settings dialog."""
        self._select_files_to_upload()
        # Update the dialog's file status
        if hasattr(self, 'dialog_file_status'):
            self.dialog_file_status.setText(self._get_file_status_text())
    def _update_file_status(self):
        """Update the file status display."""
        total_files = len(self.clip_map)
        processed_count = 0
        new_files = []
        
        for filename, clip_info in self.clip_map.items():
            if isinstance(clip_info, list):
                # Handle multiple clips with same filename
                for clip in clip_info:
                    filepath = clip.get('filepath')
                    if filepath:
                        file_hash = self._get_file_hash(filepath)
                        if file_hash in self.processed_files:
                            processed_count += 1
                        else:
                            new_files.append(filename)
            else:
                filepath = clip_info.get('filepath')
                if filepath:
                    file_hash = self._get_file_hash(filepath)
                    if file_hash in self.processed_files:
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
        
        # Update status bar in new UI
        if hasattr(self, 'status_label') and self.status_label:
            self.status_label.setText(status_text)
            
        # Update dialog status label if open
        if hasattr(self, 'dialog_file_status') and self.dialog_file_status and self.dialog_file_status.isVisible():
            self.dialog_file_status.setText(status_text)
        
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
            file_hash = self._get_file_hash(filepath)

            if file_hash in self.processed_files:
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
        
    def _check_if_file_exists_in_backend(self, filename: str, namespace: Optional[str] = None, hashed_identifier: Optional[str] = None) -> bool:
        """Backend verification disabled: rely on local storage only."""
        _ = (filename, namespace, hashed_identifier)
        # print("[Verify] Backend verification disabled (local storage only).")
        return False

    def _delete_backend_entry(self, filename: str, hashed_identifier: str, namespace: str):
        """Request backend deletion for a file's Pinecone data."""
        # print(f"[Delete] Backend deletion disabled for {filename} (local storage only).")
        pass
            
    def _process_upload_queue(self):
        """Process the next file in the upload queue."""
        if not self.upload_queue or self.is_uploading:
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
        self.status_label.setText(f"Uploading: {filename} ({remaining} remaining in queue)")
        
        # Build namespace from user_id and project_name
        user_id = self.get_or_create_device_id()
        project_name = self.get_project_name() or "default"
        
        # Simple namespace format: user_id-project_name (sanitized)
        user_id_safe = user_id.lower().replace(" ", "_")
        project_safe = project_name.lower().replace(" ", "_")
        namespace = f"{user_id_safe}-{project_safe}"
        
        # Upload the file
        self._upload_single_file(file_info, namespace)
            
    def _upload_single_file(self, file_info: Dict, namespace: str, retry_count: int = 0, max_retries: int = 3):
        """Upload a single file to the backend with retry logic."""
        filepath = file_info['filepath']
        filename = file_info['filename']
        file_hash = file_info['hash']
        
        try:
            if retry_count > 0:
                print(f"[Upload] Retry attempt {retry_count}/{max_retries} for {filename}")
            else:
                print(f"[Upload] Starting upload for {filename}")
            
            # Get file size first
            file_size = os.path.getsize(filepath)
            file_size_mb = file_size / (1024 * 1024)
                
            # Upload to backend - use file object directly for streaming
            # This is more memory efficient and may help with connection stability
            with open(filepath, 'rb') as f:
                # FastAPI expects files as a list - use list of tuples format
                files_data = [("files", (filename, f, "video/mp4"))]
                data = {"namespace": namespace}
                
                self.status_label.setText(f"Uploading {filename}... (attempt {retry_count + 1})")
                print(f"[Upload] Sending POST request to {Config.UPLOAD_API_URL}")
                
                # Use a session for better connection management
                session = requests.Session()
                # Increase timeout for larger files (calculate based on file size)
                # Allow at least 1 minute per MB, minimum 60 seconds, max 10 minutes
                upload_timeout = min(600, max(60, int(file_size_mb * 60)))
                
                response = session.post(
                    Config.UPLOAD_API_URL, 
                    files=files_data, 
                    data=data, 
                    timeout=upload_timeout,
                    stream=False  # Don't stream response, we need the full response
                )
            
            print(f"[Upload] Response status: {response.status_code}")
            
            if response.status_code == 200:
                result = response.json()
                job_id = result.get("job_id")
                
                if job_id:
                    print(f"[Upload] ✅ Upload successful, job_id: {job_id}")
                    temp_job_id = f"queued_{file_hash[:8]}"
                    if temp_job_id in self.current_jobs:
                        del self.current_jobs[temp_job_id]
                    # Replace temp job with real job
                    job_info = {
                        'filename': filename,
                        'filepath': filepath,
                        'file_hash': file_hash,
                        'status': 'processing',
                        'namespace': namespace
                    }
                    
                    self.current_jobs[job_id] = job_info
                    self.job_tracker.add_job(job_id, job_info)
                    self._update_jobs_display()
                    
                    self.status_label.setText(f"Upload started: {filename}")
                else:
                    error_msg = f"Upload failed for {filename}: No job ID returned. Response: {result}"
                    print(f"[Upload] ❌ {error_msg}")
                    QMessageBox.warning(self, "Error", error_msg)
                    # Continue with next upload even if this one failed
                    self._on_upload_completed(False)
            else:
                error_msg = f"Upload failed for {filename}: HTTP {response.status_code}\n{response.text}"
                print(f"[Upload] ❌ {error_msg}")
                QMessageBox.warning(self, "Error", error_msg)
                temp_job_id = f"queued_{file_hash[:8]}"
                if temp_job_id in self.current_jobs:
                    del self.current_jobs[temp_job_id]
                # Continue with next upload even if this one failed
                self._on_upload_completed(False)
                
        except (requests.exceptions.ConnectionError, requests.exceptions.ProtocolError) as e:
            # Retry connection errors with exponential backoff
            if retry_count < max_retries:
                wait_time = (2 ** retry_count) * 2  # 2, 4, 8 seconds
                error_msg = f"Connection error uploading {filename} (attempt {retry_count + 1}/{max_retries + 1}): {str(e)}"
                print(f"[Upload] ⚠️ {error_msg}")
                print(f"[Upload] Retrying in {wait_time} seconds...")
                self.status_label.setText(f"Connection error, retrying in {wait_time}s...")
                
                # Wait before retry
                time.sleep(wait_time)
                
                # Retry the upload
                self._upload_single_file(file_info, namespace, retry_count + 1, max_retries)
            else:
                error_msg = f"Connection error uploading {filename} after {max_retries + 1} attempts: {str(e)}"
                print(f"[Upload] ❌ {error_msg}")
                print(traceback.format_exc())
                temp_job_id = f"queued_{file_hash[:8]}"
                if temp_job_id in self.current_jobs:
                    del self.current_jobs[temp_job_id]
                QMessageBox.critical(self, "Network Error", f"{error_msg}\n\nPlease check your internet connection and try again.")
                self._on_upload_completed(False)
        except requests.exceptions.Timeout as e:
            # Retry timeout errors too
            if retry_count < max_retries:
                wait_time = (2 ** retry_count) * 2
                error_msg = f"Upload timeout for {filename} (attempt {retry_count + 1}/{max_retries + 1})"
                print(f"[Upload] ⚠️ {error_msg}")
                print(f"[Upload] Retrying in {wait_time} seconds...")
                self.status_label.setText(f"Timeout, retrying in {wait_time}s...")
                
                time.sleep(wait_time)
                
                self._upload_single_file(file_info, namespace, retry_count + 1, max_retries)
            else:
                error_msg = f"Upload timeout for {filename} after {max_retries + 1} attempts: {str(e)}"
                print(f"[Upload] ❌ {error_msg}")
                print(traceback.format_exc())
                temp_job_id = f"queued_{file_hash[:8]}"
                if temp_job_id in self.current_jobs:
                    del self.current_jobs[temp_job_id]
                QMessageBox.critical(self, "Upload Timeout", f"{error_msg}\n\nFile may be too large or connection too slow.")
                self._on_upload_completed(False)
        except requests.exceptions.RequestException as e:
            error_msg = f"Network error uploading {filename}: {str(e)}"
            print(f"[Upload] ❌ {error_msg}")
            print(traceback.format_exc())
            temp_job_id = f"queued_{file_hash[:8]}"
            if temp_job_id in self.current_jobs:
                del self.current_jobs[temp_job_id]
            QMessageBox.critical(self, "Network Error", error_msg)
            self._on_upload_completed(False)
        except FileNotFoundError as e:
            error_msg = f"File not found: {filepath}\n{str(e)}"
            print(f"[Upload] ❌ {error_msg}")
            print(traceback.format_exc())
            temp_job_id = f"queued_{file_hash[:8]}"
            if temp_job_id in self.current_jobs:
                del self.current_jobs[temp_job_id]
            QMessageBox.critical(self, "File Error", error_msg)
            self._on_upload_completed(False)
        except Exception as e:
            error_msg = f"Failed to upload {filename}: {str(e)}"
            print(f"[Upload] ❌ {error_msg}")
            print(traceback.format_exc())
            temp_job_id = f"queued_{file_hash[:8]}"
            if temp_job_id in self.current_jobs:
                del self.current_jobs[temp_job_id]
            QMessageBox.critical(self, "Error", error_msg)
            self._on_upload_completed(False)
            
    def _on_job_completed(self, job_id: str, result: dict):
        """Handle job completion."""
        if job_id in self.current_jobs:
            job_info = self.current_jobs[job_id]
            filename = job_info['filename']
            filepath = job_info['filepath']
            file_hash = job_info['file_hash']
            namespace = job_info['namespace']
            hashed_identifier = self._get_hashed_identifier(filepath, namespace, filename)
            expected_vectors = None
            try:
                if isinstance(result, dict) and result.get("chunks") is not None:
                    expected_vectors = int(result.get("chunks"))
            except (TypeError, ValueError):
                expected_vectors = None
            
            # Mark file as processed (keep existing tracking)
            self.processed_files[file_hash] = {
                'filename': filename,
                'filepath': filepath,
                'job_id': job_id,
                'namespace': namespace,
                'hashed_identifier': hashed_identifier,
                'processed_at': time.time(),
                'result': result,
                'backend_miss_count': 0,
                'last_backend_check': None,
                'expected_vector_count': expected_vectors,
                'vector_count': expected_vectors
            }
            self._save_processed_files()
            
            # Remove from current jobs
            del self.current_jobs[job_id]
            self._update_jobs_display()
            self._update_file_status()
            
            self.status_label.setText(f"Completed: {filename}")
            print(f"✅ Job {job_id} completed successfully for {filename}")
            
            # Continue with next upload in queue
            self._on_upload_completed(True)
            # Skip immediate consistency check; backend counts can lag right after upload
            
    def _on_job_failed(self, job_id: str, error: str):
        """Handle job failure."""
        if job_id in self.current_jobs:
            job_info = self.current_jobs[job_id]
            filename = job_info['filename']
            file_hash = job_info['file_hash']
            
            print(f"❌ Job {job_id} failed for {filename}: {error}")
            
            # Before marking as failed, check if file actually exists in backend
            # (in case job tracking failed but processing succeeded)
            namespace = job_info.get('namespace')
            if self._check_if_file_exists_in_backend(
                filename,
                namespace=namespace,
                hashed_identifier=self._get_hashed_identifier(job_info.get('filepath', ''), namespace or "", filename)
            ):
                print(f"🔄 Job failed but file {filename} found in backend - marking as completed")
                
                # Mark as processed since it's actually in the backend
                self.processed_files[file_hash] = {
                    'filename': filename,
                    'filepath': job_info.get('filepath', ''),
                    'job_id': job_id,
                    'namespace': namespace,
                    'hashed_identifier': self._get_hashed_identifier(job_info.get('filepath', ''), namespace or "", filename),
                    'processed_at': time.time(),
                    'result': {'status': 'recovered_from_backend', 'error': error}
                }
                self._save_processed_files()
                
                # Remove from current jobs
                del self.current_jobs[job_id]
                self._update_jobs_display()
                self._update_file_status()
                
                self.status_label.setText(f"Recovered: {filename} (found in backend)")
                
                # Continue with next upload
                self._on_upload_completed(True)
                return
            
            # Actually failed - remove from current jobs
            del self.current_jobs[job_id]
            self._update_jobs_display()
            
            self.status_label.setText(f"Failed: {filename}")
            QMessageBox.warning(self, "Upload Failed", f"Processing failed for {filename}:\n{error}")
            
            # Continue with next upload in queue even if this one failed
            self._on_upload_completed(False)
            
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
        """Update the jobs list display."""
        # Update main job list (if exists in legacy UI, usually replaced by dialog)
        if hasattr(self, 'jobs_list') and self.jobs_list:
            self.jobs_list.clear()
            for job_id, job_info in self.current_jobs.items():
                filename = job_info['filename']
                status = job_info.get('status', 'processing')
                self.jobs_list.addItem(f"{filename} - {status}")
        
        # Update dialog list if open
        if hasattr(self, 'dialog_jobs_list') and self.dialog_jobs_list and self.dialog_jobs_list.isVisible():
            self._update_jobs_list_widget(self.dialog_jobs_list)
    def _perform_search(self):
        """Perform semantic search."""
        query = self.search_input.text().strip()
        if not query:
            QMessageBox.warning(self, "Error", "Please enter a search query")
            return
            
        # Build namespace from user_id and project_name (same as upload)
        user_id = self.get_or_create_device_id()
        project_name = self.get_project_name() or "default"
        user_id_safe = user_id.lower().replace(" ", "_")
        project_safe = project_name.lower().replace(" ", "_")
        namespace = f"{user_id_safe}-{project_safe}"
        
        self.status_label.setText(f"Searching for: {query}")
        self.btn_search.setEnabled(False)
        
        # Show loading state
        self._show_loading_state()
        
        try:
            # Perform search using same approach as Streamlit
            params = {"query": query, "namespace": namespace}
            response = requests.get(Config.SEARCH_API_URL, params=params, timeout=30)
            
            if response.status_code == 200:
                result = response.json()
                results = result.get("results", [])
                self._display_search_results(results, query)
                self.status_label.setText(f"Found {len(results)} results for: {query}")
            else:
                self._show_empty_state()
                QMessageBox.warning(self, "Search Error", f"Search failed: {response.status_code}\n{response.text}")
                self.status_label.setText("Search failed")
                
        except Exception as e:
            self._show_empty_state()
            QMessageBox.critical(self, "Error", f"Search failed: {str(e)}")
            self.status_label.setText("Search error")
        finally:
            self.btn_search.setEnabled(True)
            
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
                
        # Hide empty state label
        if hasattr(self, 'empty_state_label'):
            self.empty_state_label.hide()
                
        if not results:
            no_results = QLabel("No results found for your query.")
            no_results.setObjectName("emptyState")
            no_results.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.results_layout.addWidget(no_results)
            return
        
        # Create grid layout for results
        grid_container = QWidget()
        grid_layout = QGridLayout()
        grid_layout.setSpacing(20)
        grid_layout.setContentsMargins(20, 20, 20, 20)
        
        # Display results in 3-column grid with fading opacity per row
        # Row 0: 100%, Row 1: 70%, Row 2: 30%
        num_columns = 3
        row_opacities = [1.0, 0.7, 0.3]
        
        for i, result in enumerate(results[:9]):  # Limit to 9 for 3x3 grid
            row = i // num_columns
            col = i % num_columns
            opacity = row_opacities[row] if row < len(row_opacities) else 0.3
            result_widget = self._create_result_card(result, i, opacity)
            grid_layout.addWidget(result_widget, row, col)
        
        grid_container.setLayout(grid_layout)
        self.results_layout.addWidget(grid_container)
        
        # Add stretch to push results to top
        self.results_layout.addStretch()
    
    def _clear_layout(self, layout):
        """Recursively clear a layout."""
        if layout is None:
            return
        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                item.widget().setParent(None)
            elif item.layout():
                self._clear_layout(item.layout())
        
    def _create_result_card(self, result: Dict, index: int, opacity: float = 1.0) -> QWidget:
        """Create a card widget for a single search result matching Figma design.
        
        Args:
            result: Search result dictionary
            index: Index of the result
            opacity: Opacity value (0.0 to 1.0) for fading effect
        """
        t = Theme.current
        
        card = QFrame()
        card.setObjectName("resultCard")
        card.setFixedSize(280, 220)
        
        # Apply opacity effect using QGraphicsOpacityEffect
        from PyQt6.QtWidgets import QGraphicsOpacityEffect
        opacity_effect = QGraphicsOpacityEffect()
        opacity_effect.setOpacity(opacity)
        card.setGraphicsEffect(opacity_effect)
        
        layout = QVBoxLayout()
        layout.setSpacing(0)
        layout.setContentsMargins(0, 0, 0, 0)
        
        metadata = result.get('metadata', {})
        filename = metadata.get('file_filename', 'Unknown')
        start_time = metadata.get('start_time_s', 0)
        end_time = metadata.get('end_time_s', 0)
        
        # Thumbnail placeholder - rectangular
        thumbnail = QLabel()
        thumbnail.setObjectName("thumbnail")
        thumbnail.setFixedHeight(150)
        thumbnail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        # Show filename and time range on thumbnail
        display_name = f"{filename[:20]}..." if len(filename) > 20 else filename
        thumbnail.setText(f"{display_name}\n{start_time:.1f}s - {end_time:.1f}s")
        thumbnail.setStyleSheet("""
            background-color: #D9D9D9;
            color: #666666;
            font-size: 11px;
            border-radius: 0px;
        """)
        layout.addWidget(thumbnail)
        
        # Button container - rectangular
        btn_container = QWidget()
        btn_container.setFixedHeight(50)
        btn_container.setStyleSheet(f"background-color: {t['card_bg']}; border-radius: 0px;")
        btn_layout = QHBoxLayout()
        btn_layout.setContentsMargins(10, 5, 10, 10)
        
        # Add to timeline button - light orange
        btn_add = QPushButton("Add to timeline")
        btn_add.setObjectName("addToTimelineBtn")
        btn_add.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_add.clicked.connect(lambda checked, r=result: self._add_result_to_timeline(r))
        btn_layout.addWidget(btn_add)
        
        btn_container.setLayout(btn_layout)
        layout.addWidget(btn_container)
        
        card.setLayout(layout)
        return card
    
    # Keep old method for backward compatibility
    def _create_result_widget(self, result: Dict, index: int, opacity: float = 1.0) -> QWidget:
        """Create a widget for a single search result (legacy)."""
        return self._create_result_card(result, index, opacity)
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
                    matching_clip_info = clip_info
                    if isinstance(clip_info, list):
                        matching_clip = clip_info[0]['media_pool_item']
                    else:
                        matching_clip = clip_info['media_pool_item']
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
            # Ensure current folder is the root (some Resolve versions require it)
            try:
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

            fps = 24.0
            if isinstance(matching_clip_info, dict):
                fps = matching_clip_info.get("fps") or 24.0
            elif isinstance(matching_clip_info, list) and matching_clip_info:
                fps = matching_clip_info[0].get("fps") or 24.0

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
                collected.extend(folder.GetClipList() or [])
            except Exception:
                pass
            for sub in _get_subfolders(folder):
                collected.extend(_collect_clips(sub))
            return collected

        root_folder = media_pool.GetRootFolder()
        clips = _collect_clips(root_folder)
        if debug:
            print(f"[MediaPool] Total clips found (pre-filter): {len(clips)}")

        # Apply same filter as the append action
        filtered = [c for c in clips if c and c.GetClipProperty("Type") and "Video" in c.GetClipProperty("Type")]
        if debug:
            print(f"[MediaPool] Video clips after filter: {len(filtered)}")

        mapping = {}
        for clip in filtered:
            # Prefer File Path -> filename, fall back to clip name
            try:
                file_path = clip.GetClipProperty("File Path") or clip.GetClipProperty("FilePath")
            except Exception:
                file_path = None

            if file_path:
                filename = os.path.basename(file_path)
            else:
                # Some MediaPoolItem objects provide a Name
                try:
                    filename = clip.GetName() or "<unnamed>"
                except Exception:
                    filename = "<unnamed>"

            fps = self._extract_clip_fps(clip)

            entry = {"media_pool_item": clip, "fps": fps, "filepath": file_path}

            # Handle duplicate filenames by collecting into a list
            if filename in mapping:
                if isinstance(mapping[filename], list):
                    mapping[filename].append(entry)
                else:
                    mapping[filename] = [mapping[filename], entry]
            else:
                mapping[filename] = entry

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

    def _get_auth_token_path(self) -> Path:
        """Return a platform-appropriate path to persist the auth token."""
        system = platform.system()
        if system == "Windows":
            base = os.getenv("APPDATA") or str(Path.home())
            return Path(base) / "ClipABit" / "auth_token.txt"
        elif system == "Darwin":
            return Path.home() / "Library" / "Application Support" / "ClipABit" / "auth_token.txt"
        else:
            xdg = os.getenv("XDG_CONFIG_HOME")
            base = xdg if xdg else str(Path.home() / ".config")
            return Path(base) / "clipabit" / "auth_token.txt"

    def _load_auth_token(self):
        """Load stored auth token if it exists."""
        path = self._get_auth_token_path()
        try:
            if path.exists():
                text = path.read_text(encoding="utf-8").strip()
                if text:
                    self.auth_token = text
                    self.is_authenticated = True
                    print("[Auth] Loaded stored auth token")
                    return
        except Exception as e:
            print(f"[Auth] Failed to load auth token: {e}")
        
        self.auth_token = None
        self.is_authenticated = False

    def _save_auth_token(self, token: str):
        """Save auth token to persistent storage."""
        path = self._get_auth_token_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(token, encoding="utf-8")
            self.auth_token = token
            self.is_authenticated = True
            print("[Auth] Auth token saved successfully")
        except Exception as e:
            print(f"[Auth] Failed to save auth token: {e}")

    def _clear_auth_token(self):
        """Clear stored auth token (logout)."""
        path = self._get_auth_token_path()
        try:
            if path.exists():
                path.unlink()
        except Exception as e:
            print(f"[Auth] Failed to clear auth token: {e}")
        
        self.auth_token = None
        self.is_authenticated = False

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
    
    def _show_processed_files(self):
        """Show processed files in a dialog window."""
        if not self.processed_files:
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
            header = QLabel(f"<b>Processed Files ({len(self.processed_files)} total)</b>")
            layout.addWidget(header)
            
            # Scrollable list
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll_widget = QWidget()
            scroll_layout = QVBoxLayout()
            
            # Display each processed file
            for file_hash, file_info in sorted(self.processed_files.items(), 
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
                    import datetime
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
        """Clear all processed files tracking."""
        if not self.processed_files:
            QMessageBox.information(self, "Clear Processed Files", "No processed files to clear.")
            return

        reply = QMessageBox.question(
            self,
            "Clear Processed Files",
            f"This will clear tracking for {len(self.processed_files)} processed files.\n\n"
            "Do you also want to delete their vectors from Pinecone?\n\n"
            "Yes = delete Pinecone + local\nNo = delete Pinecone only (keep local)\nCancel = do nothing",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No | QMessageBox.StandardButton.Cancel
        )
        
        if reply == QMessageBox.StandardButton.Cancel:
            return

        if reply == QMessageBox.StandardButton.Yes:
            # Delete from backend before clearing local
            for _, info in list(self.processed_files.items()):
                filename = info.get("filename", "")
                filepath = info.get("filepath", "")
                namespace = info.get("namespace")
                if not namespace:
                    user_id = self.get_or_create_device_id()
                    project_name = self.get_project_name() or "default"
                    user_id_safe = user_id.lower().replace(" ", "_")
                    project_safe = project_name.lower().replace(" ", "_")
                    namespace = f"{user_id_safe}-{project_safe}"

                hashed_identifier = info.get("hashed_identifier") or self._get_hashed_identifier(filepath, namespace, filename)
                if filename and hashed_identifier:
                    self._delete_backend_entry(filename, hashed_identifier, namespace)

            self.processed_files.clear()
            self._save_processed_files()
            self._update_file_status()
            QMessageBox.information(self, "Cleared", "Processed files tracking has been cleared.")
            print("Processed files tracking cleared")
        elif reply == QMessageBox.StandardButton.No:
            # Delete from backend only, keep local so verification can prune
            for _, info in list(self.processed_files.items()):
                filename = info.get("filename", "")
                filepath = info.get("filepath", "")
                namespace = info.get("namespace")
                if not namespace:
                    user_id = self.get_or_create_device_id()
                    project_name = self.get_project_name() or "default"
                    user_id_safe = user_id.lower().replace(" ", "_")
                    project_safe = project_name.lower().replace(" ", "_")
                    namespace = f"{user_id_safe}-{project_safe}"

                hashed_identifier = info.get("hashed_identifier") or self._get_hashed_identifier(filepath, namespace, filename)
                if filename and hashed_identifier:
                    self._delete_backend_entry(filename, hashed_identifier, namespace)

            QMessageBox.information(
                self,
                "Deleted from Pinecone",
                "Pinecone data deleted. Local tracking kept for verification."
            )
            print("Pinecone data deleted; local tracking kept")
            
    def _verify_backend_records(self):
        """Manually verify backend vector counts for processed files."""
        QMessageBox.information(
            self,
            "Verify Backend",
            "Backend verification is disabled. Using local storage only."
        )

    def _run_consistency_check(self, reason: str):
        """Sync local tracking with backend and remove dangling entries."""
        if not self.processed_files:
            return {"checked": 0, "removed": 0, "updated": 0}

        removed_count = 0
        checked_count = 0
        updated_count = 0
        now = time.time()

        for file_hash, info in list(self.processed_files.items()):
            filename = info.get("filename", "")
            filepath = info.get("filepath", "")
            namespace = info.get("namespace")
            expected_count = info.get("expected_vector_count")

            if not namespace:
                user_id = self.get_or_create_device_id()
                project_name = self.get_project_name() or "default"
                user_id_safe = user_id.lower().replace(" ", "_")
                project_safe = project_name.lower().replace(" ", "_")
                namespace = f"{user_id_safe}-{project_safe}"

            hashed_identifier = info.get("hashed_identifier") or self._get_hashed_identifier(filepath, namespace, filename)

            checked_count += 1

            if filepath and not os.path.exists(filepath):
                print(f"[Consistency] Missing local file: {filename}. Removing local record.")
                del self.processed_files[file_hash]
                removed_count += 1
                continue

            if filename:
                info['last_backend_check'] = now
                updated_count += 1

        if removed_count > 0 or updated_count > 0:
            self._save_processed_files()
            self._update_file_status()

        if checked_count > 0:
            print(f"[Consistency] {reason}: checked {checked_count}, removed {removed_count}, updated {updated_count}")
        return {"checked": checked_count, "removed": removed_count, "updated": updated_count}

    def closeEvent(self, event):
        """Handle window close event."""
        if hasattr(self, 'job_tracker'):
            self.job_tracker.stop()
            self.job_tracker.wait()
        if hasattr(self, 'refresh_timer'):
            self.refresh_timer.stop()
        if hasattr(self, 'auth_polling_timer') and self.auth_polling_timer:
            self.auth_polling_timer.stop()
        event.accept()

def main(resolve_api=None):
    """Main entry point.
    
    Args:
        resolve_api: Optional Resolve object injected from the shim.
    """
    global resolve, project, media_pool, project_manager
    
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
    
    window = ClipABitApp()
    window.show()
    window.raise_()
    window.activateWindow()
    
    print("ClipABit Plugin started.") 
    app_qt.exec()
