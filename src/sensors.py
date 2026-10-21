# -*- coding: utf-8 -*-
"""
传感器管理
================================================================
负责五件事:

  1. 上电检测 —— 每个传感器是否真的接上了
     I2C 传感器: 先扫地址, 再做一次功能探测(读序列号 / 读一帧数据并校验 CRC),
                 两者都通过才算"已连接", 避免总线被干扰时误判。
     ZE08       : 监听 UART, 2.5 秒内收到一帧校验正确的数据就算已连接。

  2. 预热管理 —— 传感器在线但读数还不可信的时间段
     预热期间屏幕会显示专门的标记和倒计时, 并且不触发报警。
     各传感器的预热时长在 config.py 里配置。

  3. 周期采集
     I2C 总线上两个传感器共用一个 1 秒周期(SGP40 的 VOC 算法必须按 1s 喂数);
     ZE08 用独立的 100ms 任务刷串口缓冲, 防止 UART 接收缓冲溢出丢帧。

  4. 热插拔 —— 运行中定时重新探测
     没接上的传感器每 RESCAN_MS 重新找一次; 已经在线的传感器如果掉线,
     也会被重新标记为"未连接"。所以分批采购、单独调试都很方便:
     插上就能用, 拔掉自动变成"未连接"。

  5. VOC 基线持久化
     每 VOC_STATE_SAVE_MS 把 voc.VocAlgorithm 的状态存入 ESP32 NVS;
     SGP40 连接时恢复, 避免每次断电都从零学习。SGP40 本身不保存基线。
"""

from time import ticks_ms, ticks_diff, sleep_us
from machine import Pin, I2C

import json
import os

try:
    import esp32
except ImportError:
    esp32 = None

import config as cfg
import sht40
import sgp40
import ze08
import voc

from ui import ST_PROBE, ST_ABSENT, ST_WARMUP, ST_READY, ST_FAULT

# VOC 基线状态存放位置: 优先 ESP32 NVS, 不可用时退回文件。
# 注意是 ESP32 自己的 flash —— SGP40 内部没有基线存储。
_VOC_NVS_NAMESPACE = "aircheck"
_VOC_NVS_KEY = "vocstate"
_VOC_STATE_FILE = "/voc_state.json"
_VOC_STATE_BUF = 2048


def _load_state_blob(namespace=_VOC_NVS_NAMESPACE, key=_VOC_NVS_KEY):
    """读取保存的状态; 没有则返回 None。"""
    if esp32 is not None:
        try:
            nvs = esp32.NVS(namespace)
            buf = bytearray(_VOC_STATE_BUF)
            n = nvs.get_blob(key, buf)
            if n:
                return bytes(buf[:n])
        except Exception:
            pass
    try:
        with open(_VOC_STATE_FILE, "rb") as f:
            return f.read()
    except OSError:
        return None


def _save_state_blob(blob, namespace=_VOC_NVS_NAMESPACE, key=_VOC_NVS_KEY):
    """保存状态。NVS 带磨损均衡和掉电保护; 文件方式做临时文件+改名。"""
    if esp32 is not None:
        try:
            nvs = esp32.NVS(namespace)
            nvs.set_blob(key, blob)
            nvs.commit()
            return True
        except Exception as e:
            print("[voc] NVS 保存失败:", e)
    try:
        tmp = _VOC_STATE_FILE + ".tmp"
        with open(tmp, "wb") as f:
            f.write(blob)
        try:
            os.remove(_VOC_STATE_FILE)
        except OSError:
            pass
        os.rename(tmp, _VOC_STATE_FILE)
        return True
    except Exception as e:
        print("[voc] 文件保存失败:", e)
        return False


def _erase_state(namespace=_VOC_NVS_NAMESPACE, key=_VOC_NVS_KEY):
    """删除已保存的状态 (测试/手动重置用)。"""
    if esp32 is not None:
        try:
            nvs = esp32.NVS(namespace)
            nvs.erase_key(key)
            nvs.commit()
        except Exception:
            pass
    try:
        os.remove(_VOC_STATE_FILE)
    except OSError:
        pass


class Channel:
    """一个传感器通道的状态机。"""

    def __init__(self, key, name, addr_str, warmup_s, enabled=True):
        self.key = key
        self.name = name
        self.addr_str = addr_str
        self.warmup_s = warmup_s
        self.enabled = enabled
        self.state = ST_PROBE if enabled else ST_ABSENT
        # 开机首次检测是否已经"有结论"。
        # 注意不能直接用 state == ST_PROBE 来判断: 热插拔重扫时 ZE08 会
        # 周期性地回到 ST_PROBE, 那样这个标记会跟着抖。
        self.resolved = not enabled
        self.warm_end = 0
        self.learn_end = 0
        self.probe_start = 0
        self.next_probe = 0
        self.fails = 0
        self.error = None

    def begin_warmup(self, now):
        self.state = ST_WARMUP
        self.resolved = True
        self.warm_end = now + self.warmup_s * 1000
        self.learn_end = now + cfg.VOC_LEARN_S * 1000
        self.fails = 0
        self.error = None

    def drop(self, now, state=ST_ABSENT):
        self.state = state
        self.resolved = True
        self.next_probe = now + cfg.RESCAN_MS
        self.fails = 0
        self.probe_start = now

    def snapshot(self, now):
        state = self.state
        # 已经有结论之后又短暂回到 ST_PROBE, 那是热插拔后台重扫
        # (ZE08 每 RESCAN_MS 会试一次)。界面上保持上次的结论,
        # 否则屏幕每 10 秒就闪一下"自检中"。
        if self.resolved and state == ST_PROBE:
            state = ST_ABSENT
        d = {
            "state": state,
            "enabled": self.enabled,
            "name": self.name,
            "addr": self.addr_str,
            # 只有 SGP40 有"学习基线"这回事; 其它传感器如果也置 true,
            # 界面上会一直显示 ~ 并且把报警星号盖掉。
            "learning": (state == ST_READY
                         and self.key == "sgp40"
                         and ticks_diff(self.learn_end, now) > 0),
            "warm_left": 0,
        }
        if state == ST_WARMUP:
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
            "ze08": Channel("ze08", "ZE08", "UART", cfg.WARMUP_ZE08,
                            cfg.ZE08_ENABLED),
        }

        self.values = {
            "temp": None, "rh": None,
            "hcho_ppb": None, "hcho_mg": None,
            "tvoc": None,
        }
        self.alarm = {"sht40": False, "ze08": False, "sgp40": False}
        self.warn = {"sht40": False, "ze08": False, "sgp40": False}
        self.info = {
            "voc_raw": 0, "voc_learning": False, "voc_uptime": 0,
            "ze08_frames": 0, "ze08_bad": 0, "scan": "",
        }

        # 驱动实例
        self.drv_sht = None
        self.drv_sgp = None
        self.drv_ze08 = None
        self.algo = voc.VocAlgorithm()

        self._sgp_learn_ms = 0
        self._voc_save_ms = None

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
        for key in ("sht40", "sgp40"):
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
        return idle

    def scan_bus(self):
        addrs = self._scan_once()

        # 上一轮还扫到设备, 这一轮整条总线却全空 —— 多半是热插拔把 I2C 卡死了
        if not addrs and self._prev_addrs:
            old = self._prev_addrs
            print("[i2c] 上次还扫到 %s, 这次总线全空, 尝试恢复"
                  % [hex(a) for a in old])
            if self._rebuild_bus():
                addrs = self._scan_once()
                self._prev_addrs = addrs
            else:
                # 总线仍被拉低或重建失败: 保留上次的地址, 下一轮继续尝试,
                # 不能在这里清空, 否则就再也不会自动恢复了。
                self._prev_addrs = old
        else:
            self._prev_addrs = addrs

        self.scan_addrs = set(addrs)
        self.info["scan"] = " ".join("%02X" % a for a in addrs)
        if cfg.DEBUG_SCAN:
            print("[i2c] 扫描:", [hex(a) for a in addrs])

        # 已在线但地址消失 => 掉线
        for key, addr in (("sht40", cfg.SHT40_ADDR),
                          ("sgp40", cfg.SGP40_ADDR)):
            ch = self.ch[key]
            if ch.enabled and ch.state in (ST_READY, ST_WARMUP) \
                    and addr not in self.scan_addrs:
                print("[%s] 掉线" % ch.name)
                self._forget(key)

    def _forget(self, key):
        ch = self.ch[key]
        # 热插拔/掉线前把最新基线补存一次, 不用等下一个 10 分钟周期。
        # 至少间隔 60 秒, 避免传感器接触不良反复掉线时高频擦写 flash。
        if key == "sgp40" and cfg.VOC_STATE_PERSIST \
                and self.drv_sgp is not None \
                and ch.state in (ST_READY, ST_WARMUP):
            now = ticks_ms()
            if self._voc_save_ms is None or \
                    ticks_diff(now, self._voc_save_ms) >= 60000:
                self._save_voc_state()
                self._voc_save_ms = now
        ch.drop(ticks_ms())
        if key == "sht40":
            self.drv_sht = None
            self.values["temp"] = None
            self.values["rh"] = None
        elif key == "sgp40":
            self.drv_sgp = None
            self.values["tvoc"] = None
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

        if ch.state in (ST_PROBE, ST_ABSENT, ST_FAULT):
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

        if ch.state in (ST_PROBE, ST_ABSENT, ST_FAULT):
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
            self.algo = voc.VocAlgorithm()      # 默认重新开始学习基线
            restored = False
            if cfg.VOC_STATE_PERSIST:
                restored = self._restore_voc_state()
            self._voc_save_ms = now
            self._sgp_learn_ms = now
            ch.begin_warmup(now)
            if restored:
                # 基线已经恢复, "学习期"标记按算法已累计的采样时间扣减
                left = cfg.VOC_LEARN_S - int(self.algo.mUptime)
                if left < 0:
                    left = 0
                ch.learn_end = now + left * 1000
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

        # 每 VOC_STATE_SAVE_MS 保存一次基线
        if cfg.VOC_STATE_PERSIST and cfg.VOC_STATE_SAVE_MS > 0:
            if self._voc_save_ms is None:
                self._voc_save_ms = now
            elif ticks_diff(now, self._voc_save_ms) >= cfg.VOC_STATE_SAVE_MS:
                self._save_voc_state()
                self._voc_save_ms = now

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
            if self.drv_ze08 is None:
                self.drv_ze08 = ze08.ZE08(self.uart)
            self.drv_ze08.reset()

        if self.drv_ze08 is None:
            self.drv_ze08 = ze08.ZE08(self.uart)
            ch.probe_start = now

        z = self.drv_ze08

        # 问答模式不会主动上传, 必须周期性地要数据。
        # 主动模式(0x17)则完全不用发命令。
        if ch.state in (ST_WARMUP, ST_READY) and z.mode == "qa":
            if z._last_cmd_ms is None or \
                    ticks_diff(now, z._last_cmd_ms) >= 900:
                z.request_reading()

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
            elif ticks_diff(now, ch.probe_start) > 2500:
                ch.drop(now, ST_ABSENT)
            elif ticks_diff(now, ch.probe_start) > 500:
                # 每 500ms 重试一次, 直到超时; 防止单次请求丢失
                if z._last_cmd_ms is None or \
                        ticks_diff(now, z._last_cmd_ms) >= 500:
                    z.request_reading()
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

    # ==================== VOC 基线持久化 ====================
    def _restore_voc_state(self):
        """SGP40 连接时调用; 找到有效状态就恢复, 否则返回 False。"""
        blob = _load_state_blob()
        if not blob:
            print("[voc] 没有已保存的基线, 重新学习")
            return False
        try:
            state = json.loads(blob.decode())
        except Exception as e:
            print("[voc] 基线数据损坏, 重新学习:", e)
            return False
        if not self.algo.import_state(state):
            print("[voc] 基线数据不兼容, 重新学习")
            return False
        print("[voc] 已恢复基线 (累计学习 %ds)" % int(self.algo.mUptime))
        return True

    def _save_voc_state(self):
        """把当前算法状态序列化后写入 NVS。"""
        try:
            blob = json.dumps(self.algo.export_state()).encode()
        except Exception as e:
            print("[voc] 基线序列化失败:", e)
            return False
        if not _save_state_blob(blob):
            return False
        if cfg.DEBUG:
            print("[voc] 基线已保存 (%d 字节)" % len(blob))
        return True

    # ==================== 报警判定 ====================
    def _eval_alarm(self):
        v = self.values
        a = self.alarm
        w = self.warn
        ze_ready = self.ch["ze08"].state == ST_READY
        sg_ready = self.ch["sgp40"].state == ST_READY
        # 只有在"预热完成"之后才判定, 避免预热期间的无效读数误报
        a["ze08"] = (ze_ready and v["hcho_mg"] is not None
                     and v["hcho_mg"] >= cfg.HCHO_ALARM)
        a["sgp40"] = (sg_ready and v["tvoc"] is not None
                      and v["tvoc"] >= cfg.TVOC_ALARM)
        a["sht40"] = False
        # 警告级(不响蜂鸣器, 主界面显示 !); 报警时警告也同时为 True,
        # 界面优先显示闪烁的 *。
        w["ze08"] = (ze_ready and v["hcho_mg"] is not None
                     and v["hcho_mg"] >= cfg.HCHO_WARN)
        w["sgp40"] = (sg_ready and v["tvoc"] is not None
                      and v["tvoc"] >= cfg.TVOC_WARN)
        w["sht40"] = False
        return a

    def any_alarm(self):
        # 注意: 这里必须自己先算一遍。
        # snapshot() 只在屏幕正常时才会被调用, 如果 OLED 没接上,
        # 光靠 snapshot() 更新 alarm 的话, 报警灯/蜂鸣器就永远不会动。
        self._eval_alarm()
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
            "warn": self.warn,
            "info": self.info,
        }
