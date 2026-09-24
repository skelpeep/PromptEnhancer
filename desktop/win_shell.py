#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Shell 宿主：隐藏窗口 + 全局热键 + 系统托盘图标 + 右键菜单。

为什么要自己写：tkinter 没有托盘 API，也没有全局热键。这里用 ctypes 建一个
隐藏的顶层窗口，把 WM_HOTKEY 和托盘回调都收进它自己的消息循环里，跑在一条
独立线程上；tkinter 主线程只需要轮询队列，互不干扰。

跨线程调用约定：其它线程要改热键 / 弹通知，一律通过 SendMessageW 把请求
"同步投递"到 shell 线程执行（SendMessage 会阻塞到 WndProc 返回，因此可以
安全地用全局字典传 Python 对象）。
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import logging
import os
import sys
import threading
import time

# 冻结尾不碰 sys.path：dirname(dirname(__file__)) 会算成 %TEMP%，见 main.py 开头的注释
if not getattr(sys, "frozen", False):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from win_core import MOD_NOREPEAT, WM_HOTKEY
from toast import Toast

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)

# ---------------------------------------------------------------- 常量
WM_APP = 0x8000
WM_TRAYICON = WM_APP + 1
WM_DESTROY = 0x0002
WM_COMMAND = 0x0111
WM_NULL = 0x0000

WM_LBUTTONUP = 0x0202
WM_LBUTTONDBLCLK = 0x0203
WM_RBUTTONUP = 0x0205
WM_CONTEXTMENU = 0x007B

MSG_SYNC = WM_APP + 10          # 跨线程同步调用
MSG_SHOW = WM_APP + 20          # 第二个实例请求打开设置
MSG_QUIT = WM_APP + 21          # 第二个实例请求退出
MSG_ENHANCE = WM_APP + 22       # 第二个实例请求执行一次增强
MSG_UNDO = WM_APP + 23          # 第二个实例请求撤销

NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
NIF_MESSAGE, NIF_ICON, NIF_TIP, NIF_INFO = 0x01, 0x02, 0x04, 0x10
NIIF_INFO, NIIF_WARNING, NIIF_ERROR = 0x01, 0x02, 0x03

IMAGE_ICON = 1
LR_LOADFROMFILE, LR_DEFAULTSIZE = 0x0010, 0x0040

MF_STRING, MF_SEPARATOR, MF_CHECKED, MF_GRAYED = 0x0000, 0x0800, 0x0008, 0x0001
TPM_RETURNCMD, TPM_NONOTIFY, TPM_RIGHTBUTTON = 0x0100, 0x0080, 0x0002

HWND_MESSAGE = -3

# 托盘菜单命令
MENU_OPEN, MENU_TOGGLE, MENU_UNDO, MENU_QUIT = 101, 102, 103, 104

LRESULT = ctypes.c_ssize_t
# ⚠️ 坑：ctypes.wintypes 里的 LPARAM/WPARAM 是 32 位的（c_long / c_ulong），
# 在 x64 上窗口消息的 lParam 会超出 32 位有符号范围，导致
# "OverflowError: int too long to convert"。必须自己用指针宽度类型。
WPARAM_T = ctypes.c_size_t          # UINT_PTR
LPARAM_T = ctypes.c_ssize_t         # LONG_PTR
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wt.HWND, wt.UINT, WPARAM_T, LPARAM_T)


class WNDCLASSW(ctypes.Structure):
    _fields_ = [("style", wt.UINT), ("lpfnWndProc", WNDPROC),
                ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                ("hInstance", wt.HINSTANCE), ("hIcon", wt.HICON),
                ("hCursor", wt.HANDLE), ("hbrBackground", wt.HBRUSH),
                ("lpszMenuName", wt.LPCWSTR), ("lpszClassName", wt.LPCWSTR)]


class GUID(ctypes.Structure):
    _fields_ = [("Data1", wt.DWORD), ("Data2", wt.WORD), ("Data3", wt.WORD),
                ("Data4", ctypes.c_byte * 8)]


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("hWnd", wt.HWND), ("uID", wt.UINT),
                ("uFlags", wt.UINT), ("uCallbackMessage", wt.UINT),
                ("hIcon", wt.HICON), ("szTip", wt.WCHAR * 128),
                ("dwState", wt.DWORD), ("dwStateMask", wt.DWORD),
                ("szInfo", wt.WCHAR * 256), ("uVersion", wt.UINT),
                ("szInfoTitle", wt.WCHAR * 64), ("dwInfoFlags", wt.DWORD),
                ("guidItem", GUID), ("hBalloonIcon", wt.HICON)]


user32.CreateWindowExW.restype = wt.HWND
user32.DefWindowProcW.restype = LRESULT
user32.DefWindowProcW.argtypes = [wt.HWND, wt.UINT, WPARAM_T, LPARAM_T]
user32.SendMessageW.restype = LRESULT
user32.SendMessageW.argtypes = [wt.HWND, wt.UINT, WPARAM_T, LPARAM_T]
user32.PostMessageW.argtypes = [wt.HWND, wt.UINT, WPARAM_T, LPARAM_T]
user32.PostQuitMessage.argtypes = [ctypes.c_int]
user32.DestroyWindow.argtypes = [wt.HWND]
user32.RegisterHotKey.argtypes = [wt.HWND, ctypes.c_int, wt.UINT, wt.UINT]
user32.UnregisterHotKey.argtypes = [wt.HWND, ctypes.c_int]
user32.GetSystemMetrics.argtypes = [ctypes.c_int]
user32.SetForegroundWindow.argtypes = [wt.HWND]
user32.DestroyMenu.argtypes = [wt.HMENU]
user32.LoadImageW.restype = wt.HANDLE
user32.GetCursorPos.argtypes = [ctypes.POINTER(wt.POINT)]
shell32.Shell_NotifyIconW.argtypes = [wt.DWORD, ctypes.POINTER(NOTIFYICONDATAW)]
shell32.Shell_NotifyIconW.restype = wt.BOOL
user32.CreatePopupMenu.restype = wt.HMENU
user32.AppendMenuW.argtypes = [wt.HMENU, wt.UINT, ctypes.c_size_t, wt.LPCWSTR]
user32.TrackPopupMenu.argtypes = [wt.HMENU, wt.UINT, ctypes.c_int, ctypes.c_int,
                                  ctypes.c_int, wt.HWND, ctypes.c_void_p]
user32.TrackPopupMenu.restype = ctypes.c_int


# 浮层停留时长（毫秒）。统一 0.6s：它只是"我收到指令了"的即时反馈，按等级给
# 2~8s 反而会在屏幕角落一直挡着；要看细节有设置窗口的状态栏和「历史」页。
TOAST_HOLD_MS = 600


def toast_hold_ms() -> int:
    """浮层停留时长。PROMPT_ENHANCER_TOAST_MS 可覆盖（诊断/自动化测试用）。

    端到端测试要连着抓两次浮层画面做像素比对，0.6s 内抓不完，所以给它留了个
    环境变量口子；日常使用不会设它，走默认 0.6s。
    """
    try:
        v = int(os.environ.get("PROMPT_ENHANCER_TOAST_MS") or "")
    except ValueError:
        return TOAST_HOLD_MS
    return max(120, v)


class ShellHost(threading.Thread):
    """跑一条独立的 Win32 消息循环线程，管理热键与托盘。"""

    def __init__(self, icon_path: str, tip: str, on_hotkey, on_menu,
                 on_show=None, on_quit=None, on_signal=None, dpi_scale: float = 1.0):
        super().__init__(daemon=True, name="ShellHost")
        self.icon_path = icon_path
        self.tip = tip
        self.dpi_scale = max(1.0, float(dpi_scale or 1.0))
        self.on_hotkey = on_hotkey          # (hotkey_id:int) -> None
        self.on_menu = on_menu              # (cmd:int) -> None
        self.on_show = on_show
        self.on_quit = on_quit
        self.on_signal = on_signal          # ("enhance"|"undo") -> None，命令行触发用

        self.hwnd = 0
        self._ready = threading.Event()
        self._error: str | None = None
        self._hotkeys: dict[int, tuple[str, int, int]] = {}   # id -> (spec, mods, vk)
        self._tray_added = False
        self._icon_small = 0
        self._icon_large = 0
        self._msg_queue: "list[tuple[int, object]]" = []
        self._lock = threading.Lock()
        self._token = 0
        self._wndproc_ref = None      # 防止 WNDPROC 回调被 GC
        self._class_name = f"PromptEnhancerShell_{id(self):x}"
        self._enabled = True
        self._toast = None            # 右下角浮层（在本线程的消息循环里创建）

    # -------------------------------------------------- 配置（线程安全）
    @property
    def enabled(self) -> bool:
        return self._enabled

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled
        self._sync(lambda: self._modify_tray_tip())

    def set_tray_tip(self, tip: str) -> None:
        self.tip = tip
        self._sync(lambda: self._modify_tray_tip())

    def notify(self, title: str, text: str, level: str = "info") -> None:
        """右下角浮层提示。

        早先这里用的是托盘气泡（Shell_NotifyIcon + NIF_INFO），但 Win10/11 会把
        气泡转成"通知"，而通知需要注册 AppUserModelID 才会显示，没注册就被系统
        静默丢弃 —— 用户按了热键看不到任何反馈。改成自绘浮层后 100% 可见。

        停留时长统一 0.6s（见 TOAST_HOLD_MS）；level 只决定左侧色条颜色。
        """
        self._sync(lambda: self._toast_show(title, text, level, toast_hold_ms()))

    def _toast_show(self, title: str, text: str, level: str, duration: int) -> None:
        if self._toast is None:
            self._balloon(title, text,
                          {"warn": NIIF_WARNING, "error": NIIF_ERROR}.get(level, NIIF_INFO))
            return
        try:
            self._toast.show(title, text, level, duration)
        except Exception:  # noqa: BLE001
            logging.getLogger(__name__).exception("浮层提示失败")

    def apply_hotkeys(self, mapping: dict[int, str]) -> dict[int, str]:
        """重新注册全部热键。返回注册失败的 {id: 原因}。"""
        result: dict[int, str] = {}
        self._sync(lambda: result.update(self._do_hotkeys(mapping)))
        return result

    def request_quit(self) -> None:
        self._sync(lambda: user32.PostMessageW(wt.HWND(self.hwnd), MSG_QUIT, 0, 0))

    def wait_ready(self, timeout: float = 5.0) -> bool:
        return self._ready.wait(timeout)

    # -------------------------------------------------- 跨线程同步调用
    def _sync(self, fn) -> None:
        if not self.hwnd or threading.current_thread() is self:
            fn()
            return
        with self._lock:
            self._token += 1
            token = self._token
            self._msg_queue.append((token, fn))
        user32.SendMessageW(wt.HWND(self.hwnd), MSG_SYNC, token, 0)

    # -------------------------------------------------- 生命周期
    def run(self) -> None:
        try:
            hinst = kernel32.GetModuleHandleW(None)
            self._wndproc_ref = WNDPROC(self._wndproc)
            wc = WNDCLASSW()
            wc.lpfnWndProc = self._wndproc_ref
            wc.hInstance = hinst
            wc.lpszClassName = self._class_name
            if not user32.RegisterClassW(ctypes.byref(wc)):
                err = ctypes.get_last_error()
                if err != 1410:      # 1410 = 类已存在
                    raise OSError(err, "RegisterClassW 失败")

            # 隐藏的顶层窗口（不用 message-only，那样右键菜单无法正常弹出）
            self.hwnd = user32.CreateWindowExW(
                0, self._class_name, "PromptEnhancer", 0,
                0, 0, 0, 0, None, None, hinst, None)
            if not self.hwnd:
                raise OSError(ctypes.get_last_error(), "CreateWindowExW 失败")

            self._load_icons()
            self._add_tray()
            try:
                # 用 Tk 报出来的缩放比（它比 GetDpiForSystem 可靠：后者在某些
                # Python 宿主里会返回 96，导致浮层在 150% 屏上明显偏小）
                self._toast = Toast(int(round(96 * self.dpi_scale)))
            except Exception:  # noqa: BLE001
                self._toast = None
            self._ready.set()

            msg = wt.MSG()
            while True:
                r = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if r == 0 or r == -1:
                    break
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        except Exception as e:  # noqa: BLE001
            self._error = f"{type(e).__name__}: {e}"
            self._ready.set()
        finally:
            if self._toast is not None:
                try:
                    self._toast.destroy()
                except Exception:  # noqa: BLE001
                    pass
            self._remove_tray()

    def stop(self) -> None:
        if self.hwnd:
            user32.PostMessageW(wt.HWND(self.hwnd), MSG_QUIT, 0, 0)

    @property
    def error(self) -> str | None:
        return self._error

    # -------------------------------------------------- 图标
    def _load_icons(self):
        if not self.icon_path:
            return
        try:
            cx = user32.GetSystemMetrics(49)      # SM_CXSMICON
            self._icon_small = user32.LoadImageW(None, self.icon_path, IMAGE_ICON,
                                                 cx or 16, cx or 16, LR_LOADFROMFILE)
            self._icon_large = user32.LoadImageW(None, self.icon_path, IMAGE_ICON,
                                                 32, 32, LR_LOADFROMFILE)
        except Exception:  # noqa: BLE001
            self._icon_small = self._icon_large = 0

    def _nid(self, flags: int) -> NOTIFYICONDATAW:
        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = self.hwnd
        nid.uID = 1
        nid.uFlags = flags
        nid.uCallbackMessage = WM_TRAYICON
        nid.hIcon = self._icon_small or self._icon_large or 0
        nid.szTip = self.tip[:127]
        return nid

    def _add_tray(self):
        nid = self._nid(NIF_MESSAGE | NIF_ICON | NIF_TIP)
        if shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid)):
            self._tray_added = True

    def _modify_tray_tip(self):
        if not self._tray_added:
            return
        tip = ("提示词增强器（已暂停）" if not self._enabled else self.tip)[:127]
        nid = self._nid(NIF_TIP)
        nid.szTip = tip
        shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid))

    def _remove_tray(self):
        if self._tray_added and self.hwnd:
            nid = self._nid(0)
            shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(nid))
            self._tray_added = False

    def _balloon(self, title: str, text: str, flags: int):
        if not self._tray_added:
            return
        nid = self._nid(NIF_INFO)
        nid.szInfoTitle = title[:63]
        nid.szInfo = text[:255]
        nid.dwInfoFlags = flags
        shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid))

    # -------------------------------------------------- 热键
    def _do_hotkeys(self, mapping: dict[int, str]) -> dict[int, str]:
        from win_core import parse_hotkey

        for hid in list(self._hotkeys):
            old = self._hotkeys.pop(hid)
            if old[2] is not None:
                user32.UnregisterHotKey(wt.HWND(self.hwnd), hid)

        failed: dict[int, str] = {}
        for hid, spec in mapping.items():
            if not spec:
                continue
            try:
                mods, vk = parse_hotkey(spec)
            except ValueError as e:
                failed[hid] = str(e)
                continue
            if user32.RegisterHotKey(wt.HWND(self.hwnd), hid, mods | MOD_NOREPEAT, vk):
                self._hotkeys[hid] = (spec, mods, vk)
            else:
                failed[hid] = f"注册失败（错误码 {ctypes.get_last_error()}），可能被其它程序占用"
        return failed

    # -------------------------------------------------- 托盘菜单
    def _show_menu(self):
        hmenu = user32.CreatePopupMenu()
        if not hmenu:
            return
        try:
            user32.AppendMenuW(hmenu, MF_STRING, MENU_OPEN, "打开设置")
            user32.AppendMenuW(hmenu, MF_STRING | (0 if self._enabled else MF_CHECKED),
                               MENU_TOGGLE, "暂停增强" if self._enabled else "恢复增强")
            user32.AppendMenuW(hmenu, MF_STRING, MENU_UNDO, "撤销上一次增强")
            user32.AppendMenuW(hmenu, MF_SEPARATOR, 0, None)
            user32.AppendMenuW(hmenu, MF_STRING, MENU_QUIT, "退出")

            pt = wt.POINT()
            user32.GetCursorPos(ctypes.byref(pt))
            user32.SetForegroundWindow(wt.HWND(self.hwnd))
            cmd = user32.TrackPopupMenu(
                hmenu, TPM_RETURNCMD | TPM_NONOTIFY | TPM_RIGHTBUTTON,
                pt.x, pt.y, 0, wt.HWND(self.hwnd), None)
            user32.PostMessageW(wt.HWND(self.hwnd), WM_NULL, 0, 0)
            if cmd:
                self.on_menu(cmd)
        finally:
            user32.DestroyMenu(hmenu)

    # -------------------------------------------------- 窗口过程
    def _wndproc(self, hwnd, msg, wparam, lparam):
        if msg == MSG_SYNC:
            with self._lock:
                fn = None
                for i, (token, f) in enumerate(self._msg_queue):
                    if token == wparam:
                        fn = f
                        del self._msg_queue[i]
                        break
            if fn:
                try:
                    fn()
                except Exception:  # noqa: BLE001
                    pass
            return 0
        if msg == WM_HOTKEY:
            try:
                self.on_hotkey(int(wparam))
            except Exception:  # noqa: BLE001
                pass
            return 0
        if msg == WM_TRAYICON:
            ev = lparam & 0xFFFF
            if ev in (WM_RBUTTONUP, WM_CONTEXTMENU):
                self._show_menu()
            elif ev in (WM_LBUTTONUP, WM_LBUTTONDBLCLK):
                self._show_menu()
            return 0
        if msg == MSG_SHOW:
            if self.on_show:
                try:
                    self.on_show()
                except Exception:  # noqa: BLE001
                    pass
            return 0
        if msg in (MSG_ENHANCE, MSG_UNDO):
            if self.on_signal:
                try:
                    self.on_signal("enhance" if msg == MSG_ENHANCE else "undo")
                except Exception:  # noqa: BLE001
                    pass
            return 0
        if msg == MSG_QUIT:
            self._remove_tray()
            user32.DestroyWindow(wt.HWND(hwnd))
            user32.PostQuitMessage(0)
            if self.on_quit:
                try:
                    self.on_quit()
                except Exception:  # noqa: BLE001
                    pass
            return 0
        if msg == WM_DESTROY:
            user32.PostQuitMessage(0)
            return 0
        return user32.DefWindowProcW(wt.HWND(hwnd), msg, wparam, lparam)


def open_icon_for_window(path: str) -> int:
    """给 Tk 窗口用的图标句柄（可选）。"""
    if not path:
        return 0
    try:
        return user32.LoadImageW(None, path, IMAGE_ICON, 0, 0,
                                 LR_LOADFROMFILE | LR_DEFAULTSIZE) or 0
    except Exception:  # noqa: BLE001
        return 0
