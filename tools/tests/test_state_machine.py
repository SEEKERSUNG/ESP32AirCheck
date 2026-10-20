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
"""

import time

import config as cfg
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

# ---------------------------------------------------------------- 汇总
print()
if FAILS:
    print("结果: %d 项失败" % len(FAILS))
    for f in FAILS:
        print("  -", f)
else:
    print("结果: 全部通过")
