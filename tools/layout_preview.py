# -*- coding: utf-8 -*-
"""
界面排版预览 (在电脑上运行)
================================================================
会在开发板上跑 tools/tests/test_ui.py(不需要真的接屏幕, 用的是假屏幕),
把返回的 ASCII 画面渲染成一张 PNG, 方便一眼看出有没有叠字 / 出界 / 缺字。

    python tools/layout_preview.py
    python tools/layout_preview.py --port COM5

输出: tools/ui_layout.png
需要 Pillow: pip install Pillow
"""

import argparse
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "ui_layout.png")

SCALE = 3
PAD = 8
COLS = 4


def run_on_board(port):
    cmd = [sys.executable, "-m", "mpremote", "connect", port, "run",
           os.path.join(HERE, "tests", "test_ui.py")]
    r = subprocess.run(cmd, capture_output=True)
    if r.returncode != 0 and not r.stdout:
        sys.stderr.write(r.stderr.decode("utf-8", "replace"))
        raise SystemExit("在开发板上运行 test_ui.py 失败, 请检查串口和固件")
    return r.stdout.decode("utf-8", "replace").splitlines()


def parse(lines):
    """把 '+'+'---' 包起来的方块解析成 (标题, 像素) 列表。"""
    screens = []
    title = ""
    px = None
    for line in lines:
        s = line.rstrip()
        if s.startswith("+" + "-"):
            if px is None:
                px = []
            else:
                screens.append((title, px))
                px = None
            continue
        if px is not None and s.startswith("|"):
            px.append([c == "#" for c in s[1:-1]])
            continue
        if px is None and s and not s.startswith("=") and "|" not in s:
            title = s
    return screens


def render(screens, path):
    from PIL import Image, ImageDraw

    if not screens:
        raise SystemExit("没有解析到任何画面")
    w = max(len(r[0]) for _, r in screens)
    h = max(len(r) for _, r in screens)
    cols = min(COLS, len(screens))
    rows = (len(screens) + cols - 1) // cols
    cw = w * SCALE + PAD * 2
    ch = h * SCALE + PAD * 2 + 14

    img = Image.new("RGB", (cw * cols, ch * rows), (26, 28, 33))
    d = ImageDraw.Draw(img)
    for i, (title, px) in enumerate(screens):
        cx = (i % cols) * cw
        cy = (i // cols) * ch
        d.text((cx + PAD, cy + 3), "%d. %s" % (i + 1, title[:58]),
               fill=(255, 210, 110))
        ox, oy = cx + PAD, cy + PAD + 13
        d.rectangle([ox - 1, oy - 1, ox + w * SCALE, oy + h * SCALE],
                    outline=(70, 74, 84))
        for y, row in enumerate(px):
            for x, on in enumerate(row):
                if on:
                    d.rectangle([ox + x * SCALE, oy + y * SCALE,
                                 ox + x * SCALE + SCALE - 1,
                                 oy + y * SCALE + SCALE - 1],
                                fill=(150, 225, 255))
    img.save(path)
    return img.size


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default=os.environ.get("MP_PORT", "COM9"))
    args = ap.parse_args()

    lines = run_on_board(args.port)
    screens = parse(lines)
    size = render(screens, OUT)
    print("解析到 %d 个画面" % len(screens))
    for i, (t, _) in enumerate(screens):
        print("  %d. %s" % (i + 1, t))
    print("已生成:", os.path.normpath(OUT), size)


if __name__ == "__main__":
    main()
