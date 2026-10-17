# -*- coding: utf-8 -*-
"""
OLED 自检 —— 判断屏幕是 SSD1306 还是 SH1106
================================================================
在开发板上运行:
    python tools/deploy.py run test_oled

脚本会轮流用 SSD1306 / SH1106 两种方式初始化屏幕, 每次画一个"贴边的方框"
和对应型号的名字, 每个型号显示 4 秒, 循环往复。

怎么看结果:
    * 方框完整、四边都在 -> 这一轮对应的型号就是你的屏幕
    * 左边少一条边 / 右边被裁掉 -> 型号选错了
然后到 config.py 里把 OLED_CONTROLLER 改成正确的型号。
"""

import time
from machine import Pin, I2C

import config as cfg

i2c = I2C(cfg.I2C_ID, sda=Pin(cfg.I2C_SDA), scl=Pin(cfg.I2C_SCL),
          freq=cfg.I2C_FREQ)

print("I2C 扫描:", [hex(a) for a in i2c.scan()])
if cfg.OLED_ADDR not in i2c.scan():
    print("!! 在地址 0x%02X 没有找到 OLED, 请检查 SDA/SCL/VCC/GND 和地址跳线"
          % cfg.OLED_ADDR)
    raise SystemExit


def make(kind):
    if kind == "ssd1306":
        import ssd1306
        return ssd1306.SSD1306_I2C(cfg.OLED_WIDTH, cfg.OLED_HEIGHT, i2c,
                                   addr=cfg.OLED_ADDR)
    import sh1106
    return sh1106.SH1106_I2C(cfg.OLED_WIDTH, cfg.OLED_HEIGHT, i2c,
                             addr=cfg.OLED_ADDR)


while True:
    for kind in ("ssd1306", "sh1106"):
        try:
            oled = make(kind)
        except Exception as e:
            print(kind, "初始化失败:", e)
            continue
        w, h = oled.width, oled.height
        oled.fill(0)
        # 贴边方框: 型号选错时四边会错位, 一眼就能看出来
        oled.rect(0, 0, w, h, 1)
        oled.rect(1, 1, w - 2, h - 2, 1)
        oled.text("If border is", 10, 14)
        oled.text("COMPLETE then", 10, 24)
        oled.text("=> " + kind.upper(), 10, 34)
        for i in range(0, w, 8):
            oled.pixel(i, h // 2 + 10, 1)
        oled.show()
        print("正在显示:", kind.upper(), "(看方框是否完整)")
        time.sleep(4)
        try:
            oled.poweroff()
        except Exception:
            pass
