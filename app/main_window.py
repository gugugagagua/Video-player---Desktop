"""主窗口 - 组合各组件构成完整界面"""

import time

from PyQt6.QtCore import Qt, QSize, QPoint, QCoreApplication, QTimer, QEvent
from PyQt6.QtGui import (
    QAction, QActionGroup, QCursor, QFont, QKeySequence, QShortcut,
)
from PyQt6.QtMultimedia import QMediaPlayer
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QStackedWidget, QFileDialog, QMessageBox, QMenuBar, QLabel,
    QLineEdit, QPushButton, QProgressBar,
)

from app import __version__
from app import database as db
from app.player import VideoPlayer
from app.playlist import Playlist
from app.video_manager import import_collection, fill_missing_durations
from app.widgets.collection_grid import CollectionGrid
from app.widgets.player_toolbar import PlayerToolbar
from app.widgets.video_list_widget import VideoListWidget
from app import theme
from app import icons
from app import i18n
from app import prefs
from app import frame_provider
from app import thumb_strip


# 左右方向键短按的跳转步长
SEEK_STEP_MS = 5000

# 沉浸模式下：鼠标进入屏幕右侧这么宽的范围内，滑出视频列表
IMMERSIVE_EDGE_PX = 100

# 搜索防抖：输入过程中不重建，停顿这么久之后才重建一次。
# 重建要清空并重新创建所有卡片（含封面解码，见 collection_grid），
# 每敲一个键都做一遍会明显发粘。
SEARCH_DEBOUNCE_MS = 160

# 长按超过这么多毫秒才算「加速」，否则算「短按快进」
LONG_PRESS_MS = 400


class MainWindow(QMainWindow):
    """主窗口"""

    def __init__(self):
        super().__init__()
        self._playlist: Playlist | None = None
        self._current_video_id: int | None = None
        self._current_video_name: str = ""
        self._immersive = False
        self._pending_seek = 0      # 续播：等媒体加载完成后要跳的位置
        # 全屏时轮询鼠标位置，决定右侧列表是否滑出（见 _poll_immersive_edge）
        self._immersive_watch = QTimer(self)
        self._immersive_watch.setInterval(60)
        self._immersive_watch.timeout.connect(self._poll_immersive_edge)
        # 搜索防抖：见 SEARCH_DEBOUNCE_MS 的说明
        self._search_text = ""
        self._search_debounce = QTimer(self)
        self._search_debounce.setSingleShot(True)
        self._search_debounce.setInterval(SEARCH_DEBOUNCE_MS)
        self._search_debounce.timeout.connect(self._run_search)
        self._init_db()
        self._setup_theme()
        self._setup_ui()
        self._setup_player()
        self._setup_menu()
        self._setup_preload()
        self._load_collections()

    def _init_db(self):
        db.init_db()

    def _setup_theme(self):
        """自动检测系统主题并应用"""
        t = theme.detect_system_theme()
        theme.set_theme(t)
        self._update_theme_stylesheets()

    def _update_theme_stylesheets(self):
        """根据当前主题更新所有样式"""
        t = theme.get()
        self.setStyleSheet(theme.window_qss(t))

    def _setup_ui(self):
        self.setWindowTitle(i18n.tr("app_name"))
        self.setMinimumSize(1200, 750)
        # 应用图标（参照 Android 启动图标的播放三角）
        self.setWindowIcon(icons.make_icon("play_arrow", theme.get().accent, 64))

        self._stack = QStackedWidget()
        self.setCentralWidget(self._stack)

        # -------- 首页：视频集网格 --------
        self._home_widget = QWidget()
        home_layout = QVBoxLayout(self._home_widget)
        home_layout.setContentsMargins(0, 0, 0, 0)

        t = theme.get()
        header_bar = QWidget()
        header_bar.setFixedHeight(70)
        header_layout = QHBoxLayout(header_bar)
        header_layout.setContentsMargins(24, 12, 24, 12)

        self._header = QLabel(i18n.tr("app_name"))
        self._header.setFont(QFont("Microsoft YaHei", 22, QFont.Weight.Bold))
        self._header.setStyleSheet(f"color: {t.text};")
        header_layout.addWidget(self._header)
        header_layout.addStretch()

        # 搜索框
        self._search_box = QLineEdit()
        self._search_box.setPlaceholderText(i18n.tr("search_placeholder"))
        self._search_box.setFixedWidth(280)
        self._search_box.setClearButtonEnabled(True)
        self._search_box.setStyleSheet(theme.search_box_qss(t))
        self._search_box.textChanged.connect(self._on_search_changed)
        header_layout.addWidget(self._search_box)

        # 预加载预览图按钮
        self._preload_btn = QPushButton()
        self._preload_btn.setFixedSize(34, 34)
        self._preload_btn.setIcon(icons.make_icon("download", t.text, 20))
        self._preload_btn.setIconSize(QSize(20, 20))
        self._preload_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._preload_btn.setToolTip(i18n.tr("preload_tip"))
        self._preload_btn.setStyleSheet(theme.btn_style_qss(t))
        self._preload_btn.clicked.connect(lambda: self._preload_all_strips(None))
        header_layout.addWidget(self._preload_btn)

        # 版本号：标题栏最右侧角落，一眼就能确认装的是哪一版
        self._version_label = QLabel(f"v{__version__}")
        self._version_label.setStyleSheet(
            f"color: {t.text2}; font-size: 11px;")
        self._version_label.setToolTip(f"{i18n.tr('app_name')} {__version__}")
        header_layout.addSpacing(12)
        header_layout.addWidget(self._version_label)

        home_layout.addWidget(header_bar)

        self._grid = CollectionGrid()
        self._grid.collection_selected.connect(self._open_collection)
        self._grid.video_activated.connect(self._play_video_from_search)
        home_layout.addWidget(self._grid)

        self._stack.addWidget(self._home_widget)

        # -------- 播放页面 --------
        self._play_widget = QWidget()
        play_layout = QVBoxLayout(self._play_widget)
        play_layout.setContentsMargins(0, 0, 0, 0)
        play_layout.setSpacing(0)

        # 视频 + 侧栏
        self._video_area = QWidget()
        self._video_area_layout = QHBoxLayout(self._video_area)
        self._video_area_layout.setContentsMargins(0, 0, 0, 0)
        self._video_area_layout.setSpacing(0)

        # mpv 需要真实窗口句柄才能渲染，所以用普通 QWidget 而不是 QVideoWidget，
        # 并显式要求原生窗口（否则 winId() 可能只拿到合成句柄，mpv 画不上去）。
        # 不加样式表/背景：画面由 mpv 自己铺满，让 Qt 别去重绘同一块区域。
        self._video_widget = QWidget()
        self._video_widget.setMinimumSize(800, 450)
        self._video_widget.setAttribute(
            Qt.WidgetAttribute.WA_NativeWindow, True)
        self._video_widget.setAttribute(
            Qt.WidgetAttribute.WA_DontCreateNativeAncestors, True)
        self._video_widget.setAttribute(
            Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self._video_area_layout.addWidget(self._video_widget, 1)

        self._video_list = VideoListWidget()
        self._video_list.video_selected.connect(self._on_video_selected)
        self._video_area_layout.addWidget(self._video_list)

        play_layout.addWidget(self._video_area, 1)

        # 工具栏
        self._toolbar = PlayerToolbar()
        self._toolbar.play_toggled.connect(self._toggle_play)
        self._toolbar.previous_clicked.connect(self._play_previous)
        self._toolbar.next_clicked.connect(self._play_next)
        self._toolbar.seek_requested.connect(self._seek)

        self._toolbar.fullscreen_toggled.connect(self._toggle_fullscreen)
        self._toolbar.speed_changed.connect(self._set_playback_rate)
        play_layout.addWidget(self._toolbar)

        # 返回首页按钮
        self._back_btn = QLabel(i18n.tr("back_home"))
        self._back_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._back_btn.setStyleSheet(
            f"color: {t.accent}; background: rgba(30,30,30,180); "
            "padding: 6px 12px; border-radius: 6px; font-size: 13px;"
        )
        self._back_btn.setFixedHeight(32)
        self._back_btn.mousePressEvent = lambda e: self._back_home()
        play_layout.insertWidget(0, self._back_btn)

        # 沉浸模式（全屏）下的退出按钮：浮在播放页右上角
        self._immersive_exit = QPushButton(self._play_widget)
        self._immersive_exit.setFixedSize(40, 40)
        self._immersive_exit.setIcon(icons.make_icon("fullscreen_exit", "#FFFFFF", 22))
        self._immersive_exit.setIconSize(QSize(22, 22))
        self._immersive_exit.setCursor(Qt.CursorShape.PointingHandCursor)
        self._immersive_exit.setToolTip(i18n.tr("fullscreen_exit"))
        self._immersive_exit.setStyleSheet(
            "QPushButton { background: rgba(0,0,0,170); border: none;"
            " border-radius: 20px; }"
            "QPushButton:hover { background: rgba(0,0,0,230); }"
        )
        self._immersive_exit.clicked.connect(self._toggle_fullscreen)
        self._immersive_exit.hide()

        # 沉浸模式下靠鼠标位置决定列表是否出现
        self._play_widget.setMouseTracking(True)
        self._video_widget.setMouseTracking(True)
        self._play_widget.installEventFilter(self)
        self._video_widget.installEventFilter(self)

        self._stack.addWidget(self._play_widget)

    def _setup_player(self):
        self._player = VideoPlayer(self._video_widget)
        self._player.position_changed.connect(self._toolbar.set_position)
        self._player.duration_changed.connect(self._toolbar.set_duration)
        self._player.duration_changed.connect(self._on_duration_changed)
        self._player.playback_state_changed.connect(self._on_playback_state_changed)
        self._player.error_occurred.connect(self._on_player_error)
        self._player.set_volume(50)

        # 取代 QtMultimedia 的 mediaStatusChanged
        self._player.media_loaded.connect(self._on_media_loaded)
        self._player.end_of_media.connect(self._on_end_of_media)

        # 双击视频切换全屏
        self._video_widget.mouseDoubleClickEvent = self._on_video_double_click

        self._setup_shortcuts()
        self._setup_boost()

    def _setup_shortcuts(self):
        self._shortcut_space = QShortcut(QKeySequence(Qt.Key.Key_Space), self)
        self._shortcut_space.activated.connect(self._toggle_play)
        self._shortcut_left = QShortcut(QKeySequence(Qt.Key.Key_Left), self)
        self._shortcut_left.activated.connect(self._seek_backward)
        self._shortcut_f = QShortcut(QKeySequence(Qt.Key.Key_F), self)
        self._shortcut_f.activated.connect(self._toggle_fullscreen)
        self._shortcut_esc = QShortcut(QKeySequence(Qt.Key.Key_Escape), self)
        self._shortcut_esc.activated.connect(self._exit_fullscreen)
        # 供搜索框聚焦时临时禁用
        self._play_shortcuts = [
            self._shortcut_space, self._shortcut_left,
            self._shortcut_f, self._shortcut_esc,
        ]
        # 搜索框聚焦时禁用播放快捷键，避免输入冲突
        self._search_box.installEventFilter(self)

    def eventFilter(self, obj, event):
        if obj is self._search_box:
            if event.type() == QEvent.Type.FocusIn:
                for sc in self._play_shortcuts:
                    sc.setEnabled(False)
            elif event.type() == QEvent.Type.FocusOut:
                for sc in self._play_shortcuts:
                    sc.setEnabled(True)
        elif self._immersive and obj in (self._play_widget, self._video_widget):
            if event.type() == QEvent.Type.Resize:
                self._position_immersive_exit()
                self._position_immersive_list()
        return super().eventFilter(obj, event)

    # ─── 沉浸模式（全屏）─────────────────────────────

    def _set_immersive(self, on: bool):
        """全屏时切到沉浸布局：只留左下「播放按钮 + 进度条」、右上退出按钮

        普通窗口保持完整的控制栏。
        """
        self._immersive = on
        self._toolbar.set_immersive(on)
        self._back_btn.setVisible(not on)
        # 退出按钮不进常驻控件：它和浮层列表**同进同出**，统一交给
        # _poll_immersive_edge 按鼠标位置控制。这里两种模式都先收起来。
        self._immersive_exit.setVisible(False)
        # 列表先统一从视频区布局里摘出来，两种模式都由这里决定它去哪儿
        self._video_area_layout.removeWidget(self._video_list)

        if on:
            # 沉浸模式：改成浮层，覆盖在画面上，不挤压视频
            self._video_list.setParent(self._play_widget)
            self._video_list.hide()
            self._position_immersive_list()
            self._position_immersive_exit()
            self._immersive_watch.start()
            return

        # 退出全屏：放回视频区布局并恢复常显
        # （以前这里无条件 setVisible(False)，所以退出后列表再也不出来了）
        self._immersive_watch.stop()
        self._video_area_layout.addWidget(self._video_list)
        self._video_list.show()

    def _position_immersive_exit(self):
        if not self._immersive:
            return
        w = self._play_widget.width()
        self._immersive_exit.move(
            max(8, w - self._immersive_exit.width() - 16), 16)
        self._immersive_exit.raise_()

    def _position_immersive_list(self):
        """把浮层列表贴到播放页右侧，高度与视频区一致（不含底部控制栏）"""
        if not self._immersive:
            return
        pw = self._play_widget
        top_left = self._video_area.mapTo(pw, QPoint(0, 0))
        lw = self._video_list.width()
        self._video_list.setGeometry(
            max(0, pw.width() - lw), top_left.y(), lw,
            max(1, self._video_area.height()))
        self._video_list.raise_()
        self._immersive_exit.raise_()

    def _poll_immersive_edge(self):
        """按全局鼠标位置决定右侧列表是否滑出

        这里刻意不用 MouseMove 事件：视频画面由 mpv 画在原生窗口上，
        鼠标移动不一定能送到 Qt 这边，靠事件在全屏里会完全失效。
        轮询 QCursor.pos() 与具体控件无关，最稳。

        间隔 60ms：一次只做两次坐标换算，开销可以忽略。
        """
        if not self._immersive:
            return
        local = self._play_widget.mapFromGlobal(QCursor.pos())
        width = self._play_widget.width()
        if width <= 0:
            return
        near_edge = local.x() >= width - IMMERSIVE_EDGE_PX
        # 鼠标压在列表或退出按钮上时也算「还在里面」，
        # 否则刚移到按钮上就会闪掉，根本点不到
        over_list = self._video_list.isVisible() and (
            self._video_list.geometry().contains(local)
            or self._immersive_exit.geometry().contains(local))
        if near_edge or over_list:
            if not self._video_list.isVisible():
                self._video_list.show()
                self._video_list.raise_()
            if not self._immersive_exit.isVisible():
                self._immersive_exit.show()
                self._immersive_exit.raise_()
        else:
            if self._video_list.isVisible():
                self._video_list.hide()
            if self._immersive_exit.isVisible():
                self._immersive_exit.hide()

    # ─── 长按加速（D / → 触发 2 倍速，松手恢复）─────────

    def _setup_boost(self):
        self._boost_keys = {Qt.Key.Key_D, Qt.Key.Key_Right}
        self._boost_arm_timers: dict = {}
        self._boost_pressed_at: dict = {}
        self._boost_keys_down: set = set()
        self._boosting = False
        self._saved_rate = 1.0

    def keyPressEvent(self, event):
        key = event.key()
        if key in getattr(self, "_boost_keys", ()) and not event.isAutoRepeat():
            self._on_boost_press(key)
            return
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event):
        key = event.key()
        if key in getattr(self, "_boost_keys", ()) and not event.isAutoRepeat():
            self._on_boost_release(key)
            return
        super().keyReleaseEvent(event)

    def _on_boost_press(self, key):
        if key in self._boost_keys_down:
            return
        self._boost_keys_down.add(key)
        self._boost_pressed_at[key] = time.monotonic()
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.setInterval(LONG_PRESS_MS)
        timer.timeout.connect(lambda k=key: self._activate_boost(k))
        self._boost_arm_timers[key] = timer
        timer.start()

    def _on_boost_release(self, key):
        """按「实际按住时长」判定短按/长按

        早先看的是计时器还在不在跑，但计时器边界只有 250ms —— 稍微按久一点
        就被当成长按去加速，右箭头的「短按快进」几乎按不出来。
        现在按下时就记时间，松手按真实时长决定：
          - 未到阈值：撤销可能已触发的加速，改判为短按快进
          - 超过阈值：保持长按语义，松手恢复倍速
        """
        if key not in self._boost_keys_down:
            return
        self._boost_keys_down.discard(key)
        timer = self._boost_arm_timers.pop(key, None)
        if timer is not None:
            timer.stop()
        started = self._boost_pressed_at.pop(key, None)
        held_ms = (time.monotonic() - started) * 1000 if started else 0.0

        if held_ms < LONG_PRESS_MS:
            if self._boosting:
                self._stop_boost()
            if key == Qt.Key.Key_Right:
                self._seek_forward()
            return

        if self._boosting and not self._boost_keys_down:
            self._stop_boost()

    def _activate_boost(self, key):
        self._boost_arm_timers.pop(key, None)
        if self._boosting:
            return
        self._boosting = True
        self._saved_rate = self._player.playback_rate() or 1.0
        self._player.set_playback_rate(2.0)
        self._toolbar.set_boost(True, self._saved_rate)

    def _stop_boost(self):
        if not self._boosting:
            return
        self._boosting = False
        self._player.set_playback_rate(self._saved_rate or 1.0)
        self._toolbar.set_boost(False, self._saved_rate)

    def _setup_menu(self):
        menubar = self.menuBar()
        menubar.clear()
        t = theme.get()
        menubar.setStyleSheet(theme.menu_bar_qss(t))

        file_menu = menubar.addMenu(i18n.tr("menu_file"))
        import_action = QAction(icons.make_icon("create_new_folder", t.text, 18),
                                i18n.tr("menu_import"), self)
        import_action.triggered.connect(self._manual_import)
        file_menu.addAction(import_action)

        preload_action = QAction(icons.make_icon("download", t.text, 18),
                                 i18n.tr("menu_preload"), self)
        preload_action.triggered.connect(lambda: self._preload_all_strips(None))
        file_menu.addAction(preload_action)

        exit_action = QAction(icons.make_icon("close", t.text, 18), i18n.tr("menu_exit"), self)
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        view_menu = menubar.addMenu(i18n.tr("menu_view"))
        home_action = QAction(icons.make_icon("arrow_back", t.text, 18),
                              i18n.tr("menu_home"), self)
        home_action.triggered.connect(self._back_home)
        view_menu.addAction(home_action)

        # 主题切换
        theme_menu = menubar.addMenu(i18n.tr("menu_theme"))
        self._dark_action = QAction(i18n.tr("theme_dark"), self)
        self._dark_action.setCheckable(True)
        self._dark_action.setChecked(theme.get().name == "dark")
        self._dark_action.triggered.connect(lambda: self._switch_theme("dark"))
        theme_menu.addAction(self._dark_action)

        self._light_action = QAction(i18n.tr("theme_light"), self)
        self._light_action.setCheckable(True)
        self._light_action.setChecked(theme.get().name == "light")
        self._light_action.triggered.connect(lambda: self._switch_theme("light"))
        theme_menu.addAction(self._light_action)

        # 语言切换
        lang_menu = menubar.addMenu(i18n.tr("menu_language"))
        self._lang_zh_action = QAction(i18n.tr("lang_zh"), self)
        self._lang_zh_action.setCheckable(True)
        self._lang_zh_action.setChecked(i18n.current() == i18n.ZH)
        self._lang_zh_action.triggered.connect(lambda: self._switch_language(i18n.ZH))
        lang_menu.addAction(self._lang_zh_action)

        self._lang_en_action = QAction(i18n.tr("lang_en"), self)
        self._lang_en_action.setCheckable(True)
        self._lang_en_action.setChecked(i18n.current() == i18n.EN)
        self._lang_en_action.triggered.connect(lambda: self._switch_language(i18n.EN))
        lang_menu.addAction(self._lang_en_action)

        # 设置：预览图抽帧间隔
        settings_menu = menubar.addMenu(i18n.tr("menu_settings"))
        interval_menu = settings_menu.addMenu(i18n.tr("menu_thumb_interval"))
        group = QActionGroup(interval_menu)
        group.setExclusive(True)
        for name, _ms in prefs.THUMB_PRESETS:
            act = QAction(i18n.tr(f"thumb_interval_{name}"), interval_menu)
            act.setCheckable(True)
            act.setChecked(prefs.thumb_interval_name() == name)
            act.triggered.connect(lambda checked, n=name: self._set_thumb_interval(n))
            group.addAction(act)
            interval_menu.addAction(act)

        # 设置：预览图精细度
        quality_menu = settings_menu.addMenu(i18n.tr("menu_thumb_quality"))
        qgroup = QActionGroup(quality_menu)
        qgroup.setExclusive(True)
        for name, _w, _h in prefs.THUMB_QUALITIES:
            act = QAction(i18n.tr(f"thumb_quality_{name}"), quality_menu)
            act.setCheckable(True)
            act.setChecked(prefs.thumb_quality_name() == name)
            act.triggered.connect(lambda checked, n=name: self._set_thumb_quality(n))
            qgroup.addAction(act)
            quality_menu.addAction(act)

        # 音频输出设备
        audio_menu = menubar.addMenu(i18n.tr("menu_audio"))
        for name, dev_id in self._player.list_audio_outputs():
            act = QAction(f" {name}", self)
            act.setCheckable(True)
            act.setData(dev_id)
            act.triggered.connect(lambda checked, d=dev_id: self._select_audio_device(d))
            audio_menu.addAction(act)

    def _switch_language(self, lang: str):
        """切换界面语言"""
        i18n.set_language(lang)
        self._lang_zh_action.setChecked(lang == i18n.ZH)
        self._lang_en_action.setChecked(lang == i18n.EN)
        self._retranslate_ui()

    def _retranslate_ui(self):
        """按当前语言刷新所有界面文本"""
        self._update_window_title()
        self._header.setText(i18n.tr("app_name"))
        self._version_label.setToolTip(f"{i18n.tr('app_name')} {__version__}")
        self._search_box.setPlaceholderText(i18n.tr("search_placeholder"))
        self._back_btn.setText(i18n.tr("back_home"))
        self._preload_btn.setToolTip(i18n.tr("preload_tip"))
        self._preload_cancel.setText(i18n.tr("cancel"))
        self._setup_menu()
        if self._search_box.text().strip():
            self._grid.load_search(self._search_box.text())
        else:
            self._grid.load(self._grid.current_group_id)
        self._video_list.retranslate()

    def _update_window_title(self):
        """窗口标题：播放中显示当前文件名"""
        if self._current_video_name:
            self.setWindowTitle(f"{i18n.tr('app_name')} - {self._current_video_name}")
        else:
            self.setWindowTitle(i18n.tr("app_name"))

    # ─── 预览图预加载 ────────────────────────────────────

    def _setup_preload(self):
        """批量生成预览图的状态条

        直接嵌在主窗口底部的状态栏里，**不是浮动窗口**，
        所以不会浮在视频上面挡着看。
        """
        bar = self.statusBar()
        bar.setSizeGripEnabled(False)

        self._preload_text = QLabel("")
        self._preload_bar = QProgressBar()
        self._preload_bar.setRange(0, 100)
        self._preload_bar.setFixedWidth(200)
        self._preload_bar.setTextVisible(True)
        self._preload_cancel = QPushButton(i18n.tr("cancel"))
        self._preload_cancel.setFixedWidth(70)
        self._preload_cancel.clicked.connect(frame_provider.cancel_preload)

        bar.addWidget(self._preload_text, 1)
        bar.addPermanentWidget(self._preload_bar)
        bar.addPermanentWidget(self._preload_cancel)
        bar.setVisible(False)

        frame_provider.signals.strip_progress.connect(self._on_strip_progress)
        frame_provider.signals.strip_finished.connect(self._on_strip_finished)

    def _set_preload_visible(self, visible: bool):
        self.statusBar().setVisible(visible)

    def _set_thumb_interval(self, name: str):
        """切换预览图抽帧间隔"""
        if prefs.thumb_interval_name() == name:
            return
        prefs.set_thumb_interval(name)
        frame_provider.reset_strips()   # 丢弃已加载的雪碧图，改用新间隔
        QMessageBox.information(
            self, i18n.tr("tip"),
            i18n.tr("thumb_interval_changed",
                    name=i18n.tr(f"thumb_interval_{name}")))

    def _set_thumb_quality(self, name: str):
        """切换预览图精细度（画面尺寸 + 抽帧分辨率）"""
        if prefs.thumb_quality_name() == name:
            return
        prefs.set_thumb_quality(name)
        frame_provider.reset_strips()   # 已生成的图尺寸不对，丢弃重建
        # 抽帧分辨率与预览窗尺寸始终一致，这里同步把浮窗也调整过来
        self._toolbar.refresh_preview_size()
        QMessageBox.information(
            self, i18n.tr("tip"),
            i18n.tr("thumb_quality_changed",
                    name=i18n.tr(f"thumb_quality_{name}")))

    def _collect_preload_items(self, collection_id=None):
        """收集要生成预览图的视频：[(路径, 时长毫秒, 显示名), ...]"""
        collections = db.get_all_collections()
        if collection_id is not None:
            collections = [c for c in collections if c["id"] == collection_id]
        items = []
        for col in collections:
            for v in db.get_videos_by_collection(col["id"]):
                items.append((v["file_path"],
                              int((v.get("duration") or 0) * 1000),
                              v["file_name"]))
        return items

    def _preload_all_strips(self, collection_id=None):
        """在后台批量生成预览图；collection_id 为 None 表示整个库"""
        if frame_provider.preload_active():
            self._set_preload_visible(True)      # 已在跑，直接把状态条亮出来
            return
        items = self._collect_preload_items(collection_id)
        if not items:
            QMessageBox.information(self, i18n.tr("tip"), i18n.tr("preload_nothing"))
            return
        self._preload_bar.setVisible(True)
        self._preload_cancel.setVisible(True)
        self._preload_bar.setValue(0)
        self._preload_text.setText(i18n.tr("preload_starting"))
        self._set_preload_visible(True)
        frame_provider.preload_strips(items)

    def _ask_preload(self, collection_id: int, name: str):
        """首次打开视频集时询问是否预生成预览图"""
        reply = QMessageBox.question(
            self, i18n.tr("preload_ask_title"),
            i18n.tr("preload_ask_body", name=name),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._preload_all_strips(collection_id)

    def _on_strip_progress(self, done_videos, total_videos,
                           cur_done, cur_total, name):
        if total_videos <= 0:
            return
        self._set_preload_visible(True)     # 确保状态条处于可见状态
        ratio = (cur_done / cur_total) if cur_total else 0.0
        percent = int(min(1.0, (done_videos + ratio) / total_videos) * 100)
        self._preload_bar.setValue(percent)
        self._preload_text.setText(
            i18n.tr("preload_label", name=name, percent=percent,
                    done=done_videos, total=total_videos))

    def _on_strip_finished(self, completed: bool):
        """生成结束：收起进度条，只留一句结果，几秒后自动消失"""
        self._preload_bar.setVisible(False)
        self._preload_cancel.setVisible(False)
        self._preload_text.setText(
            i18n.tr("preload_done") if completed else i18n.tr("preload_cancelled"))
        QTimer.singleShot(4000, self._hide_preload_bar)

    def _hide_preload_bar(self):
        self._set_preload_visible(False)
        self._preload_bar.setVisible(True)
        self._preload_cancel.setVisible(True)

    def _select_audio_device(self, device_id: str):
        self._player.set_audio_output(device_id)

    def _switch_theme(self, mode: str):
        """切换主题"""
        new_theme = theme.LIGHT if mode == "light" else theme.DARK
        theme.set_theme(new_theme)
        self._dark_action.setChecked(mode == "dark")
        self._light_action.setChecked(mode == "light")
        self._refresh_theme()

    def _refresh_theme(self):
        """刷新所有界面的主题"""
        t = theme.get()
        # 主窗口
        self._update_theme_stylesheets()
        # 菜单栏
        self.menuBar().setStyleSheet(theme.menu_bar_qss(t))
        # 首页标题
        self._header.setStyleSheet(f"color: {t.text};")
        # 版本号
        self._version_label.setStyleSheet(
            f"color: {t.text2}; font-size: 11px;")
        # 搜索框
        self._search_box.setStyleSheet(theme.search_box_qss(t))
        # 返回按钮
        self._back_btn.setStyleSheet(
            f"color: {t.accent}; background: rgba(30,30,30,180); "
            "padding: 6px 12px; border-radius: 6px; font-size: 13px;"
        )
        # 重新加载网格和侧栏
        if self._search_box.text().strip():
            self._grid.load_search(self._search_box.text())
        else:
            self._grid.load()
        self._toolbar.refresh_theme()
        self._video_list.refresh_theme()

    def _load_collections(self):
        self._grid.load()

    def _open_collection(self, collection_id: int, video_id: int | None = None):
        self._playlist = Playlist(collection_id)
        # 补全缺失时长（用于进度百分比）
        fill_missing_durations(collection_id)
        self._video_list.load_collection(collection_id)
        self._stack.setCurrentWidget(self._play_widget)

        target = None
        if video_id is not None:
            target = self._playlist.jump_to(video_id)
        if target is None:
            # 取 last_video_id 对应的视频，没有则从头开始
            collections = db.get_all_collections()
            col = next((c for c in collections if c["id"] == collection_id), None)
            last_id = col.get("last_video_id") if col else None
            if last_id:
                target = self._playlist.jump_to(last_id)
        if not target:
            target = self._playlist.current_video
        if target:
            self._play_video(target["id"])

        # 首次打开该视频集 → 询问是否预生成全部预览图
        collections = db.get_all_collections()
        col = next((c for c in collections if c["id"] == collection_id), None)
        if col and not col.get("thumbs_asked"):
            db.update_collection(collection_id, thumbs_asked=1)
            QTimer.singleShot(
                600,
                lambda cid=collection_id, name=col["name"]: self._ask_preload(cid, name),
            )

    def _play_video_from_search(self, collection_id: int, video_id: int):
        """从搜索结果直接跳转到指定视频播放"""
        self._open_collection(collection_id, video_id)

    def _on_search_changed(self, text: str):
        """搜索框内容变化（防抖：连续输入只在停下来后重建一次）"""
        self._search_text = text
        self._search_debounce.start()

    def _run_search(self):
        """真正执行搜索（由防抖定时器触发）"""
        self._grid.load_search(self._search_text)

    def _set_playback_rate(self, rate: float):
        self._player.set_playback_rate(rate)

    def _on_duration_changed(self, duration_ms: int):
        """播放时把真实时长写回数据库（用于进度百分比）"""
        if self._current_video_id is not None and duration_ms > 0:
            db.update_video(self._current_video_id, duration=duration_ms / 1000.0)

    def _play_video(self, video_id: int):
        if self._playlist is None:
            return

        # 刷新事件队列后保存当前视频进度（确保拿到最新位置）
        QCoreApplication.processEvents()
        self._save_current_position()

        video = self._playlist.jump_to(video_id)
        if video is None:
            return

        self._current_video_id = video_id
        self._current_video_name = video["file_name"]
        # 更新最后观看的集（不覆盖位置）
        db.set_last_video(self._playlist._collection_id, video_id)
        self._toolbar.set_video_source(video["file_path"])
        self._player.set_source(video["file_path"])
        self._player.play()
        self._video_list.set_playing_video(video_id)
        self._update_window_title()

        # 有保存的进度则跳转
        saved_pos = video.get("last_position", 0)
        if saved_pos and saved_pos > 3000:  # 跳过 <3s 的零碎进度
            self._seek_after_load(saved_pos)

    def _on_video_selected(self, video_id: int):
        self._play_video(video_id)

    def _save_current_position(self):
        """保存当前视频的播放位置和最后观看的集"""
        if self._current_video_id is None or self._playlist is None:
            return
        pos = self._player.position()
        if pos > 0:
            db.save_progress(self._current_video_id, self._playlist._collection_id, pos)

    def _seek_after_load(self, position_ms: int):
        """等媒体加载完成后跳转到指定位置（续播）"""
        self._pending_seek = position_ms
        # 如果已经有足够的时长信息，直接跳转
        if self._player.duration() > 0:
            self._player.seek(min(position_ms, self._player.duration()))
            self._pending_seek = 0

    def _on_media_loaded(self):
        """媒体加载完成后执行待跳转（续播）"""
        if self._pending_seek:
            self._player.seek(min(self._pending_seek, self._player.duration()))
            self._pending_seek = 0

    def _on_end_of_media(self):
        """播完自动下一集"""
        self._play_next()

    def _toggle_play(self):
        self._player.toggle_play_pause()

    def _play_previous(self):
        if self._playlist is None:
            return
        prev = self._playlist.previous()
        if prev:
            self._play_video(prev["id"])

    def _play_next(self):
        if self._playlist is None:
            return
        nxt = self._playlist.next()
        if nxt:
            self._play_video(nxt["id"])

    def _seek(self, pos_ms: int):
        # 用户主动跳转 → 作废尚未执行的续播跳转。
        # 否则「刚打开视频就按方向键」时，两个跳转会打架，前进可能被续播覆盖。
        self._pending_seek = 0
        self._player.seek(pos_ms)

    def _toggle_fullscreen(self):
        if self.isFullScreen():
            self.showNormal()
            self._toolbar.set_fullscreen(False)
            self._set_immersive(False)
        else:
            self.showFullScreen()
            self._toolbar.set_fullscreen(True)
            self._set_immersive(True)

    def _exit_fullscreen(self):
        if self.isFullScreen():
            self.showNormal()
            self._toolbar.set_fullscreen(False)
            self._set_immersive(False)

    def _on_playback_state_changed(self, state):
        playing = state == QMediaPlayer.PlaybackState.PlayingState
        self._toolbar.set_playing(playing)

    def _seek_forward(self):
        pos = self._player.position()
        self._seek(min(pos + SEEK_STEP_MS, self._player.duration()))

    def _seek_backward(self):
        pos = self._player.position()
        self._seek(max(pos - SEEK_STEP_MS, 0))

    def _on_video_double_click(self, event):
        self._toggle_fullscreen()

    def _on_player_error(self, msg):
        QMessageBox.warning(self, i18n.tr("playback_error_title"),
                            i18n.tr("playback_error_body", msg=msg))

    def _back_home(self):
        cid = self._playlist._collection_id if self._playlist else None
        QCoreApplication.processEvents()  # 刷新事件队列，拿到最新播放位置
        self._save_current_position()
        self._player.stop()
        self._toolbar.set_video_source("")
        self._current_video_id = None
        self._current_video_name = ""

        # 先把首页显示出来，再做刷新——切换是瞬时的，卡顿不会被感知成「点了没反应」
        self._stack.setCurrentWidget(self._home_widget)
        self._update_window_title()
        self._playlist = None

        if self._search_box.text().strip():
            # 之前在看搜索结果：清空后必须整体重建
            self._search_box.blockSignals(True)
            self._search_box.clear()
            self._search_box.blockSignals(False)
            self._grid.load()
        elif cid is None or not self._grid.refresh_collection(cid):
            # 只有刚看完的那个视频集进度变了。它不在当前视图里（例如在别的分组内）
            # 就什么也不用刷新；重建整页要重新读盘加载所有封面，会有明显卡顿。
            pass

    def _manual_import(self):
        folder = QFileDialog.getExistingDirectory(self, i18n.tr("dlg_choose_folder"))
        if not folder:
            return
        collection_id = import_collection(folder)
        if collection_id and collection_id > 0:
            self._grid.load(self._grid.current_group_id)
        else:
            QMessageBox.information(self, i18n.tr("tip"), i18n.tr("msg_import_skipped"))

    def closeEvent(self, event):
        # 先隐藏窗口：后续收尾即使略慢，也不会出现「点了叉没反应」的假死观感
        self.hide()
        QCoreApplication.processEvents()  # 刷新事件队列，拿到最新播放位置
        self._save_current_position()
        self._player.stop()
        self._player.release()          # 释放 mpv 实例
        frame_provider.shutdown()  # 通知后台抽帧线程退出（不阻塞等待）
        event.accept()
