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
7. **并发生成**：雪碧图按时间切成若干段，各段独立解码器并行抽帧。
   实测随机取帧约 1.2 秒/帧且几乎全是串行的「关键帧解码追击」，
   分段并行加速比接近线性（_StripGenerator）
8. **可整体暂停**：离开预览场景后停止预加载，不占用 CPU

模块内有两个后台线程：
- `_Worker`        —— 悬停抽帧与预加载（交互优先，单线程）
- `_StripGenerator`—— 雪碧图并发生成（分段多线程，与悬停互不阻塞）

返回 QImage（工作线程安全），由 GUI 线程转 QPixmap。
"""

import concurrent.futures
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
from app import video_frames


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

# 悬停抽帧队列的优先级（同时作为堆排序键，值越小越先处理）
PRIORITY_HOVER = 0
PRIORITY_PRELOAD = 1


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
            return

        _cache_put((path, arg // BUCKET_MS), image)

        if priority == PRIORITY_HOVER:
            signals.ready.emit(image, extra)
            self._enqueue_preload(path, arg)

    # ── 雪碧图生成 ───────────────────────────────────────












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
            self._cond.notify()
        if not path:
            # 离开播放页：停止隐式雪碧图生成并释放内存
            # （用户主动发起的批量预生成不受影响，继续跑完）
            _strip_gen.leave_player()

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


# ─── 雪碧图并发生成器 ─────────────────────────────────────

# 并行度：把一部视频按时间切成 N 份同时抽帧。
#
# 【为什么用进程而不是线程】
# 瓶颈是「seek 后从关键帧逐帧解码追击目标帧」，单格约 540ms。但 OpenCV 的
# VideoCapture 在解码期间**不释放 GIL**，Python 线程因此被彻底串行化，实测：
#     1 线程 77.7s / 4 线程 66.7s(1.16x) / 8 线程 59.4s(1.31x)
# 同样的活交给 4 个进程则是 3.86x —— 限制来自 GIL，不是磁盘也不是 CPU。
# 子进程只导入 cv2（见 app/video_frames.py），不碰 Qt，spawn 才够快。
STRIP_PROCESSES = 4


# ─── 子进程池 ─────────────────────────────────────────────

_pool = None
_pool_lock = threading.Lock()


def _get_pool():
    """惰性创建进程池；创建失败返回 None（调用方回退到本地串行）"""
    global _pool
    with _pool_lock:
        if _pool is None:
            workers = min(STRIP_PROCESSES, max(1, (os.cpu_count() or 2) - 1))
            try:
                from concurrent.futures import ProcessPoolExecutor
                _pool = ProcessPoolExecutor(max_workers=workers)
            except Exception:
                _pool = None
        return _pool


def _shutdown_pool():
    """关掉进程池。退出和取消时都要调，否则子进程会挂着不放。"""
    global _pool
    with _pool_lock:
        pool, _pool = _pool, None
    if pool is None:
        return
    try:
        pool.shutdown(wait=False, cancel_futures=True)
    except TypeError:               # 老版本没有 cancel_futures
        try:
            pool.shutdown(wait=False)
        except Exception:
            pass
    except Exception:
        pass


def _read_frame_image(cap, fps: float, position_ms: int, max_width: int):
    """在给定解码器上定位并读出目标帧，返回 QImage（悬停抽帧用）"""
    item = video_frames.grab_tile_rgb(cap, fps, position_ms, max_width)
    if item is None:
        return None
    w, h, raw = item
    return QImage(raw, w, h, 3 * w, QImage.Format.Format_RGB888).copy()


class _StripGenerator(threading.Thread):
    """雪碧图生成器：把一部视频按时间切成若干块，交给子进程并行抽帧

    与悬停抽帧完全分离——悬停走 _Worker 在主进程里的解码器，
    这边的解码全部发生在子进程，两边互不阻塞、也不争 GIL。
    """

    def __init__(self):
        super().__init__(daemon=True, name="thumb-strip")
        self._cond = threading.Condition()
        self._queue: list = []          # [(path, duration_ms, name, batch)]
        self._stopping = False
        self._cancel = threading.Event()

        self._strip = None              # 当前 ThumbStrip
        self._strip_path = ""

        # 批量进度
        self._batch_active = False
        self._total_videos = 0
        self._done_videos = 0
        self._name = ""
        self._cur_total = 0
        self._cur_done = 0
        self._progress_lock = threading.Lock()
        self._last_emit = 0.0

    # ── 线程主循环 ───────────────────────────────────────

    def run(self):
        while True:
            with self._cond:
                while not self._stopping and not self._queue:
                    self._cond.wait(0.5)
                if self._stopping:
                    return
                target = self._queue.pop(0)
            try:
                self._generate_one(*target)
            except Exception:
                self._finish_video()

    def _generate_one(self, path, duration_ms, name, batch):
        if self._cancel.is_set():
            return
        if duration_ms <= 0:
            duration_ms = self._probe_duration(path)
        strip = thumb_strip.ThumbStrip(path, duration_ms)
        self._strip = strip
        self._strip_path = path
        start = strip.done if strip.load() else 0    # 支持断点续传

        if batch:
            self._name = name
            self._cur_total = strip.count
            self._cur_done = start
            self._emit_progress(force=True)

        if start < strip.count:
            self._run_chunks(strip, path, start)
        strip.save()

        if batch:
            self._finish_video()

    def _run_chunks(self, strip, path, start):
        """把 [start, count) 切成小块，交给进程池并行抽帧

        块切得比进程数细：负载更匀、进度条能小步前进。子进程内部会缓存
        解码器，所以「块多」并不等于「反复打开文件」。
        """
        count = strip.count
        remaining = count - start
        if remaining <= 0:
            return

        workers = min(STRIP_PROCESSES, max(1, (os.cpu_count() or 2) - 1))
        workers = max(1, min(workers, remaining))
        chunks = min(remaining, workers * 6)
        size = (remaining + chunks - 1) // chunks
        ranges = []
        lo = start
        while lo < count:
            hi = min(count, lo + size)
            ranges.append((lo, hi))
            lo = hi

        pool = _get_pool()
        if pool is None:
            self._serial(strip, path, start, count)   # 进程池起不来：本地兜底
            return

        futures = []
        for lo, hi in ranges:
            try:
                futures.append(pool.submit(video_frames.extract_tiles,
                                           path, strip.interval_ms, lo, hi))
            except Exception:
                break

        for fut in concurrent.futures.as_completed(futures):
            if self._cancel.is_set() or self._stopping:
                for f in futures:
                    f.cancel()
                break
            try:
                tiles = fut.result()
            except Exception:
                tiles = []
            self._paint(strip, tiles)

        # 让子进程把缓存的解码器放掉，别一直占着文件句柄
        for _ in range(workers):
            try:
                pool.submit(video_frames.release_cap)
            except Exception:
                break

    def _serial(self, strip, path, start, count):
        """兜底：进程池不可用时退回本地串行，功能不打折，只是慢"""
        try:
            cap = cv2.VideoCapture(path)
        except Exception:
            return
        if not cap.isOpened():
            try:
                cap.release()
            except Exception:
                pass
            return
        fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
        if fps <= 0:
            fps = 25.0
        try:
            for index in range(start, count):
                if self._cancel.is_set() or self._stopping:
                    break
                image = _read_frame_image(cap, fps,
                                          index * strip.interval_ms,
                                          thumb_strip.TILE_W)
                if image is not None and strip.put_tile(index, image):
                    strip.save()
                self._tick()
        finally:
            try:
                cap.release()
            except Exception:
                pass

    def _paint(self, strip, tiles):
        """把子进程算好的一批格子画进雪碧图"""
        for index, w, h, raw in tiles:
            image = QImage(raw, w, h, 3 * w,
                           QImage.Format.Format_RGB888).copy()
            if strip.put_tile(index, image):
                strip.save()
            self._tick()
        self._emit_progress(force=True)

    # ── 进度 ─────────────────────────────────────────────

    def _tick(self):
        with self._progress_lock:
            self._cur_done += 1
        self._emit_progress()

    def _emit_progress(self, force: bool = False):
        if not self._batch_active:
            return
        now = time.time()
        if not force and now - self._last_emit < 0.15:
            return
        self._last_emit = now
        signals.strip_progress.emit(self._done_videos, self._total_videos,
                                    self._cur_done, self._cur_total, self._name)

    def _finish_video(self):
        if not self._batch_active:
            return
        self._done_videos += 1
        with self._progress_lock:
            self._cur_done = self._cur_total
        self._emit_progress(force=True)
        with self._cond:
            if self._queue and not self._cancel.is_set():
                return          # 队列还有，主循环继续下一个
        self._batch_active = False
        signals.strip_finished.emit(not self._cancel.is_set())

    def _probe_duration(self, path: str) -> int:
        return video_frames.probe_duration_ms(path)

    # ── 对外操作 ─────────────────────────────────────────

    def enqueue(self, path: str, duration_ms: int, name: str = "",
                batch: bool = False, front: bool = True):
        with self._cond:
            self._queue = [t for t in self._queue if t[0] != path]
            item = (path, duration_ms, name, batch)
            if front:
                self._queue.insert(0, item)
            else:
                self._queue.append(item)
            self._cancel.clear()
            self._cond.notify()

    def start_batch(self, items):
        with self._cond:
            self._queue = [(p, d, n, True) for (p, d, n) in items]
            self._cancel.clear()
            self._batch_active = True
            self._total_videos = len(self._queue)
            self._done_videos = 0
            self._cur_total = 0
            self._cur_done = 0
            self._name = ""
            self._cond.notify()

    def cancel(self):
        with self._cond:
            if not self._batch_active:
                return
            self._queue.clear()
        self._cancel.set()
        self._batch_active = False
        signals.strip_finished.emit(False)

    def _drop_implicit(self):
        """丢掉隐式任务与已加载的雪碧图；批量任务保留"""
        with self._cond:
            self._queue = [t for t in self._queue if t[3]]
            if not self._batch_active:
                self._strip = None
                self._strip_path = ""

    def leave_player(self):
        self._drop_implicit()

    def drop(self):
        self._drop_implicit()

    def current(self, path: str):
        if self._strip is None or self._strip_path != path:
            return None
        return self._strip

    def batch_active(self) -> bool:
        return self._batch_active

    def stop(self):
        with self._cond:
            self._stopping = True
            self._queue.clear()
            self._cancel.set()
            self._cond.notify()
        _shutdown_pool()


_worker = _Worker()
_worker.start()

_strip_gen = _StripGenerator()
_strip_gen.start()


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
    """为某视频准备缩略图雪碧图（先读磁盘缓存，没有则在后台并发生成）"""
    if not video_path or duration_ms <= 0:
        return
    if _strip_gen.batch_active():
        return          # 批量任务进行中，会覆盖到这个视频
    _strip_gen.enqueue(video_path, duration_ms)


def tile_at(video_path: str, position_ms: int):
    """从雪碧图直接裁一格（GUI 线程调用，微秒级）。

    雪碧图尚未覆盖该位置时返回 None，调用方回退到 request_frame。
    """
    if not video_path:
        return None
    strip = _strip_gen.current(video_path)
    if strip is None:
        return None
    return strip.tile(position_ms)


def preload_strips(items):
    """批量预生成缩略图。items = [(视频路径, 时长毫秒, 显示名), ...]

    时长传 0 时会由后台自行探测。每个视频内部再按时间分段并行抽帧。
    生成进度通过 signals.strip_progress 上报，
    全部结束（或取消）通过 signals.strip_finished 上报。
    """
    items = [(p, int(d or 0), n or os.path.basename(p)) for (p, d, n) in items]
    if not items:
        signals.strip_finished.emit(True)
        return
    _strip_gen.start_batch(items)


def cancel_preload():
    """取消批量预生成"""
    _strip_gen.cancel()


def reset_strips():
    """丢弃已加载的雪碧图（修改间隔后调用，下次使用时按新参数重建）"""
    _strip_gen.drop()


def preload_active() -> bool:
    """是否正在批量预生成"""
    return _strip_gen.batch_active()


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
    # 先关进程池：取消排队中的任务，让正在 as_completed 的生成线程能立刻返回
    _shutdown_pool()
    _strip_gen.stop()
    _worker.stop()
    _worker.join(timeout=SHUTDOWN_JOIN_SEC)
    _cache_clear()
