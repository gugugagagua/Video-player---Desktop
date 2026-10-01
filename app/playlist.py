"""播放列表管理 - 视频集内自动生成播放列表，支持上下集切换"""

from typing import List, Optional

from app import database as db


class Playlist:
    """播放列表"""

    def __init__(self, collection_id: int):
        self._collection_id = collection_id
        self._videos: List[dict] = []
        self._current_index: int = -1
        self._load()

    def _load(self):
        self._videos = db.get_videos_by_collection(self._collection_id)
        self._current_index = 0 if self._videos else -1

    def reload(self):
        self._load()

    @property
    def current_index(self) -> int:
        return self._current_index

    @current_index.setter
    def current_index(self, index: int):
        if 0 <= index < len(self._videos):
            self._current_index = index

    @property
    def current_video(self) -> Optional[dict]:
        if 0 <= self._current_index < len(self._videos):
            return self._videos[self._current_index]
        return None

    @property
    def videos(self) -> List[dict]:
        return self._videos

    @property
    def count(self) -> int:
        return len(self._videos)

    def has_previous(self) -> bool:
        return self._current_index > 0

    def has_next(self) -> bool:
        return self._current_index < len(self._videos) - 1

    def previous(self) -> Optional[dict]:
        if self.has_previous():
            self._current_index -= 1
            return self._videos[self._current_index]
        return None

    def next(self) -> Optional[dict]:
        if self.has_next():
            self._current_index += 1
            return self._videos[self._current_index]
        return None

    def jump_to(self, video_id: int) -> Optional[dict]:
        for i, v in enumerate(self._videos):
            if v["id"] == video_id:
                self._current_index = i
                return v
        return None
