"""播放列表侧栏 - 显示当前视频集的所有视频及观看进度"""

from typing import Optional

from PyQt6.QtCore import Qt, QSize, pyqtSignal
from PyQt6.QtGui import QFont, QColor, QPainter
from PyQt6.QtWidgets import (
    QWidget, QListWidget, QListWidgetItem, QAbstractItemView,
    QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame,
)

from app import database as db
from app.video_manager import import_collection
from app import theme
from app import icons
from app import i18n


# 必须与 theme.list_widget_qss 中 QListWidget::item 的上下 margin 一致，
# 否则 setItemWidget 会按扣除 margin 后的高度摆放行控件，导致内容被压缩。
ITEM_MARGIN_V = 2


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


def _qc(hex_color: str, alpha: float = 1.0) -> QColor:
    """十六进制颜色 + 透明度 → QColor"""
    color = QColor(hex_color)
    if not color.isValid():
        color = QColor(128, 128, 128)
    color.setAlphaF(max(0.0, min(1.0, alpha)))
    return color


class _ProgressLine(QFrame):
    """细进度条

    用 QPainter 自绘：setStyleSheet("background: ...") 会向子控件继承样式，
    自绘可以完全避开这个坑，也省去反复重建子控件。
    """

    def __init__(self, ratio: float, color: str, alpha: float = 1.0,
                 track: str = "#808080", track_alpha: float = 0.20, parent=None):
        super().__init__(parent)
        self._ratio = max(0.0, min(1.0, ratio))
        self._color = _qc(color, alpha)
        self._track = _qc(track, track_alpha)
        self.setFixedHeight(3)

    def paintEvent(self, event):
        painter = QPainter(self)
        rect = self.rect()
        painter.fillRect(rect, self._track)
        width = int(rect.width() * self._ratio)
        if width > 0:
            painter.fillRect(0, 0, width, rect.height(), self._color)
        painter.end()


class VideoRow(QWidget):
    """播放列表中的单集条目

    整行的高亮、图标、文字都由本控件统一绘制，避免
    「列表实心选中色」与「行内自设颜色」互相冲突。
    """

    def __init__(self, video: dict, playing: bool, parent=None):
        super().__init__(parent)
        t = theme.get()
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        pos = video.get("last_position") or 0
        dur = video.get("duration") or 0
        watched = _is_watched(pos, dur)

        # ── 按状态决定配色 ────────────────────────
        if playing:
            icon_name, icon_color = "play_arrow", t.accent
            name_color, weight = t.text, QFont.Weight.DemiBold
            row_bg, bar_color = theme.with_alpha(t.accent, 0.15), t.accent
            line_color, line_alpha = t.accent, 1.0
        elif watched:
            icon_name, icon_color = "check_circle", t.success
            name_color, weight = t.text2, QFont.Weight.Normal
            row_bg, bar_color = "transparent", "transparent"
            line_color, line_alpha = t.success, 1.0
        else:
            icon_name, icon_color = "radio_button_unchecked", t.text2
            name_color, weight = t.text2, QFont.Weight.Normal
            row_bg, bar_color = "transparent", "transparent"
            line_color, line_alpha = t.text2, 0.45

        self.setStyleSheet(f"""
            VideoRow {{
                background: {row_bg};
                border-radius: 8px;
            }}
            VideoRow:hover {{
                background: {t.card_hover};
            }}
        """)

        outer = QHBoxLayout(self)
        outer.setContentsMargins(10, 9, 12, 9)
        outer.setSpacing(9)

        # 左侧状态条（非播放态留空占位，保证各行对齐）
        bar = QFrame()
        bar.setFixedWidth(3)
        bar.setStyleSheet(f"background: {bar_color}; border-radius: 2px;")
        outer.addWidget(bar)

        content = QVBoxLayout()
        content.setContentsMargins(0, 0, 0, 0)
        content.setSpacing(5)

        top = QHBoxLayout()
        top.setSpacing(7)

        icon = QLabel()
        icon.setFixedWidth(16)
        icon.setPixmap(icons.make_pixmap(icon_name, icon_color, 15))
        top.addWidget(icon)

        name = QLabel(video["file_name"])
        name.setFont(QFont("Microsoft YaHei", 9, weight))
        name.setStyleSheet(f"color: {name_color}; background: transparent;")
        top.addWidget(name, 1)

        if watched:
            status = i18n.tr("status_completed")
        elif pos > 3000:
            status = i18n.tr("status_watching", time=_fmt(pos))
        else:
            status = ""
        status_label = QLabel(status)
        status_label.setStyleSheet(
            f"color: {t.success if watched else t.text2};"
            "font-size: 11px; background: transparent;"
        )
        top.addWidget(status_label)

        content.addLayout(top)

        # 进度比例
        ratio = 0.0
        if dur and dur > 0:
            ratio = max(0.0, min(1.0, pos / (dur * 1000)))
        elif watched:
            ratio = 1.0

        content.addWidget(_ProgressLine(
            ratio, line_color, line_alpha,
            track=t.text2, track_alpha=0.20,
        ))

        outer.addLayout(content, 1)


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

        # ── 标题栏：标题 + 刷新图标按钮 ──────────────
        header = QWidget()
        header.setFixedHeight(52)
        hl = QHBoxLayout(header)
        hl.setContentsMargins(16, 0, 10, 0)
        hl.setSpacing(4)

        self._title_label = QLabel(i18n.tr("playlist"))
        self._title_label.setFont(QFont("Microsoft YaHei", 12, QFont.Weight.Bold))
        self._title_label.setStyleSheet(f"color: {t.text}; background: transparent;")
        hl.addWidget(self._title_label, 1)

        self._refresh_btn = QPushButton()
        self._refresh_btn.setFixedSize(30, 30)
        self._refresh_btn.setIcon(icons.make_icon("refresh", t.text2, 18))
        self._refresh_btn.setIconSize(QSize(18, 18))
        self._refresh_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._refresh_btn.setToolTip(i18n.tr("refresh"))
        self._refresh_btn.setStyleSheet(theme.btn_style_qss(t))
        self._refresh_btn.clicked.connect(self._refresh)
        hl.addWidget(self._refresh_btn)

        layout.addWidget(header)

        # 分隔线
        self._line = QFrame()
        self._line.setFixedHeight(1)
        self._line.setStyleSheet(f"background: {t.border2};")
        layout.addWidget(self._line)

        self._list = QListWidget()
        self._list.setStyleSheet(theme.list_widget_qss(t))
        self._list.setVerticalScrollMode(
            QAbstractItemView.ScrollMode.ScrollPerPixel
        )
        self._list.currentRowChanged.connect(self._on_row_changed)
        layout.addWidget(self._list)

    def load_collection(self, collection_id: int):
        self._collection_id = collection_id
        self._reload()

    def refresh_theme(self):
        """主题切换后重新应用配色"""
        t = theme.get()
        self.setStyleSheet(theme.side_panel_qss(t))
        self._title_label.setStyleSheet(f"color: {t.text}; background: transparent;")
        self._line.setStyleSheet(f"background: {t.border2};")
        self._refresh_btn.setIcon(icons.make_icon("refresh", t.text2, 18))
        self._refresh_btn.setStyleSheet(theme.btn_style_qss(t))
        self._list.setStyleSheet(theme.list_widget_qss(t))
        if self._collection_id is not None:
            self._reload()

    def retranslate(self):
        """切换语言后刷新文本"""
        self._refresh_btn.setToolTip(i18n.tr("refresh"))
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
            item.setSizeHint(QSize(0, row.sizeHint().height() + ITEM_MARGIN_V * 2))
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
