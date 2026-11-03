# -*- coding: utf-8 -*-
"""PCB 风格「请勿触动」横幅生成器。

两种用法：
  1) 命令行：
     python make_banner.py [输出.png] [宽x高] [--title 第一行 第二行]
                           [--plate 文字] [--sub 第一行 第二行] [--status 文字]
                           [--no-supersample]
     例：python make_banner.py banner_2560x1440.png 2560x1440
  2) 作为库（banner.pyw 自动出图用）：
     import make_banner
     make_banner.render("out.png", 2560, 1440)
     make_banner.render("out.png", 2560, 1440, text={"title": ["占线中", "请稍候"]})

布局以 2560 宽为设计画布，高度按目标比例换算；16:9 / 16:10 两套手工校准值
之间线性插值，超出范围时按比例整体缩放。字体按回退链查找，缺字体不会崩。
"""
import argparse
import math
import os
import sys

from PIL import Image, ImageDraw, ImageFilter, ImageFont

DESIGN_W = 2560
DEFAULT_SIZE = (2560, 1600)
_S = 2                     # 超采样倍数，render(supersample=False) 可关闭

# ---------- 默认文案 ----------
DEFAULT_TEXT = {
    "status": "// SYSTEM STATUS : BUSY",
    "title": ["此计算机", "正在运行任务"],
    "plate": "请 勿 触 动",
    "sub": ["任务执行中，触碰键盘或鼠标可能导致数据丢失或系统异常",
            "感谢你的理解与配合 —— 让它安静地跑完吧。"],
    "chip": ["TASK·CORE", "U1  ARM-M4  168MHz", "LOT 2026·11  RUNNING"],
}

# ---------- 配色 ----------
BG_TOP = (6, 16, 13)
BG_BOT = (9, 24, 19)
GRID = (20, 44, 34)
COPPER = (214, 146, 66)
COPPER_DIM = (88, 62, 30)
SILK = (236, 242, 236)
SILK_DIM = (120, 150, 136)
CYAN = (62, 232, 200)
AMBER = (246, 168, 38)
INK = (10, 18, 15)

# ---------- 布局：16:9(1440) 与 16:10(1600) 两套基准，均以设计宽 2560 计 ----------
LAYOUT_WIDE = dict(
    status=180, t1=410, t2=640, plate=(705, 855), plate_font=96, tri=92,
    sub1=935, sub2=990, sub_trace=(1010, 970),
    scope=(1060, 1340), term=(1080, 1340), wave=(30, 95, 150, 28), term_lines=(80, 40, 232),
    CY=500, LY=225, JY=975, jlabel=1022, y1=(850, 910), top_end=100,
)
LAYOUT_TALL = dict(
    status=212, t1=470, t2=720, plate=(800, 960), plate_font=104, tri=100,
    sub1=1040, sub2=1100, sub_trace=(1110, 1070),
    scope=(1180, 1480), term=(1200, 1480), wave=(40, 120, 190, 34), term_lines=(90, 44, 248),
    CY=560, LY=250, JY=1090, jlabel=1140, y1=(880, 940), top_end=150,
)
LAYOUT_WIDE_H, LAYOUT_TALL_H = 1440, 1600

# ---------- 字体回退链 ----------
FONT_DIRS = [r"C:\Windows\Fonts",
             os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\Windows\Fonts"),
             "/usr/share/fonts", "/usr/local/share/fonts"]
FONT_FALLBACK = {
    "bold": ["HarmonyOS_Sans_SC_Bold.ttf", "msyhbd.ttc", "simhei.ttf",
             "SourceHanSansSC-Bold.otf", "NotoSansSC-Bold.otf"],
    "regular": ["HarmonyOS_Sans_SC_Regular.ttf", "msyh.ttc", "simsun.ttc",
                "SourceHanSansSC-Regular.otf", "NotoSansSC-Regular.otf"],
    "mono": ["CascadiaMono.ttf", "CascadiaCode.ttf", "consola.ttf", "msyh.ttc"],
}
_FONT_CACHE = {}
_FONT_PATHS = {}


def _find_font_path(kind):
    if kind in _FONT_PATHS:
        return _FONT_PATHS[kind]
    path = None
    for name in FONT_FALLBACK.get(kind, []):
        for d in FONT_DIRS:
            p = os.path.join(d, name)
            if os.path.exists(p):
                path = p
                break
        if path:
            break
    _FONT_PATHS[kind] = path
    return path


def font(kind, design_size):
    """按设计尺寸取字体（内部乘超采样倍数），缺字体时退回 PIL 默认字体。"""
    key = (kind, int(design_size))
    cached = _FONT_CACHE.get(key)
    if cached is not None:
        return cached
    px = max(8, int(round(design_size * _S)))
    path = _find_font_path(kind)
    f = None
    if path:
        try:
            f = ImageFont.truetype(path, px)
        except Exception:
            f = None
    if f is None:
        try:
            f = ImageFont.load_default(size=px)
        except TypeError:
            f = ImageFont.load_default()
    _FONT_CACHE[key] = f
    return f


# ---------- 布局计算 ----------
def _lerp(a, b, t):
    return a + (b - a) * t


def _lerp_val(a, b, t):
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return _lerp(a, b, t)
    if isinstance(a, tuple) and isinstance(b, tuple):
        return tuple(_lerp(x, y, t) for x, y in zip(a, b))
    return a


def _scale_val(v, k):
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return max(1, int(round(v * k)))
    if isinstance(v, tuple):
        return tuple(_scale_val(x, k) for x in v)
    return v


def layout_for(width, height):
    """按目标分辨率算设计布局。返回 (L, design_w, design_h, k)。

    16:9 及更宽的屏幕：设计高度固定 1440，设计宽度随比例加宽（右侧电路/网格/
    板框按宽度自适应延伸），内容不做纵向压缩。
    比 16:9 更窄的屏幕（16:10、3:2、4:3…）：设计宽度固定 2560，高度按比例增加，
    在 16:9 / 16:10 两套校准值之间插值，超出范围整体等比缩放（k 为缩放系数）。
    """
    ratio = float(width) / float(height)
    if ratio >= 16.0 / 9.0:
        return dict(LAYOUT_WIDE), int(round(LAYOUT_WIDE_H * ratio)), LAYOUT_WIDE_H, 1.0
    design_w = DESIGN_W
    design_h = max(1, int(round(DESIGN_W / ratio)))
    if design_h >= LAYOUT_TALL_H:
        t = 1.0
    elif design_h <= LAYOUT_WIDE_H:
        t = 0.0
    else:
        t = (design_h - LAYOUT_WIDE_H) / float(LAYOUT_TALL_H - LAYOUT_WIDE_H)
    base = {key: _lerp_val(LAYOUT_WIDE[key], LAYOUT_TALL[key], t) for key in LAYOUT_WIDE}
    base_h = _lerp(LAYOUT_WIDE_H, LAYOUT_TALL_H, t)
    k = design_h / float(base_h)
    L = {key: _scale_val(val, k) for key, val in base.items()}
    # 字号不跟着纵向拉伸放大，否则窄屏（4:3 等）下文字会横向溢出压到右侧电路
    for key in ("plate_font", "tri"):
        L[key] = int(round(base[key]))
    return L, design_w, design_h, k


def _merge_text(text):
    merged = {k: (list(v) if isinstance(v, list) else v) for k, v in DEFAULT_TEXT.items()}
    if isinstance(text, dict):
        for k, v in text.items():
            if k not in merged:
                continue
            if isinstance(merged[k], list) and isinstance(v, (list, tuple)):
                merged[k] = [str(x) for x in v][:len(merged[k])]
                while len(merged[k]) < len(DEFAULT_TEXT[k]):
                    merged[k].append(DEFAULT_TEXT[k][len(merged[k])])
            else:
                merged[k] = str(v)
    return merged


# ---------- 主绘制 ----------
def render(out_path, width, height, text=None, supersample=True):
    """生成一张 width×height 的横幅并保存到 out_path；返回 out_path。"""
    global _S
    if not width or not height or width <= 0 or height <= 0:
        raise ValueError("invalid size %r x %r" % (width, height))
    _S = 2 if supersample else 1
    _FONT_CACHE.clear()
    txt = _merge_text(text)
    L, design_w, design_h, K = layout_for(width, height)

    def s(v):
        return int(round(v * _S))

    def P(*pts):
        return [(s(x), s(y)) for x, y in pts]

    def fs(v):
        """字号只允许因横向空间不足而缩小，不随纵向拉伸放大。"""
        return v * min(K, 1.0)

    W, H = design_w, design_h
    CX, CY, HB = 1960, L["CY"], 230
    NP, PITCH, PL = 12, 32, 40

    def F_BOLD(sz):
        return font("bold", sz)

    def F_REG(sz):
        return font("regular", sz)

    def F_MONO(sz):
        return font("mono", sz)

    base = Image.new("RGB", (s(W), s(H)), BG_TOP)
    bd = ImageDraw.Draw(base)
    for y in range(s(H)):
        t = y / float(s(H))
        bd.line([(0, y), (s(W), y)], fill=tuple(int(a + (b - a) * t) for a, b in zip(BG_TOP, BG_BOT)))

    # 芯片处柔光
    glow_bg = Image.new("L", (s(W), s(H)), 0)
    gd = ImageDraw.Draw(glow_bg)
    gd.ellipse(P((1500, CY - 440), (2420, CY + 440)), fill=70)
    glow_bg = glow_bg.filter(ImageFilter.GaussianBlur(s(220)))
    base = Image.composite(Image.new("RGB", base.size, (20, 70, 58)), base, glow_bg)

    img = base.convert("RGBA")
    copper = Image.new("RGBA", img.size, (0, 0, 0, 0))
    cd = ImageDraw.Draw(copper)
    over = Image.new("RGBA", img.size, (0, 0, 0, 0))
    od = ImageDraw.Draw(over)

    # 点阵网格
    gdraw = ImageDraw.Draw(img)
    for gx in range(60, W - 40, 40):
        for gy in range(60, H - 40, 40):
            r = 1.6
            gdraw.ellipse(P((gx - r, gy - r), (gx + r, gy + r)), fill=GRID)

    # ---------- 走线工具 ----------
    def trace(pts, w=6, color=COPPER, layer=cd):
        pp = P(*pts)
        layer.line(pp, fill=color, width=s(w), joint="curve")
        for x, y in pp:
            layer.ellipse((x - s(w) / 2, y - s(w) / 2, x + s(w) / 2, y + s(w) / 2), fill=color)

    def via(x, y, r=11, color=COPPER, layer=cd):
        layer.ellipse(P((x - r, y - r), (x + r, y + r)), fill=color)
        layer.ellipse(P((x - r * 0.45, y - r * 0.45), (x + r * 0.45, y + r * 0.45)), fill=INK)

    def fan(p0, d, a, lat, total):
        dx, dy = d
        px, py = -dy, dx
        x0, y0 = p0
        p1 = (x0 + dx * a, y0 + dy * a)
        p2 = (p1[0] + dx * abs(lat) + px * lat, p1[1] + dy * abs(lat) + py * lat)
        rest = total - a - abs(lat)
        p3 = (p2[0] + dx * rest, p2[1] + dy * rest)
        return [p0, p1, p2, p3]

    # ---------- 板框与安装孔 ----------
    od.rounded_rectangle(P((36, 36), (W - 36, H - 36)), radius=s(40), outline=(40, 78, 62), width=s(3))
    for hx, hy in [(96, 96), (W - 96, 96), (96, H - 96), (W - 96, H - 96)]:
        cd.ellipse(P((hx - 30, hy - 30), (hx + 30, hy + 30)), fill=COPPER)
        cd.ellipse(P((hx - 17, hy - 17), (hx + 17, hy + 17)), fill=(3, 8, 6))
        for k in range(8):
            a = k * math.pi / 4
            via(hx + 44 * math.cos(a), hy + 44 * math.sin(a), r=5)

    # ---------- 芯片 U1 ----------
    pins = {"top": [], "bottom": [], "left": [], "right": []}
    for i in range(NP):
        o = (i - (NP - 1) / 2) * PITCH
        pins["top"].append((CX + o, CY - HB - PL))
        pins["bottom"].append((CX + o, CY + HB + PL))
        pins["left"].append((CX - HB - PL, CY + o))
        pins["right"].append((CX + HB + PL, CY + o))

    for (x, y) in pins["top"]:
        lat = (x - CX) * 0.6
        pts = fan((x, y + 6), (0, -1), 30, lat, (y + 6) - L["top_end"])
        trace(pts, w=7)
        via(*pts[-1])
    for (x, y) in pins["right"]:
        lat = (y - CY) * 0.7
        pts = fan((x - 6, y), (1, 0), 30, lat, W - 150 - (x - 6))
        trace(pts, w=7)
        via(*pts[-1])
    J_PITCH, JY = 44, L["JY"]
    for i, (x, y) in enumerate(pins["bottom"]):
        tx = CX + (i - (NP - 1) / 2) * J_PITCH
        lat = -(tx - x)
        pts = fan((x, y - 6), (0, 1), 40, lat, JY - (y - 6))
        trace(pts, w=7)
    for i in range(NP):
        tx = CX + (i - (NP - 1) / 2) * J_PITCH
        if i == 0:
            cd.rectangle(P((tx - 16, JY - 16), (tx + 16, JY + 16)), fill=COPPER)
        else:
            cd.ellipse(P((tx - 16, JY - 16), (tx + 16, JY + 16)), fill=COPPER)
        cd.ellipse(P((tx - 7, JY - 7), (tx + 7, JY + 7)), fill=INK)
    for i, (x, y) in enumerate(pins["left"]):
        end = x - 50 - (i % 3) * 34
        trace([(x + 6, y), (end, y)], w=7)
        via(end, y, r=10)

    # 装饰走线
    trace([(560, H - 60), (1500, H - 60), (1540, H - 100), (1540, H - 110)], w=6, color=COPPER_DIM)
    trace([(560, H - 40), (1520, H - 40), (1580, H - 100), (1580, H - 300)], w=6, color=COPPER_DIM)
    via(1580, H - 300, color=COPPER_DIM)
    via(1540, H - 110, color=COPPER_DIM)
    trace([(70, 260), (70, H - 300)], w=6, color=COPPER_DIM)
    trace([(150, 66), (1300, 66), (1330, 96), (1500, 96)], w=6, color=COPPER_DIM)
    via(1500, 96, color=COPPER_DIM)
    ya, yb = L["sub_trace"]
    trace([(1100, ya), (1450, ya), (1490, yb), (1560, yb)], w=6, color=COPPER_DIM)

    # 芯片引脚与本体
    for side, lst in pins.items():
        for (x, y) in lst:
            if side in ("top", "bottom"):
                sgn = 1 if side == "top" else -1
                od.rectangle(P((x - 7, y), (x + 7, y + sgn * PL)) if sgn > 0 else P((x - 7, y - PL), (x + 7, y)),
                             fill=(196, 202, 204))
            else:
                sgn = 1 if side == "left" else -1
                od.rectangle(P((x, y - 7), (x + PL, y + 7)) if sgn > 0 else P((x - PL, y - 7), (x, y + 7)),
                             fill=(196, 202, 204))
    od.rounded_rectangle(P((CX - HB - 4, CY - HB - 4), (CX + HB + 4, CY + HB + 4)), radius=s(14), fill=(4, 8, 7))
    od.rounded_rectangle(P((CX - HB, CY - HB), (CX + HB, CY + HB)), radius=s(12), fill=(22, 27, 29),
                         outline=(52, 62, 66), width=s(3))
    od.ellipse(P((CX - HB + 30, CY - HB + 30), (CX - HB + 56, CY - HB + 56)), fill=(40, 48, 52))
    od.text((s(CX), s(CY - 70)), txt["chip"][0], font=F_MONO(fs(58)), fill=(190, 198, 200), anchor="mm")
    od.text((s(CX), s(CY + 6)), txt["chip"][1], font=F_MONO(fs(28)), fill=(120, 130, 134), anchor="mm")
    od.text((s(CX), s(CY + 52)), txt["chip"][2], font=F_MONO(fs(28)), fill=(120, 130, 134), anchor="mm")
    od.text((s(CX + HB + 70), s(CY - HB - 60)), "U1", font=F_MONO(fs(34)), fill=SILK_DIM, anchor="mm")

    # LED / 电容 / 晶振 / 排针标注
    led = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ld = ImageDraw.Draw(led)
    LX, LY = 1600, L["LY"]
    ld.ellipse(P((LX - 70, LY - 70), (LX + 70, LY + 70)), fill=(60, 255, 170, 120))
    led = led.filter(ImageFilter.GaussianBlur(s(40)))
    img = Image.alpha_composite(img, led)
    od.rounded_rectangle(P((LX - 26, LY - 16), (LX + 26, LY + 16)), radius=s(5), fill=(150, 255, 210))
    od.text((s(LX), s(LY - 50)), "D1 RUN", font=F_MONO(fs(26)), fill=SILK_DIM, anchor="mm")
    for k, (x, y) in enumerate([(1585, CY - 90), (1585, CY), (1585, CY + 90)]):
        od.rectangle(P((x - 22, y - 12), (x - 8, y + 12)), fill=(190, 196, 196))
        od.rectangle(P((x - 8, y - 12), (x + 8, y + 12)), fill=(160, 120, 70))
        od.rectangle(P((x + 8, y - 12), (x + 22, y + 12)), fill=(190, 196, 196))
        od.text((s(x - 50), s(y)), "C%d" % (k + 1), font=F_MONO(fs(22)), fill=SILK_DIM, anchor="mm")
    y1a, y1b = L["y1"]
    od.rounded_rectangle(P((2300, y1a), (2420, y1b)), radius=s(30), fill=(176, 184, 188),
                         outline=(90, 98, 100), width=s(3))
    od.text((s(2360), s((y1a + y1b) / 2)), "Y1", font=F_MONO(fs(26)), fill=(40, 46, 48), anchor="mm")
    od.text((s(CX), s(L["jlabel"])), "J1  DEBUG", font=F_MONO(fs(26)), fill=SILK_DIM, anchor="mm")

    # 铜层辉光
    cglow = copper.filter(ImageFilter.GaussianBlur(s(10)))
    img = Image.alpha_composite(img, cglow)
    img = Image.alpha_composite(img, copper)

    # ---------- 左侧文字 ----------
    tx0 = 150
    od.text((s(tx0), s(L["status"])), txt["status"], font=F_MONO(fs(40)), fill=CYAN, anchor="ls")
    title_layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    tl = ImageDraw.Draw(title_layer)
    tl.text((s(tx0 - 8), s(L["t1"])), txt["title"][0], font=F_BOLD(fs(200)), fill=SILK, anchor="ls")
    tl.text((s(tx0 - 8), s(L["t2"])), txt["title"][1], font=F_BOLD(fs(200)), fill=SILK, anchor="ls")
    tglow = title_layer.filter(ImageFilter.GaussianBlur(s(16)))
    r, g, b, a = tglow.split()
    tglow = Image.merge("RGBA", (r, g, b, a.point(lambda v: v * 0.35)))

    # 「请勿触动」警示牌
    PX0, PX1 = 150, 1290
    PY0, PY1 = L["plate"]
    plate_poly = P((PX0 + 40, PY0), (PX1 - 40, PY0), (PX1, PY0 + 40), (PX1, PY1 - 40), (PX1 - 40, PY1),
                   (PX0 + 40, PY1), (PX0, PY1 - 40), (PX0, PY0 + 40))
    plate = Image.new("RGBA", img.size, (0, 0, 0, 0))
    pd = ImageDraw.Draw(plate)
    pd.polygon(plate_poly, fill=AMBER)
    for k in range(0, 120, 24):
        pd.polygon(P((PX0 + 70 + k, PY0), (PX0 + 82 + k, PY0), (PX0 + 22 + k, PY1), (PX0 + 10 + k, PY1)),
                   fill=(214, 140, 20))
        pd.polygon(P((PX1 - 70 - k, PY1), (PX1 - 82 - k, PY1), (PX1 - 22 - k, PY0), (PX1 - 10 - k, PY0)),
                   fill=(214, 140, 20))
    pmask = Image.new("L", img.size, 0)
    ImageDraw.Draw(pmask).polygon(plate_poly, fill=255)
    over.paste(plate, (0, 0), pmask)

    def warn_tri(cx, cy, size, fill, mark):
        h = size * math.sqrt(3) / 2
        od.polygon(P((cx, cy - h * 0.62), (cx + size / 2, cy + h * 0.38), (cx - size / 2, cy + h * 0.38)),
                   fill=fill)
        od.rounded_rectangle(P((cx - 6, cy - h * 0.28), (cx + 6, cy + h * 0.08)), radius=s(5), fill=mark)
        od.ellipse(P((cx - 7, cy + h * 0.15), (cx + 7, cy + h * 0.29)), fill=mark)

    warn_tri(PX0 + 210, (PY0 + PY1) / 2, L["tri"], INK, AMBER)
    warn_tri(PX1 - 210, (PY0 + PY1) / 2, L["tri"], INK, AMBER)
    od.text((s((PX0 + PX1) / 2), s((PY0 + PY1) / 2 + 4)), txt["plate"],
            font=F_BOLD(L["plate_font"]), fill=INK, anchor="mm")

    od.text((s(tx0), s(L["sub1"])), txt["sub"][0], font=F_REG(fs(42)), fill=(196, 210, 202), anchor="ls")
    od.text((s(tx0), s(L["sub2"])), txt["sub"][1], font=F_REG(fs(42)), fill=SILK_DIM, anchor="ls")

    # ---------- 示波器面板 ----------
    SX0, SX1 = 150, 1500
    SY0, SY1 = L["scope"]
    od.rounded_rectangle(P((SX0, SY0), (SX1, SY1)), radius=s(14), fill=(4, 11, 9, 235),
                         outline=(44, 84, 68), width=s(3))
    GX0, GY0, GX1, GY1 = SX0 + 24, SY0 + 56, SX1 - 24, SY1 - 24
    for k in range(11):
        x = GX0 + (GX1 - GX0) * k / 10
        for y in range(GY0, GY1, 10):
            od.line(P((x, y), (x, y + 4)), fill=(30, 62, 50) if k != 5 else (50, 96, 78), width=s(2))
    for k in range(5):
        y = GY0 + (GY1 - GY0) * k / 4
        for x in range(GX0, GX1, 10):
            od.line(P((x, y), (x + 4, y)), fill=(30, 62, 50) if k != 2 else (50, 96, 78), width=s(2))
    od.text((s(SX0 + 24), s(SY0 + 36)), "CH1  3.3V/div    CH2  1.0V/div    10ms/div    TRIG ↑",
            font=F_MONO(fs(26)), fill=SILK_DIM, anchor="ls")
    od.ellipse(P((SX1 - 140, SY0 + 18), (SX1 - 122, SY0 + 36)), fill=(70, 255, 150))
    od.text((s(SX1 - 112), s(SY0 + 36)), "RUN", font=F_MONO(fs(26)), fill=(70, 255, 150), anchor="ls")

    wave = Image.new("RGBA", img.size, (0, 0, 0, 0))
    wd = ImageDraw.Draw(wave)
    w_hi, w_lo, w_mid, w_amp = L["wave"]
    hi, lo = GY0 + w_hi, GY0 + w_lo
    pts = []
    x = GX0
    period = 108
    duty = [0.5, 0.3, 0.7, 0.5, 0.25, 0.6, 0.45, 0.75, 0.5, 0.35, 0.65, 0.5, 0.5]
    k = 0
    pts.append((x, lo))
    while x < GX1:
        d = duty[k % len(duty)]
        x1 = min(x + period * d, GX1)
        pts += [(x, lo), (x, hi), (x1, hi)]
        x2 = min(x + period, GX1)
        pts += [(x1, hi), (x1, lo), (x2, lo)]
        x = x2
        k += 1
    wd.line(P(*pts), fill=CYAN + (255,), width=s(4), joint="curve")
    pts2 = []
    for i in range(0, int(GX1 - GX0) + 1, 3):
        t = i / float(GX1 - GX0)
        y = GY0 + w_mid + w_amp * math.sin(t * 2 * math.pi * 7) * (0.55 + 0.45 * math.cos(t * 2 * math.pi * 1.3))
        pts2.append((GX0 + i, y))
    wd.line(P(*pts2), fill=AMBER + (220,), width=s(4), joint="curve")
    wglow = wave.filter(ImageFilter.GaussianBlur(s(8)))

    # ---------- 终端面板 ----------
    TX0, TX1 = 1620, 2410
    TY0, TY1 = L["term"]
    LN0, LNS, BYO = L["term_lines"]
    od.rounded_rectangle(P((TX0, TY0), (TX1, TY1)), radius=s(14), fill=(4, 11, 9, 235),
                         outline=(44, 84, 68), width=s(3))
    for k, c in enumerate([(255, 95, 86), (255, 189, 46), (39, 201, 63)]):
        od.ellipse(P((TX0 + 24 + k * 30, TY0 + 18), (TX0 + 42 + k * 30, TY0 + 36)), fill=c)
    od.text((s(TX1 - 24), s(TY0 + 34)), "tty0", font=F_MONO(fs(22)), fill=SILK_DIM, anchor="rs")
    lines = [
        [("$ ", CYAN), ("./run_task --keep-alive", SILK)],
        [("[ OK ] ", (70, 255, 150)), ("scheduler online", (196, 210, 202))],
        [("[ OK ] ", (70, 255, 150)), ("data stream locked", (196, 210, 202))],
        [("[ .. ] ", AMBER), ("processing", (196, 210, 202))],
    ]
    fm = F_MONO(fs(28))
    for i, segs in enumerate(lines):
        x = TX0 + 28
        y = TY0 + LN0 + i * LNS
        for string, col in segs:
            od.text((s(x), s(y)), string, font=fm, fill=col, anchor="ls")
            x += od.textlength(string, font=fm) / _S
    BX0, BY, BX1 = TX0 + 28, TY0 + BYO, TX1 - 140
    od.rounded_rectangle(P((BX0, BY - 18), (BX1, BY + 4)), radius=s(4), outline=(60, 110, 92), width=s(2))
    fill_to = BX0 + (BX1 - BX0) * 0.75
    for x in range(int(BX0 + 4), int(fill_to), 14):
        od.rectangle(P((x, BY - 14), (x + 9, BY)), fill=CYAN)
    od.text((s(TX1 - 28), s(BY + 2)), "75%", font=F_MONO(fs(28)), fill=CYAN, anchor="rs")
    cur_x = TX0 + 28 + od.textlength("[ .. ] processing", font=fm) / _S
    cur_y = TY0 + LN0 + 3 * LNS
    od.rectangle(P((cur_x + 10, cur_y - 26), (cur_x + 26, cur_y + 2)), fill=SILK)

    # ---------- 丝印角标 ----------
    od.text((s(W - 150), s(H - 60)), "HANDS OFF · LET IT RUN   REV 2.0", font=F_MONO(fs(24)),
            fill=SILK_DIM, anchor="rs")
    od.text((s(150), s(H - 52)), "BANNER-PCB  %d×%d" % (width, height), font=F_MONO(fs(24)),
            fill=(70, 100, 88), anchor="ls")

    # ---------- 合成 ----------
    img = Image.alpha_composite(img, over)
    img = Image.alpha_composite(img, wglow)
    img = Image.alpha_composite(img, wave)
    img = Image.alpha_composite(img, tglow)
    img = Image.alpha_composite(img, title_layer)

    img = img.convert("RGB").resize((width, height), Image.LANCZOS)
    img.save(out_path, optimize=True)
    return out_path


# ---------- 命令行 ----------
def _parse_size(value):
    try:
        w, h = value.lower().split("x")
        return int(w), int(h)
    except Exception:
        raise argparse.ArgumentTypeError("尺寸格式应为 宽x高，例如 2560x1440")


def main(argv=None):
    ap = argparse.ArgumentParser(description="生成 PCB 风格「请勿触动」横幅")
    ap.add_argument("out", nargs="?", default="banner_new.png", help="输出文件")
    ap.add_argument("size", nargs="?", default="%dx%d" % DEFAULT_SIZE, type=_parse_size,
                    help="宽x高，例如 2560x1440")
    ap.add_argument("--title", nargs=2, metavar=("L1", "L2"), help="大标题两行")
    ap.add_argument("--plate", help="警示牌文字")
    ap.add_argument("--sub", nargs=2, metavar=("L1", "L2"), help="说明两行")
    ap.add_argument("--status", help="状态行文字")
    ap.add_argument("--no-supersample", action="store_true", help="关闭 2 倍超采样（更快）")
    args = ap.parse_args(argv)

    text = {}
    if args.title:
        text["title"] = list(args.title)
    if args.plate:
        text["plate"] = args.plate
    if args.sub:
        text["sub"] = list(args.sub)
    if args.status:
        text["status"] = args.status
    w, h = args.size
    path = render(args.out, w, h, text=text, supersample=not args.no_supersample)
    print("saved", path, (w, h))
    return 0


if __name__ == "__main__":
    sys.exit(main())
