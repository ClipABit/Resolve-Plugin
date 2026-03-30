"""Non-blocking HTTP client built on Qt's QNetworkAccessManager.

All I/O runs on the Qt event loop — no threads, no subprocesses.
Works identically in standalone Python and inside DaVinci Resolve's fuscript.exe.
"""

import json
import os
import traceback
from typing import Callable
from urllib.parse import urlencode

from PyQt6.QtCore import QObject, pyqtSignal, QUrl, QFile, QIODevice
from PyQt6.QtNetwork import (
    QNetworkAccessManager,
    QNetworkRequest,
    QNetworkReply,
    QHttpMultiPart,
    QHttpPart,
)


class NetworkClient(QObject):
    """Async HTTP client with automatic auth header injection and 401 retry.

    Uses QNetworkAccessManager internally — every request is non-blocking
    and integrates with the Qt event loop via signals.
    """

    reauth_required = pyqtSignal()

    def __init__(self, auth_manager=None, parent=None):
        super().__init__(parent)
        self._nam = QNetworkAccessManager(self)
        self._auth_manager = auth_manager
        print("[Network] NetworkClient initialized (QNetworkAccessManager)")

    # ── helpers ──────────────────────────────────────────────────────

    def _build_url(self, base: str, params: dict | None = None) -> QUrl:
        if params:
            return QUrl(f"{base}?{urlencode(params)}")
        return QUrl(base)

    def _apply_auth(self, request: QNetworkRequest):
        if self._auth_manager:
            token = self._auth_manager.get_valid_access_token()
            if token:
                request.setRawHeader(b"Authorization", f"Bearer {token}".encode())
                print("[Network] Auth header applied")
            else:
                print("[Network] No valid access token available")

    def _wire_reply(self, reply: QNetworkReply, on_success, on_error,
                    retry_fn=None, retried=False, method="", url=""):
        """Connect the reply's finished signal with 401-retry logic."""

        def _finished():
            status = reply.attribute(
                QNetworkRequest.Attribute.HttpStatusCodeAttribute
            )
            net_err = reply.error()

            if net_err == QNetworkReply.NetworkError.NoError:
                raw = reply.readAll().data()
                print(f"[Network] {method} {url} -> {status} ({len(raw)} bytes)")
                try:
                    data = json.loads(raw)
                except (json.JSONDecodeError, ValueError):
                    data = raw
                try:
                    if on_success:
                        on_success(status or 200, data)
                except Exception as e:
                    print(f"[Network] Error in success callback: {e}")
                    traceback.print_exc()
                reply.deleteLater()

            elif status == 401 and not retried and self._auth_manager and retry_fn:
                print(f"[Network] {method} {url} -> 401 — refreshing token and retrying")
                refreshed = self._auth_manager.refresh_tokens()
                if refreshed:
                    reply.deleteLater()
                    retry_fn()
                    return
                print("[Network] Refresh failed — prompting re-auth")
                self._auth_manager.delete_tokens()
                self.reauth_required.emit()
                try:
                    if on_error:
                        on_error("Authentication failed (HTTP 401)")
                except Exception as e:
                    print(f"[Network] Error in error callback: {e}")
                    traceback.print_exc()
                reply.deleteLater()

            else:
                raw = reply.readAll().data()
                body = raw.decode("utf-8", errors="replace")
                # Extract user-facing message from JSON error responses
                try:
                    detail = json.loads(raw).get("detail")
                    if detail:
                        body = detail
                except (json.JSONDecodeError, ValueError, AttributeError):
                    pass
                if status:
                    msg = f"HTTP {status}: {body}"
                else:
                    msg = f"Network error: {reply.errorString()}"
                print(f"[Network] {method} {url} -> FAILED: {msg[:200]}")
                try:
                    if on_error:
                        on_error(msg)
                except Exception as e:
                    print(f"[Network] Error in error callback: {e}")
                    traceback.print_exc()
                reply.deleteLater()

        reply.finished.connect(_finished)

    # ── public API ───────────────────────────────────────────────────

    def get(self, url: str, params: dict = None, timeout: int = 30,
            on_success: Callable = None, on_error: Callable = None):
        """Non-blocking GET.  Calls on_success(status, data) or on_error(msg)."""
        short_url = url.split("?")[0].split("/")[-1] or url
        print(f"[Network] GET {short_url} (timeout={timeout}s)")

        def _do(retried=False):
            req = QNetworkRequest(self._build_url(url, params))
            if hasattr(req, "setTransferTimeout"):
                req.setTransferTimeout(timeout * 1000)
            self._apply_auth(req)
            reply = self._nam.get(req)
            self._wire_reply(
                reply, on_success, on_error,
                retry_fn=lambda: _do(True), retried=retried,
                method="GET", url=short_url,
            )

        _do()

    def get_github_release_version(self, owner: str, repo: str,
                                   on_success: Callable = None,
                                   on_error: Callable = None):
        """Fetch latest release version from GitHub API (no auth required)."""
        url = f"https://api.github.com/repos/{owner}/{repo}/releases/latest"
        print(f"[Network] Fetching latest release from {owner}/{repo}")

        req = QNetworkRequest(QUrl(url))
        if hasattr(req, "setTransferTimeout"):
            req.setTransferTimeout(30_000)
        reply = self._nam.get(req)
        self._wire_reply(
            reply, on_success, on_error,
            method="GET", url="releases/latest",
        )

    def download_file(self, url: str, dest_path: str, timeout: int = 120,
                      on_success: Callable = None,
                      on_error: Callable = None,
                      on_progress: Callable = None):
        """Non-blocking file download (no auth). on_success(dest_path), on_progress(received, total)."""
        print(f"[Network] Downloading {url} -> {dest_path}")

        req = QNetworkRequest(QUrl(url))
        req.setAttribute(
            QNetworkRequest.Attribute.RedirectPolicyAttribute,
            QNetworkRequest.RedirectPolicy.NoLessSafeRedirectPolicy,
        )
        if hasattr(req, "setTransferTimeout"):
            req.setTransferTimeout(timeout * 1000)
        reply = self._nam.get(req)

        if on_progress:
            reply.downloadProgress.connect(on_progress)

        def _finished():
            net_err = reply.error()
            status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
            if net_err == QNetworkReply.NetworkError.NoError:
                data = reply.readAll().data()
                print(f"[Network] Download complete: {len(data)} bytes -> {dest_path}")
                try:
                    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
                    with open(dest_path, "wb") as f:
                        f.write(data)
                    if on_success:
                        on_success(dest_path)
                except Exception as e:
                    print(f"[Network] Error writing file: {e}")
                    if on_error:
                        on_error(str(e))
            else:
                body = reply.readAll().data().decode("utf-8", errors="replace")
                msg = f"HTTP {status}: {body}" if status else f"Network error: {reply.errorString()}"
                print(f"[Network] Download failed: {msg[:200]}")
                if on_error:
                    on_error(msg)
            reply.deleteLater()

        reply.finished.connect(_finished)

    def post_multipart(self, url: str, fields: dict = None,
                       file_path: str = None, file_field: str = "files",
                       filename: str = None, timeout: int = 300,
                       on_success: Callable = None,
                       on_error: Callable = None,
                       on_progress: Callable = None):
        """Non-blocking multipart POST.  on_progress(bytes_sent, bytes_total)."""
        fn_display = filename or (os.path.basename(file_path) if file_path else "?")
        file_size_mb = (
            os.path.getsize(file_path) / (1024 * 1024)
            if file_path and os.path.exists(file_path) else 0
        )
        print(f"[Network] POST multipart {fn_display} ({file_size_mb:.1f} MB, timeout={timeout}s)")

        def _do(retried=False):
            mp = QHttpMultiPart(QHttpMultiPart.ContentType.FormDataType)

            # Form fields
            if fields:
                for k, v in fields.items():
                    p = QHttpPart()
                    p.setHeader(
                        QNetworkRequest.KnownHeaders.ContentDispositionHeader,
                        f'form-data; name="{k}"',
                    )
                    p.setBody(str(v).encode())
                    mp.append(p)

            # File part
            if file_path:
                fn = filename or os.path.basename(file_path)
                fp = QHttpPart()
                fp.setHeader(
                    QNetworkRequest.KnownHeaders.ContentDispositionHeader,
                    f'form-data; name="{file_field}"; filename="{fn}"',
                )
                fp.setHeader(
                    QNetworkRequest.KnownHeaders.ContentTypeHeader,
                    "video/mp4",
                )
                qf = QFile(file_path)
                if not qf.open(QIODevice.OpenModeFlag.ReadOnly):
                    print(f"[Network] Cannot open file for upload: {file_path}")
                    if on_error:
                        on_error(f"Cannot open file: {file_path}")
                    return
                print(f"[Network] File opened for streaming: {file_path}")
                fp.setBodyDevice(qf)
                qf.setParent(mp)  # prevent GC while upload is in-flight
                mp.append(fp)

            req = QNetworkRequest(QUrl(url))
            if hasattr(req, "setTransferTimeout"):
                req.setTransferTimeout(timeout * 1000)
            self._apply_auth(req)

            reply = self._nam.post(req, mp)
            mp.setParent(reply)  # prevent GC until reply finishes

            if on_progress:
                reply.uploadProgress.connect(on_progress)

            self._wire_reply(
                reply, on_success, on_error,
                retry_fn=lambda: _do(True), retried=retried,
                method="POST", url=fn_display,
            )

        _do()

    def delete(self, url: str, params: dict = None, timeout: int = 10,
               on_success: Callable = None, on_error: Callable = None):
        """Non-blocking DELETE."""
        short_url = url.split("?")[0].split("/")[-1] or url
        print(f"[Network] DELETE {short_url} (timeout={timeout}s)")

        def _do(retried=False):
            req = QNetworkRequest(self._build_url(url, params))
            if hasattr(req, "setTransferTimeout"):
                req.setTransferTimeout(timeout * 1000)
            self._apply_auth(req)
            reply = self._nam.deleteResource(req)
            self._wire_reply(
                reply, on_success, on_error,
                retry_fn=lambda: _do(True), retried=retried,
                method="DELETE", url=short_url,
            )

        _do()
