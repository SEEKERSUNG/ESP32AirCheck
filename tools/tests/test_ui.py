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
        self.powered = True
        super().__init__(self.buf, w, h, framebuf.MONO_VLSB)

    def show(self):
        pass

    def contrast(self, v):
        pass

    def poweroff(self):
        self.powered = False

    def poweron(self):
        self.powered = True


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


def chan(state, warm_left=0, learning=False, enabled=True):
    return {"state": state, "name": "X", "addr": "", "learning": learning,
            "warm_left": warm_left, "enabled": enabled}


def snapshot(states, values, alarm=None, uptime=12 * 60 + 34, oled_ok=True):
    en = {"sht40": cfg.SHT40_ENABLED, "sgp40": cfg.SGP40_ENABLED,
          "ze08": cfg.ZE08_ENABLED}
    channels = {}
    for k in ("sht40", "ze08", "sgp40"):
        channels[k] = chan(states.get(k, ui.ST_READY), enabled=en[k])
    a = {"sht40": False, "ze08": False, "sgp40": False}
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
                 "scan": "3C 44 59"},
    }


FULL = {"temp": 25.4, "rh": 48.2, "hcho_ppb": 32, "hcho_mg": 0.04,
        "tvoc": 123}

oled = FakeOLED(cfg.OLED_WIDTH, cfg.OLED_HEIGHT)
u = ui.UI(oled, cfg.VERSION)

# ---------------------------------------------------------------- 开机画面
u.splash()
dump(oled, "开机画面")

# ---------------------------------------------------------------- 主界面 全部正常
u.show_main(snapshot({}, FULL))
dump(oled, "第 1 页 主界面 - 全部就绪")

# ---------------------------------------------------------------- 主界面 预热中
warm = {
    "sht40": ui.ST_READY,
    "sgp40": ui.ST_WARMUP,
    "ze08": ui.ST_ABSENT,
}
v = dict(FULL)
v["hcho_ppb"] = None
v["hcho_mg"] = None
v["tvoc"] = None
s = snapshot(warm, v)
s["channels"]["sgp40"] = chan(ui.ST_WARMUP, warm_left=47,
                              enabled=cfg.SGP40_ENABLED)
u.show_main(s)
dump(oled, "第 1 页 主界面 - 有通道未连接/预热时仍只显示读数")

# ---------------------------------------------------------------- 主界面 报警
s = snapshot({}, dict(FULL, tvoc=310, hcho_mg=0.12, hcho_ppb=96),
             alarm={"sgp40": True, "ze08": True})
u.show_main(s)
dump(oled, "第 1 页 主界面 - 两项超标")

# ---------------------------------------------------------------- 主界面 学习期
s = snapshot({}, FULL)
s["channels"]["sgp40"]["learning"] = True
u.show_main(s)
dump(oled, "第 1 页 主界面 - TVOC 学习期不在本页显示")

# ---------------------------------------------------------------- 某一项被关掉
s = snapshot({}, FULL)
s["channels"]["ze08"]["enabled"] = False
u.show_main(s)
dump(oled, "第 1 页 主界面 - 甲醛通道关闭时不占版面")

# ---------------------------------------------------------------- 第 2 页
s = snapshot({"ze08": ui.ST_FAULT}, FULL)
u.page = ui.PAGE_INFO
u.show_info(s)
dump(oled, "第 2 页 运行信息")

# ---- 超过 99 小时: 运行时间退化成 H:MM, 不能压到右上角页码 ----
u.show_info(snapshot({}, FULL, uptime=100 * 3600 + 12 * 60 + 34))
dump(oled, "第 2 页 - 运行超过 99 小时")

# ---------------------------------------------------------------- 第 3 页
u.page = ui.PAGE_RAW
u.show_raw(s)
dump(oled, "第 3 页 原始数据")

# ---------------------------------------------------------------- 息屏/唤醒
off_ok = u.screen_off()
was_off = not oled.powered
on_ok = u.screen_on()
print("息屏/唤醒:", off_ok and was_off and on_ok and oled.powered)

print("排版检查完毕: 请确认没有文字重叠、没有超出边框(最后一列/最后一行)")
