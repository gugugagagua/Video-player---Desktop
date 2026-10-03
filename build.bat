@echo off
chcp 65001 >nul
echo ================================
echo    视频集播放器 - 打包脚本
echo ================================
echo.

cd /d "%~dp0"

if not exist "libmpv\libmpv-2.dll" (
  echo [错误] 缺少 libmpv\libmpv-2.dll
  echo        获取方式见 README 的「依赖准备」一节。
  pause
  exit /b 1
)

.venv\Scripts\pyinstaller --noconfirm --clean -w ^
  --name "视频集播放器" ^
  --icon app_icon.ico ^
  --version-file version_info.txt ^
  --add-binary "libmpv\libmpv-2.dll;." ^
  --hidden-import PyQt6.QtMultimedia ^
  --hidden-import PyQt6.QtMultimediaWidgets ^
  --hidden-import PyQt6.QtSvg ^
  --hidden-import cv2 ^
  --hidden-import PIL ^
  main.py

echo.
echo 打包完成！执行文件在 dist\视频集播放器\ 目录下。
echo 如需生成安装包： "D:\Inno Setup 6\ISCC.exe" installer.iss
pause
