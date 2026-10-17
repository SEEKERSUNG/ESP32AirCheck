# -*- coding: utf-8 -*-
"""
单独测试 ZE08-CH2O 甲醛传感器 (UART)
    python tools/deploy.py run test_ze08

ZE08 出厂默认每秒主动上传一帧, 这里把收到的原始字节也用十六进制打出来,
方便判断接线/波特率是否正确。
"""

import time
from machine import Pin, UART

import config as cfg
import ze08

u = UART(cfg.ZE08_UART_ID, baudrate=cfg.ZE08_BAUD,
         tx=Pin(cfg.ZE08_TX), rx=Pin(cfg.ZE08_RX), timeout=0, rxbuf=512)

z = ze08.ZE08(u)
print("监听 UART%d  TX=GPIO%d RX=GPIO%d  %d bps"
      % (cfg.ZE08_UART_ID, cfg.ZE08_TX, cfg.ZE08_RX, cfg.ZE08_BAUD))

t0 = time.ticks_ms()
asked = False
last_report = 0
dumped = 0
while True:
    raw = u.read()
    if raw:
        if dumped < 3:
            print("原始字节:", " ".join("%02X" % b for b in raw))
            dumped += 1
        z.buf.extend(raw)

    ppb = z.poll()

    now = time.ticks_ms()
    if ppb is not None and time.ticks_diff(now, last_report) > 900:
        last_report = now
        print("甲醛 %4d ppb = %.3f mg/m3    (有效帧 %d, 坏帧 %d)"
              % (z.ppb, z.ppb * ze08.PPB_TO_MG, z.frames, z.bad_frames))
    elif ppb is None and not asked and time.ticks_diff(now, t0) > 1500:
        print("1.5 秒没收到主动上传帧, 发一次问答读取命令…")
        z.request_reading()
        asked = True

    time.sleep_ms(20)
