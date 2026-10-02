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
from app import prefs
from app.video_frames import TILE_H, TILE_W


# ─── 规格 ─────────────────────────────────────────────────
# TILE_W / TILE_H 与抽帧模块共用同一定义（见 app/video_frames.py）
COLS = 10                   # 每行格数
MAX_TILES = 1200            # 硬上限：超长视频会自动放宽间隔，避免雪碧图过大
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
        self._save_lock = threading.Lock()

        # 间隔由用户在「设置 → 预览图间隔」里选，默认 10 秒
        self.interval_ms = prefs.thumb_interval_ms()
        if self.duration_ms <= 0:
            self.count = 0
        else:
            # 向上取整：最后一格必须落在视频长度内，否则取不到帧
            count = (self.duration_ms + self.interval_ms - 1) // self.interval_ms
            if count > MAX_TILES:
                # 超长视频自动放宽间隔，把格数压回上限
                self.interval_ms = (self.duration_ms + MAX_TILES - 1) // MAX_TILES
                count = (self.duration_ms + self.interval_ms - 1) // self.interval_ms
            self.count = max(1, count)

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
                # 支持断点续传：已生成多少格就恢复多少格
                self.done = min(int(meta.get("done", self.count)), self.count)
            return True
        except Exception:
            return False

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


# ─── 磁盘探测（不加载图片，只读元信息，很快）────────────────

def read_meta(video_path: str, duration_ms: int) -> Optional[dict]:
    """读取磁盘上的元信息；不存在或与当前参数不符则返回 None"""
    strip = ThumbStrip(video_path, duration_ms)
    if strip.count <= 0:
        return None
    try:
        with open(strip.meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
    except Exception:
        return None
    if (meta.get("key") == strip.key
            and meta.get("interval_ms") == strip.interval_ms
            and meta.get("count") == strip.count):
        return meta
    return None


def cached_count(video_path: str, duration_ms: int) -> int:
    """磁盘上已生成到第几格；无缓存返回 -1"""
    meta = read_meta(video_path, duration_ms)
    if meta is None:
        return -1
    return int(meta.get("done", 0))


def is_cached(video_path: str, duration_ms: int) -> bool:
    """磁盘上是否已有完整的雪碧图"""
    meta = read_meta(video_path, duration_ms)
    return bool(meta is not None and int(meta.get("done", 0)) >= int(meta.get("count", 0)))
