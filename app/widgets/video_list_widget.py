"""播放列表侧栏 - 显示当前视频集的所有视频及观看进度"""

from typing import Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QWidget, QListWidget, QListWidgetItem, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QFrame,
)

from app import database as db
from app.video_manager import import_collection
from app import theme
from app import icons
from app import i18n


def _fmt(ms: int) -> str:
    s = int(ms // 1000)
    m, sec = divmod(s, 60)
    if m >= 60:
        h, m = divmod(m, 60)
        return f"{h}:{m:02d}:{sec:02d}"
    return f"{m:02d}:{sec:02d}"


def _is_watched(last_position: int, duration: float) -> bool:
    if last_position <= 0:
        return False
    if duration and duration > 0:
        return last_position >= duration * 1000 * 0.95
    return False


class VideoRow(QWidget):
    """播放列表中的单集条目"""

    def __init__(self, video: dict, playing: bool, parent=None):
        super().__init__(parent)
        t = theme.get()
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 6, 12, 6)
        lay.setSpacing(3)

        top = QHBoxLayout()
        top.setSpacing(6)

        pos = video.get("last_position") or 0
        dur = video.get("duration") or 0
        watched = _is_watched(pos, dur)

        if playing:
            icon_name, color = "play_arrow", t.accent
        elif watched:
            icon_name, color = "check_circle", t.success
        else:
            icon_name, color = "radio_button_unchecked", t.text2

        self._icon = QLabel()
        self._icon.setFixedWidth(16)
        self._icon.setPixmap(icons.make_pixmap(icon_name, color, 14))
        top.addWidget(self._icon)

        name = QLabel(video["file_name"])
        name.setStyleSheet(f"color: {t.text if playing else t.text2}; font-size: 13px;")
        name.setWordWrap(False)
        top.addWidget(name, 1)

        # 进度文字
        if watched:
            status = i18n.tr("status_completed")
        elif pos > 3000:
            status = i18n.tr("status_watching", time=_fmt(pos))
        else:
            status = ""
        self._status = QLabel(status)
        self._status.setStyleSheet(f"color: {t.text2}; font-size: 11px;")
        top.addWidget(self._status)

        lay.addLayout(top)

        # 进度条
        bar = QFrame()
        bar.setFixedHeight(3)
        bar.setStyleSheet("background: rgba(128,128,128,90); border-radius: 1px;")
        ratio = 0.0
        if dur and dur > 0:
            ratio = max(0.0, min(1.0, pos / (dur * 1000)))
        elif watched:
            ratio = 1.0
        inner = QFrame(bar)
        inner.setStyleSheet(f"background: {t.success if watched else t.accent}; border-radius: 1px;")
        bar.resizeEvent = lambda e, b=bar, inn=inner, r=ratio: inn.setGeometry(0, 0, int(b.width() * r), 3)
        lay.addWidget(bar)


class VideoListWidget(QWidget):
    """视频列表侧栏"""

    video_selected = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._collection_id: Optional[int] = None
        self._current_playing_id: Optional[int] = None
        self._setup_ui()

    def _setup_ui(self):
        t = theme.get()
        self.setFixedWidth(280)
        self.setStyleSheet(theme.side_panel_qss(t))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._title_label = QLabel(i18n.tr("playlist"))
        self._title_label.setFont(QFont("Microsoft YaHei", 12, QFont.Weight.Bold))
        self._title_label.setStyleSheet(f"color: {t.text}; padding: 12px;")
        self._title_label.setFixedHeight(44)
        layout.addWidget(self._title_label)

        self._refresh_btn = QPushButton(i18n.tr("refresh"))
        self._refresh_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {t.text2}; border: none;
                text-align: left; padding: 6px 12px;
            }}
            QPushButton:hover {{ color: {t.accent}; }}
        """)
        self._refresh_btn.clicked.connect(self._refresh)
        layout.addWidget(self._refresh_btn)

        self._list = QListWidget()
        self._list.setStyleSheet(theme.list_widget_qss(t) + """
            QListWidget::item { padding: 0px; }
        """)
        self._list.currentRowChanged.connect(self._on_row_changed)
        layout.addWidget(self._list)

    def load_collection(self, collection_id: int):
        self._collection_id = collection_id
        self._reload()

    def retranslate(self):
        """切换语言后刷新文本"""
        self._refresh_btn.setText(i18n.tr("refresh"))
        if self._collection_id is None:
            self._title_label.setText(i18n.tr("playlist"))
        else:
            self._reload()

    def _reload(self):
        collection_id = self._collection_id
        if collection_id is None:
            return
        self._list.blockSignals(True)
        self._list.clear()

        collections = db.get_all_collections()
        collection = next((c for c in collections if c["id"] == collection_id), None)
        self._title_label.setText(collection["name"] if collection else i18n.tr("playlist"))

        videos = db.get_videos_by_collection(collection_id)
        for v in videos:
            playing = (v["id"] == self._current_playing_id)
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, v["id"])
            row = VideoRow(v, playing)
            item.setSizeHint(row.sizeHint())
            self._list.addItem(item)
            self._list.setItemWidget(item, row)

        self._list.blockSignals(False)

    def set_playing_video(self, video_id: int):
        """标记当前正在播放的集，并刷新列表状态"""
        self._current_playing_id = video_id
        self._reload()
        self.select_video(video_id)

    def select_video(self, video_id: int):
        for i in range(self._list.count()):
            item = self._list.item(i)
            if item.data(Qt.ItemDataRole.UserRole) == video_id:
                self._list.blockSignals(True)
                self._list.setCurrentRow(i)
                self._list.blockSignals(False)
                break

    def _on_row_changed(self, row: int):
        if row < 0:
            return
        item = self._list.item(row)
        if item:
            video_id = item.data(Qt.ItemDataRole.UserRole)
            if video_id is not None:
                self.video_selected.emit(video_id)

    def _refresh(self):
        if self._collection_id is None:
            return
        collections = db.get_all_collections()
        collection = next((c for c in collections if c["id"] == self._collection_id), None)
        if collection:
            import_collection(collection["folder_path"])
            self._reload()
