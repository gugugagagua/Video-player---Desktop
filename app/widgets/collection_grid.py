"""视频集网格展示组件 - 支持分组与拖拽排序"""

import os
from typing import List, Optional, Tuple

from PyQt6.QtCore import Qt, pyqtSignal, QTimer, QPoint, QRect, QSize, QEvent
from PyQt6.QtGui import QPixmap, QFont, QAction, QPainter, QColor
from PyQt6.QtWidgets import (
    QWidget, QGridLayout, QLabel, QVBoxLayout, QHBoxLayout,
    QPushButton, QScrollArea, QFileDialog, QMessageBox, QMenu,
    QInputDialog, QLineEdit, QFrame,
)

from app import database as db
from app.video_manager import import_collection
from app.cover_manager import ensure_collection_cover, set_custom_cover
from app import theme
from app import icons
from app import i18n

COVER_W = 184
COVER_H = 230
CARD_W = 200
CARD_H = 310

# 封面缩放结果缓存
#
# 卡片每次重建都要 `QPixmap(封面) + 平滑缩放`，而搜索框每敲一个键就会整页
# 重建一次 —— 同一张封面被反复解码、反复做 SmoothTransformation，完全是白费。
# 按「路径 + 修改时间 + 目标尺寸」做键，图换了自动失效。
# 上限 64 张（184×230×4B ≈ 169 KB/张，约 11 MB）。
_COVER_CACHE: dict = {}
_COVER_CACHE_MAX = 64


def _cover_pixmap(path, width: int, height: int):
    """读取并缩放封面；同一张图只解码一次"""
    if not path or not os.path.exists(path):
        return None
    try:
        key = (path, os.path.getmtime(path), width, height)
    except OSError:
        return None
    cached = _COVER_CACHE.get(key)
    if cached is not None:
        return cached
    src = QPixmap(path)
    if src.isNull():
        return None
    pix = src.scaled(width, height,
                     Qt.AspectRatioMode.KeepAspectRatio,
                     Qt.TransformationMode.SmoothTransformation)
    if len(_COVER_CACHE) >= _COVER_CACHE_MAX:
        _COVER_CACHE.pop(next(iter(_COVER_CACHE)))
    _COVER_CACHE[key] = pix
    return pix


class BaseCard(QWidget):
    """卡片基类"""
    clicked = pyqtSignal(object)

    def __init__(self, name: str, cover_path: Optional[str], parent=None):
        super().__init__(parent)
        self._name = name
        self._cover_path = cover_path
        self._setup_ui()

    def _setup_ui(self):
        t = theme.get()
        self.setFixedSize(CARD_W, CARD_H)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet(f"""
            BaseCard {{
                background: {t.card};
                border-radius: 10px;
                border: 1px solid {t.border};
            }}
            BaseCard:hover {{
                border: 2px solid {t.accent};
                background: #353535;
            }}
        """)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)
        self._cover = QLabel()
        self._cover.setFixedSize(COVER_W, COVER_H)
        self._cover.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._cover.setStyleSheet(f"background: {t.input_bg}; border-radius: 6px;")
        pix = _cover_pixmap(self._cover_path, COVER_W, COVER_H)
        if pix is not None:
            self._cover.setPixmap(pix)
        layout.addWidget(self._cover, alignment=Qt.AlignmentFlag.AlignCenter)
        self._name_label = QLabel(self._name)
        self._name_label.setWordWrap(True)
        self._name_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._name_label.setFont(QFont("Microsoft YaHei", 10, QFont.Weight.Bold))
        self._name_label.setStyleSheet(f"color: {t.text};")
        layout.addWidget(self._name_label)

    def drag_data(self) -> Optional[Tuple[str, int]]:
        """返回 (类型, id) 用于拖拽，子类需重写"""
        return None


class GroupCard(BaseCard):
    """分组文件夹卡片"""
    delete_group_requested = pyqtSignal(int)
    rename_group_requested = pyqtSignal(int)
    setcover_group_requested = pyqtSignal(int)

    def __init__(self, group_id: int, name: str, cover_path: Optional[str], count: int):
        self._group_id = group_id
        self._count = count
        super().__init__(name, cover_path)

    def drag_data(self) -> Optional[Tuple[str, int]]:
        return ("group", self._group_id)

    def _setup_ui(self):
        super()._setup_ui()
        t = theme.get()
        row = QWidget()
        rl = QHBoxLayout(row)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(4)
        rl.addStretch()
        ic = QLabel()
        ic.setPixmap(icons.make_pixmap("folder", t.accent, 14))
        rl.addWidget(ic)
        lbl = QLabel(i18n.tr("group_count", n=self._count))
        lbl.setStyleSheet(f"color: {t.text2}; font-size: 11px;")
        rl.addWidget(lbl)
        rl.addStretch()
        self.layout().addWidget(row)

    def contextMenuEvent(self, event):
        t = theme.get()
        menu = QMenu(self)
        menu.setStyleSheet(f"""
            QMenu {{ background: {t.surface}; color: {t.text}; border: 1px solid {t.border}; }}
            QMenu::item:selected {{ background: {t.accent}; }}
        """)
        rename = QAction(i18n.tr("menu_rename_group"), self)
        rename.triggered.connect(lambda: self.rename_group_requested.emit(self._group_id))
        menu.addAction(rename)
        setcover = QAction(i18n.tr("menu_set_group_cover"), self)
        setcover.triggered.connect(lambda: self.setcover_group_requested.emit(self._group_id))
        menu.addAction(setcover)
        delete = QAction(i18n.tr("menu_delete_group"), self)
        delete.triggered.connect(lambda: self.delete_group_requested.emit(self._group_id))
        menu.addAction(delete)
        menu.exec(event.globalPos())


class CollectionCard(BaseCard):
    """视频集卡片"""
    delete_requested = pyqtSignal(int)
    setcover_requested = pyqtSignal(int)
    extractframe_requested = pyqtSignal(int)
    rename_requested = pyqtSignal(int)
    new_group_with_collection_requested = pyqtSignal(int)
    move_to_group_requested = pyqtSignal(int, int)
    remove_from_group_requested = pyqtSignal(int)

    def __init__(self, collection: dict, stats: Optional[dict] = None):
        self._collection = collection
        self._collection_id = collection["id"]
        self._stats = stats or {"watched": 0, "total": 0, "progress": 0.0}
        super().__init__(collection["name"], collection.get("cover_path"))
        self._setup_progress_badge()

    def _setup_progress_badge(self):
        """在封面底部叠加观看进度角标与进度条"""
        t = theme.get()
        total = self._stats.get("total", 0)
        watched = self._stats.get("watched", 0)
        progress = self._stats.get("progress", 0.0)
        if total <= 0:
            return
        # 状态图标与文字
        if watched >= total:
            icon_name, text, color = "check_circle", i18n.tr("badge_completed"), t.success
        elif watched > 0:
            icon_name, text, color = (
                "play_arrow",
                i18n.tr("badge_watching", watched=watched, total=total),
                t.accent,
            )
        else:
            icon_name, text, color = (
                "radio_button_unchecked",
                i18n.tr("badge_unwatched", watched=0, total=total),
                t.text2,
            )

        self._badge = QFrame(self)
        self._badge.setStyleSheet(
            "background: rgba(0,0,0,170); border-radius: 6px;"
        )
        bl = QHBoxLayout(self._badge)
        bl.setContentsMargins(6, 3, 8, 3)
        bl.setSpacing(4)
        ic = QLabel()
        ic.setPixmap(icons.make_pixmap(icon_name, color, 14))
        bl.addWidget(ic)
        tx = QLabel(text)
        # 角标底色是半透明黑，文字统一用白色才清楚；
        # 状态由左侧图标区分，文字不再跟着变色
        tx.setStyleSheet("color: #FFFFFF; font-size: 11px; background: transparent;")
        bl.addWidget(tx)
        self._badge.adjustSize()
        self._badge.move(16, COVER_H - self._badge.height() + 4)

        # 进度条（封面底部）
        self._progress_bar = QFrame(self)
        self._progress_bar.setFixedHeight(4)
        bar_w = COVER_W - 16
        self._progress_bar.setGeometry(16, COVER_H + 4, bar_w, 4)
        self._progress_bar.setStyleSheet(f"background: {t.border}; border-radius: 2px;")
        inner = QFrame(self._progress_bar)
        color = t.success if watched >= total else t.accent
        inner.setStyleSheet(f"background: {color}; border-radius: 2px;")
        inner.setGeometry(0, 0, int(bar_w * max(0.0, min(1.0, progress))), 4)

    def drag_data(self) -> Optional[Tuple[str, int]]:
        return ("collection", self._collection_id)

    def contextMenuEvent(self, event):
        t = theme.get()
        menu = QMenu(self)
        menu.setStyleSheet(f"""
            QMenu {{ background: {t.surface}; color: {t.text}; border: 1px solid {t.border}; }}
            QMenu::item:selected {{ background: {t.accent}; }}
        """)
        rn = QAction(i18n.tr("menu_rename_collection"), self)
        rn.triggered.connect(lambda: self.rename_requested.emit(self._collection_id))
        menu.addAction(rn)
        menu.addSeparator()
        sc = QAction(i18n.tr("menu_set_cover"), self)
        sc.triggered.connect(lambda: self.setcover_requested.emit(self._collection_id))
        menu.addAction(sc)
        ex = QAction(i18n.tr("menu_extract_frame"), self)
        ex.triggered.connect(lambda: self.extractframe_requested.emit(self._collection_id))
        menu.addAction(ex)
        menu.addSeparator()
        ng = QAction(i18n.tr("menu_new_group"), self)
        ng.triggered.connect(lambda: self.new_group_with_collection_requested.emit(self._collection_id))
        menu.addAction(ng)
        mv = menu.addMenu(i18n.tr("menu_move_to_group"))
        for g in db.get_all_groups():
            a = QAction(g["name"], self)
            a.triggered.connect(lambda checked, gid=g["id"]: self.move_to_group_requested.emit(self._collection_id, gid))
            mv.addAction(a)
        rm = QAction(i18n.tr("menu_remove_from_group"), self)
        rm.triggered.connect(lambda: self.remove_from_group_requested.emit(self._collection_id))
        mv.addAction(rm)
        menu.addSeparator()
        dl = QAction(i18n.tr("menu_delete_collection"), self)
        dl.triggered.connect(lambda: self.delete_requested.emit(self._collection_id))
        menu.addAction(dl)
        menu.exec(event.globalPos())


class DropIndicator(QFrame):
    """拖拽插入位置指示器"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet("background: #1e90ff; border-radius: 2px;")
        self.hide()


class VideoResultRow(QWidget):
    """搜索结果中的视频条目"""
    activated = pyqtSignal(int, int)  # collection_id, video_id

    def __init__(self, video: dict):
        super().__init__()
        self._video = video
        self._collection_id = video["collection_id"]
        self._video_id = video["id"]
        t = theme.get()
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(44)
        self.setStyleSheet(f"""
            VideoResultRow {{
                background: {t.card}; border-radius: 6px;
                border: 1px solid {t.border};
            }}
            VideoResultRow:hover {{ border: 1px solid {t.accent}; }}
        """)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 0, 12, 0)
        icon = QLabel("▶")
        icon.setStyleSheet(f"color: {t.accent}; font-size: 12px;")
        lay.addWidget(icon)
        name = QLabel(video["file_name"])
        name.setStyleSheet(f"color: {t.text}; font-size: 13px;")
        lay.addWidget(name)
        lay.addStretch()
        from_name = QLabel(video.get("collection_name", ""))
        from_name.setStyleSheet(f"color: {t.text2}; font-size: 12px;")
        lay.addWidget(from_name)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.activated.emit(self._collection_id, self._video_id)
        super().mouseReleaseEvent(event)


class CollectionGrid(QScrollArea):
    """支持拖拽排序的视频集网格"""
    collection_selected = pyqtSignal(int)
    video_activated = pyqtSignal(int, int)  # collection_id, video_id

    def __init__(self, parent=None):
        super().__init__(parent)
        self._current_group_id: Optional[int] = None
        # 当前展示的搜索词。窗口尺寸变化时需要按「当前视图」重排，
        # 早先一律调 load()，搜索结果显示 150ms 后就会被根视图覆盖掉。
        self._search_keyword: str = ""
        self._cards: List[BaseCard] = []
        self._resize_timer = QTimer(self)
        self._resize_timer.setSingleShot(True)
        self._resize_timer.setInterval(150)
        self._resize_timer.timeout.connect(self._reload_current)
        # 拖拽状态
        self._drag_active = False
        self._drag_src_data: Optional[Tuple[str, int]] = None
        self._drag_card: Optional[BaseCard] = None
        self._drag_press_pos = QPoint(0, 0)
        self._drag_offset = QPoint(0, 0)
        self._drag_preview: Optional[QLabel] = None
        self._drop_indicator = None
        self._setup_ui()

    @property
    def current_group_id(self):
        return self._current_group_id

    def _setup_ui(self):
        self.setWidgetResizable(True)
        self.viewport().setMouseTracking(True)
        self.viewport().installEventFilter(self)
        t = theme.get()
        self.setStyleSheet(theme.scroll_area_qss(t))
        self._container = QWidget()
        self._layout = QGridLayout(self._container)
        self._layout.setContentsMargins(20, 10, 20, 20)
        self._layout.setSpacing(16)
        self._layout.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.setWidget(self._container)
        self._drop_indicator = DropIndicator(self._container)
        # 拖拽预览浮层（全局，跟随鼠标）
        self._drag_preview = QLabel(self)
        self._drag_preview.setWindowFlags(Qt.WindowType.ToolTip)
        self._drag_preview.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._drag_preview.hide()

    def _cols(self) -> int:
        return max(1, self.width() // 220) if self.width() > 0 else 4

    # ─── 事件过滤：拦截 viewport 鼠标事件 ────────────

    def eventFilter(self, obj, event):
        if obj is self.viewport():
            etype = event.type()
            if etype == QEvent.Type.MouseButtonPress:
                self._on_vp_press(event)
            elif etype == QEvent.Type.MouseMove:
                self._on_vp_move(event)
            elif etype == QEvent.Type.MouseButtonRelease:
                self._on_vp_release(event)
        return super().eventFilter(obj, event)

    def _on_vp_press(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        vp_pos = event.position().toPoint()
        card = self._card_at(vp_pos)
        if card is not None:
            self._drag_active = False
            self._drag_src_data = card.drag_data()
            self._drag_press_pos = vp_pos
            self._drag_card = card
            vp = self.viewport()
            card_top_left = card.mapTo(vp, QPoint(0, 0))
            self._drag_offset = vp_pos - card_top_left

    def _on_vp_move(self, event):
        if not (event.buttons() & Qt.MouseButton.LeftButton):
            return
        global_pos = event.globalPosition().toPoint()
        vp_pos = self.viewport().mapFromGlobal(global_pos)

        if not self._drag_active and self._drag_src_data is not None:
            dist = (vp_pos - self._drag_press_pos).manhattanLength()
            if dist >= 10:
                self._drag_active = True
                self.viewport().grabMouse()
                self._drag_card.hide()
                pix = self._drag_card.grab()
                painter = QPainter(pix)
                painter.fillRect(pix.rect(), QColor(255, 255, 255, 120))
                painter.end()
                self._drag_preview.setPixmap(pix)
                self._drag_preview.adjustSize()
                self._drag_preview.move(global_pos - self._drag_offset)
                self._drag_preview.show()

        if self._drag_active:
            self._drag_preview.move(global_pos - self._drag_offset)
            self._update_drop_indicator(vp_pos)

    def _on_vp_release(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        global_pos = event.globalPosition().toPoint()
        vp_pos = self.viewport().mapFromGlobal(global_pos)
        try:
            self.viewport().releaseMouse()
        except Exception:
            pass
        if self._drag_active:
            target_idx = self._pos_to_item_index(vp_pos)
            if target_idx is not None and self._drag_src_data is not None:
                typ, item_id = self._drag_src_data
                # 判断落在目标卡片的左半还是右半
                if 0 <= target_idx < len(self._cards):
                    card = self._cards[target_idx]
                    vp = self.viewport()
                    card_vp = card.mapTo(vp, QPoint(0, 0))
                    mid_x = card_vp.x() + card.width() // 2
                    if vp_pos.x() >= mid_x:
                        # 落在右半 → 插在后面
                        target_idx += 1
                self._reorder_items(typ, item_id, target_idx)
            # 隐藏预览和指示器，只重排卡片不重建
            self._drag_preview.hide()
            self._drop_indicator.hide()
            self.viewport().setCursor(Qt.CursorShape.ArrowCursor)
            self._drag_card = None
            self._drag_src_data = None
            self._drag_active = False
            self._relayout_cards()
        else:
            card = self._card_at(vp_pos)
            if card is not None:
                card.clicked.emit(card)
        self._drag_src_data = None
        self._drag_active = False

    def _drag_cleanup(self):
        self._drag_active = False
        self._drag_src_data = None
        if self._drag_card:
            self._drag_card.show()
            self._drag_card = None
        self._drag_preview.hide()
        self._drop_indicator.hide()

    def _card_at(self, vp_pos: QPoint) -> Optional[BaseCard]:
        """返回视口坐标下最上层的卡片"""
        vp = self.viewport()
        for card in reversed(self._cards):
            if not card.isVisible():
                continue
            card_vp = card.mapTo(vp, QPoint(0, 0))
            rect = QRect(card_vp, QSize(card.width(), card.height()))
            if rect.contains(vp_pos):
                return card
        return None

    def _pos_to_item_index(self, vp_pos: QPoint) -> Optional[int]:
        """将视口坐标映射到卡片索引（最近卡片）"""
        best_idx = None
        best_dist = float("inf")
        vp = self.viewport()
        for i, card in enumerate(self._cards):
            if not card.isVisible():
                continue
            card_vp = card.mapTo(vp, QPoint(0, 0))
            cx = card_vp.x() + card.width() // 2
            cy = card_vp.y() + card.height() // 2
            d = ((vp_pos.x() - cx) ** 2 + (vp_pos.y() - cy) ** 2) ** 0.5
            if d < best_dist:
                best_dist = d
                best_idx = i
        if best_dist > max(CARD_W, CARD_H) * 1.5:
            return None
        return best_idx

    def _update_drop_indicator(self, vp_pos: QPoint):
        idx = self._pos_to_item_index(vp_pos)
        if idx is None:
            self._drop_indicator.hide()
            return
        if idx < len(self._cards):
            card = self._cards[idx]
            cr = card.geometry()
            vp = self.viewport()
            card_vp = card.mapTo(vp, QPoint(0, 0))
            mid_x = card_vp.x() + card.width() // 2
            if vp_pos.x() >= mid_x:
                # 右半：竖线条在右侧（插在后面）
                self._drop_indicator.setFixedSize(4, cr.height())
                self._drop_indicator.setGeometry(cr.right() - 2, cr.top(), 4, cr.height())
            else:
                # 左半：横线条在上方（插在前面）
                self._drop_indicator.setFixedSize(cr.width(), 4)
                self._drop_indicator.setGeometry(cr.left(), cr.top() - 2, cr.width(), 4)
        else:
            ref = self._cards[-1].geometry() if self._cards else QRect(20, 20, COVER_W, COVER_H)
            self._drop_indicator.setFixedSize(ref.width(), 4)
            self._drop_indicator.setGeometry(ref.left(), ref.bottom() + 8, ref.width(), 4)
        self._drop_indicator.show()

    # ─── 排序持久化 ─────────────────────────────────

    def _relayout_cards(self):
        """根据数据库中的 sort_order 重新排列已有卡片，不销毁重建"""
        if not self._cards:
            return
        cols = self._cols()
        cards_map: dict = {}
        for card in self._cards:
            data = card.drag_data()
            if data:
                cards_map[data] = card
        items = self._build_sorted_items(self._current_group_id)
        # 从布局中取出所有卡片（不移除导航栏和导入按钮）
        for card in self._cards:
            self._layout.removeWidget(card)
        # 按新顺序重新放入布局
        self._cards.clear()
        row = 1
        for i, (_, typ, data) in enumerate(items):
            key = (typ, data["id"])
            card = cards_map.get(key)
            if card is None:
                continue
            self._cards.append(card)
            self._layout.addWidget(card, row + i // cols, i % cols,
                                   alignment=Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
            card.show()
        # 把导入按钮移到最下面
        import_btn = self._layout.itemAtPosition(99, 0)
        if import_btn and import_btn.widget():
            import_btn.widget().show()

    def _reorder_items(self, src_type: str, src_id: int, target_idx: int):
        items = self._build_sorted_items(self._current_group_id)
        src_idx = None
        for i, (_, typ, data) in enumerate(items):
            item_id = data["id"]
            if typ == src_type and item_id == src_id:
                src_idx = i
                break
        if src_idx is None:
            return
        item = items.pop(src_idx)
        if target_idx > src_idx:
            target_idx -= 1
        target_idx = max(0, min(target_idx, len(items)))
        items.insert(target_idx, item)
        group_ids = [(pos, data["id"]) for pos, (_, typ, data) in enumerate(items) if typ == "group"]
        collection_ids = [(pos, data["id"]) for pos, (_, typ, data) in enumerate(items) if typ == "collection"]
        conn = db.get_connection()
        cursor = conn.cursor()
        for pos, gid in group_ids:
            cursor.execute("UPDATE groups SET sort_order = ? WHERE id = ?", (pos, gid))
        for pos, cid in collection_ids:
            cursor.execute("UPDATE collections SET sort_order = ? WHERE id = ?", (pos, cid))
        conn.commit()
        conn.close()

    def _build_sorted_items(self, group_id: Optional[int] = None):
        items: List[Tuple[int, str, dict]] = []
        if group_id is not None:
            for c in db.get_group_collections(group_id):
                so = c.get("sort_order")
                items.append((so if so is not None else 99999, "collection", c))
        else:
            for g in db.get_all_groups():
                so = g.get("sort_order")
                items.append((so if so is not None else 99999, "group", g))
            for c in db.get_ungrouped_collections():
                so = c.get("sort_order")
                items.append((so if so is not None else 99999, "collection", c))
        items.sort(key=lambda x: x[0])
        return items

    # ─── 加载与渲染 ─────────────────────────────────

    def _reload_current(self):
        """按当前视图（搜索结果 / 分组 / 根）重建"""
        if self._search_keyword:
            self.load_search(self._search_keyword)
        else:
            self.load(self._current_group_id)

    def _connect_collection_card(self, card: CollectionCard):
        """统一接线：三处建卡的地方都用它，避免漏掉某个信号"""
        cid = card._collection_id
        card.clicked.connect(lambda _, c=cid: self.collection_selected.emit(c))
        card.delete_requested.connect(self._delete_collection)
        card.setcover_requested.connect(self._set_cover)
        card.extractframe_requested.connect(self._extract_first_frame)
        card.rename_requested.connect(self._rename_collection)
        card.new_group_with_collection_requested.connect(self._create_group)
        card.move_to_group_requested.connect(self._move_to_group)
        card.remove_from_group_requested.connect(self._remove_from_group)

    def refresh_collection(self, collection_id: int) -> bool:
        """只重建某一张视频集卡片（观看进度变了，没必要整页重建）

        返回 False 表示这张卡当前不在视图里（比如它在某个分组内，而此刻停在根层），
        此时也没有任何需要更新的东西。
        """
        for i, card in enumerate(self._cards):
            if card.drag_data() != ("collection", collection_id):
                continue
            collections = db.get_all_collections()
            col = next((c for c in collections if c["id"] == collection_id), None)
            idx = self._layout.indexOf(card)
            if col is None or idx < 0:
                return True
            row, column, rs, cs = self._layout.getItemPosition(idx)
            self._layout.removeWidget(card)
            card.deleteLater()
            stats = db.get_all_collection_stats().get(collection_id)
            new_card = CollectionCard(col, stats)
            self._connect_collection_card(new_card)
            self._cards[i] = new_card
            self._layout.addWidget(
                new_card, row, column, rs, cs,
                alignment=Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
            new_card.show()
            return True
        return False

    def load(self, group_id: Optional[int] = None):
        self._current_group_id = group_id
        self._search_keyword = ""
        self._clear_layout()
        self._cards.clear()
        t = theme.get()
        cols = self._cols()

        nav = QWidget()
        nav_layout = QHBoxLayout(nav)
        nav_layout.setContentsMargins(0, 0, 0, 0)
        if group_id is not None:
            back_btn = QPushButton(i18n.tr("btn_back_all"))
            back_btn.setIcon(icons.make_icon("arrow_back", t.accent, 18))
            back_btn.setStyleSheet(f"""
                QPushButton {{
                    background: transparent; color: {t.accent}; border: none;
                    font-size: 14px; text-align: left; padding: 4px 8px;
                }}
                QPushButton:hover {{ color: {t.accent_hover}; }}
            """)
            back_btn.clicked.connect(self._go_root)
            nav_layout.addWidget(back_btn)
            for g in db.get_all_groups():
                if g["id"] == group_id:
                    title = QLabel(f" / {g['name']}")
                    title.setStyleSheet(f"color: {t.text}; font-size: 14px;")
                    nav_layout.addWidget(title)
                    break
        nav_layout.addStretch()
        self._layout.addWidget(nav, 0, 0, 1, cols)

        row = 1
        stats_map = db.get_all_collection_stats()

        if group_id is None:
            items = self._build_sorted_items()
            for i, (_, typ, data) in enumerate(items):
                if typ == "group":
                    g = data
                    g_cols = db.get_group_collections(g["id"])
                    cover_path = g.get("cover_path")
                    if not cover_path and g_cols:
                        cover_path = g_cols[0].get("cover_path")
                    card = GroupCard(g["id"], g["name"], cover_path, len(g_cols))
                    card.clicked.connect(lambda _, gid=g["id"]: self._enter_group(gid))
                    card.delete_group_requested.connect(self._delete_group)
                    card.rename_group_requested.connect(self._rename_group)
                    card.setcover_group_requested.connect(self._set_group_cover)
                else:
                    card = CollectionCard(data, stats_map.get(data["id"]))
                    self._connect_collection_card(card)
                self._cards.append(card)
                self._layout.addWidget(card, row + i // cols, i % cols,
                                       alignment=Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        else:
            cols_in = db.get_group_collections(group_id)
            for i, col in enumerate(cols_in):
                card = CollectionCard(col, stats_map.get(col["id"]))
                self._connect_collection_card(card)
                self._cards.append(card)
                self._layout.addWidget(card, row + i // cols, i % cols,
                                       alignment=Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)

        import_btn = QPushButton(i18n.tr("btn_import"))
        import_btn.setIcon(icons.make_icon("add", "#FFFFFF", 20))
        import_btn.setFixedHeight(50)
        import_btn.setStyleSheet(theme.import_btn_qss(t))
        import_btn.clicked.connect(self._import_collection)
        self._layout.addWidget(import_btn, 99, 0, 1, cols)

    # ─── 搜索 ─────────────────────────────────

    def load_search(self, keyword: str):
        """显示搜索结果：匹配的视频集 + 匹配的视频文件名"""
        self._current_group_id = None
        self._search_keyword = keyword.strip()
        self._clear_layout()
        self._cards.clear()
        t = theme.get()
        cols = self._cols()
        row = 0

        kw = keyword.strip()
        if not kw:
            self.load(None)
            return

        collections = db.search_collections(kw)
        videos = db.search_videos(kw)
        stats_map = db.get_all_collection_stats()

        # 视频集结果
        title_c = QLabel(i18n.tr("search_title_collections", n=len(collections)))
        title_c.setStyleSheet(f"color: {t.text}; font-size: 15px; font-weight: bold; padding: 4px 0;")
        self._layout.addWidget(title_c, row, 0, 1, cols)
        row += 1
        if collections:
            for i, c in enumerate(collections):
                card = CollectionCard(c, stats_map.get(c["id"]))
                self._connect_collection_card(card)
                self._cards.append(card)
                self._layout.addWidget(card, row + i // cols, i % cols,
                                       alignment=Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
            row += (len(collections) - 1) // cols + 1
        else:
            empty = QLabel(i18n.tr("search_empty_collections"))
            empty.setStyleSheet(f"color: {t.text2}; font-size: 13px; padding: 4px 0;")
            self._layout.addWidget(empty, row, 0, 1, cols)
            row += 1

        row += 1

        # 视频结果
        title_v = QLabel(i18n.tr("search_title_videos", n=len(videos)))
        title_v.setStyleSheet(f"color: {t.text}; font-size: 15px; font-weight: bold; padding: 4px 0;")
        self._layout.addWidget(title_v, row, 0, 1, cols)
        row += 1
        if videos:
            for v in videos:
                row_widget = VideoResultRow(v)
                row_widget.activated.connect(self.video_activated.emit)
                self._layout.addWidget(row_widget, row, 0, 1, cols)
                row += 1
        else:
            empty = QLabel(i18n.tr("search_empty_videos"))
            empty.setStyleSheet(f"color: {t.text2}; font-size: 13px; padding: 4px 0;")
            self._layout.addWidget(empty, row, 0, 1, cols)

    # ─── 分组操作 ─────────────────────────────────

    def _enter_group(self, group_id: int):
        self.load(group_id)

    def _go_root(self):
        self.load(None)

    def _create_group(self, collection_id: Optional[int] = None):
        name, ok = QInputDialog.getText(self, i18n.tr("dlg_new_group"),
                                        i18n.tr("dlg_new_group_label"),
                                        text=i18n.tr("default_new_group"))
        if ok and name.strip():
            gid = db.add_group(name.strip())
            if collection_id is not None:
                db.update_collection(collection_id, group_id=gid)
            self.load(self._current_group_id)

    def _rename_group(self, group_id: int):
        groups = db.get_all_groups()
        g = next((x for x in groups if x["id"] == group_id), None)
        if not g:
            return
        name, ok = QInputDialog.getText(self, i18n.tr("dlg_rename_group"),
                                        i18n.tr("label_new_name"),
                                        text=g["name"], editor=QLineEdit.EchoMode.Normal)
        if ok and name.strip():
            db.rename_group(group_id, name.strip())
            self.load(self._current_group_id)

    def _delete_group(self, group_id: int):
        groups = db.get_all_groups()
        g = next((x for x in groups if x["id"] == group_id), None)
        name = g["name"] if g else i18n.tr("default_group")
        reply = QMessageBox.question(
            self, i18n.tr("dlg_confirm_delete"),
            i18n.tr("confirm_delete_group", name=name),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            db.delete_group(group_id)
            self.load(self._current_group_id)

    def _set_group_cover(self, group_id: int):
        """从组内视频集的封面中选择作为分组封面"""
        g_cols = db.get_group_collections(group_id)
        if not g_cols:
            QMessageBox.information(self, i18n.tr("tip"), i18n.tr("msg_group_no_collection"))
            return
        names = [c["name"] for c in g_cols]
        clear_label = i18n.tr("clear_group_cover")
        choices = [clear_label] + names
        item, ok = QInputDialog.getItem(self, i18n.tr("dlg_set_group_cover"),
                                        i18n.tr("dlg_set_group_cover_label"),
                                        choices, 0, False)
        if not ok:
            return
        if item == clear_label:
            db.set_group_cover(group_id, None)
        else:
            idx = choices.index(item) - 1
            cover = g_cols[idx].get("cover_path")
            if not cover:
                QMessageBox.information(self, i18n.tr("tip"), i18n.tr("msg_collection_no_cover"))
                return
            db.set_group_cover(group_id, cover)
        self.load(self._current_group_id)

    def _move_to_group(self, collection_id: int, group_id: int):
        db.update_collection(collection_id, group_id=group_id)
        self.load(self._current_group_id)

    def _remove_from_group(self, collection_id: int):
        db.update_collection(collection_id, group_id=None)
        self.load(self._current_group_id)

    # ─── 视频集操作 ─────────────────────────────────

    def _delete_collection(self, collection_id: int):
        collections = db.get_all_collections()
        c = next((x for x in collections if x["id"] == collection_id), None)
        name = c["name"] if c else i18n.tr("default_collection")
        reply = QMessageBox.question(
            self, i18n.tr("dlg_confirm_delete"),
            i18n.tr("confirm_delete_collection", name=name),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            db.delete_collection(collection_id)
            self.load(self._current_group_id)

    def _rename_collection(self, collection_id: int):
        collections = db.get_all_collections()
        c = next((x for x in collections if x["id"] == collection_id), None)
        if not c:
            return
        name, ok = QInputDialog.getText(self, i18n.tr("dlg_rename_collection"),
                                        i18n.tr("label_new_name"),
                                        text=c["name"], editor=QLineEdit.EchoMode.Normal)
        if ok and name.strip():
            db.update_collection(collection_id, name=name.strip())
            self.load(self._current_group_id)

    def _set_cover(self, collection_id: int):
        path, _ = QFileDialog.getOpenFileName(self, i18n.tr("dlg_choose_cover"), "",
                                               i18n.tr("filter_images"))
        if not path:
            return
        if set_custom_cover(collection_id, path):
            self.load(self._current_group_id)
        else:
            QMessageBox.warning(self, i18n.tr("error"), i18n.tr("err_cover_failed"))

    def _extract_first_frame(self, collection_id: int):
        if ensure_collection_cover(collection_id):
            self.load(self._current_group_id)
            QMessageBox.information(self, i18n.tr("tip"), i18n.tr("msg_frame_extracted"))
        else:
            QMessageBox.warning(self, i18n.tr("tip"), i18n.tr("msg_extract_failed"))

    def _import_collection(self):
        folder = QFileDialog.getExistingDirectory(self, i18n.tr("dlg_choose_folder"))
        if not folder:
            return
        cid = import_collection(folder)
        if cid and cid > 0:
            if self._current_group_id is not None:
                db.update_collection(cid, group_id=self._current_group_id)
            self.load(self._current_group_id)
        else:
            QMessageBox.information(self, i18n.tr("tip"), i18n.tr("msg_import_skipped"))

    def _clear_layout(self):
        while self._layout.count() > 0:
            item = self._layout.takeAt(0)
            if item is None:
                continue
            w = item.widget()
            if w is not None:
                w.deleteLater()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._resize_timer.start()
