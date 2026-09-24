#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""抓屏幕右下角一小块区域存成 PNG。

用途：验证右下角浮层提示（toast）是否真的被画出来了。
只截右下角一小块，避免把整个桌面内容拍进来。

用法：python tools/screenshot_corner.py 输出.png [宽 高]
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
from png_util import to_png  # noqa: E402

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)

SPI_GETWORKAREA = 0x0030
SRCCOPY = 0x00CC0020


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG),
                ("biPlanes", wt.WORD), ("biBitCount", wt.WORD),
                ("biCompression", wt.DWORD), ("biSizeImage", wt.DWORD),
                ("biXPelsPerMeter", wt.LONG), ("biYPelsPerMeter", wt.LONG),
                ("biClrUsed", wt.DWORD), ("biClrImportant", wt.DWORD)]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wt.DWORD * 3)]


user32.GetDC.restype = wt.HDC
user32.GetDC.argtypes = [wt.HWND]
user32.ReleaseDC.argtypes = [wt.HWND, wt.HDC]
user32.SystemParametersInfoW.argtypes = [wt.UINT, wt.UINT, ctypes.c_void_p, wt.UINT]
gdi32.CreateCompatibleDC.restype = wt.HDC
gdi32.CreateCompatibleDC.argtypes = [wt.HDC]
gdi32.CreateCompatibleBitmap.restype = wt.HBITMAP
gdi32.CreateCompatibleBitmap.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int]
gdi32.SelectObject.argtypes = [wt.HDC, wt.HANDLE]
gdi32.SelectObject.restype = wt.HANDLE
gdi32.BitBlt.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                         wt.HDC, ctypes.c_int, ctypes.c_int, wt.DWORD]
gdi32.GetDIBits.argtypes = [wt.HDC, wt.HBITMAP, wt.UINT, wt.UINT, ctypes.c_void_p,
                            ctypes.POINTER(BITMAPINFO), wt.UINT]
gdi32.DeleteObject.argtypes = [wt.HANDLE]
gdi32.DeleteDC.argtypes = [wt.HDC]


def _blit_to_png(x: int, y: int, width: int, height: int, out_path: str) -> int:
    hdc = user32.GetDC(None)
    mem = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, width, height)
    old = gdi32.SelectObject(mem, bmp)
    try:
        if not gdi32.BitBlt(mem, 0, 0, width, height, hdc, x, y, SRCCOPY):
            return 2
        bi = BITMAPINFO()
        bi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bi.bmiHeader.biWidth = width
        bi.bmiHeader.biHeight = -height          # 负值 = 从上到下
        bi.bmiHeader.biPlanes = 1
        bi.bmiHeader.biBitCount = 32
        bi.bmiHeader.biCompression = 0
        buf = ctypes.create_string_buffer(width * height * 4)
        if not gdi32.GetDIBits(mem, bmp, 0, height, buf, ctypes.byref(bi), 0):
            return 3
        data = bytearray(buf.raw)
        for i in range(0, len(data), 4):         # BGRA -> RGBA
            data[i], data[i + 2] = data[i + 2], data[i]
        with open(out_path, "wb") as f:
            f.write(to_png(width, height, bytes(data)))
    finally:
        gdi32.SelectObject(mem, old)
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(mem)
        user32.ReleaseDC(None, hdc)
    return 0


user32.FindWindowW.restype = ctypes.c_void_p
user32.FindWindowW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p]
user32.GetWindowRect.argtypes = [ctypes.c_void_p, ctypes.POINTER(wt.RECT)]
user32.PrintWindow.argtypes = [ctypes.c_void_p, wt.HDC, wt.UINT]
user32.PrintWindow.restype = wt.BOOL
user32.GetClientRect.argtypes = [ctypes.c_void_p, ctypes.POINTER(wt.RECT)]

PW_RENDERFULLCONTENT = 0x00000002


def grab_hwnd_rgba(hwnd: int):
    """取某个窗口自己渲染出来的像素，返回 (宽, 高, RGBA bytes) 或 None。"""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:  # noqa: BLE001
        pass
    rc = wt.RECT()
    if not user32.GetClientRect(ctypes.c_void_p(hwnd), ctypes.byref(rc)):
        return None
    width, height = rc.right - rc.left, rc.bottom - rc.top
    if width <= 0 or height <= 0:
        return None

    screen = user32.GetDC(None)
    mem = gdi32.CreateCompatibleDC(screen)
    bmp = gdi32.CreateCompatibleBitmap(screen, width, height)
    old = gdi32.SelectObject(mem, bmp)
    try:
        if not user32.PrintWindow(ctypes.c_void_p(hwnd), mem, PW_RENDERFULLCONTENT):
            return None
        bi = BITMAPINFO()
        bi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bi.bmiHeader.biWidth = width
        bi.bmiHeader.biHeight = -height
        bi.bmiHeader.biPlanes = 1
        bi.bmiHeader.biBitCount = 32
        bi.bmiHeader.biCompression = 0
        buf = ctypes.create_string_buffer(width * height * 4)
        if not gdi32.GetDIBits(mem, bmp, 0, height, buf, ctypes.byref(bi), 0):
            return None
        data = bytearray(buf.raw)
        for i in range(0, len(data), 4):          # BGRA -> RGBA
            data[i], data[i + 2] = data[i + 2], data[i]
            data[i + 3] = 255                     # GDI 位图 alpha 恒为 0，补成不透明
        return width, height, bytes(data)
    finally:
        gdi32.SelectObject(mem, old)
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(mem)
        user32.ReleaseDC(None, screen)


def capture_hwnd(hwnd: int, out_path: str) -> int:
    """直接把某个窗口自己的内容渲染出来存成 PNG（发 WM_PRINT → 走它自己的 WM_PAINT）。

    为什么需要这个：右下角浮层是 WS_EX_LAYERED 窗口，用屏幕 BitBlt 抓桌面时
    经常抓不到它的内容（DWM 合成下 layered 窗口不在屏幕 DC 里），
    截出来是它背后的桌面，看着像"浮层没显示"。走这条路径才能真正看到它画了什么。
    """
    got = grab_hwnd_rgba(hwnd)
    if got is None:
        return 8
    width, height, data = got
    with open(out_path, "wb") as f:
        f.write(to_png(width, height, data))
    return 0


def capture_corner(out_path: str, width: int, height: int) -> int:
    """截屏幕右下角。注意：坐标空间取决于本进程的 DPI 感知状态，
    和另一个进程里算出来的窗口坐标不一定一致 —— 优先用 capture_window。"""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:  # noqa: BLE001
        pass
    sw = user32.GetSystemMetrics(0)
    sh = user32.GetSystemMetrics(1)
    return _blit_to_png(max(0, sw - width), max(0, sh - height), width, height, out_path)


def capture_window(title: str, out_path: str, pad: int = 16) -> int:
    """按窗口标题找到窗口，连它周围一点边距一起截下来。

    这样做的好处：区域和坐标都来自同一个进程的 GetWindowRect，
    不会出现"用 A 进程算坐标、到 B 进程去截屏"导致的 DPI 换算错位。
    """
    h = user32.FindWindowW(None, title)
    if not h:
        return 4
    r = wt.RECT()
    user32.GetWindowRect(ctypes.c_void_p(h), ctypes.byref(r))
    x, y = r.left - pad, r.top - pad
    w = (r.right - r.left) + pad * 2
    hh = (r.bottom - r.top) + pad * 2
    if w <= 0 or hh <= 0:
        return 5
    return _blit_to_png(x, y, w, hh, out_path)


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "--window":
        title = sys.argv[2]
        out = sys.argv[3] if len(sys.argv) > 3 else "window.png"
        rc = capture_window(title, out)
    elif len(sys.argv) > 2 and sys.argv[1] == "--hwnd":
        out = sys.argv[3] if len(sys.argv) > 3 else "hwnd.png"
        rc = capture_hwnd(int(sys.argv[2], 0), out)
    else:
        out = sys.argv[1] if len(sys.argv) > 1 else "corner.png"
        w = int(sys.argv[2]) if len(sys.argv) > 2 else 640
        h = int(sys.argv[3]) if len(sys.argv) > 3 else 220
        rc = capture_corner(out, w, h)
    print(f"{'ok' if rc == 0 else 'fail rc=' + str(rc)} -> {out}")
    raise SystemExit(rc)
