"""关键帧索引与按关键帧抽帧（基于 PyAV）

为什么需要这个模块
------------------
进度条悬停预览需要「整段视频按固定间隔的画面」。用 OpenCV 的
`cap.set(CAP_PROP_POS_FRAMES, n)` 定位时，它会先 seek 到目标帧之前的关键帧，
再**逐帧解码追到目标帧**。实测那一步占了单格耗时的 95%：

    单格 492ms ── set() 467ms ／ read() 21ms ／ 缩放+转色 5ms
    顺序解码 2.89~3.89 ms/帧  →  467ms ≈ 解码 162 帧 ≈ 5 秒素材

而 H.264 的关键帧间隔往往接近 10 秒（实测样本 GOP = 9.92 秒），
于是每取一格都要把大半个 GOP 解一遍。

PyAV 能直接在**解复用层**读到包的 keyframe 标志（`packet.is_keyframe`），
完全不解码。于是：

1. 一次解复用扫出全部关键帧时间戳（77MB 影片约 1.4 秒）
2. 每个格子的时间锚定到「离名义时间最近的关键帧」
3. 抽帧时 seek 到该关键帧，**只解这一帧** —— 成本从解码约 162 帧降到 1 帧

实测同一部 23.9 分钟影片、144 格：

    OpenCV（4 进程）    47.9 s
    PyAV（1 线程）      17.9 s
    PyAV（4 线程）      10.2 s

副作用是好的：关键帧时间戳是真实时间，写进元信息后，
预览窗显示的时间与实际画面**严格一致**，不会出现「标签说 60 秒、画面是 55 秒」。

PyAV 不可用时全部退回 OpenCV 的等间隔方案，功能不受影响，只是慢。
"""

import bisect
from typing import List, Optional, Tuple

from app.lazy_cv2 import cv2   # 延迟加载，别让 cv2 拖慢启动（见该模块说明）

from app.video_frames import MAX_TILES, TILE_W


# ─── PyAV 可用性 ───────────────────────────────────────────

_av = None
_av_checked = False


def available() -> bool:
    """PyAV 是否可用。只探测一次。"""
    global _av, _av_checked
    if not _av_checked:
        _av_checked = True
        try:
            import av
            _av = av
        except Exception:
            _av = None
    return _av is not None


def backend_name() -> str:
    return "PyAV 关键帧" if available() else "OpenCV 等间隔"


# ─── 关键帧枚举 ───────────────────────────────────────────

def enumerate_keyframes(video_path: str) -> List[int]:
    """扫出全部关键帧的时间戳（毫秒）。只解复用，不解码。"""
    if not available():
        return []
    times: List[int] = []
    try:
        with _av.open(video_path) as container:
            stream = container.streams.video[0]
            base = stream.time_base
            for packet in container.demux(stream):
                pts = packet.pts
                if pts is None or not packet.is_keyframe:
                    continue
                times.append(int(float(pts * base) * 1000))
    except Exception:
        return []
    return times


def _nearest(sorted_times: List[int], value: int) -> int:
    """返回最接近 value 的下标"""
    pos = bisect.bisect_left(sorted_times, value)
    if pos <= 0:
        return 0
    if pos >= len(sorted_times):
        return len(sorted_times) - 1
    before, after = sorted_times[pos - 1], sorted_times[pos]
    return pos - 1 if value - before <= after - value else pos


def pick_tile_times(keyframes: List[int], interval_ms: int,
                    duration_ms: int) -> List[int]:
    """把每个名义时间点 i*interval 锚定到最近的关键帧

    这样格子的数量与间隔仍由用户的「预览图间隔」设置决定，
    但每格都落在真实关键帧上，可以只解一帧就拿到画面。
    """
    if not keyframes or interval_ms <= 0 or duration_ms <= 0:
        return []
    count = max(1, (duration_ms + interval_ms - 1) // interval_ms)
    if count > MAX_TILES:
        interval_ms = (duration_ms + MAX_TILES - 1) // MAX_TILES
        count = max(1, (duration_ms + interval_ms - 1) // interval_ms)

    picked: List[int] = []
    used = set()
    for i in range(count):
        j = _nearest(keyframes, i * interval_ms)
        if j in used:
            continue
        used.add(j)
        picked.append(int(keyframes[j]))
    return sorted(picked)


def uniform_times(duration_ms: int, interval_ms: int) -> List[int]:
    """退回等间隔方案时使用（PyAV 不可用）"""
    if duration_ms <= 0 or interval_ms <= 0:
        return []
    count = max(1, (duration_ms + interval_ms - 1) // interval_ms)
    if count > MAX_TILES:
        interval_ms = (duration_ms + MAX_TILES - 1) // MAX_TILES
        count = max(1, (duration_ms + interval_ms - 1) // interval_ms)
    return [i * interval_ms for i in range(count)]


def tile_times(video_path: str, duration_ms: int,
               interval_ms: int) -> Tuple[List[int], bool]:
    """给一部视频算出格子时间表。返回 (时间表, 是否为关键帧锚定)"""
    if available():
        kfs = enumerate_keyframes(video_path)
        picked = pick_tile_times(kfs, interval_ms, duration_ms)
        if picked:
            return picked, True
    return uniform_times(duration_ms, interval_ms), False


# ─── 抽帧（子进程任务）────────────────────────────────────

# 子进程内复用的容器：同一部视频的多个分块落到同一进程时不必重新打开
_container = None
_container_path = ""


def _ensure_container(video_path: str):
    global _container, _container_path
    if (_container is not None and _container_path == video_path):
        return _container
    release_container()
    try:
        _container = _av.open(video_path)
        _container_path = video_path
    except Exception:
        _container = None
        _container_path = ""
    return _container


def release_container():
    """释放本进程缓存的容器（收尾时调用，别一直占着文件句柄）"""
    global _container, _container_path
    if _container is not None:
        try:
            _container.close()
        except Exception:
            pass
    _container = None
    _container_path = ""


def _grab_pyav(container, stream, target_ms: int, tile_w: int):
    """seek 到 <= target 的关键帧，只解一帧，返回 (宽, 高, RGB 字节)"""
    container.seek(int(target_ms) * 1000, backward=True)
    for frame in container.decode(stream):
        arr = frame.to_ndarray(format="rgb24")
        h, w = arr.shape[:2]
        if w > tile_w:
            scale = tile_w / w
            arr = cv2.resize(arr, (tile_w, max(1, int(h * scale))),
                             interpolation=cv2.INTER_AREA)
            h, w = arr.shape[:2]
        return w, h, arr.tobytes()
    return None


def extract_tiles(video_path: str, times: List[int],
                  start: int, stop: int, tile_w: int = TILE_W):
    """【子进程任务】抽取 [start, stop) 这几格的画面。

    times 是每格的真实时间戳（毫秒），tile_w 是目标单格宽度（由用户在
    「预览图精细度」里选，作为参数传进来，避免子进程去导入 Qt 相关的 prefs）。
    返回 [(格号, 宽, 高, RGB 字节), ...]。参数与返回值都必须可 pickle。
    PyAV 不可用时自动退回 OpenCV。
    """
    if not available():
        from app.video_frames import extract_tiles as _fallback
        return _fallback(video_path, times, start, stop, tile_w)

    container = _ensure_container(video_path)
    if container is None:
        from app.video_frames import extract_tiles as _fallback
        return _fallback(video_path, times, start, stop, tile_w)

    stream = container.streams.video[0]
    out = []
    for index in range(int(start), int(stop)):
        try:
            item = _grab_pyav(container, stream, times[index], tile_w)
        except Exception:
            item = None
        if item is not None:
            out.append((index, item[0], item[1], item[2]))
    return out


def release():
    """本进程占用的抽帧资源（雪碧图收尾任务调用，避免一直占着文件句柄）"""
    release_container()
    try:
        from app.video_frames import release_cap
        release_cap()
    except Exception:
        pass


def is_available_optional() -> Optional[bool]:
    """给外部展示用；None 表示未探测"""
    return _av is not None if _av_checked else None
