# -*- coding: utf-8 -*-
"""
OLED 界面渲染
================================================================
本模块只负责"把数据画到屏幕上", 不碰任何传感器, 方便单独调试界面。

界面一共有 3 页(按键 GPIO0 循环切换):
    第 1 页 主界面   : 温湿度 / 甲醛 / TVOC (只显示读数, 不显示连接状态)
    第 2 页 运行信息 : 各传感器在线状态 + 运行时间 (判断连接/预热/异常看这页)
    第 3 页 原始数据 : VOC 原始值、串口收包统计等, 单独调传感器时用

排版约定 (屏幕 128x64, 左上角为原点)
    英文 : 用 MicroPython 内置的 8x8 字库, 每字符宽 8 像素
    汉字 : 用 cfont.py 里 16x16 的点阵, 每字宽 16 像素、高 16 像素
    两者可混排, 由 ptext() 统一处理。
    汉字行高 16, 所以中文状态行的 y 坐标最大只能到 52。

    如果没有 cfont.py(没生成中文字库), 会自动退回英文界面。
"""

import time
import framebuf

try:
    import cfont
    HAS_CFONT = True
except ImportError:
    HAS_CFONT = False

# --------------------------------------------------------------------------
# 传感器状态 (与 sensors.py 保持一致)
# --------------------------------------------------------------------------
ST_PROBE = "PROBE"      # 正在检测是否连接
ST_ABSENT = "ABSENT"    # 没检测到(未连接)
ST_WARMUP = "WARMUP"    # 已连接, 正在预热
ST_READY = "READY"      # 正常
ST_FAULT = "FAULT"      # 在线但读不到数据

# --------------------------------------------------------------------------
# 页面
# --------------------------------------------------------------------------
PAGE_MAIN = 0
PAGE_INFO = 1
PAGE_RAW = 2
PAGE_COUNT = 3

# --------------------------------------------------------------------------
# 文案 (中文, 英文兜底)
# --------------------------------------------------------------------------
_TXT = {
    "title":    ("空气检测", "AIR CHECK"),
    "raw":      ("原始数据", "RAW DATA"),
    "uptime":   ("运行", "UP"),
    "probe":    ("自检中", "SCAN"),
    "absent":   ("未连接", "N/A"),
    "warmup":   ("预热中", "WARM"),
    "ready":    ("就绪", "OK"),
    "fault":    ("异常", "ERROR"),
    "alarm":    ("超标", "OVER"),
    "allok":    ("全部就绪", "ALL OK"),
}


def T(key):
    zh, en = _TXT[key]
    return zh if HAS_CFONT else en


# --------------------------------------------------------------------------
# 字符串 -> 码点
# --------------------------------------------------------------------------
# 有的 MicroPython 固件把 str 当 UTF-8 字节串(此时 len("空")==3),
# 有的带完整 Unicode 支持(此时 len("空")==1)。两种都兼容。
_IS_UNICODE = len("\u7a7a") == 1


def iter_codepoints(s):
    if _IS_UNICODE:
        for ch in s:
            yield ord(ch)
        return
    data = s.encode() if hasattr(s, "encode") else s
    i = 0
    n = len(data)
    while i < n:
        b0 = data[i]
        if b0 < 0x80:
            yield b0
            i += 1
        elif (b0 & 0xE0) == 0xC0:
            yield ((b0 & 0x1F) << 6) | (data[i + 1] & 0x3F)
            i += 2
        elif (b0 & 0xF0) == 0xE0:
            yield (((b0 & 0x0F) << 12) | ((data[i + 1] & 0x3F) << 6)
                   | (data[i + 2] & 0x3F))
            i += 3
        else:
            yield 0xFFFD
            i += 4


def text_width(s):
    """混排文本的像素宽度。"""
    w = 0
    for cp in iter_codepoints(s):
        w += 16 if cp > 0x7F else 8
    return w


def fmt_hms(seconds):
    seconds = int(seconds)
    return "%d:%02d:%02d" % (seconds // 3600, (seconds // 60) % 60, seconds % 60)


def _lsb_first_mono_format():
    """
    挑出"字节最低位 = 最左边像素"的单色 framebuf 格式。

    cfont.py 里的点阵是按 LSB-first 生成的(bit0 = 最左像素)。
    但是 MicroPython 各固件对 MONO_HLSB / MONO_HMSB 的位序定义并不统一
    (在 ESP32 v1.29.0 上, MONO_HLSB 是 bit7 在最左, MONO_HMSB 才是 bit0 在最左),
    一旦选错, 汉字会左右按字节对称错位, 看起来就是一堆乱码。

    所以这里直接画一个像素试出来, 别猜。
    """
    for name in ("MONO_HMSB", "MONO_HLSB"):
        fmt = getattr(framebuf, name, None)
        if fmt is None:
            continue
        buf = bytearray(2)
        fb = framebuf.FrameBuffer(buf, 16, 1, fmt)
        fb.pixel(0, 0, 1)
        if buf[0] == 0x01:      # bit0 亮说明这一位就是最左像素
            return fmt
    return framebuf.MONO_HLSB


MONO_GLYPH = _lsb_first_mono_format()


# --------------------------------------------------------------------------
# 创建显示屏
# --------------------------------------------------------------------------
def create_display(i2c, cfg, scan_addrs=None):
    """
    按 config 创建 OLED 对象。
    返回 (oled, err): 成功时 oled 不为 None; 失败时 err 是原因字符串。
    """
    if not getattr(cfg, "OLED_ENABLED", False):
        return None, "OLED 已在 config 中关闭"

    # 先确认地址在总线上, 避免驱动初始化抛出难懂的总线错误
    if scan_addrs is not None and cfg.OLED_ADDR not in scan_addrs:
        return None, "地址 0x%02X 无应答" % cfg.OLED_ADDR

    try:
        if cfg.OLED_CONTROLLER == "sh1106":
            import sh1106
            oled = sh1106.SH1106_I2C(
                cfg.OLED_WIDTH, cfg.OLED_HEIGHT, i2c, addr=cfg.OLED_ADDR,
                col_offset=getattr(cfg, "OLED_SH1106_OFFSET", 2))
        else:
            import ssd1306
            oled = ssd1306.SSD1306_I2C(
                cfg.OLED_WIDTH, cfg.OLED_HEIGHT, i2c, addr=cfg.OLED_ADDR)
    except Exception as e:
        return None, "%s: %s" % (type(e).__name__, e)

    try:
        oled.contrast(getattr(cfg, "OLED_CONTRAST", 0xFF))
        if getattr(cfg, "OLED_ROTATE", False) and hasattr(oled, "rotate"):
            oled.rotate(True)
    except Exception:
        pass
    return oled, None


# --------------------------------------------------------------------------
# UI
# --------------------------------------------------------------------------
class UI:
    def __init__(self, oled, version=""):
        self.oled = oled
        self.version = version
        self.width = oled.width
        self.height = oled.height
        self.page = PAGE_MAIN
        self._glyphs = {}
        if HAS_CFONT:
            for cp, data in cfont.GLYPHS.items():
                self._glyphs[cp] = framebuf.FrameBuffer(
                    bytearray(data), cfont.GLYPH_W, cfont.GLYPH_H,
                    MONO_GLYPH)

    # ================= 基础绘制 =================
    def clear(self):
        self.oled.fill(0)

    def flush(self):
        self.oled.show()

    def rebind_i2c(self, i2c):
        """
        I2C 总线被重建后, 把新的总线对象交给屏幕驱动。

        否则屏幕会继续用那个已经 deinit 掉的旧对象, 画面就再也不刷新了
        (看起来像"屏幕死机", 其实是总线换了)。
        """
        if hasattr(self.oled, "i2c"):
            self.oled.i2c = i2c

    # ================= 息屏 / 唤醒 =================
    def screen_off(self):
        """关闭显示。只发 DISP OFF, 不清显存, 唤醒后先恢复上一帧。"""
        try:
            self.oled.poweroff()
            return True
        except Exception:
            return False

    def screen_on(self):
        try:
            self.oled.poweron()
            return True
        except Exception:
            return False

    def text(self, s, x, y):
        """纯英文/数字文本。"""
        self.oled.text(s, x, y)

    def text_center(self, s, y):
        self.oled.text(s, (self.width - 8 * len(s)) // 2, y)

    def ptext(self, s, x, y):
        """中英文混排, 返回结束时的 x 坐标。"""
        for cp in iter_codepoints(s):
            if cp <= 0x7F:
                self.oled.text(chr(cp), x, y)
                x += 8
            else:
                g = self._glyphs.get(cp)
                if g is None:
                    self.oled.rect(x + 1, y, 14, 14, 1)   # 缺字占位
                else:
                    self.oled.blit(g, x, y, 0)
                x += 16
        return x

    def ptext_center(self, s, y):
        self.ptext(s, (self.width - text_width(s)) // 2, y)

    def _page_indicator(self, x=64):
        self.oled.text("%d/%d" % (self.page + 1, PAGE_COUNT), x, 4)

    # ================= 开机 / 通用提示 =================
    def splash(self):
        self.clear()
        self.ptext_center(T("title"), 14)
        self.text_center("ESP32 AirCheck", 38)
        if self.version:
            self.text_center("v" + self.version, 50)
        self.flush()

    def message(self, title, line1="", line2=""):
        self.clear()
        self.ptext_center(title, 8)
        if line1:
            self.ptext_center(line1, 32)
        if line2:
            self.text_center(line2, 50)
        self.flush()

    # ================= 第 1 页 主界面 =================
    def show_main(self, snap):
        self.clear()
        chans = snap["channels"]
        vals = snap["values"]
        alarm = snap["alarm"]
        warn = snap["warn"]

        # 顶部只留标题 + 页码。连接/预热/异常状态统一放第 2 页,
        # 主界面只保留读数和超标/警告标记。
        self.ptext(T("title"), 0, 0)
        self._page_indicator(104)

        # 4 行读数: 标签左对齐, 数值从固定列(x=40)开始,
        # 行距 12px, 把标题以下的 48px 均匀铺满。
        # HCHO 保留 2 位小数: ZE08 分辨率约 10 ppb (0.0125 mg/m3),
        # 第 3 位小数没有实际意义。
        rows = (
            ("sht40", "TEMP",
             "----" if vals["temp"] is None else "%.1f C" % vals["temp"]),
            ("sht40", "HUM",
             "----" if vals["rh"] is None else "%.1f %%" % vals["rh"]),
            ("ze08", "HCHO",
             "----" if vals["hcho_mg"] is None
             else "%.2fmg/m3" % vals["hcho_mg"]),
            ("sgp40", "VOC",
             "----" if vals["tvoc"] is None else "%3d idx" % vals["tvoc"]),
        )
        y = 16
        for key, label, text in rows:
            if not chans[key].get("enabled", True):
                continue
            self._value_row(y, label, text, alarm.get(key), warn.get(key))
            y += 12

        self.flush()

    def _value_row(self, y, label, value, is_alarm, is_warn):
        self.text(label, 0, y)
        self.text(value, 40, y)
        if is_alarm:
            # 报警优先: 数值右侧闪烁 *
            self.text("*" if (time.ticks_ms() // 400) & 1 else " ", 120, y)
        elif is_warn:
            # 警告级: 常亮 !; 不响蜂鸣器
            self.text("!", 120, y)

    # ================= 第 2 页 运行信息 =================
    # 这一页是诊断用的, 行距只有 8 像素, 放不下 16 像素高的汉字,
    # 所以状态用英文缩写表示。主界面不再显示这些状态, 判断连接情况看这里。
    _STATE_EN = {
        ST_READY: "OK",
        ST_WARMUP: "WARM",
        ST_ABSENT: "N/A",
        ST_FAULT: "ERR",
        ST_PROBE: "SCAN",
    }

    def show_info(self, snap):
        self.clear()
        chans = snap["channels"]
        t = fmt_hms(snap["uptime"])
        # 右上角还要放 3 个字符的页码, 运行超过 99 小时后 H:MM:SS 会挤到页码上,
        # 这时退化成 H:MM (例如 "100:12"), 仍能看出运行了多久。
        if len(t) > 8:
            total = int(snap["uptime"])
            t = "%d:%02d" % (total // 3600, (total // 60) % 60)
        self.ptext(T("uptime") + " " + t, 0, 0)
        self._page_indicator(104)
        rows = (
            ("SHT40", "0x44", "sht40"),
            ("SGP40", "0x59", "sgp40"),
            ("ZE08", "UART", "ze08"),
        )
        y = 16
        for name, addr, key in rows:
            self.text("%s %s" % (name, addr), 0, y)
            ch = chans[key]
            if not ch.get("enabled", True):
                s = "OFF"                    # 这个传感器没启用(不是坏了)
            else:
                s = self._STATE_EN.get(ch["state"], "?")
            self.oled.text(s, self.width - 8 * len(s), y)
            y += 8
        # 扫描结果可能超过 16 字符, 截断避免画出屏幕右边界
        self.text(("I2C %s" % snap["info"].get("scan", ""))[:16], 0, y)
        self.flush()

    # ================= 第 3 页 原始数据 =================
    def show_raw(self, snap):
        self.clear()
        self.ptext_center(T("raw"), 0)
        self._page_indicator(104)
        info = snap["info"]
        self.text("VOCraw %5d" % info.get("voc_raw", 0), 0, 16)
        self.text("VOCidx %5d" % (snap["values"]["tvoc"] or 0), 0, 24)
        self.text("SGP40 lrn %3d" % (1 if info.get("voc_learning") else 0), 0, 32)
        self.text("ZE08 rx %4d" % info.get("ze08_frames", 0), 0, 40)
        self.text("ZE08 bad %3d" % info.get("ze08_bad", 0), 0, 48)
        self.text(("I2C %s" % info.get("scan", ""))[:16], 0, 56)
        self.flush()

    # ================= 按页刷新 =================
    def next_page(self):
        self.page = (self.page + 1) % PAGE_COUNT
        return self.page

    def render(self, snap):
        if self.page == PAGE_MAIN:
            self.show_main(snap)
        elif self.page == PAGE_INFO:
            self.show_info(snap)
        else:
            self.show_raw(snap)
