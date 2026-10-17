# -*- coding: utf-8 -*-
"""
SH1106 OLED 驱动 (I2C)
================================================================
部分 1.3 寸 / 早期 1.2 寸 128x64 OLED 用的是 SH1106 而不是 SSD1306。
两者指令集基本兼容, 但有两个关键区别:

  1. SH1106 不支持 SSD1306 的 "内存寻址模式"(0x20/0x21/0x22) 指令,
     只能用页寻址: 每页先设页地址 0xB0+page, 再设列地址。
  2. SH1106 内部是 132 列 RAM, 而面板只有 128 列, 所以列地址要从 2 开始,
     否则整幅画面会向左偏 2 个像素(表现为右边缺 2 列、左边多 2 列)。

如果你的屏幕是 SSD1306 却按本驱动初始化, 通常也能显示, 但可能错位。
把 config.py 里的 OLED_CONTROLLER 改成对应的型号即可。

参考: https://github.com/micropython/micropython
"""

from micropython import const
import framebuf

SET_CONTRAST = const(0x81)
SET_ENTIRE_ON = const(0xA4)
SET_NORM_INV = const(0xA6)
SET_DISP = const(0xAE)
SET_DISP_START_LINE = const(0x40)
SET_SEG_REMAP = const(0xA0)
SET_MUX_RATIO = const(0xA8)
SET_COM_OUT_DIR = const(0xC0)
SET_DISP_OFFSET = const(0xD3)
SET_COM_PIN_CFG = const(0xDA)
SET_DISP_CLK_DIV = const(0xD5)
SET_PRECHARGE = const(0xD9)
SET_VCOM_DESEL = const(0xDB)
SET_CHARGE_PUMP = const(0x8D)

# SH1106 面板 128 列, 但内部 RAM 有 132 列: 通常向右偏移 2 列
_COL_OFFSET = 2

# 兼容部分 SPD0301/其他 128 列控制器: 设为 0
_COL_OFFSET_ALT = 2


class SH1106(framebuf.FrameBuffer):
    def __init__(self, width, height, external_vcc, col_offset=_COL_OFFSET):
        self.width = width
        self.height = height
        self.external_vcc = external_vcc
        self.pages = self.height // 8
        self.col_offset = col_offset
        self.buffer = bytearray(self.pages * self.width)
        super().__init__(self.buffer, self.width, self.height, framebuf.MONO_VLSB)
        self.init_display()

    def init_display(self):
        for cmd in (
                SET_DISP,                        # 关显示
                SET_DISP_START_LINE,             # 从第 0 行开始
                SET_SEG_REMAP | 0x01,            # 列地址 127 映射到 SEG0
                SET_MUX_RATIO,
                self.height - 1,
                SET_COM_OUT_DIR | 0x08,          # COM[N] -> COM0
                SET_DISP_OFFSET,
                0x00,
                SET_COM_PIN_CFG,
                0x02 if self.width > 2 * self.height else 0x12,
                SET_DISP_CLK_DIV,
                0x80,
                SET_PRECHARGE,
                0x22 if self.external_vcc else 0xF1,
                SET_VCOM_DESEL,
                0x30,
                SET_CONTRAST,
                0xFF,
                SET_ENTIRE_ON,
                SET_NORM_INV,
                SET_CHARGE_PUMP,
                0x10 if self.external_vcc else 0x14,
                SET_DISP | 0x01,                 # 开显示
        ):
            self.write_cmd(cmd)
        self.fill(0)
        self.show()

    def poweroff(self):
        self.write_cmd(SET_DISP)

    def poweron(self):
        self.write_cmd(SET_DISP | 0x01)

    def contrast(self, contrast):
        self.write_cmd(SET_CONTRAST)
        self.write_cmd(contrast)

    def invert(self, invert):
        self.write_cmd(SET_NORM_INV | (invert & 1))

    def show(self):
        """SH1106 只能按页写, 每页 128 字节。"""
        buf = self.buffer
        for page in range(self.pages):
            self.write_cmd(0xB0 + page)
            self.write_cmd(self.col_offset & 0x0F)
            self.write_cmd(0x10 | ((self.col_offset >> 4) & 0x0F))
            start = page * self.width
            self.write_data(buf[start:start + self.width])

    def clear(self):
        self.fill(0)


class SH1106_I2C(SH1106):
    def __init__(self, width, height, i2c, addr=0x3C, external_vcc=False,
                 col_offset=_COL_OFFSET):
        self.i2c = i2c
        self.addr = addr
        self.temp = bytearray(2)
        self.write_list = [b"\x40", None]     # Co=0, D/C#=1
        super().__init__(width, height, external_vcc, col_offset)

    def write_cmd(self, cmd):
        self.temp[0] = 0x80                   # Co=1, D/C#=0
        self.temp[1] = cmd
        self.i2c.writeto(self.addr, self.temp)

    def write_data(self, buf):
        self.write_list[1] = buf
        self.i2c.writevto(self.addr, self.write_list)
