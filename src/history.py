# -*- coding: utf-8 -*-
"""
历史均值: 近 3 / 6 / 12 小时
================================================================
主界面显示的是瞬时值, 这一页显示慢变量: 过去 3 / 6 / 12 小时的平均值。
甲醛和 VOC 的瞬时读数抖动很大, 看长时间均值更能判断"屋里到底有没有问题":
一次开窗、一次喷香水都会让瞬时值跳一下, 但不会让 3 小时均值明显变化。

为什么分"桶", 而不是把每个采样点都存下来
    传感器是 1Hz 采样, 12 小时就是 43200 个点, 四个通道按 int16 也要
    345KB —— ESP32 的 MicroPython 堆放不下, 而且完全没必要: 求均值只需要
    "和"与"个数"。这里每 5 分钟把桶内的所有 1Hz 采样平均成一个桶的值,
    12 小时正好 144 个桶。桶内平均对每个采样点等权, 所以 3h/6h/12h 均值
    与直接对原始 1Hz 点求平均是完全一致的(只差最多 5 分钟的"当前未满桶")。

    5 分钟这个粒度是这么定的:
      * 3h = 36 桶, 6h = 72 桶, 12h = 144 桶, 都是整数, 窗口边界干净;
      * 屏幕上的均值每 5 分钟才变一次, 与"慢变量"的定位相符(再快只是在抖);
      * 144 桶 x 4 通道 x 2 字节 = 1152 字节, 还顺手存进了 NVS 跨断电保留。

数值用 int16 存储, 按 SCALES 放大(温度/湿度 0.01, 甲醛 1µg/m³, VOC 1),
内存和 NVS blob 都只有 float 的一半; 计算均值时再还原成工程单位。
"""

import struct

try:
    import esp32
except ImportError:                 # 电脑上跑测试时没有 esp32 模块
    esp32 = None

try:
    from time import ticks_diff
except ImportError:                 # 电脑上的 time 没有 ticks_*
    import time as _time

    _MASK = 0x3FFFFFFF

    def ticks_diff(a, b):
        d = (a - b) & _MASK
        if d >= 0x20000000:
            d -= 0x40000000
        return d

import config as cfg

# 通道顺序就是 NVS blob 里的顺序, 不能随便改(改了要升 _STATE_VERSION)
KEYS = ("temp", "rh", "hcho", "voc")
# 各通道在 values 里的字段名(和历史键不同名, 单独列出来避免搞混)
VALUE_KEYS = ("temp", "rh", "hcho_mg", "tvoc")
# 存 int16 时的放大倍数: 温度/湿度 0.01, 甲醛 µg/m³(即 mg/m³ x 1000), VOC 原值
SCALES = (100, 100, 1000, 1)

INVALID = -32768            # int16 里的"这个桶没有数据"
_MAX = 32000
_MIN = -32000

_STATE_VERSION = 1
_NVS_NAMESPACE = "aircheck"
_NVS_KEY = "histstate"

_WINDOWS = (("3h", 3 * 3600000), ("6h", 6 * 3600000), ("12h", 12 * 3600000))


def _rdiv(total, count):
    """四舍五入的整数除法(负数也要正确, 温度可能低于 0)。"""
    if total >= 0:
        return (total + count // 2) // count
    return -((-total + count // 2) // count)


def _encode(value, scale):
    """工程单位 -> int16, 超出范围就夹住(坏线/乱码时不至于把 blob 写坏)。"""
    if value >= 0:
        n = int(value * scale + 0.5)
    else:
        n = -int(-value * scale + 0.5)
    if n > _MAX:
        return _MAX
    if n < _MIN:
        return _MIN
    return n


class History:
    """
    环形桶缓冲。每个 tick() 把当前读数累加进"当前桶", 桶满了就压入环,
    并清空缓存; 平均值只在需要时按窗口重新累加一遍。
    """

    def __init__(self, bucket_ms=None, slots=None, save_ms=None,
                 persist=None):
        self.bucket_ms = int(bucket_ms if bucket_ms is not None
                             else cfg.HISTORY_BUCKET_MS)
        self.slots = int(slots if slots is not None else cfg.HISTORY_SLOTS)
        if self.bucket_ms <= 0:
            self.bucket_ms = 5 * 60 * 1000
        if self.slots < 1:
            self.slots = 1
        self.save_ms = int(save_ms if save_ms is not None
                           else cfg.HISTORY_SAVE_MS)
        self.persist = (cfg.HISTORY_PERSIST if persist is None else persist)

        self.ring = {}
        for key in KEYS:
            self.ring[key] = [None] * self.slots
        self.pos = 0            # 下一个要写的槽位(也是最旧的一格)
        self.filled = 0         # 已经有结论的桶数(不足一整圈时 < slots)
        self.acc = {}
        for key in KEYS:
            self.acc[key] = [0, 0]      # [和, 采样点数]
        self.bucket_start = None
        self.restored = False

        # 每个窗口对应多少个桶; 配置不当时夹到环的大小, 不至于越界
        self.windows = []
        for name, ms in _WINDOWS:
            n = ms // self.bucket_ms
            if n < 1:
                n = 1
            if n > self.slots:
                n = self.slots
            self.windows.append((name, n))

        self._save_ms = None
        self._cache = None

    # ==================== 采样 ====================
    def tick(self, now, values, ready=None):
        """
        把当前读数喂进来。由 1Hz 的采集任务调用。

        values : SensorManager.values
        ready  : {通道名: 是否已经预热完成}; 预热/未连接时不采样,
                 否则 ZE08 前 3 分钟的无效读数会污染均值。
        """
        if self.bucket_start is None:
            self.bucket_start = now
            if self._save_ms is None:
                self._save_ms = now
        elif ticks_diff(now, self.bucket_start) >= self.bucket_ms:
            # 先关桶再累加: 正好落在桶边界上的这个采样点属于新桶,
            # 不应该被算进已经结束的旧桶里。
            # 一次只关一个桶: 如果中间被长时间阻塞(比如网络超时), 这段时间
            # 的采样会合并进下一个桶, 比伪造多个空桶更接近真实情况。
            self._close_bucket()
            self.bucket_start = now

        for i, key in enumerate(KEYS):
            if ready is not None and not ready.get(key, False):
                continue
            v = values.get(VALUE_KEYS[i])
            if v is None:
                continue
            n = _encode(v, SCALES[i])
            acc = self.acc[key]
            acc[0] += n
            acc[1] += 1

        if self.persist and self.save_ms > 0:
            if self._save_ms is None:
                self._save_ms = now
            elif ticks_diff(now, self._save_ms) >= self.save_ms:
                # 保存失败也照常推进计时, 否则每秒钟都会重试写 flash
                self.save()
                self._save_ms = now

    def _close_bucket(self):
        for key in KEYS:
            total, count = self.acc[key]
            self.ring[key][self.pos] = \
                _rdiv(total, count) if count else None
            self.acc[key] = [0, 0]
        self.pos = (self.pos + 1) % self.slots
        if self.filled < self.slots:
            self.filled += 1
        self._cache = None

    # ==================== 查询 ====================
    def averages(self):
        """
        返回 {通道: {"3h": 值, "6h": 值, "12h": 值, "3h_n": 桶数, ...}}。
        值已还原成工程单位; 没有数据时为 None。
        """
        if self._cache is None:
            self._cache = self._compute()
        return self._cache

    def _compute(self):
        out = {}
        nwin = len(self.windows)
        for i, key in enumerate(KEYS):
            ring = self.ring[key]
            sums = [0] * nwin
            cnts = [0] * nwin
            for age in range(self.filled):
                v = ring[(self.pos - 1 - age) % self.slots]
                if v is None:
                    continue
                for w in range(nwin):
                    # 窗口按 3h/6h/12h 递增: 超出 3h 的桶不再进 3h 窗口,
                    # 但还要继续判断 6h / 12h, 所以不能 break。
                    if age < self.windows[w][1]:
                        sums[w] += v
                        cnts[w] += 1
            scale = SCALES[i]
            d = {}
            for w, (name, _) in enumerate(self.windows):
                d[name] = (sums[w] / (cnts[w] * scale)) if cnts[w] else None
                d[name + "_n"] = cnts[w]
            out[key] = d
        return out

    def clear(self):
        for key in KEYS:
            self.ring[key] = [None] * self.slots
            self.acc[key] = [0, 0]
        self.pos = 0
        self.filled = 0
        self.bucket_start = None
        self._cache = None

    # ==================== 跨断电保存 ====================
    def _blob_size(self):
        return 4 + len(KEYS) * self.slots * 2

    def export_blob(self):
        """定长二进制: <版本:u16><pos:u16><4 通道 x slots 个 int16>。"""
        blob = bytearray(self._blob_size())
        struct.pack_into("<HH", blob, 0, _STATE_VERSION, self.pos)
        off = 4
        for key in KEYS:
            for v in self.ring[key]:
                struct.pack_into("<h", blob, off, INVALID if v is None else v)
                off += 2
        return blob

    def import_blob(self, blob):
        """恢复 export_blob() 的内容; 格式/版本不对时返回 False。"""
        if blob is None or len(blob) != self._blob_size():
            return False
        try:
            version, pos = struct.unpack_from("<HH", blob, 0)
        except Exception:
            return False
        if version != _STATE_VERSION or pos >= self.slots:
            return False
        off = 4
        for key in KEYS:
            ring = self.ring[key]
            for i in range(self.slots):
                v = struct.unpack_from("<h", blob, off)[0]
                off += 2
                ring[i] = None if v == INVALID else v
        self.pos = pos
        # 存盘时无法知道环里到底写过几格; 没写过的格子是 INVALID,
        # 统计时会跳过, 所以保守地认为整圈都扫描一遍即可。
        self.filled = self.slots
        self._cache = None
        return True

    def save(self):
        if not self.persist or esp32 is None:
            return False
        try:
            blob = self.export_blob()
            nvs = esp32.NVS(_NVS_NAMESPACE)
            nvs.set_blob(_NVS_KEY, blob)
            nvs.commit()
            if cfg.DEBUG:
                print("[hist] 历史已保存 (%d 字节)" % len(blob))
            return True
        except Exception as e:
            print("[hist] 保存失败:", e)
            return False

    def load(self):
        """
        上电时恢复。注意板子没有 RTC: 断电期间的空白会被当作"一直在线"
        处理, 也就是说断电前最后 12 小时的数据仍然算在窗口里。
        """
        if esp32 is None:
            return False
        try:
            nvs = esp32.NVS(_NVS_NAMESPACE)
            buf = bytearray(self._blob_size())
            n = nvs.get_blob(_NVS_KEY, buf)
        except Exception:
            return False
        if not n:
            return False
        ok = self.import_blob(bytes(buf[:n]))
        self.restored = ok
        return ok


def erase_state():
    """删除已保存的历史(在 REPL 里手动清空时用, 之后复位生效)。"""
    if esp32 is None:
        return False
    try:
        nvs = esp32.NVS(_NVS_NAMESPACE)
        nvs.erase_key(_NVS_KEY)
        nvs.commit()
        return True
    except Exception:
        return False
