# -*- coding: utf-8 -*-
"""
SCD40 二氧化碳(CO2)传感器驱动 (Sensirion SCD4x 系列)
================================================================
总线 : I2C, 地址固定 0x62
量程 : CO2 400~5000 ppm, 温度 -10~60 C, 湿度 0~100 %RH

SCD40 是光声(NDIR)原理, 内部有自己的测量节奏:
  * 上电后需要发命令启动周期测量: start_periodic_measurement()
  * 之后每 5 秒产生一个新结果
  * 用 get_data_ready_status() 查询是否有新数据, 有了再 read_measurement()

这样整个采集过程不阻塞, 屏幕上其它数据可以照常刷新。

注意 SCD4x 的"字"格式和 SHT4x 一样: 2 字节数据 + 1 字节 CRC。
CRC: 多项式 0x31, 初值 0xFF。

单独测试本驱动:
    >>> from machine import Pin, I2C
    >>> from time import sleep
    >>> import scd40
    >>> i2c = I2C(0, sda=Pin(32), scl=Pin(33), freq=400000)
    >>> c = scd40.SCD40(i2c)
    >>> print(c.serial_number())
    >>> c.start_periodic_measurement(); sleep(5)
    >>> print(c.read_measurement())     # -> (612, 25.1, 47.9)
"""

from time import sleep_ms, ticks_ms, ticks_diff

_ADDR_DEFAULT = 0x62

_CMD_START_PERIODIC = 0x21B1
_CMD_READ_MEASUREMENT = 0xEC05
_CMD_STOP_PERIODIC = 0x3F86
_CMD_SET_TEMP_OFFSET = 0x241D
_CMD_GET_TEMP_OFFSET = 0x2318
_CMD_GET_DATA_READY = 0xE4B8
_CMD_REINIT = 0x3646
_CMD_FACTORY_RESET = 0x3632
_CMD_GET_SERIAL = 0x3682
_CMD_SELF_TEST = 0x3639


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


class SCD40:
    def __init__(self, i2c, addr=_ADDR_DEFAULT):
        self.i2c = i2c
        self.addr = addr
        self.co2 = None
        self.temperature = None
        self.humidity = None
        self._measuring = False

    # ---------------- 底层 ----------------
    def _send(self, cmd):
        self.i2c.writeto(self.addr, bytes([(cmd >> 8) & 0xFF, cmd & 0xFF]))

    def _send_arg(self, cmd, value):
        self.i2c.writeto(
            self.addr,
            bytes([(cmd >> 8) & 0xFF, cmd & 0xFF]) + _word_with_crc(value),
        )

    def _read_words(self, nwords):
        data = self.i2c.readfrom(self.addr, nwords * 3)
        words = []
        for i in range(nwords):
            w = data[i * 3:i * 3 + 3]
            if crc8(w[:2]) != w[2]:
                raise OSError("SCD40 CRC 校验失败")
            words.append((w[0] << 8) | w[1])
        return words

    def _read8(self):
        return self.i2c.readfrom(self.addr, 1)[0]

    # ---------------- 探测 / 自检 ----------------
    def present(self):
        try:
            self.serial_number()
            return True
        except Exception:
            return False

    def serial_number(self):
        self._send(_CMD_GET_SERIAL)
        sleep_ms(1)
        w = self._read_words(3)
        return (w[0] << 32) | (w[1] << 16) | w[2]

    def self_test(self):
        """返回 True 表示自检通过。"""
        self._send(_CMD_SELF_TEST)
        sleep_ms(10000)              # 数据手册要求等待 10s
        return self._read_words(1)[0] == 0

    # ---------------- 周期测量 ----------------
    def start_periodic_measurement(self):
        self._send(_CMD_START_PERIODIC)
        self._measuring = True

    def stop_periodic_measurement(self):
        if self._measuring:
            self._send(_CMD_STOP_PERIODIC)
            sleep_ms(500)           # 数据手册要求 >=500ms
            self._measuring = False

    def data_ready(self):
        """内部是否有尚未读取的新数据。"""
        self._send(_CMD_GET_DATA_READY)
        sleep_ms(1)
        return (self._read_words(1)[0] & 0x07FF) != 0

    def read_measurement(self):
        """读取一次结果, 返回 (CO2 ppm, 温度 C, 湿度 %RH)。"""
        self._send(_CMD_READ_MEASUREMENT)
        sleep_ms(1)
        co2_raw, t_raw, rh_raw = self._read_words(3)

        self.co2 = co2_raw
        self.temperature = -45.0 + 175.0 * t_raw / 65535.0
        self.humidity = 100.0 * rh_raw / 65535.0
        return self.co2, self.temperature, self.humidity

    # ---------------- 配置 ----------------
    def set_temperature_offset(self, offset_c):
        """设置温度补偿值, 抵消主板自发热带来的偏差(单位 C)。"""
        ticks = int(offset_c * 65535.0 / 175.0)
        self._send_arg(_CMD_SET_TEMP_OFFSET, ticks & 0xFFFF)
        sleep_ms(1)

    def get_temperature_offset(self):
        self._send(_CMD_GET_TEMP_OFFSET)
        sleep_ms(1)
        return self._read_words(1)[0] * 175.0 / 65535.0

    def reinit(self):
        """保留 ASC 校准数据, 重新初始化(不改变配置)。"""
        self._send(_CMD_REINIT)
        sleep_ms(20)
        self._measuring = False

    def factory_reset(self):
        self._send(_CMD_FACTORY_RESET)
        sleep_ms(1200)
        self._measuring = False


class SCD40Async:
    """
    在 SCD40 之上加一层"就绪才读"的包装, 供主循环非阻塞调用。

    poll() 返回:
        None        本次没有新数据(或还在预热)
        (co2, t, rh) 有新数据
    """

    def __init__(self, scd, min_interval_ms=4800):
        self.scd = scd
        self.min_interval_ms = min_interval_ms
        self._last = ticks_ms() - min_interval_ms

    def start(self):
        self.scd.start_periodic_measurement()
        self._last = ticks_ms()

    def poll(self):
        now = ticks_ms()
        if ticks_diff(now, self._last) < self.min_interval_ms:
            return None
        try:
            if not self.scd.data_ready():
                return None
        except OSError:
            return None
        self._last = now
        return self.scd.read_measurement()
