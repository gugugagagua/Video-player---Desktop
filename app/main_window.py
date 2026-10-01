"""主窗口 - 组合各组件构成完整界面"""

from PyQt6.QtCore import Qt, QCoreApplication, QTimer, QEvent
from PyQt6.QtGui import QAction, QFont, QKeySequence, QShortcut
from PyQt6.QtMultimedia import QMediaPlayer
from PyQt6.QtMultimediaWidgets import QVideoWidget
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QStackedWidget, QFileDialog, QMessageBox, QMenuBar, QLabel,
    QLineEdit,
)

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
from app import frame_provider
from app import frame_provider


class MainWindow(QMainWindow):
    """主窗口"""

    def __init__(self):
        super().__init__()
        self._playlist: Playlist | None = None
        self._current_video_id: int | None = None
        self._current_video_name: str = ""
        self._init_db()
        self._setup_theme()
        self._setup_ui()
        self._setup_player()
        self._setup_menu()
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
        video_area = QWidget()
        video_layout = QHBoxLayout(video_area)
        video_layout.setContentsMargins(0, 0, 0, 0)
        video_layout.setSpacing(0)

        self._video_widget = QVideoWidget()
        self._video_widget.setStyleSheet("background: black;")
        self._video_widget.setMinimumSize(800, 450)
        video_layout.addWidget(self._video_widget, 1)

        self._video_list = VideoListWidget()
        self._video_list.video_selected.connect(self._on_video_selected)
        video_layout.addWidget(self._video_list)

        play_layout.addWidget(video_area, 1)

        # 工具栏
        self._toolbar = PlayerToolbar()
        self._toolbar.play_toggled.connect(self._toggle_play)
        self._toolbar.previous_clicked.connect(self._play_previous)
        self._toolbar.next_clicked.connect(self._play_next)
        self._toolbar.seek_requested.connect(self._seek)
        self._toolbar.volume_changed.connect(self._set_volume)
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

        self._stack.addWidget(self._play_widget)

    def _setup_player(self):
        self._player = VideoPlayer(self._video_widget)
        self._player.position_changed.connect(self._toolbar.set_position)
        self._player.duration_changed.connect(self._toolbar.set_duration)
        self._player.duration_changed.connect(self._on_duration_changed)
        self._player.playback_state_changed.connect(self._on_playback_state_changed)
        self._player.error_occurred.connect(self._on_player_error)
        self._player.set_volume(50)

        self._player.player.mediaStatusChanged.connect(self._on_media_status_changed)

        # 双击视频切换全屏
        self._video_widget.mouseDoubleClickEvent = self._on_video_double_click

        self._setup_shortcuts()
        self._setup_boost()

    def _setup_shortcuts(self):
        self._shortcut_space = QShortcut(QKeySequence(Qt.Key.Key_Space), self)
        self._shortcut_space.activated.connect(self._toggle_play)
        self._shortcut_left = QShortcut(QKeySequence(Qt.Key.Key_Left), self)
        self._shortcut_left.activated.connect(self._seek_backward)
        self._shortcut_up = QShortcut(QKeySequence(Qt.Key.Key_Up), self)
        self._shortcut_up.activated.connect(self._volume_up)
        self._shortcut_down = QShortcut(QKeySequence(Qt.Key.Key_Down), self)
        self._shortcut_down.activated.connect(self._volume_down)
        self._shortcut_f = QShortcut(QKeySequence(Qt.Key.Key_F), self)
        self._shortcut_f.activated.connect(self._toggle_fullscreen)
        self._shortcut_esc = QShortcut(QKeySequence(Qt.Key.Key_Escape), self)
        self._shortcut_esc.activated.connect(self._exit_fullscreen)
        # 供搜索框聚焦时临时禁用
        self._play_shortcuts = [
            self._shortcut_space, self._shortcut_left, self._shortcut_up,
            self._shortcut_down, self._shortcut_f, self._shortcut_esc,
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
        return super().eventFilter(obj, event)

    # ─── 长按加速（D / → 触发 2 倍速，松手恢复）─────────

    def _setup_boost(self):
        self._boost_keys = {Qt.Key.Key_D, Qt.Key.Key_Right}
        self._boost_arm_timers: dict = {}
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
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.setInterval(250)  # 长按 250ms 判定为加速
        timer.timeout.connect(lambda k=key: self._activate_boost(k))
        self._boost_arm_timers[key] = timer
        timer.start()

    def _on_boost_release(self, key):
        if key not in self._boost_keys_down:
            return
        self._boost_keys_down.discard(key)
        timer = self._boost_arm_timers.pop(key, None)
        if timer is not None and timer.isActive():
            # 短按：未达到长按阈值
            timer.stop()
            if key == Qt.Key.Key_Right:
                self._seek_forward()
        else:
            # 长按已触发加速 → 全部松开后恢复
            if not self._boost_keys_down:
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
        self._search_box.setPlaceholderText(i18n.tr("search_placeholder"))
        self._back_btn.setText(i18n.tr("back_home"))
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

    def _play_video_from_search(self, collection_id: int, video_id: int):
        """从搜索结果直接跳转到指定视频播放"""
        self._open_collection(collection_id, video_id)

    def _on_search_changed(self, text: str):
        """搜索框内容变化"""
        self._grid.load_search(text)

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
        """等媒体加载完成后跳转到指定位置"""
        self._pending_seek = position_ms
        # 如果已经有足够的时长信息，直接跳转
        if self._player.duration() > 0:
            self._player.seek(min(position_ms, self._player.duration()))

    def _on_media_status_changed(self, status):
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            self._play_next()
        elif status == QMediaPlayer.MediaStatus.LoadedMedia:
            # 媒体加载完成后执行待跳转
            if hasattr(self, '_pending_seek') and self._pending_seek:
                self._player.seek(min(self._pending_seek, self._player.duration()))
                self._pending_seek = 0

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
        self._player.seek(pos_ms)

    def _set_volume(self, vol: int):
        self._player.set_volume(vol)

    def _toggle_fullscreen(self):
        if self.isFullScreen():
            self.showNormal()
            self._toolbar.set_fullscreen(False)
        else:
            self.showFullScreen()
            self._toolbar.set_fullscreen(True)

    def _exit_fullscreen(self):
        if self.isFullScreen():
            self.showNormal()
            self._toolbar.set_fullscreen(False)

    def _on_playback_state_changed(self, state):
        playing = state == QMediaPlayer.PlaybackState.PlayingState
        self._toolbar.set_playing(playing)

    def _seek_forward(self):
        pos = self._player.position()
        self._player.seek(min(pos + 10000, self._player.duration()))

    def _seek_backward(self):
        pos = self._player.position()
        self._player.seek(max(pos - 10000, 0))

    def _volume_up(self):
        vol = min(self._player.volume() + 5, 100)
        self._player.set_volume(vol)

    def _volume_down(self):
        vol = max(self._player.volume() - 5, 0)
        self._player.set_volume(vol)

    def _on_video_double_click(self, event):
        self._toggle_fullscreen()

    def _on_player_error(self, msg):
        QMessageBox.warning(self, i18n.tr("playback_error_title"),
                            i18n.tr("playback_error_body", msg=msg))

    def _back_home(self):
        QCoreApplication.processEvents()  # 刷新事件队列，拿到最新播放位置
        self._save_current_position()
        self._player.stop()
        self._toolbar.set_video_source("")
        self._current_video_id = None
        self._current_video_name = ""
        self._stack.setCurrentWidget(self._home_widget)
        # 清空搜索并刷新网格
        self._search_box.blockSignals(True)
        self._search_box.clear()
        self._search_box.blockSignals(False)
        self._grid.load()
        self._update_window_title()
        self._playlist = None

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
        frame_provider.shutdown()  # 通知后台抽帧线程退出（不阻塞等待）
        event.accept()
