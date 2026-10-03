"""cv2 的延迟代理

cv2 加载要 200 ms 上下，实测占整个启动导入耗时的近一半。而它只在「真的去
解码一帧」时才用得上 —— frame_provider / video_frames / keyframes 在**模块
顶层** import 它，等于每次启动都白付这笔钱。

做成代理后，调用点写法完全不变（仍然写 `cv2.xxx`），只有首次访问某个属性时
才真正加载 cv2。加载发生在后台抽帧线程或子进程里，不阻塞启动。

注意 __getattr__ 里的 `import cv2` 是**显式**写法，不能换成
importlib.import_module("cv2") —— PyInstaller 靠静态扫描 import 语句收集
依赖，写成字符串会让打包版缺模块。
"""


class _LazyCv2:
    _module = None

    def __getattr__(self, name):
        if _LazyCv2._module is None:
            import cv2
            _LazyCv2._module = cv2
        return getattr(_LazyCv2._module, name)


cv2 = _LazyCv2()
