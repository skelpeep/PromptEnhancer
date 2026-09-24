#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""探测一组候选热键在本机是否可用（被占用会返回 1409）。

用法：python tools/probe_hotkeys.py
"""

from __future__ import annotations

import ctypes
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from win_core import MOD_NOREPEAT, parse_hotkey, user32  # noqa: E402

CANDIDATES = [
    "ctrl+alt+e", "ctrl+alt+z", "ctrl+alt+s", "ctrl+alt+p", "ctrl+alt+q",
    "ctrl+alt+u", "ctrl+alt+o", "ctrl+alt+g", "ctrl+alt+space",
    "ctrl+shift+e", "ctrl+shift+p", "ctrl+shift+alt+e", "ctrl+shift+alt+s",
    "alt+shift+e", "alt+shift+p", "win+alt+e",
]


def main() -> int:
    free, taken = [], []
    for i, spec in enumerate(CANDIDATES):
        try:
            mods, vk = parse_hotkey(spec)
        except ValueError as e:
            print(f"{spec:18} 解析失败：{e}")
            continue
        if user32.RegisterHotKey(None, 900 + i, mods | MOD_NOREPEAT, vk):
            user32.UnregisterHotKey(None, 900 + i)
            print(f"{spec:18} 可用")
            free.append(spec)
        else:
            err = ctypes.get_last_error()
            print(f"{spec:18} 被占用（错误码 {err}）")
            taken.append(spec)
    print("\n可用：", ", ".join(free))
    print("被占用：", ", ".join(taken))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
