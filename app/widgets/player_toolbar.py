"""播放控制工具栏 - 播放/暂停、音量、进度、倍速、全屏"""

from PyQt6.QtCore import Qt, pyqtSignal, QEvent, QElapsedTimer, QPoint
from PyQt6.QtGui import QFont, QIcon, QAction, QPixmap
from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QPushButton, QSlider, QLabel,
    QVBoxLayout, QMenu, QFrame,
)

from app import theme
from app import icons
from app import i18n
from app import frame_provider


def _format_time(ms: int) -> str:
    seconds = ms // 1000
    m, s = divmod(seconds, 60)
    if m >= 60:
        h, m = divmod(m, 60)
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


SPEED_OPTIONS = [0.5, 0.75, 1.0, 1.25, 1.5, 2.0]


class SeekPreview(QWidget):
    """进度条悬停预览浮窗：上方目标画面，下方时间"""

    def __init__(self, parent=None):
        super().__init__(parent, Qt.WindowType.ToolTip)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(4)
        self._img = QLabel()
        self._img.setFixedSize(240, 135)
        self._img.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._img.setStyleSheet("background: #000; border-radius: 4px;")
        lay.addWidget(self._img, alignment=Qt.AlignmentFlag.AlignCenter)
        self._time = QLabel("00:00")
        self._time.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._time.setStyleSheet("color: #fff; font-size: 12px; font-weight: bold;")
        lay.addWidget(self._time)

    def set_content(self, pixmap: QPixmap | None, text: str):
        if pixmap is not None and not pixmap.isNull():
            self._img.setPixmap(pixmap.scaled(
                self._img.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            ))
        else:
            self._img.clear()
            self._img.setText("…")
            self._img.setStyleSheet("background: #000; color: #888; border-radius: 4px;")
        self._time.setText(text)
        self.adjustSize()


class PlayerToolbar(QWidget):
    """播放控制工具栏"""

    play_toggled = pyqtSignal()
    previous_clicked = pyqtSignal()
    next_clicked = pyqtSignal()
    seek_requested = pyqtSignal(int)
    volume_changed = pyqtSignal(int)
    fullscreen_toggled = pyqtSignal()
    speed_changed = pyqtSignal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._duration = 0
        self._is_slider_dragging = False
        self._is_fullscreen = False
        self._current_rate = 1.0
        self._is_playing = False
        self._is_boost = False
        self._prev_btn = None
        self._play_btn = None
        self._next_btn = None
        self._vol_btn = None
        self._fullscreen_btn = None
        self._speed_btn = None
        self._vol_slider = None
        self._progress_slider = None
        self._time_label = None
        self._boost_hint = None
        # 悬停预览
        self._video_path: str = ""
        self._preview = SeekPreview(self)
        self._preview_timer = QElapsedTimer()
        self._preview_timer.start()
        self._setup_ui()
        self._apply_theme_style()

    def _setup_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(12, 6, 12, 10)
        main_layout.setSpacing(6)

        # 进度条
        self._progress_slider = QSlider(Qt.Orientation.Horizontal)
        self._progress_slider.setRange(0, 0)
        self._progress_slider.setMouseTracking(True)
        self._progress_slider.installEventFilter(self)
        self._progress_slider.sliderPressed.connect(self._on_slider_pressed)
        self._progress_slider.sliderReleased.connect(self._on_slider_released)
        self._progress_slider.sliderMoved.connect(self._on_slider_moved)

        time_layout = QHBoxLayout()
        self._time_label = QLabel("00:00 / 00:00")
        self._time_label.setFont(QFont("Consolas", 10))
        time_layout.addWidget(self._time_label)
        time_layout.addStretch()
        # 加速提示
        self._boost_hint = QLabel("»2x")
        self._boost_hint.setStyleSheet(
            "color: #03DAC6; font-size: 12px; font-weight: bold;"
        )
        self._boost_hint.hide()
        time_layout.addWidget(self._boost_hint)

        progress_layout = QVBoxLayout()
        progress_layout.setSpacing(2)
        progress_layout.addLayout(time_layout)
        progress_layout.addWidget(self._progress_slider)
        main_layout.addLayout(progress_layout)

        # 控制按钮
        controls_layout = QHBoxLayout()
        controls_layout.setSpacing(8)

        self._prev_btn = QPushButton()
        self._prev_btn.setFixedSize(38, 38)
        self._prev_btn.clicked.connect(self.previous_clicked.emit)
        controls_layout.addWidget(self._prev_btn)

        self._play_btn = QPushButton()
        self._play_btn.setFixedSize(48, 48)
        self._play_btn.clicked.connect(self.play_toggled.emit)
        controls_layout.addWidget(self._play_btn)

        self._next_btn = QPushButton()
        self._next_btn.setFixedSize(38, 38)
        self._next_btn.clicked.connect(self.next_clicked.emit)
        controls_layout.addWidget(self._next_btn)

        controls_layout.addStretch()

        # 倍速
        self._speed_btn = QPushButton("1.0x")
        self._speed_btn.setFixedSize(56, 34)
        self._speed_btn.setFont(QFont("Segoe UI", 9))
        self._speed_btn.clicked.connect(self._show_speed_menu)
        controls_layout.addWidget(self._speed_btn)

        # 音量
        self._vol_btn = QPushButton()
        self._vol_btn.setFixedSize(34, 34)
        controls_layout.addWidget(self._vol_btn)

        self._vol_slider = QSlider(Qt.Orientation.Horizontal)
        self._vol_slider.setRange(0, 100)
        self._vol_slider.setValue(50)
        self._vol_slider.setFixedWidth(96)
        self._vol_slider.valueChanged.connect(self._on_volume_changed)
        controls_layout.addWidget(self._vol_slider)

        # 全屏
        self._fullscreen_btn = QPushButton()
        self._fullscreen_btn.setFixedSize(34, 34)
        self._fullscreen_btn.clicked.connect(self.fullscreen_toggled.emit)
        controls_layout.addWidget(self._fullscreen_btn)

        main_layout.addLayout(controls_layout)
        self._refresh_icons()

    def _refresh_icons(self):
        """按当前主题绘制 Material 图标"""
        t = theme.get()
        c = t.text
        self._prev_btn.setIcon(icons.make_icon("skip_previous", c, 22))
        self._prev_btn.setIconSize(self._prev_btn.size() * 0.55)
        self._play_btn.setIcon(icons.make_icon("pause" if self._is_playing else "play_arrow", c, 30))
        self._play_btn.setIconSize(self._play_btn.size() * 0.55)
        self._next_btn.setIcon(icons.make_icon("skip_next", c, 22))
        self._next_btn.setIconSize(self._next_btn.size() * 0.55)
        self._fullscreen_btn.setIcon(icons.make_icon(
            "fullscreen_exit" if self._is_fullscreen else "fullscreen", c, 20))
        self._fullscreen_btn.setIconSize(self._fullscreen_btn.size() * 0.6)
        self._update_vol_icon(self._vol_slider.value())

    def _style_buttons(self):
        t = theme.get()
        for btn in (self._prev_btn, self._play_btn, self._next_btn,
                    self._vol_btn, self._fullscreen_btn, self._speed_btn):
            btn.setStyleSheet(theme.btn_style_qss(t))
        # 播放按钮用主题色强调
        self._play_btn.setStyleSheet(f"""
            QPushButton {{
                background: {t.accent}; border: none; border-radius: 24px;
            }}
            QPushButton:hover {{ background: {t.accent_hover}; }}
        """)

    def _apply_theme_style(self):
        t = theme.get()
        self.setStyleSheet(theme.toolbar_qss(t) + f"\nQLabel {{ color: {t.text2}; }}")
        self._progress_slider.setStyleSheet(theme.slider_qss(t))
        self._vol_slider.setStyleSheet(theme.slider_qss(t))
        self._style_buttons()
        self._refresh_icons()

    def refresh_theme(self):
        self._apply_theme_style()

    # ─── 倍速 ─────────────────────────────────

    def _show_speed_menu(self):
        t = theme.get()
        menu = QMenu(self)
        menu.setStyleSheet(theme.menu_bar_qss(t))
        for rate in SPEED_OPTIONS:
            label = f"{rate:g}x"
            if rate == 1.0:
                label += i18n.tr("speed_normal")
            act = QAction(label, self)
            act.setCheckable(True)
            act.setChecked(abs(rate - self._current_rate) < 1e-6)
            act.triggered.connect(lambda checked, r=rate: self.set_speed(r))
            menu.addAction(act)
        menu.exec(self._speed_btn.mapToGlobal(self._speed_btn.rect().bottomLeft()))

    def set_speed(self, rate: float):
        """设置倍速并更新按钮文字"""
        self._current_rate = rate
        self._speed_btn.setText(f"{rate:g}x")
        self.speed_changed.emit(rate)

    def set_boost(self, boosting: bool, restore_rate: float = 1.0):
        """长按加速状态切换"""
        self._is_boost = boosting
        if boosting:
            self._speed_btn.setText("2.0x")
            self._boost_hint.show()
            self._speed_btn.setStyleSheet(f"""
                QPushButton {{
                    background: {theme.get().secondary}; color: #000;
                    border: none; border-radius: 8px; font-weight: bold;
                }}
            """)
        else:
            self._speed_btn.setText(f"{restore_rate:g}x")
            self._boost_hint.hide()
            self._style_buttons()

    # ─── 进度条悬停预览 ─────────────────────────

    def set_video_source(self, video_path: str):
        """设置当前视频路径，供悬停预览抽帧"""
        self._video_path = video_path or ""
        self._preview.hide()

    def eventFilter(self, obj, event):
        if obj is self._progress_slider:
            et = event.type()
            if et == QEvent.Type.MouseMove:
                self._on_slider_hover(event)
            elif et in (QEvent.Type.Leave, QEvent.Type.Hide):
                self._preview.hide()
        return super().eventFilter(obj, event)

    def _ms_at_x(self, x: int) -> int:
        if self._duration <= 0:
            return 0
        w = self._progress_slider.width()
        if w <= 0:
            return 0
        ratio = max(0.0, min(1.0, x / w))
        return int(ratio * self._duration)

    def _on_slider_hover(self, event):
        if self._duration <= 0:
            self._preview.hide()
            return
        pos = event.position().toPoint()
        ms = self._ms_at_x(pos.x())
        self._update_time_label_hover(ms)
        # 节流：每 120ms 抽一帧，避免拖动时频繁解码
        if self._preview_timer.elapsed() < 120 and self._preview.isVisible():
            self._move_preview(pos)
            return
        self._preview_timer.restart()
        pix = frame_provider.grab_frame(self._video_path, ms, max_width=240) if self._video_path else None
        self._preview.set_content(pix, _format_time(ms))
        self._move_preview(pos)
        self._preview.show()

    def _move_preview(self, pos: QPoint):
        global_pos = self._progress_slider.mapToGlobal(pos)
        x = global_pos.x() - self._preview.width() // 2
        y = global_pos.y() - self._preview.height() - 12
        self._preview.move(x, y)

    def _update_time_label_hover(self, ms: int):
        self._time_label.setText(f"{_format_time(ms)} / {_format_time(self._duration)}")

    # ─── 进度 ─────────────────────────────────

    def set_duration(self, ms: int):
        self._duration = ms
        self._progress_slider.setRange(0, ms)
        self._update_time_label(0)

    def set_position(self, ms: int):
        if not self._is_slider_dragging:
            self._progress_slider.setValue(ms)
            self._update_time_label(ms)

    def set_playing(self, playing: bool):
        self._is_playing = playing
        t = theme.get()
        self._play_btn.setIcon(icons.make_icon("pause" if playing else "play_arrow", t.text, 30))

    def set_fullscreen(self, fullscreen: bool):
        self._is_fullscreen = fullscreen
        t = theme.get()
        self._fullscreen_btn.setIcon(icons.make_icon(
            "fullscreen_exit" if fullscreen else "fullscreen", t.text, 20))

    def _on_volume_changed(self, vol: int):
        self._update_vol_icon(vol)
        self.volume_changed.emit(vol)

    def _update_vol_icon(self, vol: int):
        t = theme.get()
        name = "volume_off" if vol == 0 else "volume_up"
        self._vol_btn.setIcon(icons.make_icon(name, t.text, 20))
        self._vol_btn.setIconSize(self._vol_btn.size() * 0.6)

    def _update_time_label(self, current_ms: int):
        self._time_label.setText(
            f"{_format_time(current_ms)} / {_format_time(self._duration)}"
        )

    def _on_slider_pressed(self):
        self._is_slider_dragging = True

    def _on_slider_released(self):
        self._is_slider_dragging = False
        self.seek_requested.emit(self._progress_slider.value())

    def _on_slider_moved(self, pos: int):
        self._update_time_label(pos)
