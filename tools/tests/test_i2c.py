# -*- coding: utf-8 -*-
"""
I2C 总线体检 (跑在开发板上)
    python tools/deploy.py run test_i2c

一次排查掉最常见的几种"模块接了但扫不到"的原因:

  1. 引脚对照  —— 按 config.py 的 SDA/SCL 扫一遍
  2. SDA/SCL 接反 —— 把两个脚对调再扫一遍
  3. 频率太高  —— 分别用 400k / 100k / 50k 扫
  4. 线路电平  —— 用内部上拉读引脚电平, 判断线是不是被拉死/短路
  5. 已知地址  —— 逐个点名 0x3C / 0x3D / 0x44 / 0x59 / 0x62

任何一组扫到设备都会单独报出来, 方便直接定位问题。
"""

import time
from machine import Pin, I2C

import config as cfg

# 本项目会用到的地址
KNOWN = {
    0x3C: "OLED (SSD1306/SH1106 最常见地址)",
    0x3D: "OLED (少数模块)",
    0x44: "SHT40 温湿度",
    0x45: "SHT40 (ADDR 拉高)",
    0x59: "SGP40 TVOC",
    0x62: "SCD40 CO2",
}

FREQS = (400000, 100000, 50000)

hits = []


def try_scan(sda, scl, freq):
    """返回扫到的地址列表; 出错返回 (None, 错误原因)。"""
    i2c = None
    try:
        i2c = I2C(0, sda=Pin(sda), scl=Pin(scl), freq=freq)
    except Exception as e:
        return None, "%s" % e
    try:
        found = i2c.scan()
    except Exception as e:
        found = None
        err = "%s" % e
    else:
        err = None
    try:
        i2c.deinit()
    except Exception:
        pass
    if found is None:
        return None, err
    return found, None


print("=" * 56)
print(" I2C 总线体检")
print("=" * 56)
print("config.py 配置 : SDA=GPIO%d  SCL=GPIO%d  @%d Hz"
      % (cfg.I2C_SDA, cfg.I2C_SCL, cfg.I2C_FREQ))

# ---------------------------------------------------------------- 1/2/3
for label, sda, scl in (("配置的引脚", cfg.I2C_SDA, cfg.I2C_SCL),
                        ("SDA/SCL 对调", cfg.I2C_SCL, cfg.I2C_SDA)):
    print()
    print("--- %s : SDA=GPIO%d  SCL=GPIO%d" % (label, sda, scl))
    for freq in FREQS:
        found, err = try_scan(sda, scl, freq)
        if err:
            print("    %6d Hz : 出错 %s" % (freq, err))
            continue
        if found:
            print("    %6d Hz : 扫到 %s   <== 通了"
                  % (freq, [hex(a) for a in found]))
            hits.append((sda, scl, freq, found))
        else:
            print("    %6d Hz : 无设备" % freq)
        time.sleep_ms(50)

# ---------------------------------------------------------------- 4
print()
print("--- 线路电平 (内部上拉, 1=高电平/正常空闲, 0=被拉低)")
for name, pin in (("SDA", cfg.I2C_SDA), ("SCL", cfg.I2C_SCL)):
    try:
        p = Pin(pin, Pin.IN, Pin.PULL_UP)
        time.sleep_ms(20)
        print("    GPIO%-2d %s : %d" % (pin, name, p.value()))
        p.init(Pin.IN)          # 恢复成普通输入
    except Exception as e:
        print("    GPIO%-2d %s : 读取失败 %s" % (pin, name, e))

# ---------------------------------------------------------------- 5
print()
print("--- 点名已知地址 (100kHz)")
found, err = try_scan(cfg.I2C_SDA, cfg.I2C_SCL, 100000)
if err:
    print("    扫描出错:", err)
else:
    for addr in sorted(KNOWN):
        mark = "有响应" if addr in found else "无响应"
        print("    0x%02X %-10s %s" % (addr, mark, KNOWN[addr]))

# ---------------------------------------------------------------- 6
print()
print("--- 总线恢复流程 (模拟热插拔后重建 I2C)")

try:
    import sensors

    events = []

    def on_rebuilt(new_i2c):
        events.append(new_i2c)

    probe = I2C(0, sda=Pin(cfg.I2C_SDA), scl=Pin(cfg.I2C_SCL),
                freq=cfg.I2C_FREQ)
    mgr = sensors.SensorManager(probe, None, oled_ok=False,
                                on_bus_rebuilt=on_rebuilt)

    # i2c_unstick 本身不能抛异常
    idle = sensors.i2c_unstick(cfg.I2C_SDA, cfg.I2C_SCL)
    print("    i2c_unstick      : 不抛异常, 总线%s"
          % ("空闲" if idle else "被拉低"))

    before = mgr.i2c
    ok = mgr._rebuild_bus()
    after = mgr.i2c
    print("    重建返回         : %s" % ok)
    print("    I2C 对象已更换   : %s" % (before is not after))
    print("    回调被调用       : %s" % (len(events) == 1))

    time.sleep_ms(50)
    found2, err2 = try_scan(cfg.I2C_SDA, cfg.I2C_SCL, cfg.I2C_FREQ)
    if err2:
        print("    重建后重扫       : 出错 %s" % err2)
    else:
        print("    重建后重扫       : %s"
              % ([hex(a) for a in found2] if found2 else "无设备"))

    # 总线正常时不能反复重建
    n = len(events)
    mgr._prev_addrs = [0x3C]
    mgr.scan_bus()
    print("    总线正常不误触发 : %s" % (len(events) == n))
except Exception as e:
    print("    失败:", type(e).__name__, e)

# ---------------------------------------------------------------- 总结
print()
if hits:
    sda, scl, freq, found = hits[0]
    print("结论: 总线是通的！")
    print("      用的引脚 SDA=GPIO%d SCL=GPIO%d @%d Hz, 扫到 %s"
          % (sda, scl, freq, [hex(a) for a in found]))
    if (sda, scl) != (cfg.I2C_SDA, cfg.I2C_SCL):
        print("      但和 config.py 里的配置不一致 —— 需要把 config.py 改成:")
        print("      I2C_SDA = %d" % sda)
        print("      I2C_SCL = %d" % scl)
    if freq != cfg.I2C_FREQ:
        print("      而且只在 %d Hz 下才通, 说明上拉偏弱/线太长,"
              % freq)
        print("      建议把 config.py 的 I2C_FREQ 降到 %d。" % freq)
else:
    print("结论: 完全扫不到任何设备。按下面顺序查:")
    print("      1) 模块 VCC 是不是接了 3V3(GPIO 能读到电平才说明供电正常)")
    print("      2) GND 有没有和 ESP32 共地")
    print("      3) SDA/SCL 有没有接反(本测试已自动试过对调)")
    print("      4) 杜邦线是否松动 / 断芯, 换几根试试")
    print("      5) 模块有没有板载上拉电阻; 没有的话 SDA、SCL 各加 4.7k 上拉到 3V3")
    print("      6) 确认接的是 GPIO%d / GPIO%d, 不是别的脚"
          % (cfg.I2C_SDA, cfg.I2C_SCL))
