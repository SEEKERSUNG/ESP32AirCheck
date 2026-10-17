# -*- coding: utf-8 -*-
"""
SGP40 空气质量(TVOC)传感器驱动 (Sensirion SGP40)
================================================================
总线 : I2C, 地址固定 0x59

SGP40 本身输出的是"原始电阻信号 SRAW_VOC"(0~65535), 并不是给用户看的
TVOC 值。需要把 SRAW_VOC 送入 Sensirion 官方的 VOC 算法(见 voc.py),
才能得到国际通用的 VOC Index(0~500, 100 为基线)。

另外 SGP40 支持温湿度补偿: 把 SHT40 测到的温湿度作为参数传入,
可以让 VOC 读数更准。所以本项目里 SGP40 会用到 SHT40 的结果。
如果 SHT40 不在线, 就用默认的 25C / 50%RH 补偿值。

单独测试本驱动:
    >>> from machine import Pin, I2C
    >>> import sgp40, voc
    >>> i2c = I2C(0, sda=Pin(32), scl=Pin(33), freq=400000)
    >>> g = sgp40.SGP40(i2c)
    >>> print(g.self_test())                 # 0xD400 表示自检通过
    >>> algo = voc.VocAlgorithm()
    >>> print(algo.process(g.measure_raw(50.0, 25.0)))
"""

from time import sleep_ms

_ADDR_DEFAULT = 0x59

_CMD_MEASURE_RAW = 0x260F
_CMD_SELF_TEST = 0x280E
_CMD_HEATER_OFF = 0x3615
_CMD_SOFT_RESET = 0x0006

# 未做温湿度补偿时的默认参数
_RH_DEFAULT_TICKS = 0x8000      # 50 %RH
_T_DEFAULT_TICKS = 0x6666       # 25 C


def crc8(data):
    """Sensirion CRC-8: 多项式 0x31, 初值 0xFF。"""
    crc = 0xFF
    for b in data:
        crc ^= b
        for _ in range(8):
            if crc & 0x80:
                crc = ((crc << 1) ^ 0x31) & 0xFF
            else:
                crc = (crc << 1) & 0xFF
    return crc


def _word_with_crc(value):
    hi = (value >> 8) & 0xFF
    lo = value & 0xFF
    return bytes([hi, lo, crc8(bytes([hi, lo]))])


class SGP40:
    def __init__(self, i2c, addr=_ADDR_DEFAULT):
        self.i2c = i2c
        self.addr = addr
        self.raw = None

    # ---------------- 底层 ----------------
    def _send(self, cmd, args=b""):
        """命令字(2 字节) + 参数(每个 2 字节参数后跟 1 字节 CRC)。"""
        buf = bytearray([(cmd >> 8) & 0xFF, cmd & 0xFF])
        buf.extend(args)
        self.i2c.writeto(self.addr, buf)

    def _read_words(self, nwords):
        data = self.i2c.readfrom(self.addr, nwords * 3)
        words = []
        for i in range(nwords):
            w = data[i * 3:i * 3 + 3]
            if crc8(w[:2]) != w[2]:
                raise OSError("SGP40 CRC 校验失败")
            words.append((w[0] << 8) | w[1])
        return words

    # ---------------- 对外接口 ----------------
    def present(self):
        """探测传感器是否在线: 能读回一帧带正确 CRC 的数据即算在线。"""
        try:
            self.measure_raw()
            return True
        except Exception:
            return False

    def soft_reset(self):
        self._send(_CMD_SOFT_RESET)
        sleep_ms(20)

    def self_test(self):
        """自检。返回 0xD400 表示通过。"""
        self._send(_CMD_SELF_TEST)
        sleep_ms(320)
        return self._read_words(1)[0]

    def heater_off(self):
        self._send(_CMD_HEATER_OFF)
        sleep_ms(1)

    def measure_raw(self, humidity=None, temperature=None):
        """
        读取原始 VOC 信号 SRAW_VOC。
        humidity    : %RH, 传 None 表示不做补偿
        temperature : 摄氏度, 传 None 表示不做补偿
        返回 0~65535 的原始值。
        """
        if humidity is None:
            rh_ticks = _RH_DEFAULT_TICKS
        else:
            if humidity < 0.0:
                humidity = 0.0
            elif humidity > 100.0:
                humidity = 100.0
            rh_ticks = int(humidity * 65535.0 / 100.0)

        if temperature is None:
            t_ticks = _T_DEFAULT_TICKS
        else:
            if temperature < -45.0:
                temperature = -45.0
            elif temperature > 130.0:
                temperature = 130.0
            t_ticks = int((temperature + 45.0) * 65535.0 / 175.0)

        args = _word_with_crc(rh_ticks) + _word_with_crc(t_ticks)
        self._send(_CMD_MEASURE_RAW, args)
        sleep_ms(35)                     # 转换时间典型 30ms
        self.raw = self._read_words(1)[0]
        return self.raw
