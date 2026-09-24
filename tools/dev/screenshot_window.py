#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按窗口标题截图（用于验证界面渲染，纯标准库）。

用法：
    python tools/dev/screenshot_window.py "窗口标题" out.png

标题写一段就行，「包含」即算命中 —— 设置窗口标题现在带版本号
（"提示词增强器 1.0.1 · 设置"），写死全名会随版本过期。

要抓的是**某个 exe 自己弹出来的窗口**，优先用 tools/dev/exe_probe.py：
它自己起隔离实例、按进程认窗口、还能 Ctrl+Tab 逐页抓，不依赖标题。
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))     # tools/dev
TOOLS = os.path.dirname(HERE)                         # tools
ROOT = os.path.dirname(TOOLS)                         # 项目根
sys.path.insert(0, ROOT)
from png_util import to_png  # noqa: E402

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)

user32.FindWindowW.restype = wt.HWND
user32.FindWindowW.argtypes = [wt.LPCWSTR, wt.LPCWSTR]
user32.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
user32.GetDC.argtypes = [wt.HWND]
user32.GetDC.restype = wt.HDC
user32.ReleaseDC.argtypes = [wt.HWND, wt.HDC]
user32.ShowWindow.argtypes = [wt.HWND, ctypes.c_int]
user32.SetForegroundWindow.argtypes = [wt.HWND]
# ⚠️ 句柄类参数必须声明 argtypes：ctypes 默认按 C int（32 位）传，
# x64 下会直接 OverflowError: int too long to convert
gdi32.CreateCompatibleDC.argtypes = [wt.HDC]
gdi32.CreateCompatibleDC.restype = wt.HDC
gdi32.CreateCompatibleBitmap.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int]
gdi32.CreateCompatibleBitmap.restype = wt.HBITMAP
gdi32.SelectObject.argtypes = [wt.HDC, wt.HGDIOBJ]
gdi32.SelectObject.restype = wt.HGDIOBJ
gdi32.DeleteObject.argtypes = [wt.HGDIOBJ]
gdi32.DeleteDC.argtypes = [wt.HDC]
gdi32.GetDIBits.argtypes = [wt.HDC, wt.HBITMAP, wt.UINT, wt.UINT,
                            ctypes.c_void_p, ctypes.c_void_p, wt.UINT]
gdi32.BitBlt.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                         wt.HDC, ctypes.c_int, ctypes.c_int, wt.DWORD]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG),
                ("biPlanes", wt.WORD), ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
                ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", wt.LONG),
                ("biYPelsPerMeter", wt.LONG), ("biClrUsed", wt.DWORD),
                ("biClrImportant", wt.DWORD)]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wt.DWORD * 3)]


def find_window(title: str) -> int:
    """先精确匹配标题，找不到再按「包含」找。

    FindWindowW 是全等匹配，标题里一旦带上版本号就再也对不上了 —— 所以退一步
    枚举可见窗口做子串匹配。句柄都用 c_void_p 传，避免 x64 下按 int 截断。
    """
    hwnd = user32.FindWindowW(None, title)
    if hwnd:
        return hwnd

    hits = []
    enum_proc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def cb(h, _lparam):
        buf = ctypes.create_unicode_buffer(512)
        user32.GetWindowTextW(h, buf, 512)
        if buf.value and title in buf.value and user32.IsWindowVisible(h):
            hits.append(h if isinstance(h, int) else h.value)
        return True

    user32.EnumWindows(enum_proc(cb), None)
    return hits[0] if hits else 0


def capture(title: str, out_path: str) -> int:
    # 截图进程也声明 DPI 感知，否则 GetWindowRect 返回的是被虚拟化过的坐标，
    # 在高 DPI 屏上会截错位置、截不全窗口。
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:  # noqa: BLE001
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:  # noqa: BLE001
            pass

    from win_core import user32 as u32
    hwnd = find_window(title)
    if not hwnd:
        print(f"找不到窗口：{title}")
        return 1
    u32.ShowWindow(hwnd, 5)          # SW_SHOW
    u32.SetForegroundWindow(hwnd)
    time.sleep(0.8)

    rect = wt.RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        print("GetWindowRect 失败")
        return 1
    w, h = rect.right - rect.left, rect.bottom - rect.top
    print(f"窗口尺寸 {w}x{h} @ ({rect.left},{rect.top})")

    hdc_screen = user32.GetDC(None)
    hdc_mem = gdi32.CreateCompatibleDC(hdc_screen)
    hbmp = gdi32.CreateCompatibleBitmap(hdc_screen, w, h)
    gdi32.SelectObject(hdc_mem, hbmp)
    gdi32.BitBlt(hdc_mem, 0, 0, w, h, hdc_screen, rect.left, rect.top, 0x00CC0020)  # SRCCOPY

    bmi = BITMAPINFO()
    bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bmi.bmiHeader.biWidth = w
    bmi.bmiHeader.biHeight = -h          # 负数 = 自上而下
    bmi.bmiHeader.biPlanes = 1
    bmi.bmiHeader.biBitCount = 32
    buf = ctypes.create_string_buffer(w * h * 4)
    gdi32.GetDIBits(hdc_mem, hbmp, 0, h, buf, ctypes.byref(bmi), 0)

    # BGRA -> RGBA
    data = bytearray(buf.raw)
    for i in range(0, len(data), 4):
        data[i], data[i + 2] = data[i + 2], data[i]

    png = to_png(w, h, bytes(data))
    with open(out_path, "wb") as f:
        f.write(png)
    print(f"已保存 {out_path} ({len(png)} bytes)")

    gdi32.DeleteObject(hbmp)
    gdi32.DeleteDC(hdc_mem)
    user32.ReleaseDC(None, hdc_screen)
    return 0


if __name__ == "__main__":
    title = sys.argv[1] if len(sys.argv) > 1 else "提示词增强器"
    out = sys.argv[2] if len(sys.argv) > 2 else "window.png"
    raise SystemExit(capture(title, out))
