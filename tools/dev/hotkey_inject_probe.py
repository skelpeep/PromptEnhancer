#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""验证：用 SendInput 注入的按键，能不能触发 RegisterHotKey 注册的全局热键。

这是自测脚本能不能自动化驱动热键链路的前提。用法：
    python tools/dev/hotkey_inject_probe.py
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
from win_core import (MOD_NOREPEAT, VK_ALT, VK_CTRL, WM_HOTKEY,  # noqa: E402
                      parse_hotkey, send_combo, user32)


def main() -> int:
    mods, vk = parse_hotkey("ctrl+alt+f9")
    if not user32.RegisterHotKey(None, 77, mods | MOD_NOREPEAT, vk):
        print("注册失败 err =", ctypes.get_last_error())
        return 1
    print("已注册 Ctrl+Alt+F9，2 秒后注入按键…")
    time.sleep(2)
    send_combo([VK_CTRL, VK_ALT], vk)
    print("已注入，轮询消息队列 2 秒…")

    msg = wt.MSG()
    got = False
    deadline = time.time() + 2
    while time.time() < deadline:
        if user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
            if msg.message == WM_HOTKEY:
                got = True
                break
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        time.sleep(0.05)
    user32.UnregisterHotKey(None, 77)
    print("结论：SendInput 注入的按键", "能" if got else "不能", "触发 RegisterHotKey 全局热键")
    return 0 if got else 1


if __name__ == "__main__":
    raise SystemExit(main())
