"""纯 OpenCV 抽帧工具 —— **不导入任何 Qt**

为什么要单独一个模块
--------------------
雪碧图的并行生成交给**子进程**执行（OpenCV 解码期间不释放 GIL，
用线程只能拿到约 1.15 倍，用进程实测 3.86 倍）。

子进程走的是 spawn，启动成本取决于要导入多少东西。所以子进程里执行的代码
只能依赖 cv2，绝不能碰到 PyQt6 —— PyQt6 的导入是秒级的，会把并行的收益吃光。
本模块因此保持零 Qt 依赖，与 `thumb_strip` 共享同一套几何规格。

子进程内还会复用解码器：同一部视频被切成多个小任务，它们落到同一个进程时
不必反复打开文件。
"""

from app.lazy_cv2 import cv2   # 延迟加载，别让 cv2 拖慢启动（见该模块说明）


# 单格尺寸的兜底默认值。
# 实际尺寸由用户在「设置 → 预览图精细度」里选（见 prefs.thumb_tile_size），
# 通过参数传进来 —— 这些常量只作为函数签名的默认值，不参与实际决策。
TILE_W = 320
TILE_H = 180
MAX_TILES = 1200            # 硬上限：超长视频会自动放宽间隔，避免雪碧图过大


# ─── 子进程内复用的解码器 ─────────────────────────────────

_cap = None
_cap_path = ""


def _ensure_cap(video_path: str):
    global _cap, _cap_path
    if _cap is not None and _cap_path == video_path and _cap.isOpened():
        return _cap
    release_cap()
    try:
        cap = cv2.VideoCapture(video_path)
    except Exception:
        return None
    if not cap.isOpened():
        try:
            cap.release()
        except Exception:
            pass
        return None
    _cap = cap
    _cap_path = video_path
    return cap


def release_cap():
    """释放本进程缓存的解码器（任务收尾时调用，别一直占着文件句柄）"""
    global _cap, _cap_path
    if _cap is not None:
        try:
            _cap.release()
        except Exception:
            pass
    _cap = None
    _cap_path = ""


def probe_duration_ms(video_path: str) -> int:
    """探测时长（毫秒）；失败返回 0"""
    try:
        cap = cv2.VideoCapture(video_path)
    except Exception:
        return 0
    if not cap.isOpened():
        try:
            cap.release()
        except Exception:
            pass
        return 0
    frames = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    try:
        cap.release()
    except Exception:
        pass
    if frames <= 0 or fps <= 0:
        return 0
    return int(frames / fps * 1000)


def grab_tile_rgb(cap, fps: float, position_ms: int, max_width: int = TILE_W):
    """定位到目标时间点并读出画面，等比缩到 max_width 宽。

    返回 (宽, 高, RGB 原始字节)；失败返回 None。
    只做「等比缩到指定宽度」，不做补边 —— 方框居中留黑由 GUI 侧的
    put_tile 负责，这里保持和以前完全一致的行为。
    """
    frame_no = max(0, int(position_ms / 1000.0 * fps))
    try:
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_no)
        ok, frame = cap.read()
    except Exception:
        return None
    if not ok or frame is None:
        return None
    try:
        h, w = frame.shape[:2]
        if w > max_width:
            scale = max_width / w
            frame = cv2.resize(frame, (max_width, max(1, int(h * scale))),
                               interpolation=cv2.INTER_AREA)
            h, w = frame.shape[:2]
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        return w, h, rgb.tobytes()
    except Exception:
        return None


# ─── 子进程入口 ───────────────────────────────────────────

def extract_tiles(video_path: str, times, start: int, stop: int,
                  tile_w: int = TILE_W):
    """【子进程任务】抽取 [start, stop) 这几格的画面（OpenCV 实现）

    times 是每格的真实时间戳（毫秒），tile_w 是目标单格宽度。
    参数与返回值都必须可 pickle，返回 [(格号, 宽, 高, RGB 字节), ...]。

    这是 PyAV 不可用时的退路：OpenCV 的 set(POS_FRAMES) 会先 seek 到关键帧
    再逐帧解码追到目标帧，慢很多，但结果一致。
    """
    cap = _ensure_cap(video_path)
    if cap is None:
        return []
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    if fps <= 0:
        fps = 25.0
    out = []
    for index in range(int(start), int(stop)):
        item = grab_tile_rgb(cap, fps, int(times[index]), tile_w)
        if item is not None:
            out.append((index, item[0], item[1], item[2]))
    return out
