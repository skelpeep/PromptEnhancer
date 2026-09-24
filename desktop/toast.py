#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""右下角浮层提示（纯 Win32 / GDI 自绘，不依赖托盘气泡）。

为什么不直接用 Shell_NotifyIcon 的气泡：
  Win10/11 起，托盘气泡会被系统转成"通知"，而通知需要注册 AppUserModelID +
  开始菜单快捷方式才会真正显示，没注册就被静默丢弃 —— 用户什么也看不到。
  这个浮层是自己画的窗口，显示与否完全由我们控制。

为什么强调"不抢焦点"：
  增强流程要注入 Ctrl+C / Ctrl+V，注入的目标是**当前前台窗口**。只要浮层把焦点
  抢走一次，复制/粘贴就会落到浮层上，整个功能失效。因此窗口必须带
  WS_EX_NOACTIVATE，并且只用 ShowWindow(SW_SHOWNOACTIVATE) 显示。

线程约定：必须在 ShellHost 的消息循环线程里创建和使用（它需要有消息循环）。
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

WS_POPUP = 0x80000000
WS_EX_TOPMOST = 0x00000008
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_LAYERED = 0x00080000
WS_EX_NOACTIVATE = 0x08000000

SW_SHOWNOACTIVATE = 4
SW_HIDE = 0

WM_PAINT, WM_ERASEBKGND, WM_TIMER = 0x000F, 0x0014, 0x0113
WM_LBUTTONDOWN, WM_DESTROY, WM_SIZE = 0x0201, 0x0002, 0x0005

LWA_ALPHA = 0x02
SPI_GETWORKAREA = 0x0030

DT_LEFT, DT_SINGLELINE, DT_WORDBREAK = 0x0000, 0x0020, 0x0010
DT_NOPREFIX, DT_END_ELLIPSIS, DT_CALCRECT = 0x0800, 0x8000, 0x0400
DT_VCENTER = 0x0004

TRANSPARENT = 1
CLEARTYPE_QUALITY = 5
DEFAULT_CHARSET, OUT_DEFAULT_PRECIS, CLIP_DEFAULT_PRECIS, DEFAULT_PITCH = 1, 0, 0, 0

LRESULT = ctypes.c_ssize_t
WPARAM_T = ctypes.c_size_t
LPARAM_T = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wt.HWND, wt.UINT, WPARAM_T, LPARAM_T)


class WNDCLASSW(ctypes.Structure):
    _fields_ = [("style", wt.UINT), ("lpfnWndProc", WNDPROC),
                ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                ("hInstance", wt.HINSTANCE), ("hIcon", wt.HICON),
                ("hCursor", wt.HANDLE), ("hbrBackground", wt.HBRUSH),
                ("lpszMenuName", wt.LPCWSTR), ("lpszClassName", wt.LPCWSTR)]


class PAINTSTRUCT(ctypes.Structure):
    _fields_ = [("hdc", wt.HDC), ("fErase", wt.BOOL), ("rcPaint", wt.RECT),
                ("fRestore", wt.BOOL), ("fIncUpdate", wt.BOOL),
                ("rgbReserved", ctypes.c_byte * 32)]


user32.CreateWindowExW.restype = wt.HWND
user32.CreateWindowExW.argtypes = [
    wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD, ctypes.c_int, ctypes.c_int,
    ctypes.c_int, ctypes.c_int, wt.HWND, wt.HMENU, wt.HINSTANCE, ctypes.c_void_p]
user32.DefWindowProcW.restype = LRESULT
user32.DefWindowProcW.argtypes = [wt.HWND, wt.UINT, WPARAM_T, LPARAM_T]
# ⚠️ 句柄类返回值必须显式声明 restype，否则 ctypes 按 c_int 处理，
# x64 下句柄高位被截断，GDI 调用会莫名其妙失败。
user32.GetDC.restype = wt.HDC
user32.GetDC.argtypes = [wt.HWND]
user32.ReleaseDC.argtypes = [wt.HWND, wt.HDC]
user32.GetClientRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
user32.FrameRect.argtypes = [wt.HDC, ctypes.POINTER(wt.RECT), wt.HBRUSH]
user32.BeginPaint.restype = wt.HDC
user32.BeginPaint.argtypes = [wt.HWND, ctypes.POINTER(PAINTSTRUCT)]
user32.EndPaint.argtypes = [wt.HWND, ctypes.POINTER(PAINTSTRUCT)]
user32.FillRect.argtypes = [wt.HDC, ctypes.POINTER(wt.RECT), wt.HBRUSH]
user32.ShowWindow.argtypes = [wt.HWND, ctypes.c_int]
user32.SetWindowPos.argtypes = [wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, wt.UINT]
user32.SetWindowPos.restype = wt.BOOL
user32.InvalidateRect.argtypes = [wt.HWND, ctypes.c_void_p, wt.BOOL]
user32.UpdateWindow.argtypes = [wt.HWND]
user32.UpdateWindow.restype = wt.BOOL
user32.DestroyWindow.argtypes = [wt.HWND]
user32.SetLayeredWindowAttributes.argtypes = [wt.HWND, wt.DWORD, ctypes.c_byte, wt.DWORD]
user32.SetTimer.argtypes = [wt.HWND, ctypes.c_size_t, wt.UINT, ctypes.c_void_p]
user32.SetTimer.restype = ctypes.c_size_t
user32.KillTimer.argtypes = [wt.HWND, ctypes.c_size_t]
user32.SystemParametersInfoW.argtypes = [wt.UINT, wt.UINT, ctypes.c_void_p, wt.UINT]
user32.DrawTextW.argtypes = [wt.HDC, wt.LPCWSTR, ctypes.c_int,
                             ctypes.POINTER(wt.RECT), wt.UINT]
gdi32.CreateSolidBrush.argtypes = [wt.DWORD]
gdi32.CreateSolidBrush.restype = wt.HBRUSH
gdi32.CreateFontW.restype = wt.HANDLE
gdi32.CreateFontW.argtypes = [
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wt.DWORD, wt.DWORD, wt.DWORD, wt.DWORD, wt.DWORD, wt.DWORD, wt.DWORD,
    wt.DWORD, wt.LPCWSTR]
gdi32.SetBkMode.argtypes = [wt.HDC, ctypes.c_int]
gdi32.SetTextColor.argtypes = [wt.HDC, wt.DWORD]
gdi32.SelectObject.argtypes = [wt.HDC, wt.HANDLE]
gdi32.SelectObject.restype = wt.HANDLE
gdi32.DeleteObject.argtypes = [wt.HANDLE]

THEMES = {
    # level: 左侧色条
    "info": (66, 133, 244),
    "ok": (34, 160, 106),
    "warn": (232, 160, 30),
    "error": (224, 72, 72),
    "busy": (120, 120, 132),
}
BG = (252, 252, 253)
BORDER = (223, 227, 233)
TITLE_C = (30, 34, 40)
BODY_C = (98, 104, 112)

FADE_STEPS = 6
FADE_INTERVAL = 26      # ms

# 浮层停留时长（毫秒）。浮层只是"我收到指令了"的即时反馈 —— 真正要看的内容在
# 设置窗口左下角的状态栏和「历史」页里，所以它不该在屏幕角落赖着不走。
# 0.6s 是实测下来"看得见又不等它"的值。
HOLD_MS = 600


def _rgb(r: int, g: int, b: int) -> int:
    return (b << 16) | (g << 8) | r


class Toast:
    """右下角浮层。所有方法都必须在创建它的那个消息循环线程里调用。"""

    WIDTH = 372

    def __init__(self, dpi: int = 96):
        self.scale = max(1.0, dpi / 96.0)
        self.hwnd = 0
        self._proc = None
        self._class_name = f"PromptEnhancerToast_{id(self):x}"
        self._font_title = 0
        self._font_body = 0
        self.title = ""
        self.body = ""
        self.accent = THEMES["info"]
        self._alpha = 255
        self._fading = False
        self._hold_ms = HOLD_MS

    # ------------------------------------------------------------ 字体
    def _make_fonts(self):
        if self._font_title:
            return
        s = self.scale
        self._font_title = gdi32.CreateFontW(
            -int(round(15 * s)), 0, 0, 0, 600, 0, 0, 0, DEFAULT_CHARSET,
            OUT_DEFAULT_PRECIS, CLIP_DEFAULT_PRECIS, CLEARTYPE_QUALITY,
            DEFAULT_PITCH, "Microsoft YaHei UI")
        self._font_body = gdi32.CreateFontW(
            -int(round(13 * s)), 0, 0, 0, 400, 0, 0, 0, DEFAULT_CHARSET,
            OUT_DEFAULT_PRECIS, CLIP_DEFAULT_PRECIS, CLEARTYPE_QUALITY,
            DEFAULT_PITCH, "Microsoft YaHei UI")

    # ------------------------------------------------------------ 窗口
    def _ensure_window(self) -> bool:
        if self.hwnd:
            return True
        hinst = kernel32.GetModuleHandleW(None)
        self._proc = WNDPROC(self._wndproc)
        wc = WNDCLASSW()
        wc.lpfnWndProc = self._proc
        wc.hInstance = hinst
        wc.lpszClassName = self._class_name
        if not user32.RegisterClassW(ctypes.byref(wc)):
            err = ctypes.get_last_error()
            if err != 1410:      # 类已存在
                return False
        ex = WS_EX_TOPMOST | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW | WS_EX_LAYERED
        self.hwnd = user32.CreateWindowExW(
            ex, self._class_name, "PromptEnhancerToast", WS_POPUP,
            0, 0, 10, 10, None, None, hinst, None)
        if not self.hwnd:
            self.hwnd = 0
            return False
        self._make_fonts()
        return True

    # ------------------------------------------------------------ 布局
    def _width(self) -> int:
        return int(round(self.WIDTH * self.scale))

    def _measure(self) -> tuple[int, int, int, int]:
        """返回 (宽, 高, 标题高, 正文高)。"""
        s = self.scale
        pad_x, pad_y, gap = int(round(16 * s)), int(round(13 * s)), int(round(5 * s))
        text_w = self._width() - pad_x * 2 - int(round(6 * s))
        hdc = user32.GetDC(None)
        try:
            gdi32.SelectObject(hdc, self._font_title)
            tr = wt.RECT(0, 0, text_w, 0)
            user32.DrawTextW(hdc, self.title, -1, ctypes.byref(tr),
                             DT_CALCRECT | DT_SINGLELINE | DT_NOPREFIX | DT_END_ELLIPSIS)
            th = tr.bottom - tr.top
            gdi32.SelectObject(hdc, self._font_body)
            br = wt.RECT(0, 0, text_w, 0)
            user32.DrawTextW(hdc, self.body, -1, ctypes.byref(br),
                             DT_CALCRECT | DT_WORDBREAK | DT_NOPREFIX)
            bh = br.bottom - br.top
        finally:
            user32.ReleaseDC(None, hdc)
        if not self.title:
            th = 0
            gap = 0
        if not self.body:
            bh = 0
            gap = 0
        return self._width(), pad_y * 2 + th + gap + bh, th, bh

    def _place(self, w: int, h: int) -> None:
        work = wt.RECT()
        user32.SystemParametersInfoW(SPI_GETWORKAREA, 0, ctypes.byref(work), 0)
        margin = int(round(16 * self.scale))
        x = work.right - w - margin
        y = work.bottom - h - margin
        user32.SetWindowPos(self.hwnd, None, x, y, w, h, 0x0010 | 0x0004)  # NOACTIVATE|NOZORDER

    # ------------------------------------------------------------ 对外
    def show(self, title: str, body: str = "", level: str = "info",
             duration: int | None = None) -> None:
        if not self._ensure_window():
            return
        self.title = title or ""
        self.body = body or ""
        self.accent = THEMES.get(level, THEMES["info"])
        self._hold_ms = HOLD_MS if duration is None else max(120, int(duration))
        self._fading = False
        self._alpha = 255

        w, h, _, _ = self._measure()
        self._place(w, h)
        self._layout = (w, h)
        # ⚠️ 先把上一轮可能残留的两个定时器都杀掉，再重建停留定时器。
        # 只 KillTimer(1) 是不够的：淡出用的是 2 号定时器，如果上一次是先
        # hide() 收场（hide 里漏杀 2 号），2 号会一直在队列里每 26ms 醒一次，
        # 新浮层刚显示就被它拉进淡出 —— 表现就是"第二次之后一闪而过"。
        user32.KillTimer(self.hwnd, 1)
        user32.KillTimer(self.hwnd, 2)
        user32.SetLayeredWindowAttributes(self.hwnd, 0, 255, LWA_ALPHA)
        user32.ShowWindow(self.hwnd, SW_SHOWNOACTIVATE)
        user32.InvalidateRect(self.hwnd, None, True)
        # UpdateWindow 让这次重绘**同步**做完。只 Invalidate 的话重绘排在消息队列里，
        # 窗口已经在新尺寸下了但内容还是旧尺寸画的 —— 如果窗口是变大，多出来的那条
        # 就是没画过的区域（画面底部露出黑边）。等一个 WM_PAINT 循环就能避免。
        user32.UpdateWindow(self.hwnd)
        user32.SetTimer(self.hwnd, 1, self._hold_ms, None)

    def hide(self) -> None:
        if not self.hwnd:
            return
        # 两个定时器都要杀：1 号管"停留到点"，2 号管淡出。漏掉 2 号会让残留的
        # 淡出循环接着跑，下一轮浮层只显示百来毫秒。
        user32.KillTimer(self.hwnd, 1)
        user32.KillTimer(self.hwnd, 2)
        self._fading = False
        self._alpha = 255
        user32.ShowWindow(self.hwnd, SW_HIDE)

    def destroy(self) -> None:
        if not self.hwnd:
            return
        user32.KillTimer(self.hwnd, 1)
        user32.KillTimer(self.hwnd, 2)
        user32.DestroyWindow(self.hwnd)
        self.hwnd = 0

    # ------------------------------------------------------------ 绘制
    def _paint(self) -> None:
        ps = PAINTSTRUCT()
        hdc = user32.BeginPaint(self.hwnd, ctypes.byref(ps))
        if not hdc:
            return
        try:
            rc = wt.RECT()
            user32.GetClientRect(self.hwnd, ctypes.byref(rc))
            self._draw(hdc, rc)
        finally:
            user32.EndPaint(self.hwnd, ctypes.byref(ps))

    def _text_height(self, hdc, text: str, width: int, single_line: bool) -> int:
        """量出一段文本在给定宽度下要占多高。

        ⚠️ 必须用 DT_CALCRECT 单独量。DrawText 在非 CALCRECT 模式下**不保证**
        把传入的 rect 收缩到文本实际高度 —— 直接拿画完后的 rect.bottom 去算下一行
        的起始位置，会把正文推到可视区之外，表现就是"浮层只显示标题，正文不见了"。
        """
        r = wt.RECT(0, 0, width, 0)
        flags = DT_CALCRECT | DT_NOPREFIX | (DT_SINGLELINE if single_line else DT_WORDBREAK)
        user32.DrawTextW(hdc, text, -1, ctypes.byref(r), flags)
        return max(0, r.bottom - r.top)

    def _draw(self, hdc, rc) -> None:
        """把浮层画到给定 DC 上。

        单独拆出来是为了能"离屏渲染"自检 —— layered 窗口没法可靠地截屏，
        直接把同一段绘制代码画到内存 DC 生成 PNG 才能验证它真的画对了。
        """
        bg = gdi32.CreateSolidBrush(_rgb(*BG))
        user32.FillRect(hdc, ctypes.byref(rc), bg)
        gdi32.DeleteObject(bg)

        s = self.scale
        bar_w = int(round(5 * s))
        bar = wt.RECT(0, 0, bar_w, rc.bottom)
        ab = gdi32.CreateSolidBrush(_rgb(*self.accent))
        user32.FillRect(hdc, ctypes.byref(bar), ab)
        gdi32.DeleteObject(ab)

        frame = gdi32.CreateSolidBrush(_rgb(*BORDER))
        user32.FrameRect(hdc, ctypes.byref(rc), frame)
        gdi32.DeleteObject(frame)

        text_w = self._width() - int(round(16 * s)) * 2 - int(round(6 * s))
        pad_x, pad_y, gap = int(round(16 * s)), int(round(13 * s)), int(round(5 * s))
        left = pad_x + bar_w
        gdi32.SetBkMode(hdc, TRANSPARENT)
        y = pad_y
        if self.title:
            gdi32.SelectObject(hdc, self._font_title)
            gdi32.SetTextColor(hdc, _rgb(*TITLE_C))
            th = self._text_height(hdc, self.title, text_w, True)
            tr = wt.RECT(left, y, left + text_w, y + th)
            user32.DrawTextW(hdc, self.title, -1, ctypes.byref(tr),
                             DT_LEFT | DT_SINGLELINE | DT_NOPREFIX | DT_END_ELLIPSIS)
            y += th + gap
        if self.body:
            gdi32.SelectObject(hdc, self._font_body)
            gdi32.SetTextColor(hdc, _rgb(*BODY_C))
            br = wt.RECT(left, y, left + text_w, rc.bottom)
            user32.DrawTextW(hdc, self.body, -1, ctypes.byref(br),
                             DT_LEFT | DT_WORDBREAK | DT_NOPREFIX)

    def _on_timer(self, tid: int) -> None:
        """定时器到点。1 号 = 停留结束，2 号 = 淡出的一步。

        ⚠️ 必须按编号区分。早先这里不分编号、只看 self._fading 标志，于是当
        2 号定时器残留下来时，它会在新浮层刚显示时命中"还没在淡出"的分支，
        顺手把刚设好的 1 号停留定时器杀掉 —— 新浮层直接进入淡出。
        """
        if tid == 1:
            user32.KillTimer(self.hwnd, 1)
            self._fading = True
            user32.SetTimer(self.hwnd, 2, FADE_INTERVAL, None)
            return
        if not self._fading:
            # 上一轮漏下来的淡出定时器：直接掐掉，别去动新浮层的停留计时
            user32.KillTimer(self.hwnd, 2)
            return
        self._alpha -= int(255 / FADE_STEPS)
        if self._alpha <= 0:
            self.hide()
            return
        user32.SetLayeredWindowAttributes(self.hwnd, 0, self._alpha, LWA_ALPHA)

    # ------------------------------------------------------------ 窗口过程
    def _wndproc(self, hwnd, msg, wparam, lparam):
        if msg == WM_PAINT:
            self._paint()
            return 0
        if msg == WM_ERASEBKGND:
            return 1
        if msg == WM_TIMER:
            self._on_timer(int(wparam))
            return 0
        if msg == WM_LBUTTONDOWN:
            self.hide()
            return 0
        if msg == WM_SIZE:
            # 尺寸变了就必须整窗重画，否则新露出来的部分会保持未绘制状态
            user32.InvalidateRect(wt.HWND(hwnd), None, True)
            return 0
        if msg == WM_DESTROY:
            user32.KillTimer(wt.HWND(hwnd), 1)
            user32.KillTimer(wt.HWND(hwnd), 2)
            return 0
        return user32.DefWindowProcW(wt.HWND(hwnd), msg, wparam, lparam)
