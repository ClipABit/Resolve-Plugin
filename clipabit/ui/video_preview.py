import os
from typing import Dict

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QSlider, QWidget, QSizePolicy,
)
from PyQt6.QtCore import Qt, QUrl, QTimer, QObject, pyqtSignal
from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput
from PyQt6.QtMultimediaWidgets import QVideoWidget

from .theme import Theme


def _format_time(seconds: float) -> str:
    """Format seconds as MM:SS.t"""
    minutes = int(seconds) // 60
    secs = seconds - (minutes * 60)
    return f"{minutes:02d}:{secs:05.2f}"


class RangeSlider(QWidget):
    """Custom dual-handle slider for selecting start/end trim points."""
    range_changed = pyqtSignal(float, float)  # start_frac, end_frac

    def __init__(self, parent=None):
        super().__init__(parent)
        self._start = 0.0  # 0-1 fraction
        self._end = 1.0
        self._dragging = None  # "start", "end", or None
        self._handle_width = 14
        self.setFixedHeight(32)
        self.setMinimumWidth(200)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    @property
    def start_frac(self):
        return self._start

    @property
    def end_frac(self):
        return self._end

    def set_range(self, start: float, end: float):
        self._start = max(0.0, min(1.0, start))
        self._end = max(0.0, min(1.0, end))
        if self._end < self._start:
            self._end = self._start
        self.update()
        self.range_changed.emit(self._start, self._end)

    def paintEvent(self, event):
        from PyQt6.QtGui import QPainter, QColor, QBrush
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        t = Theme.current
        w = self.width()
        h = self.height()
        track_y = h // 2 - 3
        track_h = 6

        # Background track
        p.setBrush(QBrush(QColor("#555555")))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(0, track_y, w, track_h, 3, 3)

        # Selected range
        x_start = int(self._start * w)
        x_end = int(self._end * w)
        p.setBrush(QBrush(QColor(t['accent'])))
        p.drawRect(x_start, track_y, x_end - x_start, track_h)

        # Handles
        for x in (x_start, x_end):
            hx = x - self._handle_width // 2
            p.setBrush(QBrush(QColor("#FFFFFF")))
            p.drawRoundedRect(hx, 2, self._handle_width, h - 4, 4, 4)

        p.end()

    def _pos_to_frac(self, x):
        return max(0.0, min(1.0, x / max(self.width(), 1)))

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        frac = self._pos_to_frac(event.position().x())
        dist_start = abs(frac - self._start)
        dist_end = abs(frac - self._end)
        self._dragging = "start" if dist_start <= dist_end else "end"
        self._update_drag(frac)

    def mouseMoveEvent(self, event):
        if self._dragging:
            frac = self._pos_to_frac(event.position().x())
            self._update_drag(frac)

    def mouseReleaseEvent(self, event):
        self._dragging = None

    def _update_drag(self, frac):
        if self._dragging == "start":
            self._start = min(frac, self._end - 0.001)
            self._start = max(0.0, self._start)
        elif self._dragging == "end":
            self._end = max(frac, self._start + 0.001)
            self._end = min(1.0, self._end)
        self.update()
        self.range_changed.emit(self._start, self._end)


class VideoPreviewDialog(QDialog):
    """Modal dialog for previewing and trimming a video clip before timeline insertion."""
    insert_requested = pyqtSignal(dict)  # emits modified result dict with adjusted times

    def __init__(self, result: Dict, parent=None):
        super().__init__(parent)
        self.result = result
        self.metadata = result.get('metadata', {})
        self.file_path = self.metadata.get('file_path', '')
        self.filename = self.metadata.get('file_filename', 'Unknown')
        self.chunk_start = float(self.metadata.get('start_time_s', 0))
        self.chunk_end = float(self.metadata.get('end_time_s', 0))

        self.video_start = 0.0
        self.video_duration = 0.0

        self.trim_start = self.chunk_start
        self.trim_end = self.chunk_end

        self._duration_ms = 0
        self._seeking = False
        self._closed = False
        self._user_playing = False

        self.setWindowTitle(f"Preview - {self.filename}")
        self.setMinimumSize(720, 520)
        self.resize(800, 580)
        self.setModal(True)

        print(f"[Preview] Opening: {self.filename}")
        print(f"[Preview] file_path: {self.file_path}")
        print(f"[Preview] file exists: {os.path.exists(self.file_path) if self.file_path else 'N/A'}")
        print(f"[Preview] chunk: {self.chunk_start:.2f}s - {self.chunk_end:.2f}s")

        self._build_ui()
        self._setup_player()

    def _build_ui(self):
        t = Theme.current
        self.setStyleSheet(f"""
            QDialog {{
                background-color: {t['background']};
            }}
            QLabel {{
                color: {t['text']};
                background: transparent;
            }}
            QPushButton {{
                background-color: {t['accent']};
                color: {t['background']};
                border: none;
                border-radius: 6px;
                padding: 8px 20px;
                font-weight: bold;
                font-size: 13px;
            }}
            QPushButton:hover {{
                background-color: {t['accent_hover']};
            }}
            QPushButton#playBtn {{
                background-color: {t['card_bg']};
                color: {t['text']};
                padding: 6px 14px;
                font-size: 18px;
                min-width: 40px;
            }}
            QPushButton#playBtn:hover {{
                background-color: {t['border']};
            }}
        """)

        layout = QVBoxLayout()
        layout.setContentsMargins(16, 12, 16, 16)
        layout.setSpacing(10)

        # Header
        header = QHBoxLayout()
        title = QLabel(self.filename)
        title.setTextFormat(Qt.TextFormat.PlainText)
        title.setStyleSheet(f"font-size: 15px; font-weight: bold; color: {t['text']};")
        header.addWidget(title)
        header.addStretch()
        btn_close = QPushButton("X")
        btn_close.setFixedSize(30, 30)
        btn_close.setStyleSheet(f"""
            QPushButton {{
                background-color: transparent;
                color: {t['text']};
                font-size: 14px;
                font-weight: bold;
                border-radius: 15px;
            }}
            QPushButton:hover {{ background-color: {t['card_bg']}; }}
        """)
        btn_close.clicked.connect(self.reject)
        header.addWidget(btn_close)
        layout.addLayout(header)

        # Video player
        self.video_widget = QVideoWidget()
        self.video_widget.setMinimumHeight(300)
        self.video_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.video_widget.setStyleSheet("background-color: #000000; border-radius: 6px;")
        layout.addWidget(self.video_widget, 1)

        # Playback scrubber
        self.scrubber = QSlider(Qt.Orientation.Horizontal)
        self.scrubber.setRange(0, 1000)
        self.scrubber.setStyleSheet(f"""
            QSlider::groove:horizontal {{
                height: 6px;
                background: #555555;
                border-radius: 3px;
            }}
            QSlider::handle:horizontal {{
                background: {t['text']};
                width: 14px;
                height: 14px;
                margin: -4px 0;
                border-radius: 7px;
            }}
            QSlider::sub-page:horizontal {{
                background: {t['accent']};
                border-radius: 3px;
            }}
        """)
        self.scrubber.sliderPressed.connect(self._on_scrub_pressed)
        self.scrubber.sliderReleased.connect(self._on_scrub_released)
        self.scrubber.sliderMoved.connect(self._on_scrub_moved)
        layout.addWidget(self.scrubber)

        # Play controls + time display
        controls = QHBoxLayout()
        controls.setSpacing(10)

        self.btn_play = QPushButton("\u25B6")
        self.btn_play.setObjectName("playBtn")
        self.btn_play.setFixedSize(44, 36)
        self.btn_play.clicked.connect(self._toggle_play)
        controls.addWidget(self.btn_play)

        self.time_label = QLabel("00:00.00 / 00:00.00")
        self.time_label.setStyleSheet(f"color: {t['text_secondary']}; font-size: 12px; font-family: monospace;")
        controls.addWidget(self.time_label)
        controls.addStretch()
        layout.addLayout(controls)

        # Trim range section
        trim_header = QHBoxLayout()
        trim_title = QLabel("Trim Range")
        trim_title.setStyleSheet(f"color: {t['text']}; font-size: 13px; font-weight: bold;")
        trim_header.addWidget(trim_title)
        trim_header.addStretch()
        self.trim_label = QLabel("")
        self.trim_label.setStyleSheet(f"color: {t['text_secondary']}; font-size: 12px; font-family: monospace;")
        trim_header.addWidget(self.trim_label)
        layout.addLayout(trim_header)

        self.range_slider = RangeSlider()
        self.range_slider.range_changed.connect(self._on_range_changed)
        layout.addWidget(self.range_slider)

        # Chunk indicator label
        self.chunk_label = QLabel(
            f"Original chunk: {_format_time(self.chunk_start)} - {_format_time(self.chunk_end)}"
        )
        self.chunk_label.setStyleSheet(f"color: {t['text_secondary']}; font-size: 11px;")
        layout.addWidget(self.chunk_label)

        # Insert button
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self.btn_insert = QPushButton("Insert to Timeline")
        self.btn_insert.setFixedHeight(40)
        self.btn_insert.setFixedWidth(200)
        self.btn_insert.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_insert.clicked.connect(self._on_insert)
        btn_row.addWidget(self.btn_insert)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self.setLayout(layout)

    def _setup_player(self):
        self.audio_output = QAudioOutput()
        self.player = QMediaPlayer()
        self.player.setAudioOutput(self.audio_output)
        self.player.setVideoOutput(self.video_widget)

        self.player.mediaStatusChanged.connect(self._on_media_status_changed)
        self.player.durationChanged.connect(self._on_duration_changed)
        self.player.positionChanged.connect(self._on_position_changed)
        self.player.playbackStateChanged.connect(self._on_playback_state_changed)
        self.player.errorOccurred.connect(self._on_player_error)

        # Position update timer
        self._update_timer = QTimer()
        self._update_timer.setInterval(50)
        self._update_timer.timeout.connect(self._update_scrubber)

        self._pending_first_frame = False

        # Load the video file
        if self.file_path and os.path.exists(self.file_path):
            print(f"[Preview] Loading source: {self.file_path}")
            self.player.setSource(QUrl.fromLocalFile(self.file_path))
        else:
            print(f"[Preview] File not found: {self.file_path}")
            self.time_label.setText("File not found")
            self.btn_play.setEnabled(False)
            self.btn_insert.setEnabled(False)

    def _on_media_status_changed(self, status):
        status_name = status.name if hasattr(status, 'name') else str(status)
        print(f"[Preview] Media status: {status_name}")

        # Once media is loaded, seek to chunk start and show first frame
        if status in (QMediaPlayer.MediaStatus.BufferedMedia,
                      QMediaPlayer.MediaStatus.LoadedMedia):
            if not self._pending_first_frame and self._duration_ms > 0:
                self._pending_first_frame = True
                seek_ms = int(self.chunk_start * 1000)
                print(f"[Preview] Seeking to chunk start: {seek_ms}ms")
                self.player.setPosition(seek_ms)
                # Play briefly then pause to render the frame
                self.player.play()
                QTimer.singleShot(100, self._pause_for_first_frame)

        # End of media — just stop and update UI, don't try to seek (causes loop)
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            print("[Preview] End of media reached")
            self._user_playing = False
            self.btn_play.setText("\u25B6")
            self._update_timer.stop()

    def _pause_for_first_frame(self):
        if not self._closed:
            print(f"[Preview] Pausing for first frame at pos={self.player.position()}ms")
            self.player.pause()

    def _on_duration_changed(self, duration_ms: int):
        print(f"[Preview] Duration changed: {duration_ms}ms")
        self._duration_ms = duration_ms
        self.video_duration = duration_ms / 1000.0

        # Clamp chunk times to actual video duration
        self.chunk_start = min(self.chunk_start, self.video_duration)
        self.chunk_end = min(self.chunk_end, self.video_duration)
        self.trim_start = self.chunk_start
        self.trim_end = self.chunk_end

        # Set range slider to reflect chunk within full video
        if self.video_duration > 0:
            self.range_slider.set_range(
                self.chunk_start / self.video_duration,
                self.chunk_end / self.video_duration,
            )

        self._update_trim_label()

    def _on_position_changed(self, position_ms: int):
        if not self._seeking:
            self._update_scrubber_from_pos(position_ms)

    def _on_playback_state_changed(self, state):
        state_name = state.name if hasattr(state, 'name') else str(state)
        print(f"[Preview] Playback state: {state_name}")

    def _update_scrubber_from_pos(self, position_ms: int):
        if self._duration_ms > 0:
            value = int((position_ms / self._duration_ms) * 1000)
            self.scrubber.blockSignals(True)
            self.scrubber.setValue(value)
            self.scrubber.blockSignals(False)
        current_s = position_ms / 1000.0
        total_s = self._duration_ms / 1000.0
        self.time_label.setText(f"{_format_time(current_s)} / {_format_time(total_s)}")

    def _update_scrubber(self):
        pos = self.player.position()
        self._update_scrubber_from_pos(pos)

    def _seek_and_show(self, pos_ms: int):
        """Seek to position and force a frame render.
        QMediaPlayer won't decode a frame while paused, so we
        briefly play then pause to make the frame visible."""
        self._user_playing = False  # Mark that user is NOT playing
        self.player.setPosition(pos_ms)
        self.player.play()
        QTimer.singleShot(50, self._pause_after_seek)

    def _pause_after_seek(self):
        """Always pause after a seek-to-show, unless user explicitly hit play."""
        if not self._user_playing and not self._closed:
            self.player.pause()

    def _on_scrub_pressed(self):
        self._seeking = True
        # Pause playback while scrubbing
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
            self.btn_play.setText("\u25B6")
            self._update_timer.stop()

    def _on_scrub_released(self):
        self._seeking = False
        value = self.scrubber.value()
        if self._duration_ms > 0:
            pos_ms = int((value / 1000.0) * self._duration_ms)
            self._seek_and_show(pos_ms)

    def _on_scrub_moved(self, value: int):
        if self._duration_ms > 0:
            pos_ms = int((value / 1000.0) * self._duration_ms)
            self._seek_and_show(pos_ms)

    def _toggle_play(self):
        state = self.player.playbackState()
        media_status = self.player.mediaStatus()

        if state == QMediaPlayer.PlaybackState.PlayingState:
            self._user_playing = False
            self.player.pause()
            self.btn_play.setText("\u25B6")
            self._update_timer.stop()
        else:
            self._user_playing = True
            # If at end of media, restart from chunk start
            if media_status == QMediaPlayer.MediaStatus.EndOfMedia:
                self.player.setPosition(int(self.chunk_start * 1000))
            self.player.play()
            self.btn_play.setText("\u23F8")
            self._update_timer.start()

    def _on_range_changed(self, start_frac: float, end_frac: float):
        if self.video_duration > 0:
            self.trim_start = start_frac * self.video_duration
            self.trim_end = end_frac * self.video_duration
        self._update_trim_label()

    def _update_trim_label(self):
        duration = self.trim_end - self.trim_start
        self.trim_label.setText(
            f"{_format_time(self.trim_start)} - {_format_time(self.trim_end)}  "
            f"({_format_time(duration)})"
        )

    def _on_insert(self):
        modified = dict(self.result)
        modified['metadata'] = dict(self.metadata)
        modified['metadata']['start_time_s'] = self.trim_start
        modified['metadata']['end_time_s'] = self.trim_end
        self.insert_requested.emit(modified)
        self._cleanup()
        self.accept()

    def _on_player_error(self, error, message=""):
        print(f"[Preview] Player error: {error} - {message}")

    def _cleanup(self):
        """Fully release player resources so the next dialog can create fresh ones."""
        print("[Preview] Cleanup: releasing player resources")
        self._closed = True
        self._update_timer.stop()
        try:
            self.player.stop()
            self.player.setSource(QUrl())  # Clear source
            self.player.setVideoOutput(None)
            self.player.setAudioOutput(None)
        except Exception as e:
            print(f"[Preview] Cleanup error: {e}")

    def reject(self):
        self._cleanup()
        super().reject()

    def closeEvent(self, event):
        self._cleanup()
        super().closeEvent(event)


class ThumbnailExtractor(QObject):
    """Non-blocking thumbnail extractor — emits `ready` with a QPixmap or None."""

    ready = pyqtSignal(object)  # QPixmap or None

    def __init__(self, file_path: str, time_s: float, size=(280, 140), parent=None):
        super().__init__(parent)
        from PyQt6.QtGui import QPixmap
        from PyQt6.QtMultimedia import QMediaPlayer, QVideoSink

        self._size = size
        self._seeked = False
        self._done = False
        self._QPixmap = QPixmap

        self._player = QMediaPlayer(self)
        self._sink = QVideoSink(self)
        self._player.setVideoOutput(self._sink)

        self._sink.videoFrameChanged.connect(self._on_frame)
        self._player.mediaStatusChanged.connect(self._on_media_status)
        self._player.errorOccurred.connect(self._on_error)

        self._target_ms = int(time_s * 1000)

        self._timeout = QTimer(self)
        self._timeout.setSingleShot(True)
        self._timeout.timeout.connect(lambda: self._finish(None))
        self._timeout.start(5000)

        print(f"[Thumbnail] Extracting from {os.path.basename(file_path)} at {time_s:.2f}s")
        self._player.setSource(QUrl.fromLocalFile(file_path))

    def _on_media_status(self, status):
        from PyQt6.QtMultimedia import QMediaPlayer
        if self._done:
            return
        if status in (QMediaPlayer.MediaStatus.BufferedMedia,
                      QMediaPlayer.MediaStatus.LoadedMedia):
            print(f"[Thumbnail] Seeking to {self._target_ms}ms")
            self._player.setPosition(self._target_ms)
            self._seeked = True
            self._player.play()

    def _on_frame(self, frame):
        from PyQt6.QtCore import QSize
        from PyQt6.QtGui import QImage
        if not self._seeked or self._done:
            return
        if frame.isValid():
            image = frame.toImage()
            if not image.isNull():
                w, h = self._size
                scaled = image.scaled(
                    QSize(w, h),
                    Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                    Qt.TransformationMode.SmoothTransformation,
                )
                x = (scaled.width() - w) // 2
                y = (scaled.height() - h) // 2
                cropped = scaled.copy(x, y, w, h).convertToFormat(QImage.Format.Format_RGB32)
                pixmap = self._QPixmap.fromImage(cropped)
                print(f"[Thumbnail] Captured frame at {self._player.position()}ms")
                self._finish(pixmap)

    def _on_error(self, error, msg=""):
        print(f"[Thumbnail] Error: {error} - {msg}")
        self._finish(None)

    def _finish(self, pixmap):
        if self._done:
            return
        self._done = True
        self._timeout.stop()
        print(f"[Thumbnail] Result: {'OK' if pixmap else 'FAILED'}")
        self.ready.emit(pixmap)
        try:
            self._player.stop()
            self._player.setSource(QUrl())
            self._player.setVideoOutput(None)
        except Exception as e:
            print(f"[Thumbnail] Cleanup error: {e}")
        self.deleteLater()


def extract_thumbnail(file_path: str, time_s: float, size=(280, 140), on_ready=None, parent=None):
    """Extract a thumbnail from a video file at the given timestamp.

    If on_ready is provided, extraction is non-blocking: on_ready(QPixmap_or_None)
    is called when done, and the ThumbnailExtractor is returned.

    If on_ready is None, falls back to blocking extraction (legacy)."""
    if not file_path or not os.path.exists(file_path):
        if on_ready:
            on_ready(None)
            return None
        return None

    if on_ready is not None:
        extractor = ThumbnailExtractor(file_path, time_s, size, parent=parent)
        extractor.ready.connect(on_ready)
        return extractor

    # Blocking fallback (used outside of event loop contexts)
    from PyQt6.QtCore import QEventLoop
    result = [None]
    loop = QEventLoop()

    def _on_ready(pixmap):
        result[0] = pixmap
        loop.quit()

    extractor = ThumbnailExtractor(file_path, time_s, size, parent=None)
    extractor.ready.connect(_on_ready)
    loop.exec()
    return result[0]
