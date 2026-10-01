"""主题管理 - Material 3 浅色/深色主题配色

配色参照 D:\\video_player（Android）的 Material 3 方案：
深色 Primary #BB86FC + Secondary #03DAC6，浅色 Primary #6750A4。
"""

from dataclasses import dataclass
from PyQt6.QtCore import Qt


@dataclass
class Theme:
    """主题配色定义"""
    name: str
    bg: str
    card: str
    card_hover: str
    surface: str
    surface2: str
    text: str
    text2: str
    accent: str
    accent_hover: str
    secondary: str
    success: str
    border: str
    border2: str
    input_bg: str
    slider_groove: str
    slider_handle: str
    slider_sub: str


# ── 深色（Material 3 Dark）─────────────────────────────
DARK = Theme(
    name="dark",
    bg="#121212",
    card="#1E1E1E",
    card_hover="#2A2A2A",
    surface="#2A2A2A",
    surface2="#1A1A1A",
    text="#E1E1E1",
    text2="#9E9E9E",
    accent="#BB86FC",
    accent_hover="#CFA8FF",
    secondary="#03DAC6",
    success="#4CAF50",
    border="#333333",
    border2="#2A2A2A",
    input_bg="#1A1A1A",
    slider_groove="#3A3A3A",
    slider_handle="#BB86FC",
    slider_sub="#BB86FC",
)

# ── 浅色（Material 3 Light）────────────────────────────
LIGHT = Theme(
    name="light",
    bg="#FFFBFE",
    card="#FFFFFF",
    card_hover="#F3EDF7",
    surface="#F3EDF7",
    surface2="#ECE6F0",
    text="#1C1B1F",
    text2="#5F5A66",
    accent="#6750A4",
    accent_hover="#7F67BE",
    secondary="#625B71",
    success="#2E7D32",
    border="#DAD4E0",
    border2="#E7E0EC",
    input_bg="#FFFFFF",
    slider_groove="#DAD4E0",
    slider_handle="#6750A4",
    slider_sub="#6750A4",
)


_current_theme = DARK


def get() -> Theme:
    """获取当前主题"""
    return _current_theme


def set_theme(theme: Theme):
    """设置当前主题"""
    global _current_theme
    _current_theme = theme


def detect_system_theme() -> Theme:
    """检测系统颜色模式"""
    try:
        from PyQt6.QtWidgets import QApplication
        app = QApplication.instance()
        if app is None:
            return DARK
        hints = app.styleHints()
        scheme = hints.colorScheme()
        return DARK if scheme == Qt.ColorScheme.Dark else LIGHT
    except Exception:
        return DARK


def toggle() -> Theme:
    """切换主题"""
    new = LIGHT if _current_theme.name == "dark" else DARK
    set_theme(new)
    return new


def with_alpha(color: str, alpha: float) -> str:
    """把 #RRGGBB / #RGB 转成 rgba(r, g, b, a)，用于半透明配色"""
    c = (color or "").strip().lstrip("#")
    if len(c) == 3:
        c = "".join(ch * 2 for ch in c)
    if len(c) != 6:
        return color
    try:
        r, g, b = int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)
    except ValueError:
        return color
    return f"rgba({r}, {g}, {b}, {alpha:.2f})"


# --- 样式表生成 ---

def scroll_area_qss(t: Theme) -> str:
    return f"""
        QScrollArea {{ border: none; background: {t.bg}; }}
        QScrollBar:vertical {{
            background: transparent; width: 10px; margin: 4px;
        }}
        QScrollBar::handle:vertical {{
            background: {t.border}; border-radius: 5px; min-height: 30px;
        }}
        QScrollBar::handle:vertical:hover {{ background: {t.accent}; }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
        QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
    """


def menu_bar_qss(t: Theme) -> str:
    return f"""
        QMenuBar {{ background: {t.surface}; color: {t.text}; }}
        QMenuBar::item {{ padding: 6px 12px; background: transparent; }}
        QMenuBar::item:selected {{ background: {t.accent}; color: white; border-radius: 4px; }}
        QMenu {{ background: {t.card}; color: {t.text}; border: 1px solid {t.border}; padding: 4px; }}
        QMenu::item {{ padding: 6px 24px; border-radius: 4px; }}
        QMenu::item:selected {{ background: {t.accent}; color: white; }}
        QMenu::separator {{ height: 1px; background: {t.border}; margin: 4px 8px; }}
    """


def header_qss(t: Theme) -> str:
    return f"color: {t.text}; padding: 20px;"


def window_qss(t: Theme) -> str:
    return f"background: {t.bg};"


def card_qss(t: Theme) -> str:
    return f"""
        background: {t.card};
        border-radius: 12px;
        border: 1px solid {t.border};
    """


def card_hover_qss(t: Theme) -> str:
    return f"border: 2px solid {t.accent}; background: {t.card_hover};"


def toolbar_qss(t: Theme) -> str:
    return f"""
        background: {t.surface};
        border-top: 1px solid {t.border2};
    """


def slider_qss(t: Theme) -> str:
    return f"""
        QSlider::groove:horizontal {{
            height: 6px; background: {t.slider_groove}; border-radius: 3px;
        }}
        QSlider::handle:horizontal {{
            width: 14px; height: 14px; margin: -4px 0;
            background: {t.slider_handle}; border-radius: 7px;
        }}
        QSlider::handle:horizontal:hover {{ background: {t.accent_hover}; }}
        QSlider::sub-page:horizontal {{
            background: {t.slider_sub}; border-radius: 3px;
        }}
    """


def btn_style_qss(t: Theme) -> str:
    return f"""
        QPushButton {{
            background: {t.surface}; border: none; border-radius: 8px;
        }}
        QPushButton:hover {{ background: {t.card_hover}; }}
        QPushButton:pressed {{ background: {t.accent}; }}
    """


def import_btn_qss(t: Theme) -> str:
    return f"""
        QPushButton {{
            background: {t.accent}; color: white; border: none;
            border-radius: 10px; font-size: 16px; font-weight: bold;
        }}
        QPushButton:hover {{ background: {t.accent_hover}; }}
    """


def list_widget_qss(t: Theme) -> str:
    """播放列表样式

    行高亮由 VideoRow 自绘，这里一律保持透明——
    否则实心选中色会和行内自设颜色的文字/图标打架。
    """
    return f"""
        QListWidget {{
            background: transparent; border: none; outline: none;
        }}
        QListWidget::item {{
            padding: 0px; margin: 2px 10px; border: none;
            background: transparent;
        }}
        QListWidget::item:selected {{ background: transparent; }}
        QListWidget::item:hover {{ background: transparent; }}
        QScrollBar:vertical {{
            background: transparent; width: 8px; margin: 4px 2px;
        }}
        QScrollBar::handle:vertical {{
            background: {t.border}; border-radius: 4px; min-height: 30px;
        }}
        QScrollBar::handle:vertical:hover {{ background: {t.accent}; }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
        QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
    """


def side_panel_qss(t: Theme) -> str:
    return f"background: {t.surface2}; border-left: 1px solid {t.border2};"


def search_box_qss(t: Theme) -> str:
    return f"""
        QLineEdit {{
            background: {t.card}; color: {t.text};
            border: 1px solid {t.border}; border-radius: 18px;
            padding: 7px 16px; font-size: 13px;
        }}
        QLineEdit:focus {{ border: 1px solid {t.accent}; }}
    """
