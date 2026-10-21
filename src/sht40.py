# -*- coding: utf-8 -*-
"""
SHT40 高精度温湿度传感器驱动 (Sensirion SHT4x 系列)
================================================================
总线 : I2C, 默认地址 0x44
量程 : 温度 -40~125 C, 湿度 0~100 %RH
精度 : ±0.2 C / ±1.8 %RH

返回数据的换算 (官方数据手册 SHT4x Datasheet 4.1 节):
    T  = -45 + 175 * S_T  / (2^16 - 1)      [(C)]
    RH =  -6 + 125 * S_RH / (2^16 - 1)      [%RH], 需截断到 0~100

单独测试本驱动:
    >>> from machine import Pin, I2C
    >>> import sht40
    >>> i2c = I2C(0, sda=Pin(32), scl=Pin(33), freq=400000)
    >>> s = sht40.SHT40(i2c)
    >>> print(s.read_serial())
    >>> print(s.measure())          # -> (25.34, 48.7)
"""

from time import sleep_ms

_ADDR_DEFAULT = 0x44

_CMD_MEASURE_HIGH = 0xFD    # 高精度测量, 转换时间 ~8.3ms
_CMD_MEASURE_MED = 0xF6     # 中精度,   ~4.5ms
_CMD_MEASURE_LOW = 0xE0     # 低精度,   ~1.7ms
_CMD_SOFT_RESET = 0x94
_CMD_READ_SERIAL = 0x89
_CMD_HEATER_200MW_1S = 0x39


def crc8(data):
    """Sensirion CRC-8: 多项式 0x31, 初值 0xFF, 不取反。"""
    crc = 0xFF
    for b in data:
        crc ^= b
        for _ in range(8):
            if crc & 0x80:
                crc = ((crc << 1) ^ 0x31) & 0xFF
            else:
                crc = (crc << 1) & 0xFF
    return crc


class SHT40:
    def __init__(self, i2c, addr=_ADDR_DEFAULT):
        self.i2c = i2c
        self.addr = addr
        self.temperature = None
        self.humidity = None
        self.serial = None

    # ---------------- 底层 ----------------
    def _cmd(self, cmd):
        self.i2c.writeto(self.addr, bytes([cmd]))

    def _read_check(self, nwords):
        """读 nwords 个 (数据 2 字节 + CRC 1 字节) 组成的应答。"""
        data = self.i2c.readfrom(self.addr, nwords * 3)
        words = []
        for i in range(nwords):
            w = data[i * 3:i * 3 + 3]
            if crc8(w[:2]) != w[2]:
                raise OSError("SHT40 CRC 校验失败")
            words.append((w[0] << 8) | w[1])
        return words

    # ---------------- 对外接口 ----------------
    def present(self):
        """探测传感器是否在线(读序列号)。在线返回 True。"""
        try:
            self.read_serial()
            return True
        except Exception:
            return False

    def reset(self):
        self._cmd(_CMD_SOFT_RESET)
        sleep_ms(3)

    def read_serial(self):
        self._cmd(_CMD_READ_SERIAL)
        sleep_ms(3)
        w = self._read_check(2)
        self.serial = (w[0] << 16) | w[1]
        return self.serial

    def measure(self, precision="high"):
        """读一次温湿度, 返回 (温度 C, 湿度 %RH)。"""
        cmd, wait = {
            "high": (_CMD_MEASURE_HIGH, 10),
            "med": (_CMD_MEASURE_MED, 6),
            "low": (_CMD_MEASURE_LOW, 3),
        }[precision]
        self._cmd(cmd)
        sleep_ms(wait)
        t_raw, rh_raw = self._read_check(2)

        t = -45.0 + 175.0 * t_raw / 65535.0
        rh = -6.0 + 125.0 * rh_raw / 65535.0
        if rh < 0.0:
            rh = 0.0
        elif rh > 100.0:
            rh = 100.0

        self.temperature = t
        self.humidity = rh
        return t, rh

    def heater_200mw_1s(self):
        """片上加热器 200mW 1s(用于除凝露), 返回加热结束后的 (温度, 湿度)。"""
        self._cmd(_CMD_HEATER_200MW_1S)
        sleep_ms(1200)                  # 数据手册 t_heater 最大 1.19s
        t_raw, rh_raw = self._read_check(2)
        t = -45.0 + 175.0 * t_raw / 65535.0
        rh = -6.0 + 125.0 * rh_raw / 65535.0
        if rh < 0.0:
            rh = 0.0
        elif rh > 100.0:
            rh = 100.0
        self.temperature = t
        self.humidity = rh
        return t, rh
