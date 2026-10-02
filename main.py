"""视频集播放器 - 入口文件"""

# ⚠️ 必须放在所有其它导入之前
#
# 缩略图雪碧图的并行生成走的是**多进程**（见 app/frame_provider.py 里的
# ProcessPoolExecutor）。Windows 上用 spawn 启动子进程，子进程会重新执行本文件，
# 只有 freeze_support() 能识别 --multiprocessing-fork 标志、在子进程里直接跑
# 任务然后退出。
#
# 放在最前面还有第二个作用：子进程不会白白导入一次 PyQt6（秒级），
# 否则并行的收益会被启动开销吃光。
import multiprocessing

multiprocessing.freeze_support()

import sys

from PyQt6.QtWidgets import QApplication

from app import __version__, i18n
from app.main_window import MainWindow


def main():
    app = QApplication(sys.argv)
    i18n.init()
    app.setApplicationName(i18n.tr("app_name"))
    app.setApplicationVersion(__version__)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
