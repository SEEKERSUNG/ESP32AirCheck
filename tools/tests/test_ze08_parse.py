# -*- coding: utf-8 -*-
"""
ZE08 帧解析回归测试 (跑在开发板上, 用假 UART, 不需要真的接传感器)
    python tools/deploy.py run test_ze08_parse

为什么要有这个测试:
    CPython 的 bytearray 支持 del buf[i], 但 MicroPython 不支持。
    只靠电脑上的 py_compile / 主机测试发现不了这种差异, 必须让真正的
    MicroPython 去执行解析逻辑。本测试覆盖:
        正常帧 / 问答帧 / 模式标记 / 主动/问答单位换算 / 超量程限幅 /
        前面有垃圾字节 / 超长垃圾(考验缓冲区回收) / 校验错
"""

import ze08


class FakeUART:
    """假串口: 预先塞好字节, 让 poll() 像真串口一样读出来。"""

    def __init__(self):
        self.rx = bytearray()
        self.tx = bytearray()

    def feed(self, data):
        self.rx.extend(data)

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

    def write(self, data):
        self.tx.extend(data)


def active_frame(ppb):
    """主动上传帧: FF 17 04 00 <ppbHi> <ppbLo> <rngHi> <rngLo> <ck>"""
    # 量程字段用手册示例 0x1388 (5000)
    f = bytearray(b"\xff\x17\x04\x00\x00\x00\x13\x88\x00")
    f[4] = (ppb >> 8) & 0xFF
    f[5] = ppb & 0xFF
    f[8] = ze08.checksum(f)
    return bytes(f)


def qa_frame(ugm3, ppb):
    """问答应答帧: FF 86 <ugHi> <ugLo> 00 00 <ppbHi> <ppbLo> <ck>"""
    f = bytearray(b"\xff\x86\x00\x00\x00\x00\x00\x00\x00")
    f[2] = (ugm3 >> 8) & 0xFF
    f[3] = ugm3 & 0xFF
    f[6] = (ppb >> 8) & 0xFF
    f[7] = ppb & 0xFF
    f[8] = ze08.checksum(f)
    return bytes(f)


_fails = []


def check(name, cond, extra=""):
    if cond:
        print("PASS:", name)
    else:
        print("FAIL:", name, extra)
        _fails.append(name)


def new_sensor():
    return ze08.ZE08(FakeUART())


# ---- 1. 正常主动上传帧 ----
z = new_sensor()
z.uart.feed(active_frame(200))
ppb = z.poll()
check("主动帧: 返回浓度", ppb == 200, "got %r" % ppb)
check("主动帧: 模式标记", z.mode == "active", "got %r" % z.mode)
check("主动帧: ug/m3 换算", abs(z.ugm3 - 250.0) < 1e-9, "got %r" % z.ugm3)
check("主动帧: mg/m3 换算", abs(z.mgm3 - 0.25) < 1e-9, "got %r" % z.mgm3)
check("主动帧: 计数 +1", z.frames == 1 and z.bad_frames == 0)
check("主动帧: 缓冲区清空", len(z.buf) == 0)

# ---- 2. 问答模式应答帧 ----
z = new_sensor()
z.uart.feed(qa_frame(400, 320))
ppb = z.poll()
check("问答帧: 返回 ppb", ppb == 320, "got %r" % ppb)
check("问答帧: 模式标记", z.mode == "qa", "got %r" % z.mode)
check("问答帧: ug/m3", z.ugm3 == 400, "got %r" % z.ugm3)
# MicroPython 是 float32, 0.4 不能精确表示; 用 1e-6 容差
check("问答帧: mg/m3", abs(z.mgm3 - 0.4) < 1e-6, "got %r" % z.mgm3)

# ---- 2b. 问答帧只给 ug/m3 时回退换算 ppb ----
z = new_sensor()
z.uart.feed(qa_frame(40, 0))
ppb = z.poll()
check("问答帧回退: ppb", ppb == 32, "got %r" % ppb)

# ---- 3. 前面有垃圾字节, 仍然要能找回帧 ----
z = new_sensor()
z.uart.feed(b"\x00\x11\x22" + active_frame(123))
ppb = z.poll()
check("垃圾前缀: 仍解析成功", ppb == 123, "got %r" % ppb)

# ---- 4. 超长垃圾: 不能抛异常, 缓冲区要收敛 ----
z = new_sensor()
z.uart.feed(b"\xaa" * 100)
try:
    z.poll()
    ok = True
except Exception as e:
    ok = False
    print("   异常:", type(e).__name__, e)
check("超长垃圾: 不抛异常", ok)
check("超长垃圾: 缓冲区被裁剪", len(z.buf) <= ze08._MAX_BUF,
      "len=%d" % len(z.buf))

# ---- 5. 校验错的帧要被丢弃并计数 ----
z = new_sensor()
bad = bytearray(active_frame(500))
bad[8] ^= 0xFF          # 破坏校验
z.uart.feed(bytes(bad))
ppb = z.poll()
check("坏校验: 不返回数据", ppb is None, "got %r" % ppb)
check("坏校验: bad_frames 增加", z.bad_frames > 0,
      "got %d" % z.bad_frames)

# ---- 6. 坏帧后面紧跟好帧, 要能恢复 ----
z = new_sensor()
bad = bytearray(active_frame(500))
bad[8] ^= 0xFF
z.uart.feed(bytes(bad) + active_frame(88))
ppb = z.poll()
check("坏帧后恢复: 解析出正确值", ppb == 88, "got %r" % ppb)

# ---- 7. 数据分两次到达(模拟串口分包) ----
z = new_sensor()
whole = active_frame(456)
z.uart.feed(whole[:4])
check("分包: 第一段不返回", z.poll() is None)
z.uart.feed(whole[4:])
check("分包: 第二段拼出完整帧", z.poll() == 456)

# ---- 8. reset() 清空统计 ----
z.reset()
check("reset: 清空缓冲区", len(z.buf) == 0 and z.frames == 0
      and z.bad_frames == 0 and z.ppb is None and z.mode is None)

# ---- 9. 超出量程的数据按 5000 ppb 限幅 ----
z = new_sensor()
z.uart.feed(active_frame(6000))
ppb = z.poll()
check("超量程: 限幅到 5000 ppb", ppb == 5000, "got %r" % ppb)
check("超量程: mg/m3 限幅", abs(z.mgm3 - 6.25) < 1e-9, "got %r" % z.mgm3)

print()
if _fails:
    print("结果: %d 项失败 -> %s" % (len(_fails), ", ".join(_fails)))
else:
    print("结果: 全部通过")
