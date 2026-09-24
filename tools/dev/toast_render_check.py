#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""离屏渲染浮层，直接生成 PNG 检查它到底画成什么样。

layered 窗口从屏幕 DC 截不到内容（DWM 合成层），所以这里走同一条绘制代码，
把结果画到内存 DC 再存 PNG —— 能确凿看到文字、配色、圆角/边框是否正确。

用法：python tools/dev/toast_render_check.py [输出.png] [dpi]
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))     # tools/dev
TOOLS = os.path.dirname(HERE)                         # tools
ROOT = os.path.dirname(TOOLS)                         # 项目根
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "desktop"))

from toast import Toast, THEMES, user32, gdi32   # noqa: E402
from png_util import to_png                      # noqa: E402

# ⚠️ 坑：ctypes.WinDLL() 每次调用返回的都是**新的 Python 对象**，argtypes 不跨模块
# 共享。所以这里必须复用 toast 模块里那个已经配好 argtypes 的 user32/gdi32，
# 自己再 WinDLL 一次的话，FillRect 的 argtypes 会是 None —— 句柄按 32 位处理，
# 直接 OverflowError，画出来的图一片空白。


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG),
                ("biPlanes", wt.WORD), ("biBitCount", wt.WORD),
                ("biCompression", wt.DWORD), ("biSizeImage", wt.DWORD),
                ("biXPelsPerMeter", wt.LONG), ("biYPelsPerMeter", wt.LONG),
                ("biClrUsed", wt.DWORD), ("biClrImportant", wt.DWORD)]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wt.DWORD * 3)]


# 这些补在 toast 已配置好的对象上，同一个对象，argtypes 继续有效
gdi32.CreateCompatibleDC.restype = wt.HDC
gdi32.CreateCompatibleDC.argtypes = [wt.HDC]
gdi32.CreateCompatibleBitmap.restype = wt.HBITMAP
gdi32.CreateCompatibleBitmap.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int]
gdi32.DeleteDC.argtypes = [wt.HDC]
gdi32.GetDIBits.argtypes = [wt.HDC, wt.HBITMAP, wt.UINT, wt.UINT, ctypes.c_void_p,
                            ctypes.POINTER(BITMAPINFO), wt.UINT]


def diag_minimal() -> None:
    """最小 GDI 填充测试：排除"环境里连 FillRect 都不工作"这种可能。"""
    hdc = user32.GetDC(None)
    mem = gdi32.CreateCompatibleDC(hdc)
    print(f"[diag] CreateCompatibleDC.restype={gdi32.CreateCompatibleDC.restype} "
          f"mem={mem!r}")
    print(f"[diag] FillRect.argtypes={user32.FillRect.argtypes}")
    bmp = gdi32.CreateCompatibleBitmap(hdc, 60, 40)
    old = gdi32.SelectObject(mem, bmp)
    brush = gdi32.CreateSolidBrush(0x0000FF)     # BGR -> 红
    rc = wt.RECT(0, 0, 60, 40)
    try:
        ok = user32.FillRect(mem, ctypes.byref(rc), brush)
    except Exception as e:  # noqa: BLE001
        print(f"[diag] FillRect 调用失败：{type(e).__name__}: {e}")
        ok = 0
    print(f"[diag] hdc={hdc} bmp={bmp} brush={brush} FillRect={ok} "
          f"lasterr={ctypes.get_last_error()}")
    bi = BITMAPINFO()
    bi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bi.bmiHeader.biWidth = 60
    bi.bmiHeader.biHeight = -40
    bi.bmiHeader.biPlanes = 1
    bi.bmiHeader.biBitCount = 32
    buf = ctypes.create_string_buffer(60 * 40 * 4)
    got = gdi32.GetDIBits(mem, bmp, 0, 40, buf, ctypes.byref(bi), 0)
    px = buf.raw[:4]
    print(f"[diag] GetDIBits={got} 首像素(BGRA)={list(px)}")
    gdi32.SelectObject(mem, old)
    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(mem)
    user32.ReleaseDC(None, hdc)


def main() -> int:
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "shot_toast_render.png")
    dpi = int(sys.argv[2]) if len(sys.argv) > 2 else 144
    diag_minimal()

    cases = [
        ("提示词增强器", "正在增强：帮我写个爬虫，抓取网页标题", "busy"),
        ("提示词增强器", "已替换选中内容（486 ms）", "ok"),
        ("提示词增强器", "没抓到选中的文本。请先选中要增强的文字，再按热键", "warn"),
    ]

    hdc_screen = user32.GetDC(None)
    try:
        panels = []
        total_w = total_h = 0
        for title, body, level in cases:
            t = Toast(dpi)
            t._make_fonts()
            t.title, t.body = title, body
            t.accent = THEMES.get(level, THEMES["info"])
            w, h, _, _ = t._measure()
            mem = gdi32.CreateCompatibleDC(hdc_screen)
            bmp = gdi32.CreateCompatibleBitmap(hdc_screen, w, h)
            old = gdi32.SelectObject(mem, bmp)
            try:
                rc = wt.RECT(0, 0, w, h)
                t._draw(mem, rc)
                bi = BITMAPINFO()
                bi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
                bi.bmiHeader.biWidth = w
                bi.bmiHeader.biHeight = -h
                bi.bmiHeader.biPlanes = 1
                bi.bmiHeader.biBitCount = 32
                buf = ctypes.create_string_buffer(w * h * 4)
                got = gdi32.GetDIBits(mem, bmp, 0, h, buf, ctypes.byref(bi), 0)
                raw = buf.raw
                px_left = list(raw[(h // 2) * w * 4:(h // 2) * w * 4 + 4])
                px_mid = list(raw[(h // 2) * w * 4 + (w // 2) * 4:
                                  (h // 2) * w * 4 + (w // 2) * 4 + 4])
                dark = sum(1 for i in range(0, len(raw), 4) if raw[i] < 200)
                print(f"  面板[{level}] {w}x{h} mem={mem} bmp={bmp} old={old} "
                      f"GetDIBits={got} 左中像素BGRA={px_left} 中像素={px_mid} 暗像素={dark}")
                panels.append((w, h, raw))
            finally:
                gdi32.SelectObject(mem, old)
                gdi32.DeleteObject(bmp)
                gdi32.DeleteDC(mem)
        total_w = max(w for w, _, _ in panels)
        total_h = sum(h for _, h, _ in panels) + (len(panels) - 1) * 12
    finally:
        user32.ReleaseDC(None, hdc_screen)

    # 拼成一张竖排图，背景用浅灰以便看清浮层边界
    rgba = bytearray()
    bg = (238, 238, 240, 255)
    rgba.extend(bg * (total_w * total_h))
    y0 = 0
    for w, h, raw in panels:
        for y in range(h):
            dst = ((y0 + y) * total_w) * 4
            src = y * w * 4
            row = bytearray(raw[src:src + w * 4])
            for i in range(0, len(row), 4):
                row[i], row[i + 2] = row[i + 2], row[i]
                row[i + 3] = 255        # GDI 出的位图 alpha 恒为 0，不补就成了全透明 PNG
            rgba[dst:dst + w * 4] = row
        y0 += h + 12

    with open(out, "wb") as f:
        f.write(to_png(total_w, total_h, bytes(rgba)))
    print(f"ok -> {out}  ({total_w}x{total_h})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
