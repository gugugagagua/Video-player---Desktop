"""视频集播放器 - 入口文件"""
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
