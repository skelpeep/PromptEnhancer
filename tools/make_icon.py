#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成应用图标（纯标准库：手写 PNG + ICO，不依赖 Pillow）。

产物：
    desktop/app.ico    给 exe 和系统托盘用
    desktop/app.png    给 Tk 窗口标题栏用

设计：圆角方形渐变底 + 白色四角星，4 倍超采样做抗锯齿。
"""

from __future__ import annotations

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from png_util import to_ico, to_png  # noqa: E402

SIZE = 256
SS = 4                      # 超采样倍率
OUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "desktop")

C_TOP = (79, 70, 229)       # #4F46E5 靛蓝
C_BOTTOM = (147, 51, 234)   # #9333EA 紫


def _lerp(a, b, t):
    return a + (b - a) * t


def _rounded_rect_cov(x, y, w, h, r):
    """点 (x, y) 到圆角矩形的归一化距离，<=0 表示在内部。"""
    dx = max(abs(x - w / 2) - (w / 2 - r), 0.0)
    dy = max(abs(y - h / 2) - (h / 2 - r), 0.0)
    return math.hypot(dx, dy) - r


def _star_cov(x, y, cx, cy, a, b, p=0.62):
    """四角星（凹边超椭圆）：(dx/a)^p + (dy/b)^p <= 1 为内部，返回 <=0 为内部。

    p 越小星臂越细（也越容易被抗锯齿糊成光晕），0.6 左右比较锐利。
    """
    dx = abs(x - cx) / a
    dy = abs(y - cy) / b
    return dx ** p + dy ** p - 1.0


def render() -> bytearray:
    W = SIZE * SS
    # 先在高分辨率画布上算 RGBA（这里只存覆盖度，再降采样）
    hi = bytearray(W * W * 4)

    pad = 10 * SS
    rw = W - 2 * pad
    radius = rw * 0.23
    cx, cy = W / 2, W / 2

    # 主星
    main = (cx - rw * 0.035, cy - rw * 0.045, rw * 0.235, rw * 0.235)
    # 右上小星
    small1 = (cx + rw * 0.255, cy - rw * 0.265, rw * 0.085, rw * 0.085)
    # 左下小星
    small2 = (cx - rw * 0.245, cy + rw * 0.275, rw * 0.058, rw * 0.058)
    # 每个星的白色强度（主星纯白，小星略暗做层次）
    weights = (1.0, 0.88, 0.78)

    for y in range(W):
        for x in range(W):
            i = (y * W + x) * 4
            # 圆角矩形覆盖（边界从 pad 开始，半径 radius）
            dx = max(abs(x + 0.5 - W / 2) - (W / 2 - pad - radius), 0.0)
            dy = max(abs(y + 0.5 - W / 2) - (W / 2 - pad - radius), 0.0)
            dist = math.hypot(dx, dy) - radius
            if dist > 0.5:
                continue
            base_a = 1.0 if dist <= -0.5 else (0.5 - dist)

            t = min(max((y - pad) / max(rw - 1, 1), 0.0), 1.0)
            r = int(_lerp(C_TOP[0], C_BOTTOM[0], t))
            g = int(_lerp(C_TOP[1], C_BOTTOM[1], t))
            b = int(_lerp(C_TOP[2], C_BOTTOM[2], t))

            # 白色星形叠加。
            # 这里故意用"二值 + 4x 超采样"而不是解析式抗锯齿：星臂很细，
            # 解析式覆盖度会把细臂糊成一片光晕，超采样出来的边缘反而干净锐利。
            white = 0.0
            for (sx, sy, sa, sb), wq in zip((main, small1, small2), weights):
                if _star_cov(x + 0.5, y + 0.5, sx, sy, sa, sb) <= 0.0:
                    white = max(white, wq)
            if white > 0:
                r = int(_lerp(r, 255, white))
                g = int(_lerp(g, 255, white))
                b = int(_lerp(b, 255, white))

            hi[i] = r
            hi[i + 1] = g
            hi[i + 2] = b
            hi[i + 3] = int(round(base_a * 255))

    # 盒式降采样
    out = bytearray(SIZE * SIZE * 4)
    area = SS * SS
    for y in range(SIZE):
        for x in range(SIZE):
            tr = tg = tb = ta = 0
            for dy in range(SS):
                row = (y * SS + dy) * W
                for dx in range(SS):
                    j = (row + x * SS + dx) * 4
                    a = hi[j + 3]
                    tr += hi[j] * a
                    tg += hi[j + 1] * a
                    tb += hi[j + 2] * a
                    ta += a
            k = (y * SIZE + x) * 4
            if ta == 0:
                out[k:k + 4] = b"\x00\x00\x00\x00"
            else:
                out[k] = min(255, tr // ta)
                out[k + 1] = min(255, tg // ta)
                out[k + 2] = min(255, tb // ta)
                out[k + 3] = ta // area
    return out


def main() -> int:
    os.makedirs(OUT_DIR, exist_ok=True)
    print("渲染中（4x 超采样，约需几秒）...")
    rgba = render()
    png = to_png(SIZE, SIZE, rgba)
    ico = to_ico(png, SIZE)

    png_path = os.path.join(OUT_DIR, "app.png")
    ico_path = os.path.join(OUT_DIR, "app.ico")
    with open(png_path, "wb") as f:
        f.write(png)
    with open(ico_path, "wb") as f:
        f.write(ico)
    print(f"已生成 {png_path} ({len(png)} bytes)")
    print(f"已生成 {ico_path} ({len(ico)} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
