# -*- coding: utf-8 -*-
"""
在电脑上跑 tools/tests/ 里的开发板测试
================================================
给 MicroPython 的 time / machine / framebuf 打上替身, 然后原样执行
开发板上的测试脚本。适用条件: **脚本不依赖真实字形渲染**。

    python tools/host_tests/run_board_test.py tools/tests/test_state_machine.py

不适合的脚本 (例如 test_ui.py 要看排版) 会被直接拒绝 —— 电脑上的
framebuf 替身不画字, 跑出来的 ASCII 图没有意义, 只能看有没有抛异常。
"""

import os
import sys
import time as _time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
SRC = os.path.join(ROOT, "src")

# 需要真字形的脚本, 不许在电脑上跑
NEEDS_REAL_FONT = {"test_ui.py"}

_MASK = 0x3FFFFFFF


def _ticks_ms():
    return int(_time.monotonic() * 1000) & _MASK


def _ticks_us():
    return int(_time.monotonic() * 1000000) & _MASK


def _ticks_diff(a, b):
    d = (a - b) & _MASK
    if d >= 0x20000000:
        d -= 0x40000000
    return d


def _ticks_add(a, b):
    return (a + b) & _MASK


def _sleep_ms(ms):
    _time.sleep(ms / 1000.0)


def _sleep_us(us):
    _time.sleep(us / 1000000.0)


def patch_micropython():
    """把 MicroPython 独有的一批函数补到标准库 time 上。"""
    _time.ticks_ms = _ticks_ms
    _time.ticks_us = _ticks_us
    _time.ticks_diff = _ticks_diff
    _time.ticks_add = _ticks_add
    _time.sleep_ms = _sleep_ms
    _time.sleep_us = _sleep_us
    # mpstub 必须在 src 之前, 否则会 import 到真正的(不存在的)machine
    sys.path.insert(0, os.path.join(HERE, "mpstub"))
    sys.path.insert(0, SRC)


def main():
    if len(sys.argv) < 2:
        print("用法: python tools/host_tests/run_board_test.py "
              "tools/tests/test_state_machine.py")
        return 2

    path = os.path.abspath(sys.argv[1])
    name = os.path.basename(path)
    if name in NEEDS_REAL_FONT:
        print("%s 要看真实排版, 请在开发板上跑: "
              "python tools/deploy.py run %s" % (name, name[:-3]))
        return 2
    if not os.path.isfile(path):
        print("找不到脚本: %s" % path)
        return 2

    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    patch_micropython()

    with open(path, encoding="utf-8") as f:
        code = compile(f.read(), path, "exec")

    sys.argv = [name]
    exec(code, {"__name__": "__main__", "__file__": path})
    return 0


if __name__ == "__main__":
    sys.exit(main())
