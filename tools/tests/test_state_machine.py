# -*- coding: utf-8 -*-
"""
状态机回归测试 —— 不需要接任何传感器, 空总线即可
    python tools/deploy.py run test_state_machine

覆盖实际踩过的几个坑:
  1. I2C 通道初值是 ST_PROBE, 必须被探测流程"消化"掉,
     否则永远读不到数据
  2. 开机首次检测的结论(resolved)不能被热插拔重扫改回去,
     否则界面每 RESCAN_MS 就闪一次"自检中"
  3. learning 只属于 SGP40, 否则其它行会一直显示 ~ 并盖掉报警星号
  4. 报警判定不能依赖屏幕刷新(OLED 没接也要能报警)
  5. 总线恢复失败时要保留上次地址, 下一轮扫描继续重试
  6. 10 秒无操作熄屏; 按键只唤醒, 不翻页
"""

import time

import config as cfg
import main
import sensors
from ui import ST_PROBE, ST_ABSENT, ST_WARMUP, ST_READY

FAILS = []


def check(cond, msg):
    print(("  PASS  " if cond else "  FAIL  ") + msg)
    if not cond:
        FAILS.append(msg)


class FakeI2C:
    """总线上一个设备都没有"""

    def scan(self):
        return []

    def writeto(self, addr, buf):
        raise OSError("no device")

    def readfrom(self, addr, n):
        raise OSError("no device")


class FakeScanI2C:
    """扫描结果可控, 用来测总线恢复的边沿/重试逻辑。"""

    def __init__(self, found):
        self.found = found

    def scan(self):
        return list(self.found)

    def deinit(self):
        pass


class FakeUART:
    """串口永远没有数据(等价于 ZE08 没接)"""

    def any(self):
        return 0

    def read(self, n=None):
        return None

    def write(self, b):
        return len(b)


# ---------------------------------------------------------------- 1
print("=== 1. 空总线: 两个 I2C 通道必须落到 ABSENT, 不能卡在 PROBE ===")
mgr = sensors.SensorManager(FakeI2C(), FakeUART(), oled_ok=False)
mgr.scan_bus()
for _ in range(3):
    now = time.ticks_ms()
    mgr._step_sht40(now)
    mgr._step_sgp40(now)
    time.sleep_ms(30)

for k in ("sht40", "sgp40"):
    check(mgr.ch[k].state == ST_ABSENT,
          "%s 状态 = %s (期望 ABSENT)" % (k, mgr.ch[k].state))
    check(mgr.ch[k].resolved, "%s resolved 已置位" % k)

# ---------------------------------------------------------------- 2
print()
print("=== 2. ZE08 收不到数据: 超时后应判为 ABSENT ===")
t0 = time.ticks_ms()
while time.ticks_diff(time.ticks_ms(), t0) < 3000:
    mgr._step_ze08()
    time.sleep_ms(100)
check(mgr.ch["ze08"].state == ST_ABSENT,
      "ZE08 状态 = %s (期望 ABSENT)" % mgr.ch["ze08"].state)
check(all(c.resolved for c in mgr.ch.values() if c.enabled),
      "所有已启用的通道都已有结论(resolved)")

# ---------------------------------------------------------------- 3
print()
print("=== 3. 后台重扫不要在界面上闪 自检中 ===")
ch = mgr.ch["ze08"]
now = time.ticks_ms()
ch.next_probe = now          # 让下一轮重扫立刻开始
ch.state = ST_PROBE          # 模拟重扫窗口
ch.probe_start = now
check(mgr.ch["ze08"].resolved, "重扫期间 resolved 仍为 True")
seen = mgr.snapshot()["channels"]["ze08"]["state"]
check(seen == ST_ABSENT,
      "界面看到的 ZE08 仍是 ABSENT (实际 %s)" % seen)

# ---------------------------------------------------------------- 4
print()
print("=== 4. 只有 SGP40 会显示 learning ===")
now = time.ticks_ms()
for k in ("sht40", "ze08"):
    c = mgr.ch[k]
    c.state = ST_READY
    c.learn_end = now + 3600 * 1000
sg = mgr.ch["sgp40"]
sg.state = ST_READY
sg.learn_end = now + 3600 * 1000

snap = mgr.snapshot()["channels"]
for k in ("sht40", "ze08"):
    check(not snap[k]["learning"], "%s 就绪后不带 learning" % k)
check(snap["sgp40"]["learning"], "SGP40 就绪后带 learning")

# ---------------------------------------------------------------- 5
print()
print("=== 5. 没有屏幕时报警判定也要工作 ===")
mgr.values["tvoc"] = cfg.TVOC_ALARM + 10
check(mgr.any_alarm(), "TVOC 超阈值 -> any_alarm() = True")
mgr.values["tvoc"] = 10
check(not mgr.any_alarm(), "TVOC 正常 -> any_alarm() = False")
mgr.values["hcho_mg"] = cfg.HCHO_ALARM + 0.02
check(mgr.any_alarm(), "甲醛超阈值 -> any_alarm() = True")
mgr.values["hcho_mg"] = None
check(not mgr.any_alarm(), "甲醛无数据 -> any_alarm() = False")

# ---------------------------------------------------------------- 6
print()
print("=== 6. 总线恢复失败时要保留上次地址, 下一轮继续重试 ===")
m = sensors.SensorManager(FakeScanI2C([0x3C]), FakeUART(), oled_ok=False)
m.scan_bus()
check(m._prev_addrs == [0x3C], "初始扫描记录 0x3C")

m.i2c.found = []
m._rebuild_bus = lambda: False
m.scan_bus()
check(m._prev_addrs == [0x3C], "恢复失败后仍保留上次地址")

m._rebuild_bus = lambda: True
m.scan_bus()
check(m._prev_addrs == [], "恢复成功且总线空闲后清空, 不再反复重建")

# ---------------------------------------------------------------- 7
print()
print("=== 7. 10 秒无操作熄屏, 按键只唤醒不翻页 ===")


class FakeView:
    def __init__(self):
        self.page = 0
        self.off = 0
        self.on = 0
        self.pages = 0

    def screen_off(self):
        self.off += 1
        return True

    def screen_on(self):
        self.on += 1
        return True

    def next_page(self):
        self.page = (self.page + 1) % 3
        self.pages += 1


app = main.App()
view = FakeView()
app.view = view
app.button = None
now = 100000
app._last_activity = now
app._screen_on = True

app._check_screen_off(now + cfg.SCREEN_OFF_MS + 1)
check(app._screen_on and view.off == 0, "没有按键时不自动熄屏")

app.button = object()           # 有按键才允许自动熄屏
app._check_screen_off(now + cfg.SCREEN_OFF_MS - 1)
check(app._screen_on and view.off == 0, "不到 10 秒不息屏")

app._check_screen_off(now + cfg.SCREEN_OFF_MS)
check((not app._screen_on) and view.off == 1, "满 10 秒熄屏")

app._on_button_press(now + cfg.SCREEN_OFF_MS + 1)
check(app._screen_on and view.on == 1 and view.pages == 0,
      "按键只唤醒, 不翻页")

app._on_button_press(now + cfg.SCREEN_OFF_MS + 2)
check(view.pages == 1, "再按一次才翻页")

# ---------------------------------------------------------------- 汇总
print()
if FAILS:
    print("结果: %d 项失败" % len(FAILS))
    for f in FAILS:
        print("  -", f)
else:
    print("结果: 全部通过")
