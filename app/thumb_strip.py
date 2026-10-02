"""缩略图雪碧图（storyboard） - 让悬停预览「指哪打哪」

思路与主流视频网站一致：把整段视频抽帧拼成一张大图，
悬停时只从大图里裁一格，微秒级完成，完全不需要解码。

- 首次遇到某个视频时在后台逐格生成，边生成边可用
- 生成完落盘（JPEG + JSON 元信息），以后打开同一视频直接读盘
- 未生成到目标格时，仍回退到按需解码，保证任何位置都有画面

**格子时间不假定等间隔**：抽帧会把每格锚定到离名义时间最近的关键帧
（见 app/keyframes.py），间隔由关键帧分布决定，所以时间表必须如实记录并落盘，
预览时按「时间戳不晚于目标时间的最后一格」查找，标签才能和画面对上。
"""

import bisect
import hashlib
import json
import os
import threading
from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QImage, QPainter, QPixmap

from app import paths
from app import prefs
from app.video_frames import TILE_H, TILE_W


# ─── 规格 ─────────────────────────────────────────────────
# TILE_W / TILE_H 与抽帧模块共用同一定义（见 app/video_frames.py）
COLS = 10                   # 每行格数
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

    def __init__(self, video_path: str, duration_ms: int, times=None):
        self.video_path = video_path
        self.duration_ms = max(0, int(duration_ms))
        self.key = _file_key(video_path)
        self._lock = threading.Lock()
        self._save_lock = threading.Lock()

        # 间隔由用户在「设置 → 预览图间隔」里选，默认 10 秒
        self.interval_ms = prefs.thumb_interval_ms()
        self.cols = COLS
        self.image: Optional[QImage] = None
        self.done = 0               # 已生成到的格号（+1 语义，允许跳格）
        self._unflushed = 0

        # 每格的真实时间戳（毫秒，升序）。等间隔只是特例。
        self.times: list = []
        self.count = 0
        self.rows = 0
        if times:
            self.set_times(times)

    def set_times(self, times):
        """注入格子时间表（毫秒，升序）"""
        self.times = [int(t) for t in times]
        self.count = len(self.times)
        self.rows = (self.count + self.cols - 1) // self.cols if self.count else 0

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
        return self.times[index]

    # ── 磁盘读写（仅工作线程调用）──────────────────────

    def load(self) -> bool:
        """从磁盘恢复（含关键帧时间表），成功返回 True"""
        try:
            with open(self.meta_path, "r", encoding="utf-8") as f:
                meta = json.load(f)
            if (meta.get("key") != self.key
                    or meta.get("interval_ms") != self.interval_ms):
                return False
            times = meta.get("times")
            if not isinstance(times, list) or not times:
                return False        # 早期版本没有时间表 → 丢弃重建
            img = QImage(self.image_path)
            if img.isNull():
                return False
            img = img.convertToFormat(QImage.Format.Format_RGB888)
        except Exception:
            return False

        with self._lock:
            self.set_times(times)
            # 图尺寸必须与新时间表算出的行数一致，否则是别的参数生成的，丢弃
            if (img.width() != TILE_W * self.cols
                    or img.height() != TILE_H * max(1, self.rows)):
                self.times = []
                self.count = 0
                self.rows = 0
                return False
            self.image = img
            # 支持断点续传：已生成多少格就恢复多少格
            self.done = min(int(meta.get("done", self.count)), self.count)
        return True

    def save(self):
        """落盘。

        会被多个分段线程并发调用，这里用非阻塞锁串行化：
        已有线程在写就直接返回（下次再写），避免大家排队等磁盘。
        """
        if not self._save_lock.acquire(blocking=False):
            return
        try:
            with self._lock:
                if self.image is None or self.done <= 0:
                    return
                snapshot = self.image.copy(0, 0, TILE_W * self.cols,
                                           TILE_H * max(1, self.rows))
                done = self.done
                self._unflushed = 0
            snapshot.save(self.image_path, "JPEG", 85)
            with open(self.meta_path, "w", encoding="utf-8") as f:
                json.dump({
                    "key": self.key,
                    "interval_ms": self.interval_ms,
                    "count": self.count,
                    "done": done,
                    "tile_w": TILE_W,
                    "tile_h": TILE_H,
                    "cols": self.cols,
                    # 每格真实时间戳：关键帧锚定后间隔不再均匀，必须落盘
                    "times": self.times,
                }, f)
        except Exception:
            pass
        finally:
            self._save_lock.release()

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
        """裁出「时间戳不晚于目标时间的最后一格」；尚未生成则返回 None

        时间表不一定均匀（关键帧锚定），所以用二分查找而不是除法取整。
        """
        if self.count <= 0:
            return None
        index = bisect.bisect_right(self.times, position_ms) - 1
        index = min(self.count - 1, max(0, index))
        with self._lock:
            if self.image is None or index >= self.done:
                return None
            x = (index % self.cols) * TILE_W
            y = (index // self.cols) * TILE_H
            return QPixmap.fromImage(self.image.copy(x, y, TILE_W, TILE_H))
