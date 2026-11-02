# -*- coding: utf-8 -*-
"""
历史均值回归测试 (在电脑上跑, 不需要开发板)
================================================================
验证 history.History 的窗口计算、边界行为和跨断电 blob 格式:

  1. 桶边界上的采样点算进新桶, 不进旧桶
  2. 3h / 6h / 12h 三个窗口各自取最近 N 个桶
  3. 预热未完成(None / ready=False)的采样不计入
  4. 负数温度、超量程夹取
  5. export_blob / import_blob 往返一致; 版本/长度/pos 非法时拒绝
  6. 没有 esp32 模块时 save()/load() 安静地返回 False
  7. 配置不当(slots 小于窗口)时窗口被夹到环大小, 不越界

用法: python tools/host_tests/test_history.py
"""

import os
import struct
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "..", "src"))

import history  # noqa: E402

FAILS = []

BUCKET = 60 * 1000              # 测试里用 1 分钟一个桶, 跑得快
SLOTS = 720                     # 720 桶 -> 12h 窗口 = 720
DT = 2000                       # 每个桶喂 30 次(够验证"桶内平均")
READY = {"temp": True, "rh": True, "hcho": True, "voc": True}


def check(cond, msg):
    print(("PASS: " if cond else "FAIL: ") + msg)
    if not cond:
        FAILS.append(msg)


def vals(temp=20.0, rh=50.0, hcho=0.04, voc=100):
    return {"temp": temp, "rh": rh, "hcho_mg": hcho, "tvoc": voc}


def feed(h, fn, buckets, closed=None, now=0, dt=DT, ready=READY):
    """
    以固定节奏喂数据。每到一个桶的第一次 tick, 上一桶就会被关闭。
    closed 里按关闭顺序记录每个桶的取值, 用来算期望值。
    """
    if closed is None:
        closed = []
    step = h.bucket_ms // dt
    for b in range(buckets):
        v = fn(b)
        for _ in range(step):
            h.tick(now, v, ready)
            now += dt
        while len(closed) < h.filled:
            closed.append(fn(len(closed))["tvoc"])
    return now, closed


def tail_mean(seq, n):
    tail = seq[-n:] if n < len(seq) else seq
    return sum(tail) / len(tail)


def new_history(**kw):
    kw.setdefault("bucket_ms", BUCKET)
    kw.setdefault("slots", SLOTS)
    kw.setdefault("save_ms", 0)
    kw.setdefault("persist", False)
    return history.History(**kw)


def main():
    # ---- 1. 桶边界: 边界上的点属于新桶 ----
    h = new_history(bucket_ms=60000, slots=8)
    seq = (10, 99, 10)
    h.tick(0, vals(voc=seq[0]), READY)
    h.tick(60000, vals(voc=seq[1]), READY)
    h.tick(120000, vals(voc=seq[2]), READY)
    check(h.filled == 2, "边界测试: 关了两个桶")
    check(h.ring["voc"][0] == 10, "边界点算进新桶(旧桶不含它)")
    check(h.ring["voc"][1] == 99, "第二个桶的值正确")

    # ---- 2. 三个窗口各自取最近 N 个桶 ----
    h = new_history()
    fn = lambda b: vals(voc=100 if b < 200 else 200)   # noqa: E731
    _, closed = feed(h, fn, 500)
    a = h.averages()
    w3, w6, w12 = (h.windows[0][1], h.windows[1][1], h.windows[2][1])
    check(len(closed) == h.filled, "关闭桶数与 filled 一致")
    check(h.filled == 499, "喂 500 桶后关闭 499 个")
    check(a["voc"]["3h_n"] == w3, "3h 窗口桶数 = %d" % w3)
    check(a["voc"]["6h_n"] == w6, "6h 窗口桶数 = %d" % w6)
    check(a["voc"]["12h_n"] == 499, "12h 窗口只统计已有的 499 桶")
    check(a["voc"]["3h"] == tail_mean(closed, w3), "3h 均值 = 最近 %d 桶" % w3)
    check(a["voc"]["6h"] == tail_mean(closed, w6), "6h 均值 = 最近 %d 桶" % w6)
    check(a["voc"]["12h"] == tail_mean(closed, w12), "12h 均值 = 全部已有桶")
    check(a["voc"]["3h"] == 200.0, "3h 窗口里已经全是新值")
    check(abs(a["temp"]["3h"] - 20.0) < 1e-9, "温度均值 (x100 后还原)")
    check(abs(a["rh"]["3h"] - 50.0) < 1e-9, "湿度均值")
    check(abs(a["hcho"]["3h"] - 0.04) < 1e-9, "甲醛均值 (按 ug/m3 存储后还原)")

    # 缓存: 同一个桶内多次 averages() 结果一样
    check(a is h.averages(), "averages() 有缓存, 不重复计算")

    # ---- 3. 预热未完成 / 没读数时不计入 ----
    h = new_history(bucket_ms=60000, slots=8)
    off = {"temp": False, "rh": False, "hcho": False, "voc": False}
    for b in range(5):
        h.tick(b * 60000, vals(), off)
    a = h.averages()
    check(h.filled == 4, "全是未就绪: 仍然按时间关桶")
    check(a["voc"]["12h"] is None and a["voc"]["12h_n"] == 0,
          "未就绪的桶不参与均值")

    h = new_history(bucket_ms=60000, slots=8)
    for b in range(5):
        h.tick(b * 60000, {"temp": None, "rh": None,
                           "hcho_mg": None, "tvoc": None}, READY)
    a = h.averages()
    check(a["temp"]["3h"] is None, "无效读数( None )不参与均值")

    # 只有 SGP40 就绪时, 温度列不该被 VOC 的数据污染
    h = new_history(bucket_ms=60000, slots=8)
    only_voc = {"temp": False, "rh": False, "hcho": False, "voc": True}
    for b in range(5):
        h.tick(b * 60000, vals(), only_voc)
    a = h.averages()
    check(a["voc"]["3h"] == 100.0 and a["temp"]["3h"] is None,
          "按通道独立判断就绪状态")

    # ---- 4. 负数温度 / 超量程夹取 ----
    h = new_history(bucket_ms=60000, slots=8)
    for b in range(5):
        h.tick(b * 60000, vals(temp=-5.25), READY)
    check(abs(h.averages()["temp"]["3h"] + 5.25) < 1e-9, "负温度保留符号")

    h = new_history(bucket_ms=60000, slots=8)
    for b in range(5):
        h.tick(b * 60000, vals(temp=1000.0), READY)
    check(h.averages()["temp"]["3h"] == 320.0, "超量程读数被夹到 int16 上限")

    # ---- 5. export / import ----
    h = new_history(slots=64)
    feed(h, lambda b: vals(voc=100 + b % 7), 80)
    blob = h.export_blob()
    check(len(blob) == 4 + 4 * 64 * 2, "blob 长度 = 头 4 字节 + 4 通道 int16")

    h2 = new_history(slots=64)
    check(h2.import_blob(blob), "import_blob 接受合法的 blob")
    check(h2.pos == h.pos, "pos 一起恢复")
    check(h2.averages() == h.averages(), "恢复后的均值与原对象一致")

    check(not h2.import_blob(blob[:-2]), "长度不对的 blob 被拒绝")
    check(not h2.import_blob(None), "None 被拒绝")

    bad = bytearray(blob)
    struct.pack_into("<H", bad, 0, 99)
    check(not new_history(slots=64).import_blob(bytes(bad)), "版本号不对被拒绝")

    bad = bytearray(blob)
    struct.pack_into("<H", bad, 2, 64)
    check(not new_history(slots=64).import_blob(bytes(bad)), "pos 越界被拒绝")

    # ---- 6. 没有 esp32 模块时不报错 ----
    h = new_history(persist=True)
    check(h.save() is False, "没有 esp32 时 save() 返回 False")
    check(h.load() is False, "没有 esp32 时 load() 返回 False")
    check(history.erase_state() is False,
          "没有 esp32 时 erase_state() 返回 False")

    # ---- 7. slots 小于 12h 窗口时夹到环大小 ----
    h = new_history(bucket_ms=60000, slots=10)
    check(h.windows == [("3h", 10), ("6h", 10), ("12h", 10)],
          "窗口不会超过环大小")

    # ---- 8. clear ----
    h = new_history(bucket_ms=60000, slots=8)
    for b in range(5):
        h.tick(b * 60000, vals(), READY)
    h.clear()
    a = h.averages()
    check(h.filled == 0 and h.pos == 0 and a["voc"]["12h"] is None,
          "clear() 清空环和均值")

    print()
    if FAILS:
        print("结果: %d 项失败" % len(FAILS))
        return 1
    print("结果: 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
