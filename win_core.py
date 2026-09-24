#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Win32 底层能力：剪贴板、模拟按键、热键解析、DPAPI 凭据加密、单实例。

这个模块是 hotkey.py（无界面版）和桌面 App 的公共底座，避免同一套 Win32
胶水代码写两遍。
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import time

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)

# ---------------------------------------------------------------- 常量
CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002
KEYEVENTF_KEYUP = 0x0002
INPUT_KEYBOARD = 1

VK_CTRL, VK_SHIFT, VK_ALT, VK_WIN = 0x11, 0x10, 0x12, 0x5B
VK_LWIN, VK_RWIN = 0x5B, 0x5C
VK_LSHIFT, VK_RSHIFT, VK_LCONTROL, VK_RCONTROL, VK_LMENU, VK_RMENU = (
    0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5)
VK_C, VK_V, VK_Z, VK_A = 0x43, 0x56, 0x5A, 0x41

# 用户按热键时可能还按着的修饰键（左右都查，模拟器/快捷键工具常发单边键码）
MODIFIER_VKS = (VK_CTRL, VK_SHIFT, VK_ALT, VK_LWIN, VK_RWIN,
                VK_LCONTROL, VK_RCONTROL, VK_LSHIFT, VK_RSHIFT, VK_LMENU, VK_RMENU)

MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, MOD_NOREPEAT = 0x0001, 0x0002, 0x0004, 0x0008, 0x4000
WM_HOTKEY = 0x0312

ERROR_ALREADY_EXISTS = 183
ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong


# ---------------------------------------------------------------- 结构体
class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wt.WORD), ("wScan", wt.WORD), ("dwFlags", wt.DWORD),
                ("time", wt.DWORD), ("dwExtraInfo", ULONG_PTR)]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wt.LONG), ("dy", wt.LONG), ("mouseData", wt.DWORD),
                ("dwFlags", wt.DWORD), ("time", wt.DWORD), ("dwExtraInfo", ULONG_PTR)]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wt.DWORD), ("wParamL", wt.WORD), ("wParamH", wt.WORD)]


class _INPUTUNION(ctypes.Union):
    # 必须把 union 撑到 32 字节（x64），否则 sizeof(INPUT)=32 而不是 40，
    # SendInput 会静默失败。MOUSEINPUT 在这里的作用就是保证尺寸正确。
    _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wt.DWORD), ("u", _INPUTUNION)]


class DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wt.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


assert ctypes.sizeof(INPUT) == 40, "INPUT 结构尺寸不对，SendInput 会失败"

# ---------------------------------------------------------------- 剪贴板
user32.OpenClipboard.argtypes = [wt.HWND]
user32.GetClipboardData.restype = wt.HANDLE
user32.SetClipboardData.argtypes = [wt.UINT, wt.HANDLE]
user32.SetClipboardData.restype = wt.HANDLE
kernel32.GlobalAlloc.argtypes = [wt.UINT, ctypes.c_size_t]
kernel32.GlobalAlloc.restype = wt.HGLOBAL
kernel32.GlobalLock.argtypes = [wt.HGLOBAL]
kernel32.GlobalLock.restype = ctypes.c_void_p
kernel32.GlobalUnlock.argtypes = [wt.HGLOBAL]
kernel32.LocalFree.argtypes = [wt.HLOCAL]
kernel32.GlobalFree.argtypes = [wt.HGLOBAL]
kernel32.GlobalFree.restype = wt.HGLOBAL


def get_clipboard_text() -> str:
    """读剪贴板文本。拿不到（被占用/非文本）返回空串。"""
    for _ in range(10):
        if user32.OpenClipboard(None):
            break
        time.sleep(0.03)
    else:
        return ""
    try:
        h = user32.GetClipboardData(CF_UNICODETEXT)
        if not h:
            return ""
        p = kernel32.GlobalLock(h)
        if not p:
            return ""
        try:
            return ctypes.wstring_at(p) or ""
        finally:
            kernel32.GlobalUnlock(h)
    finally:
        user32.CloseClipboard()


def set_clipboard_text(text: str) -> bool:
    """写剪贴板文本。失败返回 False（被别的程序占着）。"""
    data = (text + "\0").encode("utf-16-le")
    h = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
    if not h:
        return False
    p = kernel32.GlobalLock(h)
    if not p:
        kernel32.GlobalFree(h)
        return False
    ctypes.memmove(p, data, len(data))
    kernel32.GlobalUnlock(h)

    for _ in range(10):
        if user32.OpenClipboard(None):
            break
        time.sleep(0.03)
    else:
        kernel32.GlobalFree(h)   # 没打开剪贴板，所有权还在我方，必须释放
        return False
    try:
        user32.EmptyClipboard()
        if user32.SetClipboardData(CF_UNICODETEXT, h):
            return True          # 成功后句柄所有权转交系统，不能再 free
        kernel32.GlobalFree(h)   # SetClipboardData 失败，所有权仍在我方
        return False
    finally:
        user32.CloseClipboard()


# ---------------------------------------------------------------- 模拟按键
user32.SendInput.argtypes = [wt.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
user32.SendInput.restype = wt.UINT
user32.keybd_event.argtypes = [wt.BYTE, wt.BYTE, wt.DWORD, ULONG_PTR]
user32.keybd_event.restype = None


def _key(vk: int, up: bool = False) -> INPUT:
    inp = INPUT()
    inp.type = INPUT_KEYBOARD
    inp.u.ki = KEYBDINPUT(wVk=vk, wScan=0,
                          dwFlags=KEYEVENTF_KEYUP if up else 0,
                          time=0, dwExtraInfo=0)
    return inp


def send_combo(modifiers: list[int], key: int) -> None:
    """发一个组合键，例如 send_combo([VK_CTRL], VK_C) 就是 Ctrl+C。

    ⚠️ 实测坑（本机 100% 复现）：某些环境（沙箱 / 安全软件 / EDR）会静默拦截
    SendInput —— 它返回 0 且**不设置** last error，看起来像"调用成功了但什么都没
    发生"。此时复制粘贴会全部失效，表现为"增强用的是上一次剪贴板的内容"和
    "结果没有替换选中的文本"。更老的 keybd_event 反而没被拦，所以这里做成
    两段式：优先 SendInput，数量对不上就整体回退到 keybd_event。
    """
    seq = [_key(m) for m in modifiers] + [_key(key), _key(key, up=True)]
    seq += [_key(m, up=True) for m in reversed(modifiers)]
    arr = (INPUT * len(seq))(*seq)
    if user32.SendInput(len(seq), arr, ctypes.sizeof(INPUT)) == len(seq):
        return
    for vk in modifiers:
        user32.keybd_event(vk, 0, 0, 0)
    user32.keybd_event(key, 0, 0, 0)
    user32.keybd_event(key, 0, KEYEVENTF_KEYUP, 0)
    for vk in reversed(modifiers):
        user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)


def send_ctrl(key: int) -> None:
    send_combo([VK_CTRL], key)


# ---------------------------------------------------------------- 按键状态
user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.GetAsyncKeyState.restype = ctypes.c_short


def modifier_down() -> list[int]:
    """返回当前物理按下的修饰键里、我们关心的那几个（用于诊断/日志）。"""
    return [vk for vk in MODIFIER_VKS if user32.GetAsyncKeyState(vk) & 0x8000]


def wait_modifiers_released(timeout: float = 0.8, poll: float = 0.02) -> float:
    """等用户把热键的修饰键松开，返回实际等待秒数。

    ⚠️ 为什么必须等：用户按 Ctrl+Alt+E 触发增强时，WM_HOTKEY 在按键瞬间就来了，
    此刻 Ctrl 和 Alt 在物理上还按着。如果这时注入 Ctrl+C，目标程序收到的是
    **Ctrl+Alt+C** 而不是 Ctrl+C —— 复制/粘贴双双失效，用户看到的现象就是
    "增强用的是上一次剪贴板的内容""结果没有替换选中的文本"。

    等到松开再注入，才能得到干净的 Ctrl+C / Ctrl+V。
    """
    start = time.time()
    deadline = start + max(0.0, timeout)
    while time.time() < deadline:
        if not modifier_down():
            break
        time.sleep(poll)
    return time.time() - start


# ---------------------------------------------------------------- 剪贴板变化检测
user32.GetClipboardSequenceNumber.restype = wt.DWORD


def clipboard_sequence() -> int:
    """剪贴板序号：每次剪贴板内容被改写都会 +1。

    用它判断"我们的 Ctrl+C 到底有没有让目标程序写入剪贴板"，比内容比对可靠，
    而且**不需要先清空剪贴板**（清空会丢掉用户原本复制的图片/富文本）。
    """
    return int(user32.GetClipboardSequenceNumber())


user32.GetForegroundWindow.restype = wt.HWND


def get_foreground_window() -> int:
    return int(user32.GetForegroundWindow() or 0)


class GUITHREADINFO(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("flags", wt.DWORD),
                ("hwndActive", wt.HWND), ("hwndFocus", wt.HWND),
                ("hwndCapture", wt.HWND), ("hwndMenuOwner", wt.HWND),
                ("hwndMoveSize", wt.HWND), ("hwndCaret", wt.HWND),
                ("rcCaret", wt.RECT)]


user32.GetGUIThreadInfo.argtypes = [wt.DWORD, ctypes.POINTER(GUITHREADINFO)]
user32.GetGUIThreadInfo.restype = wt.BOOL


def get_input_target() -> tuple[int, int]:
    """同时记录前台窗口和焦点控件，异步完成后只向同一目标注入按键。"""
    info = GUITHREADINFO(cbSize=ctypes.sizeof(GUITHREADINFO))
    if not user32.GetGUIThreadInfo(0, ctypes.byref(info)):
        return (0, 0)
    return int(info.hwndActive or 0), int(info.hwndFocus or 0)


class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", wt.UINT), ("dwTime", wt.DWORD)]


user32.GetLastInputInfo.argtypes = [ctypes.POINTER(LASTINPUTINFO)]
user32.GetLastInputInfo.restype = wt.BOOL


def last_input_tick() -> int | None:
    """用于发现等待模型期间的键鼠操作，包括同一控件内的光标移动。"""
    info = LASTINPUTINFO(cbSize=ctypes.sizeof(LASTINPUTINFO))
    return int(info.dwTime) if user32.GetLastInputInfo(ctypes.byref(info)) else None


# ---------------------------------------------------------------- DPI
user32.GetDC.restype = wt.HDC
user32.GetDC.argtypes = [wt.HWND]
user32.ReleaseDC.argtypes = [wt.HWND, wt.HDC]


def system_dpi() -> int:
    """系统 DPI（96 = 100%）。窗口尺寸/字体要靠它缩放，否则在高分屏上偏小。"""
    try:
        return int(user32.GetDpiForSystem()) or 96
    except AttributeError:
        pass
    try:
        gdi32 = ctypes.WinDLL("gdi32")
        hdc = user32.GetDC(None)
        try:
            return int(gdi32.GetDeviceCaps(hdc, 88)) or 96    # LOGPIXELSX
        finally:
            user32.ReleaseDC(None, hdc)
    except Exception:  # noqa: BLE001
        return 96


# ---------------------------------------------------------------- 热键解析
MODS = {"ctrl": MOD_CONTROL, "control": MOD_CONTROL, "alt": MOD_ALT,
        "shift": MOD_SHIFT, "win": MOD_WIN, "super": MOD_WIN}
KEYS = {c: 0x41 + i for i, c in enumerate("abcdefghijklmnopqrstuvwxyz")}
KEYS.update({str(d): 0x30 + d for d in range(10)})
KEYS.update({"space": 0x20, "enter": 0x0D, "tab": 0x09, "esc": 0x1B,
             "`": 0xC0, "-": 0xBD, "=": 0xBB, "[": 0xDB, "]": 0xDD,
             ";": 0xBA, "'": 0xDE, ",": 0xBC, ".": 0xBE, "/": 0xBF, "\\": 0xDC})
KEYS.update({f"f{i}": 0x6F + i for i in range(1, 13)})
# 编辑/导航键：按键录入（HotkeyEntry）里用户很自然会按到这些，win_core 认了
# 才能真的注册成热键。RegisterHotKey 本身就支持这些 VK。
KEYS.update({"backspace": 0x08, "pageup": 0x21, "pagedown": 0x22, "end": 0x23,
             "home": 0x24, "left": 0x25, "up": 0x26, "right": 0x27,
             "down": 0x28, "insert": 0x2D, "delete": 0x2E})

# 显示用的名字（format_hotkey 里查不到就按大写输出）
PRETTY_KEYS = {"pageup": "PageUp", "pagedown": "PageDown", "backspace": "Backspace",
               "delete": "Delete", "insert": "Insert", "home": "Home", "end": "End",
               "left": "←", "up": "↑", "right": "→", "down": "↓"}


def parse_hotkey(spec: str) -> tuple[int, int]:
    """把 'ctrl+alt+e' 解析成 (修饰位, 虚拟键码)。不合法抛 ValueError。"""
    parts = [p.strip().lower() for p in str(spec).replace("+", " ").split() if p.strip()]
    mods, key = 0, None
    for p in parts:
        if p in MODS:
            mods |= MODS[p]
        elif p in KEYS:
            if key is not None:
                raise ValueError("热键只能包含一个主键，例如 ctrl+alt+e")
            key = KEYS[p]
        else:
            raise ValueError(f"无法识别的热键片段：{p}")
    if key is None:
        raise ValueError("热键缺少主键，例如 ctrl+alt+e")
    if mods == 0:
        raise ValueError("热键必须带至少一个修饰键（ctrl / alt / shift），否则会误触")
    return mods, key


def format_hotkey(spec: str) -> str:
    """规范化显示，例如 'ctrl+alt+e' -> 'Ctrl + Alt + E'。"""
    out = []
    for p in str(spec).replace("+", " ").split():
        p = p.strip().lower()
        if not p:
            continue
        name = {"ctrl": "Ctrl", "control": "Ctrl", "alt": "Alt",
                "shift": "Shift", "win": "Win", "super": "Win"}.get(p)
        out.append(name or PRETTY_KEYS.get(p, p.upper()))
    return " + ".join(out)


# ---------------------------------------------------------------- DPAPI
crypt32.CryptProtectData.argtypes = [
    ctypes.POINTER(DATA_BLOB), wt.LPCWSTR, ctypes.POINTER(DATA_BLOB),
    ctypes.c_void_p, ctypes.c_void_p, wt.DWORD, ctypes.POINTER(DATA_BLOB)]
crypt32.CryptProtectData.restype = wt.BOOL
crypt32.CryptUnprotectData.argtypes = [
    ctypes.POINTER(DATA_BLOB), ctypes.POINTER(wt.LPWSTR), ctypes.POINTER(DATA_BLOB),
    ctypes.c_void_p, ctypes.c_void_p, wt.DWORD, ctypes.POINTER(DATA_BLOB)]
crypt32.CryptUnprotectData.restype = wt.BOOL


def _blob_from(data: bytes) -> tuple[DATA_BLOB, ctypes.Array]:
    buf = ctypes.create_string_buffer(data, len(data))
    return DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf


def dpapi_protect(plain: bytes) -> bytes:
    """用 Windows DPAPI 加密（绑定当前用户，换机器/换用户都解不开）。"""
    blob_in, _keep = _blob_from(plain)
    blob_out = DATA_BLOB()
    if not crypt32.CryptProtectData(ctypes.byref(blob_in), "PromptEnhancer",
                                    None, None, None, 0, ctypes.byref(blob_out)):
        raise OSError(ctypes.get_last_error(), "CryptProtectData 失败")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32.LocalFree(blob_out.pbData)


def dpapi_unprotect(cipher: bytes) -> bytes:
    blob_in, _keep = _blob_from(cipher)
    blob_out = DATA_BLOB()
    if not crypt32.CryptUnprotectData(ctypes.byref(blob_in), None, None, None, None, 0,
                                      ctypes.byref(blob_out)):
        raise OSError(ctypes.get_last_error(), "CryptUnprotectData 失败")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32.LocalFree(blob_out.pbData)


# ---------------------------------------------------------------- 单实例
def acquire_single_instance(name: str = "PromptEnhancer.SingleInstance.v1"):
    """拿到全局互斥体则返回句柄；已存在实例则返回 None。"""
    kernel32.CreateMutexW.restype = wt.HANDLE
    h = kernel32.CreateMutexW(None, False, name)
    if not h:
        return None
    if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        kernel32.CloseHandle(h)
        return None
    return h


def release_single_instance(handle) -> None:
    if handle:
        try:
            kernel32.ReleaseMutex(handle)
            kernel32.CloseHandle(handle)
        except OSError:
            pass


user32.CreateWindowExW.restype = wt.HWND
user32.PostMessageW.argtypes = [wt.HWND, wt.UINT, ctypes.c_size_t, ctypes.c_ssize_t]
user32.SendMessageW.restype = ctypes.c_ssize_t
user32.SendMessageW.argtypes = [wt.HWND, wt.UINT, ctypes.c_size_t, ctypes.c_ssize_t]
user32.RegisterHotKey.argtypes = [wt.HWND, ctypes.c_int, wt.UINT, wt.UINT]
user32.UnregisterHotKey.argtypes = [wt.HWND, ctypes.c_int]
kernel32.CreateMutexW.restype = wt.HANDLE
kernel32.CloseHandle.argtypes = [wt.HANDLE]
kernel32.ReleaseMutex.argtypes = [wt.HANDLE]


def post_message(hwnd: int, msg: int, wparam: int = 0, lparam: int = 0) -> bool:
    return bool(user32.PostMessageW(wt.HWND(hwnd), msg, wparam, lparam))


def is_valid_window(hwnd: int) -> bool:
    return bool(user32.IsWindow(wt.HWND(hwnd)))
