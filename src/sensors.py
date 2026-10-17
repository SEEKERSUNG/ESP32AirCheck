# -*- coding: utf-8 -*-
"""
传感器管理
================================================================
负责四件事:

  1. 上电检测 —— 每个传感器是否真的接上了
     I2C 传感器: 先扫地址, 再做一次功能探测(读序列号 / 读一帧数据并校验 CRC),
                 两者都通过才算"已连接", 避免总线被干扰时误判。
     ZE08       : 监听 UART, 2.5 秒内收到一帧校验正确的数据就算已连接。

  2. 预热管理 —— 传感器在线但读数还不可信的时间段
     预热期间屏幕会显示专门的标记和倒计时, 并且不触发报警。
     各传感器的预热时长在 config.py 里配置。

  3. 周期采集
     I2C 总线上三个传感器共用一个 1 秒周期(SGP40 的 VOC 算法必须按 1s 喂数);
     SCD40 内部每 5 秒出一次结果, 用 data_ready 查询后按需读取;
     ZE08 用独立的 100ms 任务刷串口缓冲, 防止 UART 接收缓冲溢出丢帧。

  4. 热插拔 —— 运行中定时重新探测
     没接上的传感器每 RESCAN_MS 重新找一次; 已经在线的传感器如果掉线,
     也会被重新标记为"未连接"。所以分批采购、单独调试都很方便:
     插上就能用, 拔掉自动变成"未连接"。
"""

from time import ticks_ms, ticks_diff, sleep_us
from machine import Pin, I2C

import config as cfg
import sht40
import sgp40
import scd40
import ze08
import voc

from ui import ST_PROBE, ST_ABSENT, ST_WARMUP, ST_READY, ST_FAULT


class Channel:
    """一个传感器通道的状态机。"""

    def __init__(self, key, name, addr_str, warmup_s, enabled=True):
        self.key = key
        self.name = name
        self.addr_str = addr_str
        self.warmup_s = warmup_s
        self.enabled = enabled
        self.state = ST_PROBE if enabled else ST_ABSENT
        self.warm_end = 0
        self.learn_end = 0
        self.probe_start = 0
        self.next_probe = 0
        self.asked = False
        self.fails = 0
        self.error = None

    def begin_warmup(self, now):
        self.state = ST_WARMUP
        self.warm_end = now + self.warmup_s * 1000
        self.learn_end = now + cfg.VOC_LEARN_S * 1000
        self.fails = 0
        self.error = None

    def drop(self, now, state=ST_ABSENT):
        self.state = state
        self.next_probe = now + cfg.RESCAN_MS
        self.fails = 0
        self.probe_start = now
        self.asked = False

    def snapshot(self, now):
        d = {
            "state": self.state,
            "name": self.name,
            "addr": self.addr_str,
            "learning": (self.state == ST_READY
                         and ticks_diff(self.learn_end, now) > 0),
            "warm_left": 0,
        }
        if self.state == ST_WARMUP:
            left = ticks_diff(self.warm_end, now) // 1000
            d["warm_left"] = left if left > 0 else 0
        return d


def i2c_unstick(sda_pin, scl_pin, clocks=9):
    """
    I2C 总线"解救": 从机在传输途中被断电/插拔时, 可能一直把 SDA 拉低不放,
    这时主机怎么扫都扫不到东西。

    标准做法是用普通 GPIO 手动打 9 个时钟, 让从机把剩余位发完从而释放 SDA,
    再补一个 STOP 条件, 总线就回到空闲状态了。

    返回 True 表示结束后两条线都是高电平(总线空闲)。
    """
    # OPEN_DRAIN 才能既当输出又不会和从机对拉; 没有的话退化成普通输出
    try:
        sda = Pin(sda_pin, Pin.OPEN_DRAIN, Pin.PULL_UP)
        scl = Pin(scl_pin, Pin.OPEN_DRAIN, Pin.PULL_UP)
    except Exception:
        sda = Pin(sda_pin, Pin.OUT)
        scl = Pin(scl_pin, Pin.OUT)

    sda.value(1)
    scl.value(1)
    sleep_us(10)

    if sda.value() == 0:            # 有人拉着数据线
        for _ in range(clocks):
            scl.value(0)
            sleep_us(5)
            scl.value(1)
            sleep_us(5)
            if sda.value():         # 被放开了
                break

    # 补一个 STOP: SCL 高电平期间, SDA 由低变高
    sda.value(0)
    sleep_us(5)
    scl.value(1)
    sleep_us(5)
    sda.value(1)
    sleep_us(10)

    return sda.value() == 1 and scl.value() == 1


class SensorManager:
    def __init__(self, i2c, uart, oled_ok=True, on_bus_rebuilt=None):
        self.i2c = i2c
        self.uart = uart
        self.oled_ok = oled_ok
        # 总线被重建后回调(把新的 I2C 对象交给屏幕等还在用它的地方)
        self.on_bus_rebuilt = on_bus_rebuilt

        self.start_ms = ticks_ms()
        self.last_scan = None
        self.scan_addrs = set()
        self._prev_addrs = []

        self.ch = {
            "sht40": Channel("sht40", "SHT40", "0x44", cfg.WARMUP_SHT40,
                             cfg.SHT40_ENABLED),
            "sgp40": Channel("sgp40", "SGP40", "0x59", cfg.WARMUP_SGP40,
                             cfg.SGP40_ENABLED),
            "scd40": Channel("scd40", "SCD40", "0x62", cfg.WARMUP_SCD40,
                             cfg.SCD40_ENABLED),
            "ze08": Channel("ze08", "ZE08", "UART", cfg.WARMUP_ZE08,
                            cfg.ZE08_ENABLED),
        }

        self.values = {
            "temp": None, "rh": None,
            "hcho_ppb": None, "hcho_mg": None,
            "tvoc": None, "co2": None,
        }
        self.alarm = {"sht40": False, "ze08": False, "sgp40": False, "scd40": False}
        self.info = {
            "voc_raw": 0, "voc_learning": False, "voc_uptime": 0,
            "ze08_frames": 0, "ze08_bad": 0, "scan": "",
        }

        # 驱动实例
        self.drv_sht = None
        self.drv_sgp = None
        self.drv_scd = None
        self.drv_ze08 = None
        self.scd_async = None
        self.algo = voc.VocAlgorithm()

        self._sgp_learn_ms = 0
        self._scd_last_ok = 0

    # ==================== 总线扫描 ====================
    def _scan_once(self):
        try:
            return sorted(self.i2c.scan())
        except OSError:
            return []

    def _open_bus(self):
        return I2C(cfg.I2C_ID, sda=Pin(cfg.I2C_SDA), scl=Pin(cfg.I2C_SCL),
                   freq=cfg.I2C_FREQ)

    def _rebuild_bus(self):
        """
        重建 I2C 外设。

        热插拔传感器时, 从机有可能卡在半截传输里一直拉低 SDA, 之后整条总线
        都扫不到东西 —— 这时光重新扫描是没用的, 必须先用普通 GPIO 把总线顶回
        空闲状态, 再重新创建 I2C 对象(MicroPython 的 I2C 不支持原地 init)。
        """
        for key in ("sht40", "sgp40", "scd40"):
            self._forget(key)
        try:
            self.i2c.deinit()
        except Exception:
            pass

        idle = i2c_unstick(cfg.I2C_SDA, cfg.I2C_SCL)
        try:
            self.i2c = self._open_bus()
        except Exception as e:
            print("[i2c] 重建失败:", e)
            return False

        # 屏幕等还在用旧的 I2C 对象, 交新的给它
        if self.on_bus_rebuilt:
            try:
                self.on_bus_rebuilt(self.i2c)
            except Exception as e:
                print("[i2c] 重建后回调失败:", e)

        print("[i2c] 已重建 I2C 外设, 总线%s"
              % ("回到空闲" if idle else "仍被拉低(查接线/短路)"))
        return True

    def scan_bus(self):
        addrs = self._scan_once()

        # 上一轮还扫到设备, 这一轮整条总线却全空 —— 多半是热插拔把 I2C 卡死了
        if not addrs and self._prev_addrs:
            print("[i2c] 上次还扫到 %s, 这次总线全空, 尝试恢复"
                  % [hex(a) for a in self._prev_addrs])
            if self._rebuild_bus():
                addrs = self._scan_once()
        self._prev_addrs = addrs

        self.scan_addrs = set(addrs)
        self.info["scan"] = " ".join("%02X" % a for a in addrs)
        if cfg.DEBUG_SCAN:
            print("[i2c] 扫描:", [hex(a) for a in addrs])

        # 已在线但地址消失 => 掉线
        for key, addr in (("sht40", cfg.SHT40_ADDR),
                          ("sgp40", cfg.SGP40_ADDR),
                          ("scd40", cfg.SCD40_ADDR)):
            ch = self.ch[key]
            if ch.enabled and ch.state in (ST_READY, ST_WARMUP) \
                    and addr not in self.scan_addrs:
                print("[%s] 掉线" % ch.name)
                self._forget(key)

    def _forget(self, key):
        ch = self.ch[key]
        ch.drop(ticks_ms())
        if key == "sht40":
            self.drv_sht = None
            self.values["temp"] = None
            self.values["rh"] = None
        elif key == "sgp40":
            self.drv_sgp = None
            self.values["tvoc"] = None
        elif key == "scd40":
            self.drv_scd = None
            self.scd_async = None
            self.values["co2"] = None
        elif key == "ze08":
            self.drv_ze08 = None
            self.values["hcho_ppb"] = None
            self.values["hcho_mg"] = None

    def _i2c_fail(self, key, err):
        ch = self.ch[key]
        ch.fails += 1
        ch.error = str(err)
        if ch.fails >= 3:
            print("[%s] 连续 %d 次读取失败: %s" % (ch.name, ch.fails, err))
            self._forget(key)
            self.ch[key].drop(ticks_ms(), ST_FAULT)

    # ==================== SHT40 ====================
    def _step_sht40(self, now):
        ch = self.ch["sht40"]
        if not ch.enabled:
            return

        if ch.state in (ST_ABSENT, ST_FAULT):
            if ticks_diff(now, ch.next_probe) < 0:
                return
            if cfg.SHT40_ADDR not in self.scan_addrs:
                ch.drop(now, ST_ABSENT)
                return
            try:
                d = sht40.SHT40(self.i2c, cfg.SHT40_ADDR)
                d.read_serial()                 # 功能探测
            except OSError as e:
                ch.drop(now, ST_FAULT)
                ch.error = str(e)
                return
            self.drv_sht = d
            ch.begin_warmup(now)
            print("[SHT40] 已连接, 预热 %ds" % ch.warmup_s)

        if ch.state == ST_WARMUP and ticks_diff(now, ch.warm_end) >= 0:
            ch.state = ST_READY
            print("[SHT40] 预热完成")

        if self.drv_sht is None:
            return
        try:
            t, rh = self.drv_sht.measure()
            self.values["temp"] = t
            self.values["rh"] = rh
            ch.fails = 0
        except OSError as e:
            self._i2c_fail("sht40", e)

    # ==================== SGP40 ====================
    def _step_sgp40(self, now):
        ch = self.ch["sgp40"]
        if not ch.enabled:
            return

        if ch.state in (ST_ABSENT, ST_FAULT):
            if ticks_diff(now, ch.next_probe) < 0:
                return
            if cfg.SGP40_ADDR not in self.scan_addrs:
                ch.drop(now, ST_ABSENT)
                return
            try:
                d = sgp40.SGP40(self.i2c, cfg.SGP40_ADDR)
                d.measure_raw()                 # 功能探测
            except OSError as e:
                ch.drop(now, ST_FAULT)
                ch.error = str(e)
                return
            self.drv_sgp = d
            self.algo = voc.VocAlgorithm()      # 重新开始学习基线
            self._sgp_learn_ms = now
            ch.begin_warmup(now)
            print("[SGP40] 已连接, 预热 %ds" % ch.warmup_s)

        if ch.state == ST_WARMUP and ticks_diff(now, ch.warm_end) >= 0:
            ch.state = ST_READY
            print("[SGP40] 预热完成(基线仍在学习中)")

        if self.drv_sgp is None:
            return
        try:
            rh = self.values["rh"]
            temp = self.values["temp"]
            raw = self.drv_sgp.measure_raw(rh, temp)
            ch.fails = 0
        except OSError as e:
            self._i2c_fail("sgp40", e)
            return

        idx = self.algo.process(raw)
        self.info["voc_raw"] = raw
        # 算法前 45 秒是 blackout, 返回 0, 此时不显示数值
        self.values["tvoc"] = idx if idx > 0 else None
        self.info["voc_uptime"] = int(self.algo.mUptime)
        self.info["voc_learning"] = (
            ch.state == ST_READY and ticks_diff(ch.learn_end, now) > 0)

    # ==================== SCD40 ====================
    def _step_scd40(self, now):
        ch = self.ch["scd40"]
        if not ch.enabled:
            return

        if ch.state in (ST_ABSENT, ST_FAULT):
            if ticks_diff(now, ch.next_probe) < 0:
                return
            if cfg.SCD40_ADDR not in self.scan_addrs:
                ch.drop(now, ST_ABSENT)
                return
            try:
                d = scd40.SCD40(self.i2c, cfg.SCD40_ADDR)
                d.serial_number()               # 功能探测
                d.start_periodic_measurement()
            except OSError as e:
                ch.drop(now, ST_FAULT)
                ch.error = str(e)
                return
            self.drv_scd = d
            self.scd_async = scd40.SCD40Async(d, min_interval_ms=4800)
            self._scd_last_ok = now
            ch.begin_warmup(now)
            print("[SCD40] 已连接, 预热 %ds" % ch.warmup_s)

        if ch.state == ST_WARMUP and ticks_diff(now, ch.warm_end) >= 0:
            ch.state = ST_READY
            print("[SCD40] 预热完成")

        if self.scd_async is None:
            return

        try:
            res = self.scd_async.poll()
        except OSError as e:
            self._i2c_fail("scd40", e)
            return

        if res is not None:
            self.values["co2"] = res[0]
            self._scd_last_ok = now
            ch.fails = 0
            if self.values["temp"] is None:
                # 没有 SHT40 时, 用 SCD40 自带的温湿度兜底
                self.values["temp"] = res[1]
                self.values["rh"] = res[2]

        # 30 秒没有任何新数据 => 认为掉线
        elif ch.state == ST_READY and ticks_diff(now, self._scd_last_ok) > 30000:
            print("[SCD40] 30s 无数据, 视为掉线")
            self._forget("scd40")
            self.ch["scd40"].drop(now, ST_FAULT)

    # ==================== ZE08 ====================
    def _step_ze08(self):
        ch = self.ch["ze08"]
        if not ch.enabled:
            return
        now = ticks_ms()

        if ch.state == ST_ABSENT:
            if ticks_diff(now, ch.next_probe) < 0:
                return
            ch.state = ST_PROBE
            ch.probe_start = now
            ch.asked = False
            if self.drv_ze08 is None:
                self.drv_ze08 = ze08.ZE08(self.uart)
            self.drv_ze08.reset()

        if self.drv_ze08 is None:
            self.drv_ze08 = ze08.ZE08(self.uart)
            ch.probe_start = now

        z = self.drv_ze08
        got = z.poll()
        self.info["ze08_frames"] = z.frames
        self.info["ze08_bad"] = z.bad_frames

        fresh = (z.last_rx_ms is not None
                 and ticks_diff(now, z.last_rx_ms) <= 5000)

        if ch.state == ST_PROBE:
            if fresh:
                self.values["hcho_ppb"] = z.ppb
                self.values["hcho_mg"] = z.ppb * ze08.PPB_TO_MG
                ch.begin_warmup(now)
                print("[ZE08] 已连接, 预热 %ds" % ch.warmup_s)
            elif ticks_diff(now, ch.probe_start) > 500 and not ch.asked:
                z.request_reading()             # 模块可能被切成问答模式
                ch.asked = True
            elif ticks_diff(now, ch.probe_start) > 2500:
                ch.drop(now, ST_ABSENT)
            return

        if got is not None:
            self.values["hcho_ppb"] = z.ppb
            self.values["hcho_mg"] = z.ppb * ze08.PPB_TO_MG

        if not fresh:
            print("[ZE08] 5s 无数据帧, 视为掉线")
            self._forget("ze08")
            self.ch["ze08"].drop(now, ST_ABSENT)
            return

        if ch.state == ST_WARMUP and ticks_diff(now, ch.warm_end) >= 0:
            ch.state = ST_READY
            print("[ZE08] 预热完成")

    # ==================== 报警判定 ====================
    def _eval_alarm(self):
        v = self.values
        a = self.alarm
        # 只有在"预热完成"之后才判定, 避免预热期间的无效读数误报
        a["ze08"] = (self.ch["ze08"].state == ST_READY
                     and v["hcho_mg"] is not None and v["hcho_mg"] >= cfg.HCHO_ALARM)
        a["sgp40"] = (self.ch["sgp40"].state == ST_READY
                      and v["tvoc"] is not None and v["tvoc"] >= cfg.TVOC_ALARM)
        a["scd40"] = (self.ch["scd40"].state == ST_READY
                      and v["co2"] is not None and v["co2"] >= cfg.CO2_ALARM)
        a["sht40"] = False
        return a

    def any_alarm(self):
        return any(self.alarm.values())

    # ==================== 快照 ====================
    def snapshot(self):
        now = ticks_ms()
        chans = {}
        for key, ch in self.ch.items():
            chans[key] = ch.snapshot(now)
        self._eval_alarm()
        return {
            "uptime": ticks_diff(now, self.start_ms) / 1000.0,
            "oled_ok": self.oled_ok,
            "oled_addr": cfg.OLED_ADDR,
            "channels": chans,
            "values": self.values,
            "alarm": self.alarm,
            "info": self.info,
        }

    def selftest_rows(self):
        """自检页要显示的行: [(名称, 状态), ...]"""
        return [(self.ch[k].name, self.ch[k].state)
                for k in ("sht40", "sgp40", "scd40", "ze08")]

    def all_resolved(self):
        """是否所有通道都已经有结论(不再是"检测中")。"""
        for ch in self.ch.values():
            if ch.enabled and ch.state == ST_PROBE:
                return False
        return True
