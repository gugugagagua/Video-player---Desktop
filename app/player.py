"""视频播放核心组件 —— 基于 libmpv（python-mpv）

为什么从 QtMultimedia 换成 mpv
------------------------------
QtMultimedia 的 FFmpeg 后端在跳转时会**清空画面再重新解码**，主观上就是
「快进卡一下」；而这一步的开销无法回避：这部片子的关键帧间隔是 9.92 秒，
任何 seek 都必须回到前一个关键帧逐帧解码追过去（实测 QtMultimedia 端到端
约 240 ms，且期间画面是黑的）。

mpv 是完整的播放器引擎，白送这些东西：

- 跳转时**保留上一帧**，画面不会闪黑；配合解复用缓存，观感明显更连贯
- HEVC 10-bit 等格式的**硬件解码**（D3D11VA），CPU 占用低得多
- 倍速播放带**变调处理**（`speed` 属性），不会变成快进怪声
- 字幕、音轨切换、音频设备枚举（直接用 mpv 自己的设备列表）
- 各种奇怪封装/编码的兼容性经过长期验证

代价是 `libmpv-2.dll` 约 115 MB（未压缩），安装包会明显变大。

与 Qt 的边界
------------
mpv 需要**真实窗口句柄**才能渲染，所以视频控件是普通 `QWidget`（不是
QVideoWidget），通过 `wid=` 把 `winId()` 交给 mpv。为了不让 mpv 吞掉鼠标
事件（沉浸模式靠鼠标位置判断是否滑出列表），创建时就关掉了它的输入绑定。

对外接口与旧版完全一致，主窗口无需感知后端差异。
"""

import os
import sys

from PyQt6.QtCore import QObject, QTimer, pyqtSignal
from PyQt6.QtMultimedia import QMediaPlayer      # 只为 PlaybackState 枚举

# ─── 载入 libmpv ──────────────────────────────────────────
# python-mpv 是用 ctypes.CDLL("libmpv-2.dll") 加载的，按 **%PATH%** 查找，
# 不认 os.add_dll_directory —— 所以必须在 import mpv 之前把目录塞进 PATH。


def _libmpv_dir() -> str:
    if getattr(sys, "frozen", False):
        # PyInstaller：--add-binary 把 dll 放进了 _MEIPASS（即 _internal/）
        return getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(root, "libmpv")


def _prepare_libmpv():
    d = _libmpv_dir()
    if d and os.path.isdir(d):
        path = os.environ.get("PATH", "")
        if d not in path.split(os.pathsep):
            os.environ["PATH"] = d + os.pathsep + path


_prepare_libmpv()

try:
    import mpv
except Exception as _exc:               # 缺 DLL 时别让整个程序起不来
    mpv = None
    _MPV_IMPORT_ERROR = str(_exc)
else:
    _MPV_IMPORT_ERROR = ""


# 轮询间隔：进度条刷新频率。不订阅 mpv 的回调是因为那些回调跑在 mpv 自己的
# 线程里，从那里发 Qt 信号虽然能靠队列连接工作，但退出阶段容易踩到线程归属
# 的问题；100ms 轮询几个属性足够顺滑，也简单得多。
POLL_MS = 100


class VideoPlayer(QObject):
    """视频播放器封装（libmpv 后端）"""

    position_changed = pyqtSignal(int)
    duration_changed = pyqtSignal(int)
    playback_state_changed = pyqtSignal(object)  # QMediaPlayer.PlaybackState
    volume_changed = pyqtSignal(int)
    error_occurred = pyqtSignal(str)
    # 取代旧版的 mediaStatusChanged：主窗口用它做「加载完成后跳转」和「播完自动下一集」
    media_loaded = pyqtSignal()
    end_of_media = pyqtSignal()

    def __init__(self, video_widget, parent=None):
        super().__init__(parent)
        self._widget = video_widget

        self._duration_ms = 0
        self._position_ms = 0
        self._last_emitted_pos = -1
        self._rate = 1.0
        self._volume = 50
        self._playing = False
        self._eof_fired = False
        self._eof_pending = False
        self._loaded_pending = False
        self._error_pending = ""

        self._mpv = self._create_mpv()

        self._timer = QTimer(self)
        self._timer.setInterval(POLL_MS)
        self._timer.timeout.connect(self._poll)
        self._timer.start()

    # ── 创建 ──────────────────────────────────────────────

    def _create_mpv(self):
        if mpv is None:
            self.error_occurred.emit(_MPV_IMPORT_ERROR or "mpv 不可用")
            return None
        try:
            wid = int(self._widget.winId())     # 会强制创建原生窗口句柄
        except Exception as exc:
            self.error_occurred.emit(f"无法获取视频窗口句柄: {exc}")
            return None

        # 基础项 = 已实测可用的最小集合；tuned 里是锦上添花的调优项。
        # 分两档尝试，避免某个选项在别的 mpv 版本上不被接受时整个播放起不来。
        base = dict(
            wid=str(wid),
            keep_open=True,                 # 播完停住，不要把实例关掉
            input_default_bindings=False,   # 不吞键盘/鼠标，事件留给 Qt
            input_vo_keyboard=False,
            terminal=False,
            loglevel="no",
        )
        tuned = dict(base, **{
            "vo": "gpu",
            "osc": False,                   # 不要 mpv 自带的进度条 UI
            "osd_level": 0,
            "cache": "yes",                 # 本地文件也开解复用缓存，跳转更顺
            "audio_client_name": "VideoSetPlayer",
        })

        last = None
        for kwargs in (tuned, base):
            try:
                player = mpv.MPV(**kwargs)
            except Exception as exc:
                last = exc
                continue
            self._bind_events(player)
            return player

        self.error_occurred.emit(f"mpv 初始化失败: {last}")
        return None

    def _bind_events(self, player):
        """只挂「事件 → 置标志位」的回调，真正的处理放在 GUI 线程的轮询里"""
        try:
            player.event_callback("file-loaded")(self._on_file_loaded)
            player.event_callback("end-file")(self._on_end_file)
        except Exception:
            pass

    # ── mpv 事件（跑在 mpv 线程，只置标志）───────────────

    def _on_file_loaded(self, _event):
        self._loaded_pending = True

    def _on_end_file(self, event):
        """mpv 的 end-file.reason 是枚举整数

        MPV_END_FILE_REASON_EOF = 0 / STOP = 2 / QUIT = 3 / ERROR = 4
        （python-mpv 不会把它转成字符串，所以这里同时兼容字符串写法）
        """
        reason = getattr(event, "reason", None)
        if reason in (0, "eof"):
            self._eof_pending = True
        elif reason in (4, "error"):
            self._error_pending = "播放出错：该文件无法解码"

    # ── 轮询（GUI 线程）───────────────────────────────────

    def _poll(self):
        p = self._mpv
        if p is None:
            return

        if self._loaded_pending:
            self._loaded_pending = False
            self.media_loaded.emit()

        if self._eof_pending and not self._eof_fired:
            self._eof_pending = False
            self._eof_fired = True
            self.end_of_media.emit()

        if self._error_pending:
            message, self._error_pending = self._error_pending, ""
            self.error_occurred.emit(message)

        try:
            duration = p.duration
            if duration is not None:
                ms = int(duration * 1000)
                if ms != self._duration_ms:
                    self._duration_ms = ms
                    self.duration_changed.emit(ms)

            pos = p.time_pos
            if pos is not None:
                ms = max(0, int(pos * 1000))
                self._position_ms = ms
                # 变化不明显就不发信号，避免无谓的重绘
                if abs(ms - self._last_emitted_pos) >= 60:
                    self._last_emitted_pos = ms
                    self.position_changed.emit(ms)

            playing = not bool(p.pause) and not bool(p.idle_active)

            # 兜底：end-file 事件万一没派发到，也能靠 eof-reached 属性发现播完。
            # 要求已经播过（position > 0），否则刚载入时该属性可能瞬时为真。
            if (not self._eof_fired and self._duration_ms > 0
                    and self._position_ms > 0 and bool(p.eof_reached)):
                self._eof_fired = True
                self.end_of_media.emit()
        except Exception:
            return

        if playing != self._playing:
            self._playing = playing
            self.playback_state_changed.emit(
                QMediaPlayer.PlaybackState.PlayingState if playing
                else QMediaPlayer.PlaybackState.PausedState)

    # ── 播放控制 ──────────────────────────────────────────

    def play(self):
        if self._mpv is not None:
            self._mpv.pause = False

    def pause(self):
        if self._mpv is not None:
            self._mpv.pause = True

    def toggle_play_pause(self):
        if self._mpv is None:
            return
        self._mpv.pause = not bool(self._mpv.pause)

    def stop(self):
        if self._mpv is None:
            return
        try:
            self._mpv.command("stop")
        except Exception:
            pass
        self._duration_ms = 0
        self._position_ms = 0
        self._last_emitted_pos = -1
        self._eof_fired = False
        self._eof_pending = False
        # 立刻把状态播报出去，别等下一次轮询 —— 否则返回首页后播放键图标还是「暂停」
        if self._playing:
            self._playing = False
            self.playback_state_changed.emit(
                QMediaPlayer.PlaybackState.StoppedState)

    def set_source(self, file_path: str):
        self._eof_fired = False
        self._eof_pending = False
        self._loaded_pending = False
        self._duration_ms = 0
        self._position_ms = 0
        self._last_emitted_pos = -1
        if self._mpv is None:
            return
        if not file_path:
            self.stop()
            return
        try:
            self._mpv.play(file_path)
        except Exception as exc:
            self.error_occurred.emit(f"无法打开文件: {exc}")

    def seek(self, position_ms: int):
        if self._mpv is None:
            return
        try:
            self._mpv.command("seek", max(0, int(position_ms)) / 1000.0,
                              "absolute")
        except Exception:
            pass

    def set_playback_rate(self, rate: float):
        """设置播放倍速，如 0.5 ~ 2.0（mpv 自带变调处理）"""
        self._rate = rate
        if self._mpv is None:
            return
        try:
            self._mpv.speed = rate
        except Exception:
            pass

    def playback_rate(self) -> float:
        return self._rate

    def set_volume(self, vol: int):
        """设置音量，vol: 0-100"""
        self._volume = max(0, min(100, int(vol)))
        if self._mpv is not None:
            try:
                self._mpv.volume = self._volume
            except Exception:
                pass
        self.volume_changed.emit(self._volume)

    def volume(self) -> int:
        return self._volume

    def is_playing(self) -> bool:
        return self._playing

    def duration(self) -> int:
        return self._duration_ms

    def position(self) -> int:
        return self._position_ms

    # ── 音频设备 ──────────────────────────────────────────

    def list_audio_outputs(self) -> list:
        """列出所有可用音频输出设备，返回 [(名称, 设备 id), ...]"""
        if self._mpv is None:
            return []
        try:
            devices = self._mpv.audio_device_list
        except Exception:
            return []
        out = []
        for dev in devices or []:
            try:
                name = dev.get("name")
                label = dev.get("description") or name
            except Exception:
                continue
            if name:
                out.append((label, name))
        return out

    def set_audio_output(self, device_id: str):
        """切换到指定音频输出设备"""
        if self._mpv is None:
            return
        try:
            self._mpv.audio_device = device_id
        except Exception:
            pass

    # ── 收尾 ──────────────────────────────────────────────

    def release(self):
        """退出前释放 mpv 实例"""
        self._timer.stop()
        player, self._mpv = self._mpv, None
        if player is None:
            return
        try:
            player.terminate()
        except Exception:
            pass
