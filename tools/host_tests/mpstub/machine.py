# -*- coding: utf-8 -*-
"""
MicroPython machine 模块的最小替身 (只用于电脑上跑测试)
===================================================
Pin / I2C / UART 都只是"能构造、不报错、返回空数据"的空壳。
之所以需要它, 是因为 sensors.py 在模块级 `from machine import Pin, I2C`,
导入时不碰硬件就会执行到这一行。
"""


class Pin:
    IN = 1
    OUT = 3
    OPEN_DRAIN = 5
    PULL_UP = 2
    PULL_DOWN = 2
    IRQ_RISING = 1
    IRQ_FALLING = 2

    def __init__(self, id, mode=-1, pull=None, value=None):
        self.id = id
        self.mode = mode
        self._v = 1 if value is None else value

    def value(self, v=None):
        if v is None:
            return self._v
        self._v = v

    def init(self, *a, **kw):
        pass

    def irq(self, *a, **kw):
        return None

    def on(self):
        self._v = 1

    def off(self):
        self._v = 0


class I2C:
    def __init__(self, *a, **kw):
        pass

    def scan(self):
        return []

    def init(self, *a, **kw):
        # 和真机一致: MicroPython 的 I2C 不支持原地 init
        raise OSError(1, "init not supported")

    def deinit(self):
        pass


class UART:
    def __init__(self, *a, **kw):
        pass

    def any(self):
        return 0

    def read(self, n=None):
        return None

    def write(self, b):
        return len(b)


class WDT:
    def __init__(self, *a, **kw):
        pass

    def feed(self):
        pass
