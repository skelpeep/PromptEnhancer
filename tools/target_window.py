#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""测试靶窗口：一个原生 Win32 窗口 + EDIT 控件，用来真实验证"复制选中 / 粘贴替换"。

为什么不用 tkinter 当靶子：Tk 窗口在 Windows 上抢不到前台键盘焦点，
注入的 Ctrl+C 根本送不到它。原生窗口可以自己 SetForegroundWindow + SetFocus，
并且 EDIT 控件是系统实现的，Ctrl+C / Ctrl+V 的行为和记事本一致。

用法：
    python tools/target_window.py --text "原始草稿文本" --out state.json --select all
    # 窗口打开后会一直把 EDIT 的内容写进 --out，供测试脚本读取
    # 关掉窗口即退出
"""

from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as wt
import json
import os
import sys
import time

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

WNDPROC_T = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wt.HWND, wt.UINT,
                               ctypes.c_size_t, ctypes.c_ssize_t)

WS_OVERLAPPEDWINDOW = 0x00CF0000
WS_CHILD, WS_VISIBLE = 0x40000000, 0x10000000
WS_VSCROLL = 0x00200000
WS_EX_CLIENTEDGE = 0x00000200
ES_MULTILINE, ES_AUTOVSCROLL = 0x0004, 0x0040
SW_SHOW = 5
WM_DESTROY, WM_TIMER, WM_CLOSE = 0x0002, 0x0113, 0x0010
EM_SETSEL, EM_GETSEL = 0x00B1, 0x00B0

# 供测试脚本远程遥控：PostMessageW(hwnd, MSG_XXX, 0, 0)
WM_APP = 0x8000
MSG_SELECT_ALL = WM_APP + 1     # 恢复原文并全选，模拟"用户选中了一段草稿"
MSG_CLEAR_SEL = WM_APP + 2      # 恢复原文、取消选择，模拟"没有选中任何文本"
WM_SETFOCUS = 0x0007


class GUITHREADINFO(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("flags", wt.DWORD), ("hwndActive", wt.HWND),
                ("hwndFocus", wt.HWND), ("hwndCapture", wt.HWND),
                ("hwndMenuOwner", wt.HWND), ("hwndMoveSize", wt.HWND),
                ("hwndCaret", wt.HWND), ("rcCaret", wt.RECT)]


def focus_of_foreground_thread() -> int:
    """取当前前台线程的焦点窗口。跨进程用它才能确认"键盘光标到底在谁身上"。"""
    gti = GUITHREADINFO()
    gti.cbSize = ctypes.sizeof(GUITHREADINFO)
    if user32.GetGUIThreadInfo(0, ctypes.byref(gti)):
        return int(gti.hwndFocus or 0)
    return 0

user32.CreateWindowExW.restype = wt.HWND
user32.CreateWindowExW.argtypes = [
    wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD, ctypes.c_int, ctypes.c_int,
    ctypes.c_int, ctypes.c_int, wt.HWND, wt.HMENU, wt.HINSTANCE, ctypes.c_void_p]
user32.DefWindowProcW.restype = ctypes.c_ssize_t
user32.DefWindowProcW.argtypes = [wt.HWND, wt.UINT, ctypes.c_size_t, ctypes.c_ssize_t]
user32.SendMessageW.restype = ctypes.c_ssize_t
user32.SendMessageW.argtypes = [wt.HWND, wt.UINT, ctypes.c_size_t, ctypes.c_ssize_t]
user32.SetWindowTextW.argtypes = [wt.HWND, wt.LPCWSTR]
user32.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.SetForegroundWindow.argtypes = [wt.HWND]
user32.SetFocus.argtypes = [wt.HWND]
user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
user32.AttachThreadInput.argtypes = [wt.DWORD, wt.DWORD, wt.BOOL]
user32.GetForegroundWindow.restype = wt.HWND
user32.GetFocus.restype = wt.HWND
user32.GetGUIThreadInfo.argtypes = [wt.DWORD, ctypes.POINTER(GUITHREADINFO)]


class WNDCLASSW(ctypes.Structure):
    _fields_ = [("style", wt.UINT), ("lpfnWndProc", WNDPROC_T),
                ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                ("hInstance", wt.HINSTANCE), ("hIcon", wt.HICON),
                ("hCursor", wt.HANDLE), ("hbrBackground", wt.HBRUSH),
                ("lpszMenuName", wt.LPCWSTR), ("lpszClassName", wt.LPCWSTR)]


class Target:
    def __init__(self, text: str, out_path: str, select: str, title: str,
                 keep_foreground: bool = False):
        self.out_path = out_path
        self.title = title
        self.select = select
        self.keep_foreground = keep_foreground
        self.initial_text = text
        self._proc = WNDPROC_T(self._wndproc)
        self.hwnd = 0
        self.edit = 0

        hinst = kernel32.GetModuleHandleW(None)
        cls = f"PromptEnhancerTarget_{os.getpid():x}"
        wc = WNDCLASSW()
        wc.lpfnWndProc = self._proc
        wc.hInstance = hinst
        wc.lpszClassName = cls
        wc.hbrBackground = ctypes.cast(ctypes.c_void_p(6), wt.HBRUSH)   # COLOR_WINDOW+1
        if not user32.RegisterClassW(ctypes.byref(wc)):
            raise OSError(ctypes.get_last_error(), "RegisterClassW 失败")

        self.hwnd = user32.CreateWindowExW(
            0, cls, title, WS_OVERLAPPEDWINDOW | WS_VISIBLE,
            120, 120, 620, 300, None, None, hinst, None)
        if not self.hwnd:
            raise OSError(ctypes.get_last_error(), "CreateWindowExW 失败")

        self.edit = user32.CreateWindowExW(
            WS_EX_CLIENTEDGE, "EDIT", "",
            WS_CHILD | WS_VISIBLE | WS_VSCROLL | ES_MULTILINE | ES_AUTOVSCROLL,
            10, 10, 580, 240, self.hwnd, None, hinst, None)
        if not self.edit:
            raise OSError(ctypes.get_last_error(), "创建 EDIT 控件失败")

        user32.SetWindowTextW(self.edit, text)
        if select == "all":
            user32.SendMessageW(self.edit, EM_SETSEL, 0, -1)

        self._take_foreground()
        user32.SetTimer(self.hwnd, 1, 200, None)
        self.write_state()

    def _take_foreground(self):
        """新进程通常可以抢前台；AttachThreadInput 是常规的兜底手法。"""
        fg = user32.GetForegroundWindow()
        fg_tid = user32.GetWindowThreadProcessId(fg, None)
        my_tid = kernel32.GetCurrentThreadId()
        attached = False
        if fg_tid and fg_tid != my_tid:
            attached = bool(user32.AttachThreadInput(fg_tid, my_tid, True))
        user32.SetForegroundWindow(self.hwnd)
        user32.SetFocus(self.edit)
        if attached:
            user32.AttachThreadInput(fg_tid, my_tid, False)

    def text(self) -> str:
        n = user32.GetWindowTextLengthW(self.edit)
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(self.edit, buf, n + 1)
        return buf.value

    def write_state(self):
        sel = ctypes.c_ulong(0), ctypes.c_ulong(0)
        a, b = ctypes.c_ulong(0), ctypes.c_ulong(0)
        user32.SendMessageW(self.edit, EM_GETSEL, ctypes.addressof(a), ctypes.addressof(b))
        state = {
            "hwnd": int(self.hwnd),
            "edit": int(self.edit),
            "text": self.text(),
            "sel_start": a.value,
            "sel_end": b.value,
            "is_foreground": int(user32.GetForegroundWindow() or 0) == int(self.hwnd),
            "focus_hwnd": focus_of_foreground_thread(),
            "update_at": time.time(),
        }
        tmp = self.out_path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False)
            os.replace(tmp, self.out_path)
        except OSError:
            pass

    def _wndproc(self, hwnd, msg, wparam, lparam):
        if msg in (MSG_SELECT_ALL, MSG_CLEAR_SEL):
            user32.SetWindowTextW(self.edit, self.initial_text)
            if msg == MSG_SELECT_ALL:
                user32.SendMessageW(self.edit, EM_SETSEL, 0, -1)
            else:
                user32.SendMessageW(self.edit, EM_SETSEL, 0, 0)
            self._take_foreground()
            self.write_state()
            return 0
        if msg == WM_TIMER:
            # 测试期间父脚本可能被别的程序抢走前台，导致注入的按键送错窗口。
            # 保持前台模式下每 200ms 检查一次并夺回来。
            if self.keep_foreground:
                if int(user32.GetForegroundWindow() or 0) != int(self.hwnd):
                    self._take_foreground()
                elif int(user32.GetFocus() or 0) != int(self.edit):
                    # 关键：前台是我们，但键盘焦点可能不在 EDIT 上 —— 那样注入的
                    # Ctrl+C 谁也不会响应。把焦点钉在编辑框上。
                    user32.SetFocus(self.edit)
            self.write_state()
            return 0
        if msg == WM_SETFOCUS:
            # 窗口拿到焦点时把键盘焦点交给编辑框（原生窗口不会自动做这件事）
            user32.SetFocus(self.edit)
            return 0
        if msg == WM_CLOSE:
            user32.DestroyWindow(wt.HWND(hwnd))
            return 0
        if msg == WM_DESTROY:
            user32.KillTimer(wt.HWND(hwnd), 1)
            user32.PostQuitMessage(0)
            return 0
        return user32.DefWindowProcW(wt.HWND(hwnd), msg, wparam, lparam)

    def run(self):
        msg = wt.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))


def main() -> int:
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument("--text", default="")
    ap.add_argument("--text-file", default="")
    ap.add_argument("--out", required=True)
    ap.add_argument("--select", default="all", choices=["all", "none"])
    ap.add_argument("--title", default="PromptEnhancerTarget")
    ap.add_argument("--keep-foreground", action="store_true",
                    help="每 200ms 检查一次并夺回前台，测试用")
    args, _ = ap.parse_known_args()

    text = args.text
    if args.text_file and os.path.isfile(args.text_file):
        with open(args.text_file, "r", encoding="utf-8") as f:
            text = f.read()

    t = Target(text, args.out, args.select, args.title, args.keep_foreground)
    # 把窗口句柄吐出来，方便测试脚本确认它抢到了前台
    print(json.dumps({"hwnd": int(t.hwnd), "edit": int(t.edit),
                      "foreground": int(user32.GetForegroundWindow() or 0)}), flush=True)
    t.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
