# -*- coding: utf-8 -*-
"""
ZE08-CH2O 甲醛传感器驱动 (Winsen 炜盛 ZE08-CH2O)
================================================================
接口 : UART, 9600 8N1, 3V TTL 电平(供电 3.7~5.5V, 建议接 5V)
量程 : 0~5 ppm, 分辨率 0.01 ppm
预热 : 数据手册标明 <= 3 分钟

协议 (数据手册 2. 通讯命令 / 3. 校验和计算):
    共 9 字节一帧, 帧头 0xFF
    校验值 = ( ~(Byte1+Byte2+...+Byte7) + 1 ) & 0xFF

  两种工作模式, 出厂默认"主动上传"(每秒自动发一帧):
    * 主动上传帧 (每秒一帧):
        FF 17 04 00 <浓度高> <浓度低> <量程高> <量程低> <校验>
        浓度单位 = ppb, = 高*256 + 低
    * 问答模式应答帧:
        FF 86 <ug/m3 高> <ug/m3 低> 00 00 <ppb 高> <ppb 低> <校验>

  本项目两种帧都能解析, 所以不管模块处在哪种模式都能工作。
  为了加快上电检测, 如果 1.5s 内没等到主动上传帧, 会自动发一次问答读取命令。

单独测试本驱动:
    >>> from machine import Pin, UART
    >>> import ze08, time
    >>> u = UART(2, baudrate=9600, tx=Pin(17), rx=Pin(16), timeout=0)
    >>> z = ze08.ZE08(u)
    >>> while True:
    ...     print(z.poll())      # -> ppb / None
    ...     time.sleep_ms(200)
"""

from time import sleep_ms, ticks_ms, ticks_diff

# 9 字节帧
CMD_SWITCH_ACTIVE = b"\xff\x01\x78\x40\x00\x00\x00\x00\x47"   # 切到主动上传
CMD_SWITCH_QA = b"\xff\x01\x78\x41\x00\x00\x00\x00\x46"       # 切到问答模式
CMD_READ_QA = b"\xff\x01\x86\x00\x00\x00\x00\x00\x79"         # 问答模式读浓度

# 1 ppb 甲醛 = 0.00125 mg/m3  (1 ppm = 1.25 mg/m3, 25C 1atm)
PPB_TO_MG = 0.00125

_MAX_BUF = 64


def checksum(frame):
    """数据手册的校验算法: (~(b1+..+b7) + 1) & 0xFF"""
    s = 0
    for i in range(1, 8):
        s += frame[i]
    return ((~s) + 1) & 0xFF


def frame_ok(frame):
    return len(frame) == 9 and frame[0] == 0xFF and checksum(frame) == frame[8]


class ZE08:
    def __init__(self, uart):
        self.uart = uart
        self.buf = bytearray()
        self.ppb = None
        self.ugm3 = None
        self.last_rx_ms = None
        self.frames = 0
        self.bad_frames = 0
        self._last_cmd_ms = None

    # ---------------- 发送 ----------------
    def reset(self):
        """清空接收缓冲和统计, 用于重新探测。"""
        self.buf = bytearray()
        self.ppb = None
        self.ugm3 = None
        self.last_rx_ms = None
        self.frames = 0
        self.bad_frames = 0
        try:
            while self.uart.any():
                self.uart.read()
        except Exception:
            pass

    def switch_to_active(self):
        self.uart.write(CMD_SWITCH_ACTIVE)

    def switch_to_qa(self):
        self.uart.write(CMD_SWITCH_QA)

    def request_reading(self):
        """问答模式下主动要一帧数据。"""
        self.uart.write(CMD_READ_QA)
        self._last_cmd_ms = ticks_ms()

    # ---------------- 接收 ----------------
    def _parse(self, frame):
        """解析一帧, 返回 True 表示解析成功。"""
        b1 = frame[1]
        if b1 == 0x17:
            # 主动上传: 浓度在 Byte4/Byte5, 单位 ppb
            self.ppb = (frame[4] << 8) | frame[5]
            self.ugm3 = self.ppb * PPB_TO_MG
        elif b1 == 0x86:
            # 问答应答: Byte2/3 是 ug/m3, Byte6/7 是 ppb
            self.ugm3 = (frame[2] << 8) | frame[3]
            self.ppb = (frame[6] << 8) | frame[7]
            if self.ppb == 0 and self.ugm3:
                self.ppb = int(self.ugm3 / 1.25)
        else:
            return False
        self.frames += 1
        self.last_rx_ms = ticks_ms()
        return True

    def poll(self):
        """
        非阻塞地吃掉串口里所有可用数据, 返回最新一次浓度(ppb)或 None。
        调用者可以高频调用(比如 100ms 一次), 不会阻塞。
        """
        n = self.uart.any()
        if n:
            chunk = self.uart.read(n)
            if chunk:
                self.buf.extend(chunk)
                if len(self.buf) > _MAX_BUF:
                    # 只保留最后一段, 防止缓冲区无限增长
                    # (注意: MicroPython 的 bytearray 不支持 del buf[i], 只能切片重建)
                    self.buf = self.buf[-_MAX_BUF:]

        got = False
        while len(self.buf) >= 9:
            if self.buf[0] != 0xFF:
                self.buf = self.buf[1:]
                continue
            frame = self.buf[:9]
            if frame_ok(frame):
                self.buf = self.buf[9:]
                if self._parse(frame):
                    got = True
                    break
            else:
                # 校验不过, 舍弃帧头字节继续找下一帧
                self.bad_frames += 1
                self.buf = self.buf[1:]
        return self.ppb if got else None

    # ---------------- 探测 ----------------
    def present(self, timeout_ms=1500):
        """
        上电检测: 在 timeout_ms 内如果能收到一帧合法数据, 说明传感器已连接。
        ZE08 出厂是主动上传模式(每秒一帧), 正常情况下 1.2s 内就能收到。
        如果模块被切成问答模式, 这里会自动补发一条读取命令。
        """
        t0 = ticks_ms()
        asked = False
        while ticks_diff(ticks_ms(), t0) < timeout_ms:
            self.poll()
            if self.last_rx_ms is not None:
                return True
            if not asked and ticks_diff(ticks_ms(), t0) > 400:
                self.request_reading()
                asked = True
            sleep_ms(20)
        return False

    # ---------------- 单位换算 ----------------
    @property
    def mgm3(self):
        """甲醛浓度, 单位 mg/m3 (国标单位)。"""
        if self.ppb is None:
            return None
        return self.ppb * PPB_TO_MG

    @property
    def ppm(self):
        if self.ppb is None:
            return None
        return self.ppb / 1000.0
