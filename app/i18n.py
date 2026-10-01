"""国际化 - 简体中文 / English 界面文本

用法：
    from app import i18n
    i18n.init()                      # 启动时调用一次（读取设置或系统语言）
    i18n.tr("menu_file")             # → "文件" / "File"
    i18n.tr("badge_watching", watched=3, total=12)
"""

from typing import Optional

from PyQt6.QtCore import QLocale, QSettings

from app import paths


ZH = "zh"
EN = "en"

# 语言设置文件（与 videos.db 同目录）
_CONFIG_PATH = paths.data_file("settings.ini")
_settings = QSettings(_CONFIG_PATH, QSettings.Format.IniFormat)
_KEY = "language"

_current = ZH


# ─── 文本表 ───────────────────────────────────────────────
# key -> {"zh": ..., "en": ...}
STRINGS: dict = {
    # 通用
    "app_name": {"zh": "视频集播放器", "en": "Video Set Player"},
    "tip": {"zh": "提示", "en": "Notice"},
    "error": {"zh": "错误", "en": "Error"},
    "confirm": {"zh": "确认", "en": "Confirm"},

    # 主窗口 / 菜单
    "search_placeholder": {
        "zh": "搜索视频集或视频文件名…",
        "en": "Search video sets or file names…",
    },
    "back_home": {"zh": "← 返回首页", "en": "← Back to Home"},
    "menu_file": {"zh": "文件", "en": "File"},
    "menu_import": {"zh": "导入视频集", "en": "Import Video Set"},
    "menu_exit": {"zh": "退出", "en": "Exit"},
    "menu_view": {"zh": "视图", "en": "View"},
    "menu_home": {"zh": "返回首页", "en": "Back to Home"},
    "menu_theme": {"zh": "主题", "en": "Theme"},
    "theme_dark": {"zh": " 深色模式", "en": " Dark Mode"},
    "theme_light": {"zh": " 浅色模式", "en": " Light Mode"},
    "menu_audio": {"zh": "音频", "en": "Audio"},
    "menu_language": {"zh": "语言", "en": "Language"},
    "lang_zh": {"zh": "简体中文", "en": "简体中文"},
    "lang_en": {"zh": "English", "en": "English"},

    # 播放错误
    "playback_error_title": {"zh": "播放错误", "en": "Playback Error"},
    "playback_error_body": {
        "zh": "无法播放视频:\n{msg}",
        "en": "Unable to play this video:\n{msg}",
    },

    # 导入
    "dlg_choose_folder": {"zh": "选择视频集文件夹", "en": "Select Video Set Folder"},
    "msg_import_skipped": {
        "zh": "该文件夹已被导入或没有视频文件",
        "en": "This folder is already imported, or it contains no video files",
    },

    # 分组卡片
    "group_count": {"zh": "{n} 个视频集", "en": "{n} video sets"},
    "menu_rename_group": {"zh": "重命名分组", "en": "Rename Group"},
    "menu_set_group_cover": {"zh": "设置分组封面", "en": "Set Group Cover"},
    "menu_delete_group": {"zh": "删除分组", "en": "Delete Group"},

    # 视频集卡片 / 进度角标
    "badge_completed": {"zh": "已看完", "en": "Completed"},
    "badge_watching": {"zh": "看到 {watched}/{total} 集", "en": "Watched {watched}/{total}"},
    "badge_unwatched": {"zh": "未看 {watched}/{total} 集", "en": "Unwatched {watched}/{total}"},
    "menu_rename_collection": {"zh": "重命名视频集", "en": "Rename Video Set"},
    "menu_set_cover": {"zh": "设置封面（从本地图片）", "en": "Set Cover (from local image)"},
    "menu_extract_frame": {"zh": "提取首帧为封面", "en": "Extract First Frame as Cover"},
    "menu_new_group": {"zh": "新建分组（移入此集）", "en": "New Group (move this in)"},
    "menu_move_to_group": {"zh": "移动到分组", "en": "Move to Group"},
    "menu_remove_from_group": {"zh": "移出分组", "en": "Remove from Group"},
    "menu_delete_collection": {"zh": "删除视频集", "en": "Delete Video Set"},

    # 网格导航 / 搜索
    "btn_back_all": {"zh": "返回全部", "en": "Back to All"},
    "btn_import": {"zh": "  导入视频集", "en": "  Import Video Set"},
    "search_title_collections": {"zh": "视频集（{n}）", "en": "Video Sets ({n})"},
    "search_empty_collections": {"zh": "无匹配视频集", "en": "No matching video sets"},
    "search_title_videos": {"zh": "视频（{n}）", "en": "Videos ({n})"},
    "search_empty_videos": {"zh": "无匹配视频", "en": "No matching videos"},

    # 分组操作对话框
    "dlg_new_group": {"zh": "新建分组", "en": "New Group"},
    "dlg_new_group_label": {"zh": "请输入分组名称：", "en": "Enter a group name:"},
    "default_new_group": {"zh": "新分组", "en": "New Group"},
    "dlg_rename_group": {"zh": "重命名分组", "en": "Rename Group"},
    "label_new_name": {"zh": "新名称：", "en": "New name:"},
    "default_group": {"zh": "此分组", "en": "this group"},
    "confirm_delete_group": {
        "zh": "确定要删除分组「{name}」吗？\n分组内的视频集不会被删除。",
        "en": "Delete the group \"{name}\"?\nThe video sets inside will be kept.",
    },

    # 分组封面
    "dlg_set_group_cover": {"zh": "设置分组封面", "en": "Set Group Cover"},
    "dlg_set_group_cover_label": {
        "zh": "选择一个视频集的封面作为分组封面：",
        "en": "Choose a video set cover to use as the group cover:",
    },
    "clear_group_cover": {"zh": "（清除分组封面）", "en": "(Clear group cover)"},
    "msg_group_no_collection": {
        "zh": "该分组内没有视频集，无法选择封面。",
        "en": "This group has no video sets, so a cover cannot be chosen.",
    },
    "msg_collection_no_cover": {
        "zh": "该视频集暂无封面，请先为其设置封面。",
        "en": "This video set has no cover yet. Please set one first.",
    },

    # 视频集操作对话框
    "dlg_confirm_delete": {"zh": "确认删除", "en": "Confirm Delete"},
    "default_collection": {"zh": "此视频集", "en": "this video set"},
    "confirm_delete_collection": {
        "zh": "确定要删除「{name}」吗？\n该操作不可恢复。",
        "en": "Delete \"{name}\"?\nThis cannot be undone.",
    },
    "dlg_rename_collection": {"zh": "重命名视频集", "en": "Rename Video Set"},
    "dlg_choose_cover": {"zh": "选择封面图片", "en": "Choose Cover Image"},
    "filter_images": {
        "zh": "图片文件 (*.png *.jpg *.jpeg *.bmp *.gif)",
        "en": "Image Files (*.png *.jpg *.jpeg *.bmp *.gif)",
    },
    "err_cover_failed": {
        "zh": "封面设置失败，请检查图片文件是否有效。",
        "en": "Failed to set the cover. Please check that the image file is valid.",
    },
    "msg_frame_extracted": {
        "zh": "已从第一集提取首帧作为封面。",
        "en": "The first frame of the first episode was extracted as the cover.",
    },
    "msg_extract_failed": {
        "zh": "提取失败，该视频集没有视频文件。",
        "en": "Extraction failed: this video set contains no video files.",
    },

    # 播放工具栏
    "speed_normal": {"zh": "（正常）", "en": " (normal)"},

    # 播放列表侧栏
    "playlist": {"zh": "播放列表", "en": "Playlist"},
    "refresh": {"zh": "刷新", "en": "Refresh"},
    "status_completed": {"zh": "已看完", "en": "Completed"},
    "status_watching": {"zh": "看到 {time}", "en": "Watched {time}"},
}


# ─── 语言状态 ─────────────────────────────────────────────

def detect_system_language() -> str:
    """按系统区域设置推断默认语言，中文环境用中文，其余用英文"""
    try:
        name = QLocale.system().name()  # 例如 "zh_CN" / "en_US"
        return ZH if name.lower().startswith("zh") else EN
    except Exception:
        return ZH


def load_language() -> str:
    """读取已保存的语言；没有则按系统语言推断"""
    saved = _settings.value(_KEY, None)
    if saved in (ZH, EN):
        return saved
    return detect_system_language()


def save_language(lang: str):
    _settings.setValue(_KEY, lang)
    _settings.sync()


def init() -> str:
    """启动时初始化语言"""
    global _current
    _current = load_language()
    return _current


def set_language(lang: str):
    """切换语言并持久化"""
    global _current
    if lang not in (ZH, EN):
        return
    _current = lang
    save_language(lang)


def current() -> str:
    """返回当前语言代码"""
    return _current


def is_zh() -> bool:
    return _current == ZH


# ─── 取词 ─────────────────────────────────────────────────

def tr(key: str, **kwargs) -> str:
    """按当前语言取文本并填充占位符"""
    entry: Optional[dict] = STRINGS.get(key)
    if not entry:
        return key
    text = entry.get(_current) or entry.get(ZH) or key
    if kwargs:
        try:
            return text.format(**kwargs)
        except (KeyError, IndexError, ValueError):
            return text
    return text
