# -*- coding: utf-8 -*-
"""
MicroPython framebuf 的最小替身 (只用于电脑上跑不画字的测试)
=======================================================
本文件只实现了 ui.py / ssd1306.py 在导入阶段会碰到的接口:

    FrameBuffer(buffer, w, h, format)  pixel()  fill()  fill_rect()
    rect()  hline()  vline()  line()  blit()  scroll()  text()

其中 text() 是**空实现** —— 电脑上没有 MicroPython 的 8x8 字库,
所以凡是需要看真实排版的测试(例如 tools/tests/test_ui.py)必须
在开发板上跑, 不能在电脑上跑。run_board_test.py 会拒绝这类脚本。
"""

MONO_VLSB = 0
RGB565 = 1
MONO_HLSB = 3
MONO_HMSB = 5


class FrameBuffer:
    def __init__(self, buffer, width, height, format=MONO_VLSB, stride=None):
        self.buf = buffer
        self.width = width
        self.height = height
        self.format = format
        self.stride = width if stride is None else stride

    def _idx(self, x, y):
        if self.format == MONO_VLSB:
            return x + (y >> 3) * self.stride, 1 << (y & 7)
        off = (y * self.stride + x) >> 3
        if self.format == MONO_HMSB:
            return off, 1 << (7 - (x & 7))
        return off, 0x80 >> (x & 7)

    # ---------------- 画点 / 读点 ----------------
    def pixel(self, x, y, c=None):
        if not (0 <= x < self.width and 0 <= y < self.height):
            return None
        off, bit = self._idx(x, y)
        if c is None:
            return 1 if self.buf[off] & bit else 0
        if c:
            self.buf[off] |= bit
        else:
            self.buf[off] &= 0xFF ^ bit
        return None

    def fill(self, c):
        v = 0xFF if c else 0x00
        for i in range(len(self.buf)):
            self.buf[i] = v

    def fill_rect(self, x, y, w, h, c):
        for yy in range(y, y + h):
            for xx in range(x, x + w):
                self.pixel(xx, yy, c)

    def rect(self, x, y, w, h, c):
        self.hline(x, y, w, c)
        self.hline(x, y + h - 1, w, c)
        self.vline(x, y, h, c)
        self.vline(x + w - 1, y, h, c)

    def hline(self, x, y, w, c):
        for xx in range(x, x + w):
            self.pixel(xx, y, c)

    def vline(self, x, y, h, c):
        for yy in range(y, y + h):
            self.pixel(x, yy, c)

    def line(self, x1, y1, x2, y2, c):
        dx = abs(x2 - x1)
        dy = -abs(y2 - y1)
        sx = 1 if x1 < x2 else -1
        sy = 1 if y1 < y2 else -1
        err = dx + dy
        while True:
            self.pixel(x1, y1, c)
            if x1 == x2 and y1 == y2:
                break
            e2 = 2 * err
            if e2 >= dy:
                err += dy
                x1 += sx
            if e2 <= dx:
                err += dx
                y1 += sy

    def blit(self, src, x, y, key=-1):
        for yy in range(src.height):
            for xx in range(src.width):
                p = src.pixel(xx, yy)
                if p and p != key:
                    self.pixel(x + xx, y + yy, 1)

    # ---------------- 电脑上画不出来的部分 ----------------
    def text(self, s, x, y, c=1):
        """电脑上没有 8x8 字库, 这里故意什么都不画。"""
        return None

    def scroll(self, dx, dy):
        pass

    def ellipse(self, *a, **kw):
        pass
