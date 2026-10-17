# -*- coding: utf-8 -*-
"""
从系统中文 TrueType 字体生成 16x16 点阵字库 -> src/cfont.py
================================================================
OLED 自带的 framebuf 只有 8x8 的 ASCII 字库, 显示不了中文。
本脚本把界面用到的中文字符渲染成 16x16 的 1 位点阵, 打包成 cfont.py。

字库格式 (与 ui.py 约定一致):
    GLYPH_W = 16, GLYPH_H = 16
    GLYPHS = { 码点: b'\\x..' x 32 }
    每行 2 字节, 共 16 行; 每字节的 bit0 是最左边的像素 (LSB-first)

用法:
    python tools/gen_font.py            # 生成 src/cfont.py
    python tools/gen_font.py --preview  # 同时输出 font_preview.png 便于肉眼检查
"""

import os
import sys

# 界面里用到的所有汉字, 多一个少一个都不影响, 改这里再重新生成即可
CHARS = (
    "空气检测自"
    "传感器"
    "预热中"
    "未连接"
    "就绪"
    "异常"
    "超标"
    "全部"
    "温度"
    "湿度"
    "甲醛"
    "二氧化碳"
    "运行"
    "秒"
    "请稍候"
    "原始数据"
    "信息"
    "版本"
    "时间"
    "学习"
    "页"
)

# 候选字体, 找到第一个可用的就用
FONT_CANDIDATES = [
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\msyhbd.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
    r"C:\Windows\Fonts\Deng.ttf",
    r"C:\Windows\Fonts\simsun.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
]

W = 16
H = 16
PAD = 1                 # 四周留 1 像素边距, 避免相邻字粘连
BIG_SIZE = 120          # 先在大画布上渲染, 再缩放回 16x16, 避免笔画被裁掉
THRESHOLD = 85          # 降采样后的二值化门限

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "..", "src", "cfont.py")
PREVIEW = os.path.join(HERE, "font_preview.png")


def find_font():
    for path in FONT_CANDIDATES:
        if os.path.exists(path):
            return path
    raise SystemExit(
        "没有找到可用的中文字体, 请把 FONT_CANDIDATES 里换成你自己的字体路径")


def render_char(ch, font):
    """
    在大画布上渲染, 按实际墨迹范围等比缩放到 16x16 内。

    直接用一个 16px 的字体渲染会因为很多字的字身框比 em 框大而被裁边,
    所以这里放大渲染再缩小 —— 保证笔画完整、且各字视觉大小一致。
    """
    from PIL import Image, ImageDraw

    big = Image.new("L", (BIG_SIZE, BIG_SIZE), 0)
    ImageDraw.Draw(big).text((BIG_SIZE // 2, BIG_SIZE // 2), ch,
                             font=font, fill=255, anchor="mm")
    bbox = big.getbbox()
    if not bbox:
        return Image.new("1", (W, H), 0)

    crop = big.crop(bbox)
    limit = W - 2 * PAD
    sc = min(limit / crop.width, limit / crop.height)
    nw = max(1, int(round(crop.width * sc)))
    nh = max(1, int(round(crop.height * sc)))
    small = crop.resize((nw, nh), Image.LANCZOS)
    small = small.point(lambda v: 255 if v >= THRESHOLD else 0).convert("1")

    out = Image.new("1", (W, H), 0)
    out.paste(small, ((W - nw) // 2, (H - nh) // 2))
    return out


def render(chars, font_path):
    from PIL import Image, ImageDraw, ImageFont

    font = ImageFont.truetype(font_path, BIG_SIZE)
    glyphs = {}
    cols = 8
    rows = (len(chars) + cols - 1) // cols
    preview = Image.new("1", (W * cols + 2, H * rows + 4), 0)

    for idx, ch in enumerate(chars):
        img = render_char(ch, font)
        px = img.load()
        data = bytearray()
        for yy in range(H):
            byte = 0
            for xx in range(W):
                if px[xx, yy]:
                    byte |= 1 << (xx & 7)      # LSB-first: bit0 = 最左
                if (xx & 7) == 7:
                    data.append(byte)
                    byte = 0
            if W % 8:
                data.append(byte)
        assert len(data) == W * H // 8, (ch, len(data))
        glyphs[ord(ch)] = bytes(data)

        gap = 2
        preview.paste(img, (1 + (idx % cols) * W, 2 + (idx // cols) * H))

    pd = ImageDraw.Draw(preview)
    pd.rectangle([0, 0, preview.width - 1, preview.height - 1], outline=1)
    for i in range(1, cols):
        pd.line([i * W + 1, 0, i * W + 1, preview.height - 1], fill=1)
    for j in range(1, rows):
        pd.line([0, j * H + 2, preview.width - 1, j * H + 2], fill=1)
    return glyphs, preview


def write_module(glyphs, font_path):
    lines = [
        "# -*- coding: utf-8 -*-",
        "# 本文件由 tools/gen_font.py 自动生成, 请勿手工修改。",
        "# 字体来源: %s" % os.path.basename(font_path),
        "# 每个汉字 16x16, 32 字节, LSB-first (bit0 = 最左像素)",
        "# 具体用哪个 framebuf 格式由 ui.py 运行时探测(MONO_GLYPH)。",
        "",
        "GLYPH_W = %d" % W,
        "GLYPH_H = %d" % H,
        "",
        "GLYPHS = {",
    ]
    for cp in sorted(glyphs):
        body = "".join("\\x%02x" % b for b in glyphs[cp])
        lines.append("    0x%x:  # %s" % (cp, chr(cp)))
        lines.append("    b'%s'," % body)
    lines.append("}")
    lines.append("")
    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines))
    return len(glyphs)


def main():
    font_path = find_font()
    glyphs, preview = render(CHARS, font_path)
    n = write_module(glyphs, font_path)
    print("字体   : %s" % font_path)
    print("字符数 : %d -> %d 字节点阵" % (n, n * W * H // 8))
    print("输出   : %s" % os.path.normpath(OUT))
    if "--preview" in sys.argv:
        preview.save(PREVIEW)
        print("预览图 : %s" % os.path.normpath(PREVIEW))


if __name__ == "__main__":
    main()
