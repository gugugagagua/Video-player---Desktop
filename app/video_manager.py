"""视频集管理 - 导入文件夹识别视频文件"""

import os
import re
from typing import List, Optional

from app import database as db


# 支持的视频文件扩展名
VIDEO_EXTENSIONS = {
    ".mp4", ".avi", ".mkv", ".mov", ".wmv", ".flv",
    ".webm", ".m4v", ".mpg", ".mpeg", ".3gp",
}

# 唯一的封面图片文件名（只能有一个 cover.png）
COVER_FILENAME = "cover.png"


def _natural_key(s: str):
    """自然排序键：'02_abc' < '10_xyz'"""
    return [int(c) if c.isdigit() else c.lower() for c in re.split(r'(\d+)', s)]


def is_video_file(filename: str) -> bool:
    """判断是否为视频文件"""
    _, ext = os.path.splitext(filename)
    return ext.lower() in VIDEO_EXTENSIONS


def is_cover_image(filename: str) -> bool:
    """判断是否为 cover.png"""
    return filename.lower() == COVER_FILENAME


def scan_folder_for_videos(folder_path: str) -> List[str]:
    """扫描文件夹中的视频文件，返回排序后的视频路径列表"""
    videos = []
    if not os.path.isdir(folder_path):
        return videos
    for entry in os.scandir(folder_path):
        if entry.is_file() and is_video_file(entry.name):
            videos.append(entry.path)
    videos.sort(key=lambda p: _natural_key(os.path.basename(p)))
    return videos


def find_cover_in_folder(folder_path: str) -> Optional[str]:
    """在文件夹中查找约定的封面图片"""
    for entry in os.scandir(folder_path):
        if entry.is_file() and is_cover_image(entry.name):
            return entry.path
    return None


def import_collection(folder_path: str) -> Optional[int]:
    """导入一个视频集：扫描文件夹、建库、返回 collection_id"""
    if not os.path.isdir(folder_path):
        return None

    folder_name = os.path.basename(folder_path)
    cover_path = find_cover_in_folder(folder_path)
    collection_id = db.add_collection(folder_name, folder_path, cover_path)

    if not collection_id or collection_id <= 0:
        # 已存在 → 重新排序（更新 sort_order）
        collections = db.get_all_collections()
        existing_id = None
        for col in collections:
            if col["folder_path"] == folder_path:
                existing_id = col["id"]
                break
        if existing_id is None:
            return None
        _resort_collection(existing_id, folder_path)
        return existing_id

    videos = scan_folder_for_videos(folder_path)
    for order, vpath in enumerate(videos):
        filename = os.path.basename(vpath)
        db.add_video(collection_id, filename, vpath, order)

    return collection_id


def _resort_collection(collection_id: int, folder_path: str):
    """用自然排序重新设置已有视频集的 sort_order"""
    videos = scan_folder_for_videos(folder_path)
    for order, vpath in enumerate(videos):
        filename = os.path.basename(vpath)
        # 查找匹配的 video 记录并更新 sort_order
        existing = db.get_videos_by_collection(collection_id)
        for v in existing:
            if v["file_path"] == vpath or v["file_name"] == filename:
                db.update_video(v["id"], sort_order=order)
                break


def probe_duration(video_path: str) -> float:
    """用 OpenCV 读取视频时长（秒），失败返回 0"""
    try:
        import cv2
        cap = cv2.VideoCapture(video_path)
        fps = cap.get(cv2.CAP_PROP_FPS) or 0
        frames = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
        cap.release()
        if fps > 0 and frames > 0:
            return frames / fps
    except Exception:
        pass
    return 0.0


def fill_missing_durations(collection_id: int):
    """补全该视频集中缺失时长的视频（用于准确计算进度）"""
    videos = db.get_videos_by_collection(collection_id)
    changed = False
    for v in videos:
        if not v.get("duration"):
            d = probe_duration(v["file_path"])
            if d > 0:
                db.update_video(v["id"], duration=d)
                changed = True
    return changed
