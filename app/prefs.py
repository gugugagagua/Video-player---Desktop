"""应用偏好设置 - 统一读写 data/settings.ini

语言、预览图精细度等都存在这里，避免多处各开一个 QSettings 导致互相覆盖。
"""

from typing import Any, Dict

from PyQt6.QtCore import QSettings

from app import paths


_settings = QSettings(paths.data_file("settings.ini"), QSettings.Format.IniFormat)


def get(key: str, default: Any = None) -> Any:
    return _settings.value(key, default)


def set_value(key: str, value: Any):
    _settings.setValue(key, value)
    _settings.sync()


def sync():
    _settings.sync()


# ─── 预览图抽帧间隔 ───────────────────────────────────────
THUMB_INTERVAL_KEY = "thumb_interval"

# 档位名 → 抽帧间隔（毫秒）。档位名即间隔秒数，便于展示与翻译。
THUMB_PRESETS = (
    ("5s", 5000),
    ("10s", 10000),
    ("20s", 20000),
    ("30s", 30000),
)
DEFAULT_THUMB_INTERVAL = "10s"

_THUMB_INTERVALS: Dict[str, int] = dict(THUMB_PRESETS)


def thumb_interval_name() -> str:
    """当前间隔档位名"""
    value = get(THUMB_INTERVAL_KEY, DEFAULT_THUMB_INTERVAL)
    return value if value in _THUMB_INTERVALS else DEFAULT_THUMB_INTERVAL


def set_thumb_interval(name: str):
    if name in _THUMB_INTERVALS:
        set_value(THUMB_INTERVAL_KEY, name)


def thumb_interval_ms() -> int:
    """当前档位对应的间隔（毫秒）"""
    return _THUMB_INTERVALS[thumb_interval_name()]
