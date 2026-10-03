"""封面管理 - 提取视频首帧、图片缩放与格式转换"""

import os
from typing import Optional

from app import database as db

# cv2 / PIL 刻意延迟到函数内部导入。
# cv2 加载约 166 ms、PIL 也要几十毫秒，而它们只在「首次给视频集生成封面」
# 这条路用得上；放在模块顶层，会让每一次启动都白付这笔钱
# （collection_grid 在顶层就 import 了本模块）。


def extract_first_frame(video_path: str, output_path: str) -> Optional[str]:
    """提取视频首帧并保存为封面图（保持原分辨率）"""
    import cv2
    from PIL import Image

    cap = cv2.VideoCapture(video_path)
    success, frame = cap.read()
    cap.release()
    if not success:
        return None

    frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    img = Image.fromarray(frame_rgb)
    img.save(output_path, "PNG")
    return output_path


def save_imported_cover(source_path: str, output_path: str) -> Optional[str]:
    """将来源图片原分辨率转为 PNG 保存为 cover.png"""
    try:
        from PIL import Image
        img = Image.open(source_path)
        img.save(output_path, "PNG")
        return output_path
    except Exception:
        return None


def ensure_collection_cover(collection_id: int) -> Optional[str]:
    """确保视频集有 cover.png
    优先级：已有封面 → 文件夹已有 cover.png → 从第一集提取
    """
    collections = db.get_all_collections()
    collection = next((c for c in collections if c["id"] == collection_id), None)
    if not collection:
        return None

    folder_path = collection["folder_path"]
    cover_path = os.path.join(folder_path, "cover.png")

    # 数据库中已有有效路径
    if collection.get("cover_path") and os.path.exists(collection["cover_path"]):
        return collection["cover_path"]

    # 文件夹中已有 cover.png
    if os.path.isfile(cover_path):
        db.update_collection(collection_id, cover_path=cover_path)
        return cover_path

    # 从第一集提取首帧写入 cover.png
    videos = db.get_videos_by_collection(collection_id)
    if not videos:
        return None

    result = extract_first_frame(videos[0]["file_path"], cover_path)
    if result:
        db.update_collection(collection_id, cover_path=cover_path)
    return result


def set_custom_cover(collection_id: int, source_image_path: str) -> Optional[str]:
    """用户手动导入本地图片 → 重命名并转化为 cover.png 保存到视频集文件夹"""
    collections = db.get_all_collections()
    collection = next((c for c in collections if c["id"] == collection_id), None)
    if not collection or not os.path.isfile(source_image_path):
        return None

    cover_path = os.path.join(collection["folder_path"], "cover.png")
    result = save_imported_cover(source_image_path, cover_path)
    if result:
        db.update_collection(collection_id, cover_path=cover_path)
    return result


def ensure_video_cover(video_id: int, collection_id: int) -> Optional[str]:
    """确保单集视频有独立封面（存入 .covers/ 子目录）"""
    videos = db.get_videos_by_collection(collection_id)
    video = next((v for v in videos if v["id"] == video_id), None)
    if not video:
        return None

    if video.get("cover_path") and os.path.exists(video["cover_path"]):
        return video["cover_path"]

    cover_dir = os.path.join(os.path.dirname(video["file_path"]), ".covers")
    os.makedirs(cover_dir, exist_ok=True)
    cover_path = os.path.join(cover_dir, f"video_cover_{video_id}.png")

    result = extract_first_frame(video["file_path"], cover_path)
    if result:
        db.update_video(video_id, cover_path=result)
    return result
