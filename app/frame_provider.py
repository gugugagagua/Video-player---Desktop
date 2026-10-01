"""视频帧提取 - 为进度条悬停预览提供目标画面

要点：
- 抽帧全部在后台线程执行，绝不在 GUI 线程里 seek（seek 需回溯关键帧，可能耗时数百毫秒）
- 按 (视频, 秒) 缓存结果，同一秒内重复悬停直接命中
- 只保留最新请求，解码期间到来的旧请求会被丢弃，避免积压
- 返回 QImage（工作线程安全），由 GUI 线程转 QPixmap
"""

import threading
from collections import OrderedDict
from typing import Optional, Tuple

import cv2
from PyQt6.QtCore import QObject, QRunnable, QThreadPool, pyqtSignal
from PyQt6.QtGui import QImage

# 预览图最大宽度
MAX_WIDTH = 240
# 缓存粒度：每 1000ms 一个桶，鼠标细微移动不会重复抽帧
BUCKET_MS = 1000
# 缓存条数上限
CACHE_MAX = 150
# 串行抽帧，避免多个解码器互相抢占
MAX_THREADS = 1


class _Signals(QObject):
    """跨线程结果载体。模块级常驻，避免回调对象被提前回收。"""

    ready = pyqtSignal(object, int)  # (QImage | None, token)


# 供外部 connect
signals = _Signals()

_pool = QThreadPool()
_pool.setMaxThreadCount(MAX_THREADS)

_cache: "OrderedDict[Tuple[str, int], QImage]" = OrderedDict()
_cache_lock = threading.Lock()

_latest_token = 0
_token_lock = threading.Lock()

_stopping = False

# 只保留当前视频的解码器句柄
_cap = None
_cap_path = ""
_cap_lock = threading.Lock()


# ─── 内部：抽帧（工作线程）────────────────────────────────

def _open_cap(video_path: str):
    """取得（必要时重建）解码器句柄，调用方需持有 _cap_lock"""
    global _cap, _cap_path
    if _cap is not None and _cap_path == video_path and _cap.isOpened():
        return _cap
    if _cap is not None:
        try:
            _cap.release()
        except Exception:
            pass
        _cap = None
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        _cap_path = ""
        return None
    _cap = cap
    _cap_path = video_path
    return _cap


def _grab(video_path: str, position_ms: int) -> Optional[QImage]:
    """按时间点抽帧，返回 QImage；失败返回 None。只能在后台线程调用。"""
    if not video_path or _stopping:
        return None
    try:
        with _cap_lock:
            cap = _open_cap(video_path)
            if cap is None:
                return None
            fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
            if fps <= 0:
                fps = 25.0
            frame_no = max(0, int(position_ms / 1000.0 * fps))
            try:
                cap.set(cv2.CAP_PROP_POS_FRAMES, frame_no)
                ok, frame = cap.read()
            except Exception:
                return None
    except Exception:
        return None

    if not ok or frame is None:
        return None

    try:
        h, w = frame.shape[:2]
        if w > MAX_WIDTH:
            scale = MAX_WIDTH / w
            frame = cv2.resize(frame, (MAX_WIDTH, max(1, int(h * scale))),
                               interpolation=cv2.INTER_AREA)
            h, w = frame.shape[:2]
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        return QImage(rgb.data, w, h, 3 * w, QImage.Format.Format_RGB888).copy()
    except Exception:
        return None


class _GrabTask(QRunnable):
    """后台抽帧任务"""

    def __init__(self, video_path: str, position_ms: int, token: int, key: Tuple[str, int]):
        super().__init__()
        self._video_path = video_path
        self._position_ms = position_ms
        self._token = token
        self._key = key

    def run(self):
        if _stopping:
            return
        # 排队期间已被更新的请求取代 → 直接丢弃
        with _token_lock:
            if self._token != _latest_token:
                return

        image = _grab(self._video_path, self._position_ms)

        # 解码期间又来了新请求 → 丢弃本次结果
        with _token_lock:
            if self._token != _latest_token:
                return
        if _stopping:
            return

        if image is not None:
            with _cache_lock:
                _cache[self._key] = image
                _cache.move_to_end(self._key)
                while len(_cache) > CACHE_MAX:
                    _cache.popitem(last=False)

        signals.ready.emit(image, self._token)


# ─── 对外接口 ─────────────────────────────────────────────

def request_frame(video_path: str, position_ms: int, token: int) -> Optional[QImage]:
    """请求一帧。

    命中缓存时直接返回 QImage（同步、极快）；
    否则返回 None，并在后台抽帧完成后通过 signals.ready 回调。
    """
    global _latest_token
    if not video_path or _stopping:
        return None

    key = (video_path, position_ms // BUCKET_MS)
    with _cache_lock:
        cached = _cache.get(key)
        if cached is not None:
            _cache.move_to_end(key)
            return cached

    with _token_lock:
        _latest_token = token

    _pool.start(_GrabTask(video_path, position_ms, token, key))
    return None


def clear_cache():
    """清空缓存并释放解码器句柄"""
    global _cap, _cap_path
    with _cache_lock:
        _cache.clear()
    with _cap_lock:
        if _cap is not None:
            try:
                _cap.release()
            except Exception:
                pass
        _cap = None
        _cap_path = ""


def shutdown():
    """退出前调用：停止抽帧并释放资源"""
    global _stopping
    _stopping = True
    _pool.clear()
    _pool.waitForDone(1500)
    clear_cache()
