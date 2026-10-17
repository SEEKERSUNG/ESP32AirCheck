# -*- coding: utf-8 -*-
"""
ESP32 空气质量检测仪 —— 主程序
================================================================
上电流程:
    1. 初始化 I2C 总线, 扫描一遍总线上的地址
    2. 初始化 OLED 并显示开机画面
    3. 初始化 ZE08 用的 UART
    4. 进入"传感器自检"画面, 逐个判断传感器是否连接
    5. 自检结束后进入主界面, 并开始周期采集:
         - 预热中的传感器显示"预热中 + 倒计时", 不参与报警判定
         - 未连接的传感器显示"未连接", 每 10 秒自动重找一次(支持热插拔)
    6. 按键(GPIO27)切换 主界面 / 运行信息 / 原始数据 三页

所有任务都在 asyncio 里跑, 采集不阻塞屏幕刷新。
"""

import time
from time import ticks_ms, ticks_diff

try:
    import uasyncio as asyncio
except ImportError:
    import asyncio

from machine import Pin, I2C, UART

import config as cfg
import ui
import sensors


def log(*args):
    if cfg.DEBUG:
        print(*args)


class App:
    def __init__(self):
        self.i2c = None
        self.uart = None
        self.oled = None
        self.view = None            # ui.UI 实例; OLED 没接上时为 None
        self.mgr = None
        self.led = None
        self.buzzer = None
        self.button = None
        self._btn_last = 1
        self._btn_ms = 0
        self._ui_fails = 0
        self.selftest_until = 0

    # ------------------------------------------------------------------
    # 初始化
    # ------------------------------------------------------------------
    def setup_i2c(self):
        self.i2c = I2C(cfg.I2C_ID, sda=Pin(cfg.I2C_SDA), scl=Pin(cfg.I2C_SCL),
                       freq=cfg.I2C_FREQ)
        try:
            found = self.i2c.scan()
        except OSError:
            found = []
        log("[i2c] SDA=GPIO%d SCL=GPIO%d 扫描到:" % (cfg.I2C_SDA, cfg.I2C_SCL),
            [hex(a) for a in found])
        if not found:
            log("[i2c] 总线上没有任何设备, 请检查接线和供电")
        return set(found)

    def rebind_i2c(self, i2c):
        """总线被 SensorManager 重建后, 同步换掉屏幕和本地持有的引用。"""
        self.i2c = i2c
        if self.view is not None:
            self.view.rebind_i2c(i2c)
        log("[i2c] 已切到重建后的总线对象")

    def setup_uart(self):
        if not cfg.ZE08_ENABLED:
            return None
        try:
            self.uart = UART(cfg.ZE08_UART_ID, baudrate=cfg.ZE08_BAUD,
                             tx=Pin(cfg.ZE08_TX), rx=Pin(cfg.ZE08_RX),
                             timeout=0, rxbuf=512)
        except (TypeError, ValueError):
            self.uart = UART(cfg.ZE08_UART_ID, baudrate=cfg.ZE08_BAUD,
                             tx=Pin(cfg.ZE08_TX), rx=Pin(cfg.ZE08_RX),
                             timeout=0)
        log("[uart] ZE08 TX=GPIO%d RX=GPIO%d %dbps"
            % (cfg.ZE08_TX, cfg.ZE08_RX, cfg.ZE08_BAUD))
        return self.uart

    def setup_oled(self, scan):
        oled, err = ui.create_display(self.i2c, cfg, scan)
        if oled is None:
            log("[oled] 未连接: %s" % err)
            return None
        log("[oled] %s %dx%d @0x%02X 就绪"
            % (cfg.OLED_CONTROLLER, cfg.OLED_WIDTH, cfg.OLED_HEIGHT,
               cfg.OLED_ADDR))
        return ui.UI(oled, cfg.VERSION)

    def setup_io(self):
        if cfg.STATUS_LED is not None:
            try:
                off = 1 if cfg.STATUS_LED_ACTIVE_LOW else 0
                self.led = Pin(cfg.STATUS_LED, Pin.OUT, value=off)
            except Exception as e:
                log("[led] 初始化失败:", e)
        if cfg.BUTTON_ENABLED:
            try:
                self.button = Pin(cfg.BUTTON_PIN, Pin.IN, Pin.PULL_UP)
                self._btn_last = self.button.value()
            except Exception as e:
                log("[btn] 初始化失败:", e)
        if cfg.BUZZER_ENABLED:
            try:
                self.buzzer = Pin(cfg.BUZZER_PIN, Pin.OUT, value=0)
            except Exception as e:
                log("[buzzer] 初始化失败:", e)

    def led_write(self, on):
        if self.led is None:
            return
        if cfg.STATUS_LED_ACTIVE_LOW:
            self.led.value(0 if on else 1)
        else:
            self.led.value(1 if on else 0)

    # ------------------------------------------------------------------
    # 任务: I2C 三个传感器
    # ------------------------------------------------------------------
    async def task_i2c(self):
        n = 0
        while True:
            t0 = ticks_ms()
            try:
                now = ticks_ms()
                if self.mgr.last_scan is None or \
                        ticks_diff(now, self.mgr.last_scan) >= cfg.RESCAN_MS:
                    self.mgr.scan_bus()
                    self.mgr.last_scan = now
                self.mgr._step_sht40(now)
                self.mgr._step_sgp40(now)
                if n % cfg.SCD40_EVERY == 0:
                    self.mgr._step_scd40(now)
                n += 1
            except Exception as e:
                log("[i2c] 任务异常:", type(e).__name__, e)
            spent = ticks_diff(ticks_ms(), t0)
            await asyncio.sleep_ms(max(50, cfg.I2C_POLL_MS - spent))

    # ------------------------------------------------------------------
    # 任务: ZE08 (需要高频率刷串口缓冲)
    # ------------------------------------------------------------------
    async def task_ze08(self):
        if self.uart is None:
            return
        while True:
            try:
                self.mgr._step_ze08()
            except Exception as e:
                log("[ze08] 任务异常:", type(e).__name__, e)
            await asyncio.sleep_ms(100)

    # ------------------------------------------------------------------
    # 任务: 屏幕刷新
    # ------------------------------------------------------------------
    async def task_ui(self):
        while True:
            if self.view is not None:
                try:
                    probing = (not self.mgr.all_resolved()) or \
                        ticks_diff(ticks_ms(), self.selftest_until) < 0
                    if probing:
                        self.view.show_selftest(self.mgr.selftest_rows())
                    else:
                        self.view.render(self.mgr.snapshot())
                    self._ui_fails = 0
                except Exception as e:
                    self._ui_fails += 1
                    if self._ui_fails == 5:
                        log("[oled] 连续刷新失败, 停止显示:", e)
                    if self._ui_fails >= 5:
                        self.view = None
            await asyncio.sleep_ms(cfg.UI_PERIOD_MS)

    # ------------------------------------------------------------------
    # 任务: 按键翻页
    # ------------------------------------------------------------------
    async def task_button(self):
        if self.button is None:
            return
        while True:
            try:
                v = self.button.value()
                now = ticks_ms()
                if self._btn_last == 1 and v == 0 and \
                        ticks_diff(now, self._btn_ms) > 250:
                    self._btn_ms = now
                    if self.view is not None:
                        self.view.next_page()
                        log("[ui] 切换到第 %d 页" % (self.view.page + 1))
                self._btn_last = v
            except Exception:
                pass
            await asyncio.sleep_ms(30)

    # ------------------------------------------------------------------
    # 任务: 指示灯 + 蜂鸣器
    # ------------------------------------------------------------------
    async def task_led(self):
        if self.led is None:
            return
        while True:
            try:
                if self.mgr.any_alarm():
                    self.led_write((ticks_ms() // 150) & 1)      # 报警: 快速闪
                    await asyncio.sleep_ms(150)
                else:
                    self.led_write(True)                          # 正常: 心跳
                    await asyncio.sleep_ms(80)
                    self.led_write(False)
                    await asyncio.sleep_ms(cfg.LED_HEARTBEAT_MS)
            except Exception:
                await asyncio.sleep_ms(500)

    async def task_buzzer(self):
        if self.buzzer is None:
            return
        while True:
            try:
                if self.mgr.any_alarm():
                    self.buzzer.value(1)
                    await asyncio.sleep_ms(200)
                    self.buzzer.value(0)
                    await asyncio.sleep_ms(800)
                else:
                    self.buzzer.value(0)
                    await asyncio.sleep_ms(200)
            except Exception:
                await asyncio.sleep_ms(500)

    # ------------------------------------------------------------------
    # 主流程
    # ------------------------------------------------------------------
    async def run(self):
        log("=" * 48)
        log(" ESP32 空气质量检测仪 v%s" % cfg.VERSION)
        log("=" * 48)

        scan = self.setup_i2c()
        self.setup_io()

        # 开机画面
        self.view = self.setup_oled(scan)
        if self.view is not None:
            self.view.splash()
            await asyncio.sleep_ms(1200)

        self.setup_uart()
        self.mgr = sensors.SensorManager(self.i2c, self.uart,
                                         oled_ok=(self.view is not None),
                                         on_bus_rebuilt=self.rebind_i2c)
        self.mgr.scan_bus()
        self.selftest_until = ticks_ms() + 2000

        if self.view is None:
            log("[oled] 屏幕未连接 —— 数据只打印到串口")
            self.mgr.oled_ok = False

        await asyncio.gather(
            self.task_i2c(),
            self.task_ze08(),
            self.task_ui(),
            self.task_button(),
            self.task_led(),
            self.task_buzzer(),
        )


def main():
    app = App()
    try:
        asyncio.run(app.run())
    except KeyboardInterrupt:
        print("\n[main] 已停止 (Ctrl-C)")


if __name__ == "__main__":
    main()
