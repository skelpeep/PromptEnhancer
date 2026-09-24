#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按 app 的真实条件复现浮层，把测量值/窗口尺寸/渲染结果都打出来。

和 toast_probe.py 的区别：这里**先**声明 DPI 感知（跟 app 一样，在创建任何窗口
之前调 SetProcessDpiAwareness(1)），用来定位"浮层底部出现一条未绘制黑边"的原因。

用法：python tools/dev/toast_dpi_check.py
产物：shot_toast_dpi.png
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))     # tools/dev
TOOLS = os.path.dirname(HERE)                         # tools
ROOT = os.path.dirname(TOOLS)                         # 项目根
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "desktop"))
sys.path.insert(0, HERE)
sys.path.insert(0, TOOLS)

# 跟 desktop/main.py 的 enable_dpi_awareness() 完全一致
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
    print("SetProcessDpiAwareness(1) 成功（进程 DPI 感知）")
except Exception as e:  # noqa: BLE001
    print(f"SetProcessDpiAwareness(1) 失败：{e}")

from toast import Toast as _T                    # noqa: E402
from toast import gdi32 as _gdi32                # noqa: E402
from screenshot_corner import grab_hwnd_rgba     # noqa: E402

user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.GetDC.restype = wt.HDC
user32.GetClientRect.argtypes = [ctypes.c_void_p, ctypes.POINTER(wt.RECT)]
user32.GetWindowRect.argtypes = [ctypes.c_void_p, ctypes.POINTER(wt.RECT)]
_gdi32.GetDeviceCaps.argtypes = [wt.HDC, ctypes.c_int]


def main() -> int:
    box: dict = {}
    ready = threading.Event()

    def worker():
        msg = wt.MSG()
        t = _T(144)
        t._ensure_window()
        box["toast"] = t
        box["dpi_of_dc"] = _gdi32.GetDeviceCaps(user32.GetDC(None), 88)
        ready.set()
        t.show("提示词增强器", "正在增强：帮我写个爬虫，抓取网页标题", "busy", 20000)
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    threading.Thread(target=worker, daemon=True).start()
    ready.wait(5)
    time.sleep(1.0)

    t = box["toast"]
    hwnd = t.hwnd
    cr, wr = wt.RECT(), wt.RECT()
    user32.GetClientRect(ctypes.c_void_p(hwnd), ctypes.byref(cr))
    user32.GetWindowRect(ctypes.c_void_p(hwnd), ctypes.byref(wr))
    print(f"GetDC(None) 的 LOGPIXELSX = {box['dpi_of_dc']}  (Toast.scale={t.scale})")
    print(f"窗口自身 _measure() = {t._measure()}")
    print(f"client rect = {(cr.left, cr.top, cr.right, cr.bottom)} "
          f"({cr.right - cr.left}x{cr.bottom - cr.top})")
    print(f"window rect = {(wr.left, wr.top, wr.right, wr.bottom)} "
          f"({wr.right - wr.left}x{wr.bottom - wr.top})")

    got = grab_hwnd_rgba(hwnd)
    if got is None:
        print("抓不到像素")
        return 1
    w, h, px = got
    print(f"抓到的位图 = {w}x{h}")
    # 逐行统计"这一行有多少非白像素"，用来定位底部那条黑边
    print("每 10 行的内容分布（行号: 非白像素数 / 纯黑像素数）：")
    for y in range(0, h, 10):
        row = y * w * 4
        nonwhite = black = 0
        for x in range(w):
            i = row + x * 4
            r, g, b = px[i], px[i + 1], px[i + 2]
            if not (r > 235 and g > 235 and b > 235):
                nonwhite += 1
            if r < 20 and g < 20 and b < 20:
                black += 1
        print(f"  y={y:>4}: 非白={nonwhite:>4}  纯黑={black:>4}")

    from png_util import to_png
    out = os.path.join(ROOT, "shot_toast_dpi.png")
    with open(out, "wb") as f:
        f.write(to_png(w, h, px))
    print(f"-> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
