"""视频帧提取 - 为进度条悬停预览提供目标画面

使用 OpenCV 按时间点抽帧，并对 video capture 做缓存以提升响应速度。
"""

from typing import Optional

import cv2
from PyQt6.QtGui import QImage, QPixmap

# 每个视频路径缓存一个 VideoCapture，避免频繁打开文件
_caps: dict = {}
_MAX_CACHE = 4


def _get_cap(video_path: str):
    cap = _caps.get(video_path)
    if cap is not None and cap.isOpened():
        return cap
    # 简单的 LRU：超出上限就释放最早的
    if len(_caps) >= _MAX_CACHE:
        old_key = next(iter(_caps))
        try:
            _caps[old_key].release()
        except Exception:
            pass
        _caps.pop(old_key, None)
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        return None
    _caps[video_path] = cap
    return cap


def grab_frame(video_path: str, position_ms: int, max_width: int = 320) -> Optional[QPixmap]:
    """按时间点（毫秒）提取一帧，返回 QPixmap；失败返回 None"""
    if not video_path:
        return None
    cap = _get_cap(video_path)
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
    if not ok or frame is None:
        return None

    h, w = frame.shape[:2]
    if w > max_width:
        scale = max_width / w
        frame = cv2.resize(frame, (max_width, int(h * scale)), interpolation=cv2.INTER_AREA)
        h, w = frame.shape[:2]

    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    img = QImage(rgb.data, w, h, 3 * w, QImage.Format.Format_RGB888).copy()
    return QPixmap.fromImage(img)


def clear_cache():
    for cap in _caps.values():
        try:
            cap.release()
        except Exception:
            pass
    _caps.clear()
