# -*- coding: utf-8 -*-
"""
单独测试 SHT40 温湿度传感器
    python tools/deploy.py run test_sht40
"""

import time
from machine import Pin, I2C

import config as cfg
import sht40

i2c = I2C(cfg.I2C_ID, sda=Pin(cfg.I2C_SDA), scl=Pin(cfg.I2C_SCL),
          freq=cfg.I2C_FREQ)
print("I2C 扫描:", [hex(a) for a in i2c.scan()])

if cfg.SHT40_ADDR not in i2c.scan():
    print("!! 0x%02X 上没有设备: 检查 SDA/SCL/VCC/GND" % cfg.SHT40_ADDR)
    raise SystemExit

s = sht40.SHT40(i2c, cfg.SHT40_ADDR)
try:
    print("序列号: 0x%08X" % s.read_serial())
except OSError as e:
    print("读序列号失败:", e)
    raise SystemExit

print("开始测量 (Ctrl-C 结束)…")
while True:
    try:
        t, rh = s.measure()
        print("温度 %.2f C   湿度 %.2f %%RH   (露点 %.1f C)"
              % (t, rh, t - (100 - rh) / 5.0))
    except OSError as e:
        print("读取失败:", e)
    time.sleep(1)
