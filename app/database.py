"""SQLite 数据库管理 - 视频集信息持久化"""

import sqlite3
import os
from typing import Optional

from app import paths


DB_PATH = paths.data_file("videos.db")


def get_connection() -> sqlite3.Connection:
    """获取数据库连接（自动创建目录和表）"""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """初始化数据库表结构"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS groups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            sort_order INTEGER DEFAULT 0,
            cover_path TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    try:
        cursor.execute("ALTER TABLE groups ADD COLUMN cover_path TEXT")
    except sqlite3.OperationalError:
        pass
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS collections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            folder_path TEXT NOT NULL UNIQUE,
            cover_path TEXT,
            group_id INTEGER DEFAULT NULL,
            last_video_id INTEGER DEFAULT NULL,
            sort_order INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (group_id) REFERENCES groups(id) ON DELETE SET NULL
        )
    """)
    try:
        cursor.execute("ALTER TABLE collections ADD COLUMN last_video_id INTEGER DEFAULT NULL")
    except sqlite3.OperationalError:
        pass
    try:
        cursor.execute("ALTER TABLE collections ADD COLUMN group_id INTEGER DEFAULT NULL")
    except sqlite3.OperationalError:
        pass
    try:
        cursor.execute("ALTER TABLE collections ADD COLUMN sort_order INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS videos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            collection_id INTEGER NOT NULL,
            file_name TEXT NOT NULL,
            file_path TEXT NOT NULL UNIQUE,
            duration REAL DEFAULT 0,
            cover_path TEXT,
            sort_order INTEGER DEFAULT 0,
            last_position INTEGER DEFAULT 0,
            FOREIGN KEY (collection_id) REFERENCES collections(id) ON DELETE CASCADE
        )
    """)
    # 兼容旧数据库：如果 last_position 列不存在则添加
    try:
        cursor.execute("ALTER TABLE videos ADD COLUMN last_position INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass
    conn.commit()
    conn.close()


def add_collection(name: str, folder_path: str, cover_path: Optional[str] = None) -> int:
    """添加视频集"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT OR IGNORE INTO collections (name, folder_path, cover_path) VALUES (?, ?, ?)",
        (name, folder_path, cover_path),
    )
    conn.commit()
    collection_id = cursor.lastrowid
    conn.close()
    return collection_id


def update_collection(collection_id: int, **kwargs):
    """更新视频集信息"""
    allowed = {"name", "folder_path", "cover_path", "last_video_id", "group_id"}
    fields = {k: v for k, v in kwargs.items() if k in allowed}
    if not fields:
        return
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    values = [v for v in fields.values()]
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        f"UPDATE collections SET {set_clause}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (*values, collection_id),
    )
    conn.commit()
    conn.close()


def delete_collection(collection_id: int):
    """删除视频集及其视频记录"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM videos WHERE collection_id = ?", (collection_id,))
    cursor.execute("DELETE FROM collections WHERE id = ?", (collection_id,))
    conn.commit()
    conn.close()


def get_all_collections() -> list:
    """获取所有视频集"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM collections ORDER BY updated_at DESC")
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def add_video(collection_id: int, file_name: str, file_path: str, sort_order: int = 0) -> int:
    """添加视频记录"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT OR IGNORE INTO videos (collection_id, file_name, file_path, sort_order) VALUES (?, ?, ?, ?)",
        (collection_id, file_name, file_path, sort_order),
    )
    conn.commit()
    video_id = cursor.lastrowid
    conn.close()
    return video_id


def update_video(video_id: int, **kwargs):
    """更新视频信息"""
    allowed = {"duration", "cover_path", "sort_order"}
    fields = {k: v for k, v in kwargs.items() if k in allowed}
    if not fields:
        return
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(f"UPDATE videos SET {set_clause} WHERE id = ?", (*fields.values(), video_id))
    conn.commit()
    conn.close()


def update_video_position(video_id: int, position_ms: int):
    """保存视频播放进度"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE videos SET last_position = ? WHERE id = ?", (position_ms, video_id))
    conn.commit()
    conn.close()


def save_progress(video_id: int, collection_id: int, position_ms: int):
    """同时保存播放进度和最后观看的集（单连接事务）"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE videos SET last_position = ? WHERE id = ?", (position_ms, video_id))
    cursor.execute("UPDATE collections SET last_video_id = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                   (video_id, collection_id))
    conn.commit()
    conn.close()


def set_last_video(collection_id: int, video_id: int):
    """只更新最后观看的集，不改变播放进度"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE collections SET last_video_id = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                   (video_id, collection_id))
    conn.commit()
    conn.close()


# ─── 分组操作 ─────────────────────────────────────────

def add_group(name: str) -> int:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("INSERT INTO groups (name) VALUES (?)", (name,))
    conn.commit()
    gid = cursor.lastrowid
    conn.close()
    return gid


def rename_group(group_id: int, name: str):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE groups SET name = ? WHERE id = ?", (name, group_id))
    conn.commit()
    conn.close()


def set_group_cover(group_id: int, cover_path: Optional[str]):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE groups SET cover_path = ? WHERE id = ?", (cover_path, group_id))
    conn.commit()
    conn.close()


def delete_group(group_id: int):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE collections SET group_id = NULL WHERE group_id = ?", (group_id,))
    cursor.execute("DELETE FROM groups WHERE id = ?", (group_id,))
    conn.commit()
    conn.close()


def get_all_groups() -> list:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM groups ORDER BY sort_order, id")
    groups = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return groups


def get_group_collections(group_id: int) -> list:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM collections WHERE group_id = ? ORDER BY sort_order, updated_at DESC",
        (group_id,),
    )
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows


def get_ungrouped_collections() -> list:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM collections WHERE group_id IS NULL ORDER BY sort_order, updated_at DESC"
    )
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows


# ─── 视频操作 ─────────────────────────────────────────

def delete_video(video_id: int):
    """删除视频记录"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM videos WHERE id = ?", (video_id,))
    conn.commit()
    conn.close()


def get_videos_by_collection(collection_id: int) -> list:
    """获取视频集下的所有视频"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM videos WHERE collection_id = ? ORDER BY sort_order, file_name, id",
        (collection_id,),
    )
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


# ─── 观看进度统计 ─────────────────────────────────────

def _is_watched(last_position: int, duration: float) -> bool:
    """判断单集是否已看完（接近片尾）"""
    if last_position <= 0:
        return False
    if duration and duration > 0:
        return last_position >= duration * 1000 * 0.95
    return False


def get_collection_stats(collection_id: int) -> dict:
    """返回某视频集的观看统计：{watched, total, progress}"""
    videos = get_videos_by_collection(collection_id)
    total = len(videos)
    watched = sum(1 for v in videos if _is_watched(v.get("last_position") or 0, v.get("duration") or 0))
    progress = (watched / total) if total else 0.0
    return {"watched": watched, "total": total, "progress": progress}


def get_all_collection_stats() -> dict:
    """批量返回所有视频集的统计：{collection_id: {watched,total,progress}}"""
    result: dict = {}
    for c in get_all_collections():
        result[c["id"]] = get_collection_stats(c["id"])
    return result


# ─── 搜索 ─────────────────────────────────────────────

def search_collections(keyword: str) -> list:
    """按名称搜索视频集"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM collections WHERE name LIKE ? ORDER BY sort_order, updated_at DESC",
        (f"%{keyword}%",),
    )
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows


def search_videos(keyword: str) -> list:
    """按文件名搜索视频（附带所属视频集名）"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT v.*, c.name AS collection_name
        FROM videos v
        JOIN collections c ON v.collection_id = c.id
        WHERE v.file_name LIKE ?
        ORDER BY c.sort_order, v.sort_order, v.file_name, v.id
    """, (f"%{keyword}%",))
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows
