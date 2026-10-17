# -*- coding: utf-8 -*-
"""
单独测试 SGP40 (TVOC 原始值 -> VOC 指数)
    python tools/deploy.py run test_sgp40

注意: VOC 算法前 45 秒是 blackout(指数恒为 0), 之后才开始出数,
      而且要过一段时间才会收敛到 100 附近, 属于正常现象。
"""

import time
from machine import Pin, I2C

import config as cfg
import sgp40
import voc

i2c = I2C(cfg.I2C_ID, sda=Pin(cfg.I2C_SDA), scl=Pin(cfg.I2C_SCL),
          freq=cfg.I2C_FREQ)
print("I2C 扫描:", [hex(a) for a in i2c.scan()])

if cfg.SGP40_ADDR not in i2c.scan():
    print("!! 0x%02X 上没有设备: 检查 SDA/SCL/VCC/GND" % cfg.SGP40_ADDR)
    raise SystemExit

g = sgp40.SGP40(i2c, cfg.SGP40_ADDR)
try:
    st = g.self_test()
    print("自检返回 0x%04X (%s)" % (st, "通过" if st == 0xD400 else "未通过"))
except OSError as e:
    print("自检失败:", e)

algo = voc.VocAlgorithm()
print("开始采集, 每秒一次 (Ctrl-C 结束)…")
print(" %6s %8s %6s" % ("秒", "SRAW", "VOC"))
n = 0
while True:
    t0 = time.ticks_ms()
    try:
        raw = g.measure_raw(50.0, 25.0)      # 固定用 50%RH / 25C 补偿
        idx = algo.process(raw)
        print(" %6d %8d %6d" % (n, raw, idx))
    except OSError as e:
        print("读取失败:", e)
    n += 1
    dt = time.ticks_diff(time.ticks_ms(), t0)
    time.sleep_ms(max(0, 1000 - dt))          # 算法要求稳定的 1s 采样间隔
