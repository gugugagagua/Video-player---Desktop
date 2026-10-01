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


# ─── 预览图精细度 ─────────────────────────────────────────
THUMB_QUALITY_KEY = "thumb_quality"

# 名称 → 每个视频最多生成多少格缩略图
THUMB_PRESETS = (
    ("fine", 600),
    ("standard", 300),
    ("fast", 150),
)
DEFAULT_THUMB_QUALITY = "standard"

_THUMB_TILES: Dict[str, int] = dict(THUMB_PRESETS)


def thumb_quality() -> str:
    """当前精细度档位名"""
    value = get(THUMB_QUALITY_KEY, DEFAULT_THUMB_QUALITY)
    return value if value in _THUMB_TILES else DEFAULT_THUMB_QUALITY


def set_thumb_quality(name: str):
    if name in _THUMB_TILES:
        set_value(THUMB_QUALITY_KEY, name)


def thumb_max_tiles() -> int:
    """当前档位对应的最大格数"""
    return _THUMB_TILES[thumb_quality()]
