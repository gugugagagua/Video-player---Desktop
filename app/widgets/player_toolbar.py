"""播放控制工具栏 - 播放/暂停、音量、进度、倍速、全屏"""

from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QEvent, QPoint
from PyQt6.QtGui import QFont, QIcon, QAction, QPixmap
from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QPushButton, QSlider, QLabel,
    QVBoxLayout, QMenu, QFrame,
)

from app import theme
from app import icons
from app import i18n
from app import prefs
from app import frame_provider


def _format_time(ms: int) -> str:
    seconds = ms // 1000
    m, s = divmod(seconds, 60)
    if m >= 60:
        h, m = divmod(m, 60)
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


SPEED_OPTIONS = [0.5, 0.75, 1.0, 1.25, 1.5, 2.0]

# 鼠标离开进度条后，多久停止后台预加载（毫秒）
PRELOAD_IDLE_MS = 5000

# 鼠标停下多久后去取精确帧（毫秒）。拖动过程中只用雪碧图，避免刷爆后台。
REFINE_DELAY_MS = 130

# 后台帧到达时，与当前悬停位置相差在此范围内就仍然显示（毫秒）。
# 若一律丢弃，连续拖动时每一帧在完成时都已「过期」，画面会完全不动。
STALE_TOLERANCE_MS = 3000


class SeekPreview(QWidget):
    """进度条悬停预览浮窗：上方目标画面，下方时间

    尺寸固定不变——尺寸抖动会触发顶层窗口重设大小，是移动时的性能杀手。
    画面尺寸跟随「设置 → 预览图精细度」，与抽帧分辨率一致，做到像素级清晰。
    """

    def __init__(self, parent=None):
        super().__init__(parent, Qt.WindowType.ToolTip)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.img_w, self.img_h = prefs.thumb_tile_size()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(4)
        self._img = QLabel()
        self._img.setFixedSize(self.img_w, self.img_h)
        self._img.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._img.setStyleSheet("background: #000; color: #888; border-radius: 4px;")
        self._img.setText("…")
        lay.addWidget(self._img, alignment=Qt.AlignmentFlag.AlignCenter)
        self._time = QLabel("00:00")
        self._time.setFixedWidth(self.img_w)  # 固定宽度，布局尺寸恒定
        self._time.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._time.setStyleSheet("color: #fff; font-size: 12px; font-weight: bold;")
        lay.addWidget(self._time)
        self.adjustSize()  # 只在初始化时算一次尺寸

    def set_size(self, width: int, height: int):
        """切换精细度时调整浮窗尺寸

        仍然只在切换的那一刻变一次，鼠标移动过程中尺寸恒定，
        不会退回到「每次移动都重设顶层窗口尺寸」的老问题。
        """
        if (width, height) == (self.img_w, self.img_h):
            return
        self.img_w, self.img_h = width, height
        self._img.setFixedSize(width, height)
        self._time.setFixedWidth(width)
        self.adjustSize()

    def set_image(self, image):
        """设置目标画面（接受 QImage 或 QPixmap）"""
        if image is None:
            return
        pixmap = image if isinstance(image, QPixmap) else QPixmap.fromImage(image)
        if pixmap.isNull():
            return
        self._img.setPixmap(pixmap.scaled(
            self._img.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        ))

    def set_time(self, text: str):
        if self._time.text() != text:
            self._time.setText(text)


class PlayerToolbar(QWidget):
    """播放控制工具栏"""

    play_toggled = pyqtSignal()
    previous_clicked = pyqtSignal()
    next_clicked = pyqtSignal()
    seek_requested = pyqtSignal(int)

    fullscreen_toggled = pyqtSignal()
    speed_changed = pyqtSignal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._duration = 0
        self._is_slider_dragging = False
        self._is_fullscreen = False
        self._immersive = False
        self._current_rate = 1.0
        self._is_playing = False
        self._is_boost = False
        self._prev_btn = None
        self._play_btn = None
        self._next_btn = None
        self._fullscreen_btn = None
        self._speed_btn = None
        self._progress_slider = None
        self._time_label = None
        self._boost_hint = None
        # 悬停预览
        self._video_path: str = ""
        self._preview = SeekPreview(self)
        self._preview_token = 0
        self._preview_bucket = -1
        self._preview_ms = 0
        self._preview_pos = None
        self._token_ms: dict = {}       # token → 该请求对应的时间点
        frame_provider.signals.ready.connect(self._on_preview_frame)
        # 雪碧图边生成边可用：生成期间悬停也能立刻换上更清晰的格子
        frame_provider.signals.strip_progress.connect(self._on_strip_progress)
        # 鼠标停下后再取精确帧
        self._refine_timer = QTimer(self)
        self._refine_timer.setSingleShot(True)
        self._refine_timer.setInterval(REFINE_DELAY_MS)
        self._refine_timer.timeout.connect(self._request_exact_frame)
        # 离开进度条后自动暂停预加载，避免无谓占用 CPU
        self._preload_idle = QTimer(self)
        self._preload_idle.setSingleShot(True)
        self._preload_idle.setInterval(PRELOAD_IDLE_MS)
        self._preload_idle.timeout.connect(frame_provider.pause_preload)
        self._setup_ui()
        self._apply_theme_style()

    def _setup_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(12, 6, 12, 10)
        main_layout.setSpacing(6)

        # 每一行都包一层容器：沉浸模式下整行隐藏，控件则搬家到精简行里
        self._info_row = QWidget()
        self._time_layout = QHBoxLayout(self._info_row)
        self._time_layout.setContentsMargins(0, 0, 0, 0)

        self._prog_row = QWidget()
        self._progress_layout = QHBoxLayout(self._prog_row)
        self._progress_layout.setContentsMargins(0, 0, 0, 0)

        self._ctrl_row = QWidget()
        self._controls_layout = QHBoxLayout(self._ctrl_row)
        self._controls_layout.setContentsMargins(0, 0, 0, 0)
        self._controls_layout.setSpacing(8)

        # 沉浸模式专用：左下角「播放按钮 + 进度条」
        self._imm_row = QWidget()
        self._immersive_layout = QHBoxLayout(self._imm_row)
        self._immersive_layout.setContentsMargins(16, 0, 16, 0)
        self._immersive_layout.setSpacing(12)

        for row in (self._info_row, self._prog_row,
                    self._ctrl_row, self._imm_row):
            main_layout.addWidget(row)

        # 进度条
        self._progress_slider = QSlider(Qt.Orientation.Horizontal)
        self._progress_slider.setRange(0, 0)
        self._progress_slider.setMouseTracking(True)
        self._progress_slider.installEventFilter(self)
        self._progress_slider.sliderPressed.connect(self._on_slider_pressed)
        self._progress_slider.sliderReleased.connect(self._on_slider_released)
        self._progress_slider.sliderMoved.connect(self._on_slider_moved)

        self._time_label = QLabel("00:00 / 00:00")
        self._time_label.setFont(QFont("Consolas", 10))

        # 加速提示
        self._boost_hint = QLabel("»2x")
        self._boost_hint.setStyleSheet(
            "color: #03DAC6; font-size: 12px; font-weight: bold;"
        )

        # 控制按钮
        self._prev_btn = QPushButton()
        self._prev_btn.setFixedSize(38, 38)
        self._prev_btn.clicked.connect(self.previous_clicked.emit)

        self._play_btn = QPushButton()
        self._play_btn.setFixedSize(48, 48)
        self._play_btn.clicked.connect(self.play_toggled.emit)

        self._next_btn = QPushButton()
        self._next_btn.setFixedSize(38, 38)
        self._next_btn.clicked.connect(self.next_clicked.emit)

        self._speed_btn = QPushButton("1.0x")
        self._speed_btn.setFixedSize(56, 34)
        self._speed_btn.setFont(QFont("Segoe UI", 9))
        self._speed_btn.clicked.connect(self._show_speed_menu)

        self._fullscreen_btn = QPushButton()
        self._fullscreen_btn.setFixedSize(34, 34)
        self._fullscreen_btn.clicked.connect(self.fullscreen_toggled.emit)

        self._relayout()
        self._refresh_icons()

    # ─── 布局切换（普通 / 沉浸）─────────────────

    def _managed_widgets(self):
        return (self._time_label, self._boost_hint, self._progress_slider,
                self._prev_btn, self._play_btn, self._next_btn,
                self._speed_btn, self._fullscreen_btn)

    @staticmethod
    def _drop_spacers(layout):
        """清掉布局里的弹簧项；控件本身由调用方负责搬走"""
        for i in range(layout.count() - 1, -1, -1):
            item = layout.itemAt(i)
            if item is not None and item.widget() is None:
                layout.takeAt(i)

    def _relayout(self):
        """按当前模式重新摆放控件。

        控件是「搬家」而不是复制，进度条始终是同一个对象 ——
        这样悬停预览、事件过滤器、信号连接都不受影响。
        """
        rows = (self._time_layout, self._progress_layout,
                self._controls_layout, self._immersive_layout)
        for w in self._managed_widgets():
            for lay in rows:
                lay.removeWidget(w)
        for lay in rows:
            self._drop_spacers(lay)

        if self._immersive:
            for row in (self._info_row, self._prog_row, self._ctrl_row):
                row.hide()
            self._imm_row.show()
            # 左下角：上一集 / 播放 / 下一集 + 进度条
            self._immersive_layout.addWidget(self._prev_btn)
            self._immersive_layout.addWidget(self._play_btn)
            self._immersive_layout.addWidget(self._next_btn)
            self._immersive_layout.addWidget(self._progress_slider, 1)
            for w in (self._prev_btn, self._play_btn, self._next_btn,
                      self._progress_slider):
                w.show()
            for w in (self._speed_btn, self._time_label, self._boost_hint,
                      self._fullscreen_btn):
                w.hide()
            return

        self._imm_row.hide()
        for row in (self._info_row, self._prog_row, self._ctrl_row):
            row.show()
        self._time_layout.addWidget(self._time_label)
        self._time_layout.addStretch()
        self._time_layout.addWidget(self._boost_hint)
        self._progress_layout.addWidget(self._progress_slider)
        self._controls_layout.addWidget(self._prev_btn)
        self._controls_layout.addWidget(self._play_btn)
        self._controls_layout.addWidget(self._next_btn)
        self._controls_layout.addStretch()
        self._controls_layout.addWidget(self._speed_btn)
        self._controls_layout.addWidget(self._fullscreen_btn)
        for w in self._managed_widgets():
            w.show()
        self._boost_hint.setVisible(self._is_boost)

    def set_immersive(self, on: bool):
        """沉浸模式：只留左下角「播放按钮 + 进度条」（全屏时使用）"""
        if self._immersive == on:
            return
        self._immersive = on
        self._relayout()

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

    def _style_buttons(self):
        t = theme.get()
        for btn in (self._prev_btn, self._play_btn, self._next_btn,
                    self._fullscreen_btn, self._speed_btn):
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
        # 往上弹：菜单底边贴住按钮顶边（默认是往下弹，会被贴到底部边缘）
        pos = self._speed_btn.mapToGlobal(self._speed_btn.rect().topLeft())
        menu.exec(pos - QPoint(0, menu.sizeHint().height()))

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

    def refresh_preview_size(self):
        """「预览图精细度」改变后同步悬停预览窗尺寸"""
        self._preview.set_size(*prefs.thumb_tile_size())
        self._preview.hide()
        self._preview_bucket = -1

    def set_video_source(self, video_path: str):
        """设置当前视频路径，供悬停预览抽帧"""
        self._video_path = video_path or ""
        self._preview.hide()
        self._preview_bucket = -1
        self._preview_token += 1  # 让上一个视频的在途请求失效
        self._token_ms.clear()
        self._refine_timer.stop()
        self._preload_idle.stop()
        frame_provider.set_active_video(self._video_path)
        if not self._video_path:
            frame_provider.pause_preload()

    def eventFilter(self, obj, event):
        if obj is self._progress_slider:
            et = event.type()
            if et == QEvent.Type.MouseMove:
                self._on_slider_hover(event)
            elif et in (QEvent.Type.Leave, QEvent.Type.Hide):
                self._preview.hide()
                # 复位：否则重新进入进度条时若还落在同一秒内，
                # 会沿用上一次的判断而不再取图，看起来就是「预览窗卡住不动」
                self._preview_bucket = -1
                self._preview_pos = None
                self._preload_idle.start()
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
        self._preview_ms = ms

        # 悬停期间保持预加载开启
        self._preload_idle.stop()

        # 以下都是轻量操作：改文字、移动浮窗
        self._update_time_label_hover(ms)
        self._preview.set_time(_format_time(ms))
        self._move_preview(pos)
        if not self._preview.isVisible():
            self._preview.show()

        bucket = ms // frame_provider.BUCKET_MS
        if bucket != self._preview_bucket:
            self._preview_bucket = bucket
            self._show_bucket_frame(ms)

        # 鼠标停下后再补一帧精确画面（拖动过程中不打扰后台）
        self._refine_timer.start()

    def _show_bucket_frame(self, ms: int):
        """取该秒的画面：雪碧图优先，没有则走按需解码"""
        # ① 雪碧图已覆盖 → 直接裁一格，微秒级，这就是「指哪打哪」
        tile = frame_provider.tile_at(self._video_path, ms)
        if tile is not None:
            self._preview.set_image(tile)
            return
        # ② 尚未覆盖 → 走按需解码（命中缓存即时，否则先给近似帧占位）
        self._request_frame_async(ms)

    def _on_strip_progress(self, *_args):
        """雪碧图又生成出新格子 → 悬停中就用它替换掉模糊的按需帧

        **只走雪碧图，绝不能落到按需抽帧上。** 生成期间这个信号每 0.15 秒
        来一次，而按需路径在拿不到精确帧时会先拿缓存里的近似帧顶上 —— 于是
        每 0.15 秒就把刚到达的精确画面覆盖回那张旧图，看起来就是「预览窗
        卡死在同一张画面、鼠标怎么移动都不变」。
        """
        if not (self._preview.isVisible() and self._duration > 0):
            return
        tile = frame_provider.tile_at(self._video_path, self._preview_ms)
        if tile is not None:
            self._preview.set_image(tile)

    def _request_frame_async(self, ms: int):
        """向后台请求 ms 处的精确帧"""
        self._preview_token += 1
        token = self._preview_token
        self._token_ms[token] = ms
        if len(self._token_ms) > 64:
            for key in list(self._token_ms)[:-16]:
                self._token_ms.pop(key, None)
        cached = frame_provider.request_frame(self._video_path, ms, token)
        if cached is not None:
            self._preview.set_image(cached)

    def _request_exact_frame(self):
        """鼠标停下后取当前位置的精确帧"""
        if not self._video_path or self._duration <= 0:
            return
        self._request_frame_async(self._preview_ms)

    def _on_preview_frame(self, image, token):
        """后台抽帧完成回调（已在 GUI 线程）"""
        req_ms = self._token_ms.pop(token, None)
        if image is None or req_ms is None:
            return
        # 只要离当前悬停位置不算远就显示。
        # 若一律按「过期」丢弃，连续拖动时每个帧在完成时都已过期，画面会完全不动。
        if abs(req_ms - self._preview_ms) > STALE_TOLERANCE_MS:
            return
        self._preview.set_image(image)

    def _move_preview(self, pos: QPoint):
        """横向跟随鼠标，纵向固定（锚定在进度条上方）"""
        pw = self._preview.width()
        # 横向：以鼠标为中心
        mouse_global = self._progress_slider.mapToGlobal(pos)
        x = mouse_global.x() - pw // 2
        # 纵向：始终以进度条顶边为基准，不随鼠标纵向位置变化
        slider_top = self._progress_slider.mapToGlobal(QPoint(0, 0)).y()
        y = slider_top - self._preview.height() - 12

        # 限制在主窗口范围内，避免贴边被裁掉
        win = self.window()
        if win is not None:
            g = win.geometry()
            x = max(g.left() + 4, min(x, g.right() - pw - 3))

        # 位置没变就不调用 move（纵向移动鼠标时零窗口操作）
        if (x, y) == self._preview_pos:
            return
        self._preview_pos = (x, y)
        self._preview.move(x, y)

    def _update_time_label_hover(self, ms: int):
        self._time_label.setText(f"{_format_time(ms)} / {_format_time(self._duration)}")

    # ─── 进度 ─────────────────────────────────

    def set_duration(self, ms: int):
        self._duration = ms
        self._progress_slider.setRange(0, ms)
        self._update_time_label(0)
        # 时长已知 → 开始准备缩略图雪碧图（先读磁盘缓存，无则后台生成）
        if self._video_path and ms > 0:
            frame_provider.prepare_strip(self._video_path, ms)

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
