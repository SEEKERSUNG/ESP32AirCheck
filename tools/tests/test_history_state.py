# -*- coding: utf-8 -*-
"""
历史均值持久化回归测试 (跑在开发板上)
    python tools/deploy.py run test_history_state

主机端 test_history.py 用的是电脑上的 struct, 这里再在板子上验证一遍:
  1. struct.pack_into / unpack_from 在 MicroPython 上可用
  2. 保存 -> 新建对象 -> 读回, 均值和 pos 完全一致
  3. 删掉后读不到

用独立的 NVS 键, 不会覆盖主程序保存的历史。
"""

import history

NS = "airchecktest"
KEY = "histstate_test"

# 换到独立的命名空间/键, 别动主程序的数据
history._NVS_NAMESPACE = NS
history._NVS_KEY = KEY

FAILS = []


def check(cond, msg):
    print(("  PASS  " if cond else "  FAIL  ") + msg)
    if not cond:
        FAILS.append(msg)


def vals(voc):
    return {"temp": 20.0, "rh": 50.0, "hcho_mg": 0.04, "tvoc": voc}


READY = {"temp": True, "rh": True, "hcho": True, "voc": True}


def new_history():
    return history.History(bucket_ms=1000, slots=8, save_ms=0, persist=True)


def erase():
    if history.esp32 is None:
        return
    try:
        nvs = history.esp32.NVS(NS)
        nvs.erase_key(KEY)
        nvs.commit()
    except Exception:
        pass


print("存储后端:", "ESP32 NVS" if history.esp32 is not None else "无(电脑)")
erase()

h = new_history()
check(h.load() is False, "初始没有保存的数据")

now = 0
for i in range(6):
    h.tick(now, vals(100 + i), READY)
    now += 1000
check(h.filled == 5, "关闭了 5 个桶")
check(len(h.export_blob()) == 4 + 4 * 8 * 2,
      "blob 长度 = %d 字节" % len(h.export_blob()))
check(h.save(), "保存成功")

h2 = new_history()
check(h2.load(), "读回成功")
check(h2.pos == h.pos, "pos 一致")
check(h2.filled == 8, "读回后按整圈扫描(空槽是 INVALID)")
check(h2.averages() == h.averages(), "均值与原对象完全一致")
check(h2.averages()["voc"]["12h"] == 102.0, "VOC 均值 = 102")

erase()
check(h2.load() is False, "删除后读不到")

# ---------------------------------------------------------------- 周期性保存
# 主程序不是手动调用 save(), 而是 tick() 里到点自动存, 这里验证那条路径
h3 = new_history()
h3.save_ms = 3000
erase()
h3.tick(0, vals(50), READY)
check(h3.load() is False, "第一个 tick 只开始计时, 还没到保存时间")
h3.tick(4000, vals(51), READY)
check(new_history().load(), "超过保存周期后 tick() 自动写盘")
check(history.erase_state(), "erase_state() 可删除已保存的历史")
check(new_history().load() is False, "erase_state() 之后读不到")

print()
if FAILS:
    print("结果: %d 项失败" % len(FAILS))
    for f in FAILS:
        print("  -", f)
else:
    print("结果: 全部通过")
