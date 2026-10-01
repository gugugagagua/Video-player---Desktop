"""缩略图雪碧图（storyboard） - 让悬停预览「指哪打哪」

思路与主流视频网站一致：把整段视频按固定间隔抽帧，拼成一张大图，
悬停时只从大图里裁一格，微秒级完成，完全不需要解码。

- 首次遇到某个视频时在后台按最低优先级逐格生成，边生成边可用
- 生成完落盘（JPEG + JSON 元信息），以后打开同一视频直接读盘
- 未生成到目标格时，仍回退到按需解码，保证任何位置都有画面
"""

import hashlib
import json
import os
import threading
from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QImage, QPainter, QPixmap

from app import paths


# ─── 规格 ─────────────────────────────────────────────────
TILE_W = 160                # 单格宽
TILE_H = 90                 # 单格高
COLS = 10                   # 每行格数
MAX_TILES = 300             # 单视频最多格数（控制生成耗时与内存）
MIN_INTERVAL_MS = 2000      # 最小间隔
MAX_INTERVAL_MS = 15000     # 最大间隔
FLUSH_EVERY = 30            # 每生成多少格落盘一次


def _thumb_dir() -> str:
    d = os.path.join(paths.data_dir(), "thumbs")
    try:
        os.makedirs(d, exist_ok=True)
    except OSError:
        pass
    return d


def _file_key(video_path: str) -> str:
    """按「路径 + 大小 + 修改时间」生成稳定键，文件一变即失效"""
    try:
        st = os.stat(video_path)
        raw = f"{os.path.abspath(video_path)}|{st.st_size}|{int(st.st_mtime)}"
    except OSError:
        raw = os.path.abspath(video_path)
    return hashlib.sha1(raw.encode("utf-8", "ignore")).hexdigest()[:20]


class ThumbStrip:
    """一部视频的缩略图雪碧图"""

    def __init__(self, video_path: str, duration_ms: int):
        self.video_path = video_path
        self.duration_ms = max(0, int(duration_ms))
        self.key = _file_key(video_path)
        self._lock = threading.Lock()

        if self.duration_ms <= 0:
            self.interval_ms = MIN_INTERVAL_MS
            self.count = 0
        else:
            step = self.duration_ms // MAX_TILES
            self.interval_ms = max(MIN_INTERVAL_MS, min(MAX_INTERVAL_MS, step))
            # 向上取整：最后一格必须落在视频长度内，否则取不到帧
            self.count = min(MAX_TILES,
                             max(1, (self.duration_ms + self.interval_ms - 1)
                                 // self.interval_ms))

        self.cols = COLS
        self.rows = (self.count + self.cols - 1) // self.cols if self.count else 0
        self.image: Optional[QImage] = None
        self.done = 0               # 已按顺序生成的格数
        self._unflushed = 0

    # ── 状态 ────────────────────────────────────────────

    @property
    def image_path(self) -> str:
        return os.path.join(_thumb_dir(), f"{self.key}.jpg")

    @property
    def meta_path(self) -> str:
        return os.path.join(_thumb_dir(), f"{self.key}.json")

    def is_complete(self) -> bool:
        return self.count > 0 and self.done >= self.count

    def time_at(self, index: int) -> int:
        return index * self.interval_ms

    # ── 磁盘读写（仅工作线程调用）──────────────────────

    def load(self) -> bool:
        """从磁盘恢复，成功返回 True"""
        if self.count <= 0:
            return False
        try:
            with open(self.meta_path, "r", encoding="utf-8") as f:
                meta = json.load(f)
            if (meta.get("key") != self.key
                    or meta.get("interval_ms") != self.interval_ms
                    or meta.get("count") != self.count):
                return False
            img = QImage(self.image_path)
            if img.isNull():
                return False
            img = img.convertToFormat(QImage.Format.Format_RGB888)
            if img.width() != TILE_W * self.cols or img.height() < TILE_H:
                return False
            with self._lock:
                self.image = img
                self.done = self.count
            return True
        except Exception:
            return False

    def save(self):
        """落盘。可直接从工作线程调用。"""
        with self._lock:
            if self.image is None or self.done <= 0:
                return
            snapshot = self.image.copy(0, 0, TILE_W * self.cols,
                                       TILE_H * max(1, self.rows))
            self._unflushed = 0
        try:
            snapshot.save(self.image_path, "JPEG", 85)
            with open(self.meta_path, "w", encoding="utf-8") as f:
                json.dump({
                    "key": self.key,
                    "interval_ms": self.interval_ms,
                    "count": self.count,
                    "tile_w": TILE_W,
                    "tile_h": TILE_H,
                    "cols": self.cols,
                }, f)
        except Exception:
            pass

    # ── 写入（仅工作线程调用）──────────────────────────

    def put_tile(self, index: int, tile: QImage) -> bool:
        """把一格画进雪碧图。返回是否需要落盘。"""
        if index < 0 or index >= self.count or tile is None or tile.isNull():
            return False
        with self._lock:
            if self.image is None:
                img = QImage(TILE_W * self.cols, TILE_H * max(1, self.rows),
                             QImage.Format.Format_RGB888)
                img.fill(Qt.GlobalColor.black)
                self.image = img
            scaled = tile.scaled(TILE_W, TILE_H,
                                 Qt.AspectRatioMode.KeepAspectRatio,
                                 Qt.TransformationMode.FastTransformation)
            x = (index % self.cols) * TILE_W
            y = (index // self.cols) * TILE_H
            painter = QPainter(self.image)
            painter.drawImage(x, y, scaled)
            painter.end()
            if index + 1 > self.done:
                self.done = index + 1
            self._unflushed += 1
            return self._unflushed >= FLUSH_EVERY

    # ── 读取（仅 GUI 线程调用）─────────────────────────

    def tile(self, position_ms: int) -> Optional[QPixmap]:
        """裁出目标时间所在的格子；尚未生成则返回 None"""
        if self.count <= 0:
            return None
        index = min(self.count - 1, max(0, position_ms // self.interval_ms))
        with self._lock:
            if self.image is None or index >= self.done:
                return None
            x = (index % self.cols) * TILE_W
            y = (index // self.cols) * TILE_H
            return QPixmap.fromImage(self.image.copy(x, y, TILE_W, TILE_H))
