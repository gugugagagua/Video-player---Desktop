"""视频帧提取 - 进度条悬停预览的画面来源

设计要点
--------
1. 抽帧全部在**后台单线程**执行，GUI 线程零阻塞
2. **优先级队列**：悬停请求永远排在预加载之前，且新悬停会作废过期的预加载
3. **预加载**：命中某个时间点后自动向前（少量向后）预取相邻秒的帧，
   鼠标连续移动时基本全程命中缓存
4. **前向顺序推进**：目标帧在当前帧之后且不远时，用 grab() 连续推进代替 seek，
   大幅降低预取成本（seek 需要回溯关键帧）
5. **近似帧兜底**：未命中时先返回最近的已缓存帧，做到即时反馈
6. **缩略图雪碧图**：整段视频按固定间隔预抽帧拼成一张大图并落盘，
   悬停时直接裁一格，微秒级 —— 这是「指哪打哪」的关键，见 thumb_strip
7. **可整体暂停**：离开预览场景后停止预加载，不占用 CPU

返回 QImage（工作线程安全），由 GUI 线程转 QPixmap。
"""

import heapq
import os
import threading
import time
from collections import OrderedDict
from typing import Optional, Tuple

import cv2
from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtGui import QImage

from app import thumb_strip


# ─── 可调参数 ─────────────────────────────────────────────
MAX_WIDTH = 240             # 预览图最大宽度
BUCKET_MS = 1000            # 缓存 / 预加载粒度：每秒一个桶
CACHE_MAX = 160             # 缓存条数上限（约 20 MB）
APPROX_BUCKETS = 3          # 未命中时允许借用最近几秒的帧

PRELOAD_AHEAD = 12          # 向前预加载秒数
PRELOAD_BEHIND = 3          # 向后预加载秒数
PRELOAD_AHEAD_SLOW = 4      # 机器较慢时缩减到的秒数
SLOW_DECODE_MS = 260.0      # 单帧耗时超过此值判定为「慢」

FORWARD_GRAB_LIMIT = 90     # 前向 grab 推进的最大帧数，超出则改用 seek

# 退出时等待后台线程的时间上限（秒）。后台可能正卡在 OpenCV 解码里，
# 绝不能无限等，否则点关闭会假死；线程是守护线程，未退干净也会随进程结束。
SHUTDOWN_JOIN_SEC = 0.4

# 队列优先级（同时作为堆排序键，值越小越先处理）
PRIORITY_HOVER = 0
PRIORITY_PRELOAD = 1
PRIORITY_STRIP = 2          # 雪碧图生成：最低优先级，只在空档里跑


class _Signals(QObject):
    """跨线程结果载体。模块级常驻，避免回调对象被提前回收。"""

    ready = pyqtSignal(object, int)  # (QImage | None, token)
    # 批量预生成进度：(已完成视频数, 总视频数, 当前视频已完成格数, 当前视频总格数, 名称)
    strip_progress = pyqtSignal(int, int, int, int, str)
    # 批量预生成结束：(是否全部完成)
    strip_finished = pyqtSignal(bool)


# 供外部 connect
signals = _Signals()


# ─── 帧缓存（GUI 线程与工作线程共享）──────────────────────

_cache: "OrderedDict[Tuple[str, int], QImage]" = OrderedDict()
_cache_lock = threading.Lock()


def _cache_get(key) -> Optional[QImage]:
    with _cache_lock:
        image = _cache.get(key)
        if image is not None:
            _cache.move_to_end(key)
        return image


def _cache_has(key) -> bool:
    with _cache_lock:
        return key in _cache


def _cache_put(key, image: QImage):
    with _cache_lock:
        _cache[key] = image
        _cache.move_to_end(key)
        while len(_cache) > CACHE_MAX:
            _cache.popitem(last=False)


def _cache_nearest(video_path: str, bucket: int, span: int) -> Optional[QImage]:
    """取距离目标桶最近的已缓存帧，用作未命中时的即时占位画面"""
    with _cache_lock:
        for dist in range(1, span + 1):
            for b in (bucket + dist, bucket - dist):
                image = _cache.get((video_path, b))
                if image is not None:
                    _cache.move_to_end((video_path, b))
                    return image
    return None


def _cache_clear():
    with _cache_lock:
        _cache.clear()


# ─── 后台工作线程 ─────────────────────────────────────────

class _Worker(threading.Thread):
    """单线程抽帧器：优先处理悬停，空闲时做预加载

    注意：threading.Thread 内部已占用 `_stop`（方法）、`_handle`（3.13 起为
    线程句柄属性）等名字，自定义成员必须避开，否则会被覆盖。
    """

    def __init__(self):
        super().__init__(daemon=True, name="frame-provider")
        self._cond = threading.Condition()
        self._jobs: list = []           # 堆：(优先级, 序号, 路径, 毫秒, token)
        self._seq = 0
        self._stopping = False

        # 当前上下文
        self._video_path = ""
        self._latest_hover = 0
        self._preload_enabled = False
        self._last_hover_ms = 0
        self._reset_requested = False

        # 解码器（仅本线程访问）
        self._cap = None
        self._cap_path = ""
        self._cap_frame = -1            # 解码器当前所在帧号，-1 表示未知
        self._fps = 25.0

        # 缩略图雪碧图
        self._strip = None              # 当前视频的 ThumbStrip
        self._strip_path = ""
        self._strip_enabled = False
        self._strip_pending = None      # 因让路而挂起的生成任务

        # 批量预生成
        self._batch_queue: list = []    # [(path, duration_ms, name)]
        self._batch_active = False
        self._batch_total_videos = 0
        self._batch_done_videos = 0
        self._batch_cur_total = 0
        self._batch_cur_done = 0
        self._batch_name = ""

        # 性能统计：用于自动收缩预加载窗口
        self._avg_decode_ms = 0.0

    # ── 线程主循环 ───────────────────────────────────────

    def run(self):
        try:
            while True:
                with self._cond:
                    while (not self._stopping and not self._jobs
                           and not self._reset_requested):
                        self._cond.wait(0.5)
                    if self._stopping:
                        return
                    if self._reset_requested:
                        self._reset_requested = False
                        job = None
                    else:
                        job = heapq.heappop(self._jobs)
                if job is None:
                    self._release_cap()
                else:
                    self._process(job)
        finally:
            # 解码器只能在本线程释放
            self._release_cap()

    def _push(self, priority: int, path: str, ms: int, token: int):
        """调用方需持有 _cond"""
        self._seq += 1
        heapq.heappush(self._jobs, (priority, self._seq, path, ms, token))

    def _has_pending_hover(self) -> bool:
        """堆顶即最高优先级；调用方需持有 _cond"""
        return bool(self._jobs) and self._jobs[0][0] == PRIORITY_HOVER

    def _process(self, job):
        if self._stopping:
            return
        priority, _, path, arg, extra = job

        # 雪碧图生成（arg = 格号，-1 表示初始化；extra = 时长毫秒）
        if priority == PRIORITY_STRIP:
            self._process_strip(path, arg, extra)
            return

        if path != self._video_path:
            return  # 视频已切换，丢弃
        if priority == PRIORITY_HOVER and arg != self._latest_hover:
            return  # 已有更新的悬停请求
        if priority == PRIORITY_PRELOAD:
            if not self._preload_enabled:
                return
            with self._cond:
                if self._has_pending_hover():
                    return  # 悬停优先，预加载让路

        started = time.perf_counter()
        image = self._decode(path, arg)
        cost_ms = (time.perf_counter() - started) * 1000.0

        if priority == PRIORITY_HOVER:
            if self._avg_decode_ms <= 0:
                self._avg_decode_ms = cost_ms
            else:
                self._avg_decode_ms = self._avg_decode_ms * 0.7 + cost_ms * 0.3

        if image is None:
            if priority == PRIORITY_HOVER:
                signals.ready.emit(None, extra)
                self._resume_strip()
            return

        _cache_put((path, arg // BUCKET_MS), image)

        if priority == PRIORITY_HOVER:
            signals.ready.emit(image, extra)
            self._enqueue_preload(path, arg)
            self._resume_strip()

    # ── 雪碧图生成 ───────────────────────────────────────

    def _probe_duration_ms(self, path: str) -> int:
        """借已打开的解码器读出时长（毫秒）"""
        cap = self._ensure_cap(path)
        if cap is None:
            return 0
        frames = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
        fps = self._fps or 25.0
        if frames <= 0 or fps <= 0:
            return 0
        return int(frames / fps * 1000)

    def _process_strip(self, path: str, index: int, duration_ms: int):
        """生成一格缩略图；最低优先级，遇到悬停就让路"""
        if not self._strip_enabled and not self._batch_active:
            return
        with self._cond:
            if self._has_pending_hover():
                # 让路：记下待办，等悬停处理完再续上
                self._strip_pending = (path, index, duration_ms)
                return

        if index < 0:
            # ── 初始化目标 ──────────────────────────────
            if duration_ms <= 0:
                duration_ms = self._probe_duration_ms(path)
            strip = thumb_strip.ThumbStrip(path, duration_ms)
            self._strip = strip
            self._strip_path = path
            start = strip.done if strip.load() else 0   # 支持断点续传

            if self._batch_active:
                self._batch_cur_total = strip.count
                self._batch_cur_done = start
                self._emit_batch_progress()

            if start < strip.count:
                self._push(PRIORITY_STRIP, path, start, duration_ms)
            else:
                strip.save()
                self._finish_strip_target()
            return

        strip = self._strip
        if strip is None or self._strip_path != path:
            return
        if index >= strip.count:
            strip.save()
            self._finish_strip_target()
            return

        frame = self._decode(path, strip.time_at(index), max_width=thumb_strip.TILE_W)
        if frame is not None and strip.put_tile(index, frame):
            strip.save()

        if self._batch_active:
            self._batch_cur_done = index + 1
            self._emit_batch_progress()

        if index + 1 < strip.count:
            self._push(PRIORITY_STRIP, path, index + 1, duration_ms)
        else:
            strip.save()
            self._finish_strip_target()

    def _finish_strip_target(self):
        """当前雪碧图生成完毕；批量队列还有就继续下一个"""
        if not self._batch_active:
            return
        self._batch_done_videos += 1
        self._emit_batch_progress()
        if self._batch_queue:
            path, duration_ms, name = self._batch_queue.pop(0)
            self._batch_name = name
            self._push(PRIORITY_STRIP, path, -1, duration_ms)
            with self._cond:
                self._cond.notify()
            return
        # 全部完成
        self._batch_active = False
        self._batch_cur_total = 0
        self._batch_cur_done = 0
        signals.strip_finished.emit(True)

    def _emit_batch_progress(self):
        if not self._batch_active:
            return
        signals.strip_progress.emit(
            self._batch_done_videos, self._batch_total_videos,
            self._batch_cur_done, self._batch_cur_total,
            self._batch_name,
        )

    def start_batch(self, items):
        """开始批量预生成。items = [(path, duration_ms, name), ...]"""
        with self._cond:
            self._jobs = [j for j in self._jobs if j[0] != PRIORITY_STRIP]
            heapq.heapify(self._jobs)
            self._strip = None
            self._strip_path = ""
            self._strip_pending = None
            self._batch_queue = list(items)
            self._batch_total_videos = len(self._batch_queue)
            self._batch_done_videos = 0
            self._batch_cur_total = 0
            self._batch_cur_done = 0
            self._batch_name = ""
            self._batch_active = bool(self._batch_queue)
            if self._batch_active:
                path, duration_ms, name = self._batch_queue.pop(0)
                self._batch_name = name
                self._push(PRIORITY_STRIP, path, -1, duration_ms)
                self._cond.notify()

    def cancel_batch(self):
        """取消批量预生成（当前这一格生成完就停）"""
        with self._cond:
            if not self._batch_active:
                return
            self._batch_queue.clear()
            self._batch_active = False
            self._jobs = [j for j in self._jobs if j[0] != PRIORITY_STRIP]
            heapq.heapify(self._jobs)
        signals.strip_finished.emit(False)

    def batch_active(self) -> bool:
        return self._batch_active

    def _resume_strip(self):
        """把因让路而挂起的雪碧图任务重新排队"""
        with self._cond:
            pending = self._strip_pending
            self._strip_pending = None
            if pending is not None:
                self._push(PRIORITY_STRIP, pending[0], pending[1], pending[2])
                self._cond.notify()

    def prepare_strip(self, path: str, duration_ms: int):
        """登记为某视频生成雪碧图（会先尝试读磁盘缓存）"""
        with self._cond:
            if (self._strip is not None and self._strip_path == path
                    and abs(self._strip.duration_ms - duration_ms) < 1000):
                return
            self._strip = None
            self._strip_path = ""
            self._strip_pending = None
            self._strip_enabled = True
            self._push(PRIORITY_STRIP, path, -1, duration_ms)
            self._cond.notify()

    def current_strip(self, path: str):
        """供 GUI 线程读取当前雪碧图（只读引用，原子）"""
        if self._strip is None or self._strip_path != path:
            return None
        return self._strip

    def drop_strip(self):
        """丢弃当前雪碧图（修改精细度后需要按新参数重建）"""
        with self._cond:
            self._jobs = [j for j in self._jobs if j[0] != PRIORITY_STRIP]
            heapq.heapify(self._jobs)
            self._strip = None
            self._strip_path = ""
            self._strip_pending = None

    # ── 解码 ─────────────────────────────────────────────

    def _ensure_cap(self, video_path: str):
        if (self._cap is not None and self._cap_path == video_path
                and self._cap.isOpened()):
            return self._cap
        self._release_cap()
        try:
            cap = cv2.VideoCapture(video_path)
        except Exception:
            return None
        if not cap.isOpened():
            try:
                cap.release()
            except Exception:
                pass
            return None
        self._cap = cap
        self._cap_path = video_path
        self._cap_frame = -1
        fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
        self._fps = fps if fps > 0 else 25.0
        return cap

    def _release_cap(self):
        if self._cap is not None:
            try:
                self._cap.release()
            except Exception:
                pass
        self._cap = None
        self._cap_path = ""
        self._cap_frame = -1

    def _read_at(self, cap, target_frame: int):
        """定位到目标帧并读出。

        目标帧在当前帧之后且相距不远时，用 grab() 连续推进：
        既避免 seek 回溯关键帧的开销，也省去定位后的重复解码。
        """
        cur = self._cap_frame
        if 0 < target_frame - cur <= FORWARD_GRAB_LIMIT:
            for step in range(target_frame - cur - 1):
                if not cap.grab():
                    self._cap_frame = -1
                    break
                # 请求停止时尽快退出，缩短关闭等待
                if step % 16 == 0 and self._stopping:
                    self._cap_frame = -1
                    return None
            else:
                ok, frame = cap.read()
                if ok:
                    self._cap_frame = target_frame
                    return frame
                self._cap_frame = -1

        # 常规路径：seek 后读取
        try:
            cap.set(cv2.CAP_PROP_POS_FRAMES, target_frame)
            ok, frame = cap.read()
        except Exception:
            self._cap_frame = -1
            return None
        if ok:
            self._cap_frame = target_frame
            return frame
        self._cap_frame = -1
        return None

    def _decode(self, video_path: str, position_ms: int,
                max_width: int = MAX_WIDTH) -> Optional[QImage]:
        if self._stopping:
            return None
        cap = self._ensure_cap(video_path)
        if cap is None:
            return None
        target_frame = max(0, int(position_ms / 1000.0 * self._fps))
        frame = self._read_at(cap, target_frame)
        if frame is None:
            return None
        try:
            h, w = frame.shape[:2]
            if w > max_width:
                scale = max_width / w
                frame = cv2.resize(frame, (max_width, max(1, int(h * scale))),
                                   interpolation=cv2.INTER_AREA)
                h, w = frame.shape[:2]
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            return QImage(rgb.data, w, h, 3 * w,
                          QImage.Format.Format_RGB888).copy()
        except Exception:
            return None

    # ── 预加载 ───────────────────────────────────────────

    def _enqueue_preload(self, path: str, ms: int):
        """围绕当前时间点排入相邻秒的预取任务（调用方不在临界区）"""
        if not self._preload_enabled:
            return
        with self._cond:
            self._enqueue_preload_locked(path, ms)
            self._cond.notify()

    def _enqueue_preload_locked(self, path: str, ms: int):
        """调用方需持有 _cond"""
        if not self._preload_enabled:
            return
        ahead = (PRELOAD_AHEAD_SLOW
                 if self._avg_decode_ms > SLOW_DECODE_MS else PRELOAD_AHEAD)
        base = ms // BUCKET_MS

        # 已在队列中的预加载秒，避免重复入队
        queued = {j[3] // BUCKET_MS for j in self._jobs
                  if j[0] == PRIORITY_PRELOAD and j[2] == path}

        for k in range(1, ahead + 1):
            bucket = base + k
            if bucket in queued or _cache_has((path, bucket)):
                continue
            self._push(PRIORITY_PRELOAD, path, bucket * BUCKET_MS, 0)

        for k in range(1, PRELOAD_BEHIND + 1):
            bucket = base - k
            if bucket < 0 or bucket in queued or _cache_has((path, bucket)):
                continue
            self._push(PRIORITY_PRELOAD, path, bucket * BUCKET_MS, 0)

    # ── 对外操作 ─────────────────────────────────────────

    def request(self, path: str, ms: int, token: int, need_decode: bool):
        """登记一次悬停请求（need_decode=False 表示已命中缓存）"""
        with self._cond:
            self._video_path = path
            self._latest_hover = token
            self._last_hover_ms = ms
            self._preload_enabled = True
            # 位置已变，作废在排队的预加载
            self._jobs = [j for j in self._jobs if j[0] != PRIORITY_PRELOAD]
            heapq.heapify(self._jobs)
            if need_decode:
                self._push(PRIORITY_HOVER, path, ms, token)
            else:
                self._enqueue_preload_locked(path, ms)
            self._cond.notify()

    def set_video(self, path: str):
        """切换当前视频：作废所有在途任务并重建解码器"""
        with self._cond:
            if path == self._video_path:
                return
            self._video_path = path
            self._latest_hover = 0
            self._jobs.clear()
            self._reset_requested = True
            if not path:
                # 离开播放页：停止隐式雪碧图生成并释放内存
                # （用户主动发起的批量预生成不受影响，继续跑完）
                self._strip_enabled = False
                if not self._batch_active:
                    self._strip = None
                    self._strip_path = ""
                    self._strip_pending = None
            self._cond.notify()

    def set_preload(self, enabled: bool):
        with self._cond:
            self._preload_enabled = enabled
            if enabled:
                # 回到预览场景时，围绕上次位置直接预取
                if self._video_path and self._last_hover_ms:
                    self._enqueue_preload_locked(self._video_path,
                                                 self._last_hover_ms)
            else:
                self._jobs = [j for j in self._jobs
                              if j[0] != PRIORITY_PRELOAD]
                heapq.heapify(self._jobs)
            self._cond.notify()

    def stop(self):
        """请求停止。

        只置标志并清空队列，**绝不触碰解码器**：解码器可能正被本线程
        用于 cap.read()/cap.set()，跨线程 release 会与其内部锁互锁。
        """
        with self._cond:
            self._stopping = True
            self._jobs.clear()
            self._cond.notify()


_worker = _Worker()
_worker.start()


# ─── 对外接口 ─────────────────────────────────────────────

def request_frame(video_path: str, position_ms: int,
                  token: int) -> Optional[QImage]:
    """请求一帧。

    命中缓存 → 直接返回该帧；
    未命中 → 返回最近的已缓存帧作即时占位（可能为 None），
            同时在后台抽帧，完成后通过 signals.ready 回调精确帧。
    """
    if not video_path:
        return None
    bucket = position_ms // BUCKET_MS
    key = (video_path, bucket)

    exact = _cache_get(key)
    if exact is not None:
        _worker.request(video_path, position_ms, token, need_decode=False)
        return exact

    approx = _cache_nearest(video_path, bucket, APPROX_BUCKETS)
    _worker.request(video_path, position_ms, token, need_decode=True)
    return approx


def prepare_strip(video_path: str, duration_ms: int):
    """为某视频准备缩略图雪碧图（先读磁盘缓存，没有则在后台生成）"""
    if not video_path or duration_ms <= 0:
        return
    _worker.prepare_strip(video_path, duration_ms)


def tile_at(video_path: str, position_ms: int):
    """从雪碧图直接裁一格（GUI 线程调用，微秒级）。

    雪碧图尚未覆盖该位置时返回 None，调用方回退到 request_frame。
    """
    if not video_path:
        return None
    strip = _worker.current_strip(video_path)
    if strip is None:
        return None
    return strip.tile(position_ms)


def preload_strips(items):
    """批量预生成缩略图。items = [(视频路径, 时长毫秒, 显示名), ...]

    时长传 0 时会由后台自行探测。生成进度通过 signals.strip_progress 上报，
    全部结束（或取消）通过 signals.strip_finished 上报。
    """
    items = [(p, int(d or 0), n or os.path.basename(p)) for (p, d, n) in items]
    if not items:
        signals.strip_finished.emit(True)
        return
    _worker.start_batch(items)


def cancel_preload():
    """取消批量预生成"""
    _worker.cancel_batch()


def reset_strips():
    """丢弃已加载的雪碧图（修改精细度后调用，下次使用时按新参数重建）"""
    _worker.drop_strip()


def preload_active() -> bool:
    """是否正在批量预生成"""
    return _worker.batch_active()


def set_active_video(video_path: str):
    """切换当前视频（切换集数时调用）"""
    _worker.set_video(video_path or "")


def pause_preload():
    """暂停预加载（离开预览场景时调用，避免占用 CPU）"""
    _worker.set_preload(False)


def resume_preload():
    """恢复预加载"""
    _worker.set_preload(True)


def clear_cache():
    _cache_clear()


def shutdown():
    """退出前调用：请求停止并短暂等待。

    后台线程可能正卡在 OpenCV 的 seek/解码里，**不能无限等待**，
    否则点关闭会假死。超时后直接放行——它是守护线程，会随进程一起结束。
    """
    _worker.stop()
    _worker.join(timeout=SHUTDOWN_JOIN_SEC)
    _cache_clear()
