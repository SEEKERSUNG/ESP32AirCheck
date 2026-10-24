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
  7. ZE08 问答模式必须周期请求, 否则 5 秒后就掉线
  8. 警告阈值和报警阈值的关系
  9. 长按 BOOT 重置 VOC 基线, 短按仍然翻页
"""

import time

import config as cfg
import main
import sensors
import ze08
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


def qa_frame(ugm3, ppb):
    f = bytearray(b"\xff\x86\x00\x00\x00\x00\x00\x00\x00")
    f[2] = (ugm3 >> 8) & 0xFF
    f[3] = ugm3 & 0xFF
    f[6] = (ppb >> 8) & 0xFF
    f[7] = ppb & 0xFF
    f[8] = ze08.checksum(f)
    return bytes(f)


class FakeQaUART:
    """只在收到 0x86 读命令时回一帧, 模拟问答模式。"""

    def __init__(self):
        self.rx = bytearray()
        self.requests = 0

    def write(self, data):
        if data == ze08.CMD_READ_QA:
            self.requests += 1
            self.rx.extend(qa_frame(40, 32))
        return len(data)

    def any(self):
        return len(self.rx)

    def read(self, n=None):
        if not self.rx:
            return None
        if n is None or n >= len(self.rx):
            chunk = bytes(self.rx)
            self.rx = bytearray()
            return chunk
        chunk = bytes(self.rx[:n])
        self.rx = self.rx[n:]
        return chunk


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

# ---------------------------------------------------------------- 5b
print()
print("=== 5b. 警告阈值: 警告不报警, 超过报警线两者都置位 ===")
mgr.values["tvoc"] = cfg.TVOC_WARN + 1
check(mgr.snapshot()["warn"]["sgp40"] and not mgr.alarm["sgp40"],
      "TVOC 在警告区 -> 警告, 不报警")
mgr.values["tvoc"] = cfg.TVOC_ALARM + 1
snap = mgr.snapshot()
check(snap["warn"]["sgp40"] and snap["alarm"]["sgp40"],
      "TVOC 超报警线 -> 警告和报警都置位")
mgr.values["tvoc"] = None

mgr.values["hcho_mg"] = cfg.HCHO_WARN + 0.005
snap = mgr.snapshot()
check(snap["warn"]["ze08"] and not snap["alarm"]["ze08"],
      "HCHO 在警告区 -> 警告, 不报警")
mgr.values["hcho_mg"] = cfg.HCHO_ALARM + 0.01
snap = mgr.snapshot()
check(snap["warn"]["ze08"] and snap["alarm"]["ze08"],
      "HCHO 超报警线 -> 警告和报警都置位")
mgr.values["hcho_mg"] = None

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

# ---------------------------------------------------------------- 8
print()
print("=== 8. ZE08 问答模式: 周期请求, 超过 5 秒仍保持在线 ===")
qa_uart = FakeQaUART()
qm = sensors.SensorManager(FakeI2C(), qa_uart, oled_ok=False)
qm.ch["ze08"].warmup_s = 1          # 缩短预热, 让测试跑得快一点
t0 = time.ticks_ms()
while time.ticks_diff(time.ticks_ms(), t0) < 7000:
    qm._step_ze08()
    time.sleep_ms(100)

check(qm.ch["ze08"].state == ST_READY,
      "ZE08 状态 = %s (期望 READY)" % qm.ch["ze08"].state)
check(qm.values["hcho_ppb"] == 32,
      "HCHO 持续更新 = %r" % qm.values["hcho_ppb"])
check(qa_uart.requests >= 5,
      "问答请求次数 = %d (期望 >= 5)" % qa_uart.requests)
check(qm.info["ze08_frames"] >= 5,
      "有效帧数 = %d (期望 >= 5)" % qm.info["ze08_frames"])

# ---------------------------------------------------------------- 9
print()
print("=== 9. 长按 BOOT 重置 VOC 基线, 短按仍然翻页 ===")


class FakeMgr:
    def __init__(self):
        self.resets = 0

    def reset_voc_baseline(self, now):
        self.resets += 1


app = main.App()
view = FakeView()
fake_mgr = FakeMgr()
app.view = view
app.mgr = fake_mgr
app._btn_last = 1
app._btn_ms = 0

app._button_tick(0, 1000)                           # 按下
app._button_tick(0, 1000 + cfg.VOC_RESET_HOLD_MS - 1)
check(fake_mgr.resets == 0 and view.pages == 0,
      "未到长按时间不重置")

app._button_tick(0, 1000 + cfg.VOC_RESET_HOLD_MS)   # 按住到阈值
check(fake_mgr.resets == 1, "长按满 %dms 触发重置" % cfg.VOC_RESET_HOLD_MS)

app._button_tick(1, 1000 + cfg.VOC_RESET_HOLD_MS + 100)
check(view.pages == 0, "长按释放不翻页")

app2 = main.App()
view2 = FakeView()
fake_mgr2 = FakeMgr()
app2.view = view2
app2.mgr = fake_mgr2
app2._btn_last = 1
app2._btn_ms = 0
app2._button_tick(0, 1000)
app2._button_tick(1, 1100)                          # 短按 100ms
check(view2.pages == 1 and fake_mgr2.resets == 0,
      "短按仍然翻页, 不重置基线")

# 重置算法本身
m = sensors.SensorManager(FakeI2C(), FakeUART(), oled_ok=False)
m.drv_sgp = object()
m.ch["sgp40"].state = ST_READY
m.values["tvoc"] = 123
m.reset_voc_baseline(1000, erase=False)
check(m.values["tvoc"] is None, "重置后 TVOC 显示值清空")
check(m.ch["sgp40"].state == ST_WARMUP, "重置后 SGP40 回到 WARMUP")
check(m.algo.mUptime == 0, "重置后算法从头开始")

# ---------------------------------------------------------------- 汇总
print()
if FAILS:
    print("结果: %d 项失败" % len(FAILS))
    for f in FAILS:
        print("  -", f)
else:
    print("结果: 全部通过")
