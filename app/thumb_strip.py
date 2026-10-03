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


# ─── 规格 ─────────────────────────────────────────────────
# 单格尺寸不再固定：由「设置 → 预览图精细度」决定（见 prefs.thumb_tile_size）
COLS = 10                   # 每行格数
FLUSH_EVERY = 30            # 每生成多少格落盘一次


def _fallback_dir() -> str:
    """只读目录下的兜底存放位置"""
    d = os.path.join(paths.data_dir(), "thumbs")
    try:
        os.makedirs(d, exist_ok=True)
    except OSError:
        pass
    return d


def _writable(folder: str) -> bool:
    probe = os.path.join(folder, ".vsp_write_test")
    try:
        with open(probe, "w", encoding="utf-8") as f:
            f.write("")
        os.remove(probe)
        return True
    except OSError:
        return False


def _thumb_dir_for(video_path: str) -> str:
    """缩略图跟视频放在同一个文件夹里

    这样把整个视频集目录拷给别人就能直接复用缓存，不必重新生成。
    目录不可写时（只读盘、光盘、部分网络盘）退回应用数据目录，功能不受影响。
    """
    folder = os.path.dirname(os.path.abspath(video_path))
    if folder and os.path.isdir(folder) and _writable(folder):
        return folder
    return _fallback_dir()


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

        # 缓存就放在视频集文件夹里（见 _thumb_dir_for）
        self._dir = _thumb_dir_for(video_path)
        # 间隔由用户在「设置 → 预览图间隔」里选，默认 10 秒
        self.interval_ms = prefs.thumb_interval_ms()
        # 单格尺寸由「预览图精细度」决定；在这里定格，避免生成中途被改坏
        self.tile_w, self.tile_h = prefs.thumb_tile_size()
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
    def _stem(self) -> str:
        """用视频文件名做前缀，放在视频旁边时一眼能看出是谁的缓存

        文件是否过期不看名字，看元信息里的 key（含大小与修改时间），
        所以视频换内容后会被识别为失效并覆盖重建。
        """
        return os.path.splitext(os.path.basename(self.video_path))[0]

    @property
    def image_path(self) -> str:
        return os.path.join(self._dir, f"{self._stem}.storyboard.jpg")

    @property
    def meta_path(self) -> str:
        return os.path.join(self._dir, f"{self._stem}.storyboard.json")

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
            if (img.width() != self.tile_w * self.cols
                    or img.height() != self.tile_h * max(1, self.rows)):
                self.times = []
                self.count = 0
                self.rows = 0
                stale = True
            else:
                stale = False
                self.image = img
                # 支持断点续传：已生成多少格就恢复多少格
                self.done = min(int(meta.get("done", self.count)), self.count)

        if stale:
            # 尺寸对不上 = 用户改过「预览图精细度」，这对旧图**永远**用不了。
            # 必须删掉：否则它会一直瘫在盘上占着位置，每次启动都白读一遍，
            # 而悬停预览只能退回按需抽帧（慢、且生成期间会被反复覆盖而卡住），
            # 永远等不到雪碧图生效。
            self.remove_files()
            return False
        return True

    def remove_files(self):
        """删掉磁盘上的缓存（尺寸不符 / 需要强制重建时调用）"""
        for path in (self.image_path, self.meta_path):
            try:
                os.remove(path)
            except OSError:
                pass

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
                snapshot = self.image.copy(0, 0, self.tile_w * self.cols,
                                           self.tile_h * max(1, self.rows))
                done = self.done
                self._unflushed = 0
            snapshot.save(self.image_path, "JPEG", 85)
            with open(self.meta_path, "w", encoding="utf-8") as f:
                json.dump({
                    "key": self.key,
                    "interval_ms": self.interval_ms,
                    "count": self.count,
                    "done": done,
                    "tile_w": self.tile_w,
                    "tile_h": self.tile_h,
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
                img = QImage(self.tile_w * self.cols,
                             self.tile_h * max(1, self.rows),
                             QImage.Format.Format_RGB888)
                img.fill(Qt.GlobalColor.black)
                self.image = img
            scaled = tile.scaled(self.tile_w, self.tile_h,
                                 Qt.AspectRatioMode.KeepAspectRatio,
                                 Qt.TransformationMode.SmoothTransformation)
            x = (index % self.cols) * self.tile_w
            y = (index // self.cols) * self.tile_h
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
            x = (index % self.cols) * self.tile_w
            y = (index // self.cols) * self.tile_h
            return QPixmap.fromImage(self.image.copy(x, y,
                                                     self.tile_w, self.tile_h))
