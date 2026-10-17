# -*- coding: utf-8 -*-
"""
界面排版检查 —— 在开发板上跑, 不需要真的接屏幕
================================================================
用一块"假屏幕"(只有显存, 不做 I2C 输出)渲染每个界面, 然后把像素
以 ASCII 画到串口上, 用来检查排版有没有重叠 / 出界 / 缺字。

    python tools/deploy.py run test_ui
"""

import framebuf

import config as cfg
import ui


class FakeOLED(framebuf.FrameBuffer):
    """和 SSD1306 一样的显存布局, 但不做任何 I2C 输出。"""

    def __init__(self, w=128, h=64):
        self.width = w
        self.height = h
        self.buf = bytearray(w * h // 8)
        super().__init__(self.buf, w, h, framebuf.MONO_VLSB)

    def show(self):
        pass

    def contrast(self, v):
        pass


def dump(oled, title):
    print("=" * 66)
    print(title)
    print("+" + "-" * oled.width + "+")
    for y in range(oled.height):
        row = []
        for x in range(oled.width):
            row.append("#" if oled.pixel(x, y) else ".")
        print("|" + "".join(row) + "|")
    print("+" + "-" * oled.width + "+")


def chan(state, warm_left=0, learning=False):
    return {"state": state, "name": "X", "addr": "", "learning": learning,
            "warm_left": warm_left}


def snapshot(states, values, alarm=None, uptime=12 * 60 + 34, oled_ok=True):
    channels = {}
    for k in ("sht40", "ze08", "sgp40", "scd40"):
        channels[k] = chan(states.get(k, ui.ST_READY))
    a = {"sht40": False, "ze08": False, "sgp40": False, "scd40": False}
    if alarm:
        a.update(alarm)
    return {
        "uptime": uptime,
        "oled_ok": oled_ok,
        "oled_addr": cfg.OLED_ADDR,
        "channels": channels,
        "values": values,
        "alarm": a,
        "info": {"voc_raw": 32145, "voc_learning": True, "voc_uptime": 120,
                 "ze08_frames": 121, "ze08_bad": 3,
                 "scan": "3C 44 59 62"},
    }


FULL = {"temp": 25.4, "rh": 48.2, "hcho_ppb": 32, "hcho_mg": 0.04,
        "tvoc": 123, "co2": 612}

oled = FakeOLED(cfg.OLED_WIDTH, cfg.OLED_HEIGHT)
u = ui.UI(oled, cfg.VERSION)

# ---------------------------------------------------------------- 开机画面
u.splash()
dump(oled, "开机画面")

# ---------------------------------------------------------------- 自检
mix = {
    "sht40": ui.ST_READY,
    "sgp40": ui.ST_PROBE,
    "scd40": ui.ST_ABSENT,
    "ze08": ui.ST_ABSENT,
}
rows = [("SHT40", mix["sht40"]), ("SGP40", mix["sgp40"]),
        ("SCD40", mix["scd40"]), ("ZE08", mix["ze08"])]
u.show_selftest(rows)
dump(oled, "传感器自检 (2 个未连接, 1 个检测中)")

# ---------------------------------------------------------------- 主界面 全部正常
u.show_main(snapshot({}, FULL))
dump(oled, "第 1 页 主界面 - 全部就绪")

# ---------------------------------------------------------------- 主界面 预热中
warm = {
    "sht40": ui.ST_READY,
    "sgp40": ui.ST_WARMUP,
    "scd40": ui.ST_WARMUP,
    "ze08": ui.ST_ABSENT,
}
v = dict(FULL)
v["hcho_ppb"] = None
v["hcho_mg"] = None
v["tvoc"] = None
v["co2"] = None
s = snapshot(warm, v)
s["channels"]["sgp40"] = chan(ui.ST_WARMUP, warm_left=47)
s["channels"]["scd40"] = chan(ui.ST_WARMUP, warm_left=132)
u.show_main(s)
dump(oled, "第 1 页 主界面 - 有未连接 + 预热倒计时")

# ---------------------------------------------------------------- 主界面 报警
s = snapshot({}, dict(FULL, co2=1680, tvoc=310, hcho_mg=0.12, hcho_ppb=96),
             alarm={"scd40": True, "sgp40": True, "ze08": True})
u.show_main(s)
dump(oled, "第 1 页 主界面 - 三项超标")

# ---------------------------------------------------------------- 主界面 学习期
s = snapshot({}, FULL)
s["channels"]["sgp40"]["learning"] = True
u.show_main(s)
dump(oled, "第 1 页 主界面 - TVOC 仍在学习基线")

# ---------------------------------------------------------------- 第 2 页
s = snapshot({"scd40": ui.ST_ABSENT, "ze08": ui.ST_FAULT}, FULL)
u.page = ui.PAGE_INFO
u.show_info(s)
dump(oled, "第 2 页 运行信息")

# ---------------------------------------------------------------- 第 3 页
u.page = ui.PAGE_RAW
u.show_raw(s)
dump(oled, "第 3 页 原始数据")

print("排版检查完毕: 请确认没有文字重叠、没有超出边框(最后一列/最后一行)")
