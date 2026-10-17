# -*- coding: utf-8 -*-
"""
单独测试 SCD40 二氧化碳传感器
    python tools/deploy.py run test_scd40

SCD40 内部每 5 秒产生一次结果, 所以这里也是 5 秒打印一行。
"""

import time
from machine import Pin, I2C

import config as cfg
import scd40

i2c = I2C(cfg.I2C_ID, sda=Pin(cfg.I2C_SDA), scl=Pin(cfg.I2C_SCL),
          freq=cfg.I2C_FREQ)
print("I2C 扫描:", [hex(a) for a in i2c.scan()])

if cfg.SCD40_ADDR not in i2c.scan():
    print("!! 0x%02X 上没有设备: 检查 SDA/SCL/VCC/GND" % cfg.SCD40_ADDR)
    raise SystemExit

c = scd40.SCD40(i2c, cfg.SCD40_ADDR)
try:
    sn = c.serial_number()
    print("序列号: 0x%012X" % sn)
except OSError as e:
    print("读序列号失败:", e)
    raise SystemExit

print("启动周期测量, 前 5 秒没有数据是正常的…")
c.start_periodic_measurement()

n = 0
while True:
    try:
        if c.data_ready():
            co2, t, rh = c.read_measurement()
            print("CO2 %4d ppm   温度 %.1f C   湿度 %.1f %%RH"
                  % (co2, t, rh))
    except OSError as e:
        print("读取失败:", e)
        time.sleep(1)
    time.sleep(0.5)
    n += 1
