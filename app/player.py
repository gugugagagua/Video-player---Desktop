"""视频播放核心组件 - 封装 PyQt6.QtMultimedia 播放功能"""

from PyQt6.QtCore import QUrl, pyqtSignal, QObject
from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput, QMediaDevices
from PyQt6.QtMultimediaWidgets import QVideoWidget


class VideoPlayer(QObject):
    """视频播放器封装"""

    position_changed = pyqtSignal(int)
    duration_changed = pyqtSignal(int)
    playback_state_changed = pyqtSignal(object)  # QMediaPlayer.PlaybackState
    volume_changed = pyqtSignal(int)
    error_occurred = pyqtSignal(str)

    def __init__(self, video_widget: QVideoWidget, parent=None):
        super().__init__(parent)
        self._player = QMediaPlayer(parent)
        self._player.setVideoOutput(video_widget)

        self._audio_output = QAudioOutput()
        self._player.setAudioOutput(self._audio_output)

        self._player.positionChanged.connect(self.position_changed.emit)
        self._player.durationChanged.connect(self.duration_changed.emit)
        self._player.playbackStateChanged.connect(self.playback_state_changed.emit)
        self._player.errorOccurred.connect(self._on_error)

    @property
    def player(self) -> QMediaPlayer:
        return self._player

    def play(self):
        self._player.play()

    def pause(self):
        self._player.pause()

    def toggle_play_pause(self):
        if self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self._player.pause()
        else:
            self._player.play()

    def stop(self):
        self._player.stop()

    def set_source(self, file_path: str):
        self._player.setSource(QUrl.fromLocalFile(file_path))

    def seek(self, position_ms: int):
        self._player.setPosition(position_ms)

    def set_volume(self, vol: int):
        """设置音量，vol: 0-100"""
        self._audio_output.setVolume(vol / 100.0)
        self.volume_changed.emit(vol)

    def volume(self) -> int:
        """返回音量 0-100"""
        return int(self._audio_output.volume() * 100)

    def is_playing(self) -> bool:
        return self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState

    def duration(self) -> int:
        return self._player.duration()

    def position(self) -> int:
        return self._player.position()

    def list_audio_outputs(self) -> list:
        """列出所有可用音频输出设备，返回 [(名称, 描述), ...]"""
        return [(d.description(), d.id()) for d in QMediaDevices.audioOutputs()]

    def set_audio_output(self, device_id: str):
        """切换到指定音频输出设备"""
        for d in QMediaDevices.audioOutputs():
            if d.id() == device_id:
                self._audio_output.setDevice(d)
                break

    def set_playback_rate(self, rate: float):
        """设置播放倍速，如 0.5 ~ 2.0"""
        self._player.setPlaybackRate(rate)

    def playback_rate(self) -> float:
        return self._player.playbackRate()

    def _on_error(self, error, error_string):
        self.error_occurred.emit(error_string)
