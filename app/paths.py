"""运行期数据目录

- 源码运行：项目下的 data/
- 打包运行：优先 exe 同级的 data/（便携模式）
           不可写（如安装到 Program Files）时回退到
           %LOCALAPPDATA%\\视频集播放器\\data
"""

import os
import sys
from typing import Optional

APP_DIR_NAME = "视频集播放器"

_cached_dir: Optional[str] = None


def _is_writable(path: str) -> bool:
    """目录可创建且可写入"""
    try:
        os.makedirs(path, exist_ok=True)
        probe = os.path.join(path, ".write_probe")
        with open(probe, "w", encoding="utf-8") as f:
            f.write("")
        os.remove(probe)
        return True
    except OSError:
        return False


def _candidate_dirs() -> list:
    """按优先级返回候选数据目录"""
    if not getattr(sys, "frozen", False):
        # 源码模式：固定使用项目 data/
        return [os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")]

    exe_dir = os.path.dirname(sys.executable)
    local_appdata = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return [
        os.path.join(exe_dir, "data"),                       # 便携：exe 同级
        os.path.join(local_appdata, APP_DIR_NAME, "data"),   # 安装后：用户目录
    ]


def data_dir() -> str:
    """返回可写的运行期数据目录（必要时创建）"""
    global _cached_dir
    if _cached_dir is not None:
        return _cached_dir

    candidates = _candidate_dirs()
    for path in candidates:
        if _is_writable(path):
            _cached_dir = path
            return path

    # 全部失败时兜底
    _cached_dir = candidates[-1]
    return _cached_dir


def data_file(name: str) -> str:
    """返回数据目录下的文件路径"""
    return os.path.join(data_dir(), name)
