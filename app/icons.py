"""Material Design 图标 - 参照 D:\\video_player 的图标风格

使用 Qt 内置的 SVG 路径解析（QPainterPath.fromString）在运行时绘制，
无需外部图片资源，可自由着色以适配深浅主题。
"""

from functools import lru_cache

from PyQt6.QtCore import Qt, QByteArray
from PyQt6.QtGui import QIcon, QPixmap, QPainter, QColor
from PyQt6.QtSvg import QSvgRenderer

# Material Icons 路径数据（24x24 视口）
ICON_PATHS = {
    # 播放控制
    "play_arrow": "M8 5v14l11-7z",
    "pause": "M6 19h4V5H6v14zm8-14v14h4V5h-4z",
    "skip_previous": "M6 6h2v12H6zm3.5 6l8.5 6V6z",
    "skip_next": "M6 18l8.5-6L6 6v12zM16 6v12h2V6h-2z",
    "playlist_play": "M3 10h11v2H3zM3 6h11v2H3zM3 14h7v2H3zm13 0v8l6-4z",
    "fullscreen": ("M7 14H5v5h5v-2H7v-3zm-2-4h2V7h3V5H5v5z"
                   "m12 7h3v3h2V5h-5v2zM14 19h5v-5h-2v3h-3v2z"),
    "fullscreen_exit": ("M5 16h3v3h2v-5H5v2zm3-8H5v2h5V5H8v3z"
                        "m6 11h2v3h3v2h-5v-5zm7-3V5h-2v5h5V8h-3z"),
    "fast_forward": "M4 18l8.5-6L4 6v12zm9-12v12l8.5-6L13 6z",
    "volume_up": ("M3 9v6h4l5 5V4L7 9H3zm13.5 3c0-1.77-1.02-3.29-2.5-4.03v8.05c1.48-.73 2.5-2.25 "
                  "2.5-4.02zM14 3.23v2.06c2.89.86 5 3.54 5 6.71s-2.11 5.85-5 6.71v2.06c4.01-.91 7-4.49 "
                  "7-8.77s-2.99-7.86-7-8.77z"),
    "volume_off": ("M16.5 12c0-1.77-1.02-3.29-2.5-4.03v2.21l2.45 2.45c.03-.2.05-.41.05-.63zm2.5 0c0 "
                   ".94-.2 1.82-.54 2.64l1.51 1.51C20.63 14.91 21 13.5 21 12c0-4.28-2.99-7.86-7-8.77v2.06c2.89.86 "
                   "5 3.54 5 6.71zM4.27 3L3 4.27 7.73 9H3v6h4l5 5v-6.73l4.25 4.25c-.67.52-1.42.93-2.25 "
                   "1.18v2.06c1.38-.31 2.63-.95 3.69-1.81L19.73 21 21 19.73l-9-9L4.27 3zM12 4L9.91 6.09 12 8.18V4z"),
    # 导航 / 操作
    "search": ("M15.5 14h-.79l-.28-.27C15.41 12.59 16 11.11 16 9.5 16 5.91 13.09 3 9.5 3S3 5.91 3 9.5 5.91 16 "
               "9.5 16c1.61 0 3.09-.59 4.23-1.57l.27.28v.79l5 4.99L20.49 19l-4.99-5zm-6 0C7.01 14 5 11.99 5 "
               "9.5S7.01 5 9.5 5 14 7.01 14 9.5 11.99 14 9.5 14z"),
    "add": "M19 13h-6v6h-2v-6H5v-2h6V5h2v6h6v2z",
    "close": ("M19 6.41L17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 13.41 17.59 19 19 "
              "17.59 13.41 12z"),
    "arrow_back": "M20 11H7.83l5.59-5.59L12 4l-8 8 8 8 1.41-1.41L7.83 13H20v-2z",
    "refresh": ("M17.65 6.35C16.2 4.9 14.21 4 12 4c-4.42 0-7.99 3.58-8 8s3.58 8 8 8c3.73 0 6.84-2.55 "
                "7.73-6h-2.08c-.82 2.33-3.04 4-5.65 4-3.31 0-6-2.69-6-6s2.69-6 6-6c1.66 0 3.14.69 4.22 "
                "1.78L13 11h7V4l-2.35 2.35z"),
    # 内容
    "folder": ("M10 4H4c-1.1 0-1.99.9-1.99 2L2 18c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V8c0-1.1-.9-2-2-2h-8l-2-2z"),
    "create_new_folder": ("M20 6h-8l-2-2H4c-1.1 0-1.99.9-1.99 2L2 18c0 1.1.9 2 2 2h16c1.1 0 2-.9 "
                          "2-2V8c0-1.1-.9-2-2-2zm-1 8h-3v3h-2v-3h-3v-2h3V9h2v3h3v2z"),
    "movie": ("M18 4l2 4h-3l-2-4h-2l2 4h-3l-2-4H8l2 4H7L5 4H4c-1.1 0-1.99.9-1.99 2L2 18c0 1.1.9 2 2 "
              "2h16c1.1 0 2-.9 2-2V4h-4z"),
    "image": ("M21 19V5c0-1.1-.9-2-2-2H5c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h14c1.1 0 2-.9 2-2zM8.5 "
              "13.5l2.5 3.01L14.5 12l4.5 6H5l3.5-4.5z"),
    "download": "M19 9h-4V3H9v6H5l7 7 7-7zM5 18v2h14v-2H5z",
    # 状态
    "check_circle": ("M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-2 15l-5-5 "
                     "1.41-1.41L10 14.17l7.59-7.59L19 8l-9 9z"),
    "radio_button_unchecked": ("M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm0 "
                               "18c-4.41 0-8-3.59-8-8s3.59-8 8-8 8 3.59 8 8-3.59 8-8 8z"),
    "check": "M9 16.17L4.83 12l-1.42 1.41L9 19 21 7l-1.41-1.41z",
    "edit": ("M3 17.25V21h3.75L17.81 9.94l-3.75-3.75L3 17.25zM20.71 7.04c.39-.39.39-1.02 0-1.41l-2.34-2.34c-.39-.39-1.02-.39-1.41 "
             "0l-1.83 1.83 3.75 3.75 1.83-1.83z"),
    "delete": ("M6 19c0 1.1.9 2 2 2h8c1.1 0 2-.9 2-2V7H6v12zM19 4h-3.5l-1-1h-5l-1 1H5v2h14V4z"),
    "settings": ("M19.14 12.94c.04-.3.06-.61.06-.94 0-.32-.02-.64-.07-.94l2.03-1.58c.18-.14.23-.41.12-.61l-1.92-3.32c-.12-.22-.37-.29-.59-.22l-2.39.96c-.5-.38-1.03-.7-1.62-.94l-.36-2.54c-.04-.24-.24-.41-.48-.41h-3.84c-.24 0-.43.17-.47.41l-.36 2.54c-.59.24-1.13.57-1.62.94l-2.39-.96c-.22-.08-.47 0-.59.22L2.74 8.87c-.12.21-.08.47.12.61l2.03 1.58c-.05.3-.09.63-.09.94s.02.64.07.94l-2.03 1.58c-.18.14-.23.41-.12.61l1.92 3.32c.12.22.37.29.59.22l2.39-.96c.5.38 1.03.7 1.62.94l.36 2.54c.05.24.24.41.48.41h3.84c.24 0 .44-.17.47-.41l.36-2.54c.59-.24 1.13-.56 1.62-.94l2.39.96c.22.08.47 0 .59-.22l1.92-3.32c.12-.22.07-.47-.12-.61l-2.01-1.58zM12 15.6c-1.98 0-3.6-1.62-3.6-3.6s1.62-3.6 3.6-3.6 3.6 1.62 3.6 3.6-1.62 3.6-3.6 3.6z"),
}


def _svg_doc(path_data: str, color: str) -> bytes:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" '
        f'width="24" height="24"><path fill="{color}" d="{path_data}"/></svg>'
    ).encode("utf-8")


@lru_cache(maxsize=512)
def make_icon(name: str, color: str = "#FFFFFF", size: int = 24) -> QIcon:
    """按名称和颜色生成 Material 风格图标（带缓存）"""
    path_data = ICON_PATHS.get(name)
    if not path_data:
        return QIcon()
    pix = QPixmap(size, size)
    pix.fill(Qt.GlobalColor.transparent)
    renderer = QSvgRenderer(QByteArray(_svg_doc(path_data, color)))
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    renderer.render(painter)
    painter.end()
    return QIcon(pix)


def make_pixmap(name: str, color: str = "#FFFFFF", size: int = 24) -> QPixmap:
    """按名称和颜色生成 Material 风格 QPixmap"""
    return make_icon(name, color, size).pixmap(size, size)
