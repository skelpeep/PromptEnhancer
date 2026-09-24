#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""浮层（toast）实测：真的把窗口显示出来，再抓下它自己的画面。

为什么不能只靠"截桌面"：
  浮层是 WS_EX_LAYERED 窗口，DWM 合成下屏幕 BitBlt 经常抓不到它 ——
  截出来是它背后的桌面内容，看着像"浮层没显示"，其实是抓屏方式的问题。
  这里改用 PrintWindow(PW_RENDERFULLCONTENT) 让窗口自己把内容画出来。

用法：
    python tools/dev/toast_probe.py            # 依次验证 busy / ok / warn 三种样式
产物：
    shot_toast_live_busy.png / _ok.png / _warn.png
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

from toast import Toast                      # noqa: E402
from screenshot_corner import capture_hwnd   # noqa: E402

user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.FindWindowW.restype = ctypes.c_void_p
user32.FindWindowW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p]
user32.GetWindowRect.argtypes = [ctypes.c_void_p, ctypes.POINTER(wt.RECT)]
user32.IsWindowVisible.argtypes = [ctypes.c_void_p]
user32.GetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int]
user32.GetWindowLongW.restype = ctypes.c_long

GWL_EXSTYLE = -20
WS_EX_LAYERED, WS_EX_NOACTIVATE, WS_EX_TOPMOST = 0x00080000, 0x08000000, 0x00000008

CASES = [
    ("busy", "正在增强：帮我写个爬虫，抓取网页标题"),
    ("ok",   "已替换选中内容（486 ms）"),
    ("warn", "没抓到选中的文本。请先选中要增强的文字，再按热键"),
]


def describe(hwnd: int) -> None:
    h = ctypes.c_void_p(hwnd)
    ex = user32.GetWindowLongW(h, GWL_EXSTYLE)
    r = wt.RECT()
    user32.GetWindowRect(h, ctypes.byref(r))
    print(f"  可见={bool(user32.IsWindowVisible(h))} "
          f"置顶={bool(ex & WS_EX_TOPMOST)} "
          f"不抢焦点={bool(ex & WS_EX_NOACTIVATE)} "
          f"rect=({r.left},{r.top},{r.right},{r.bottom}) "
          f"尺寸={r.right - r.left}x{r.bottom - r.top}")


def one_case(level: str, body: str) -> tuple[bool, str]:
    """显示一次浮层 → 抓它的画面 → 收起。返回 (是否成功, 说明)。"""
    box: dict = {}
    err: list[str] = []
    ready = threading.Event()

    def worker():
        msg = wt.MSG()
        try:
            t = Toast(144)                       # 150% 缩放下的典型情况
            t._ensure_window()
            box["toast"] = t
            box["hwnd"] = t.hwnd
            ready.set()
            t.show("提示词增强器", body, level, 20000)
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        except Exception as e:  # noqa: BLE001
            import traceback
            err.append(f"{type(e).__name__}: {e}\n{traceback.format_exc()}")
            ready.set()

    th = threading.Thread(target=worker, daemon=True)
    th.start()
    ready.wait(timeout=5)
    time.sleep(0.9)                              # 等它真正显示出来

    hwnd = box.get("hwnd") or 0
    if not hwnd:
        return False, "窗口没创建出来"
    print(f"[{level}] 窗口已创建，hwnd={hwnd}")
    describe(hwnd)

    out = os.path.join(ROOT, f"shot_toast_live_{level}.png")
    rc = capture_hwnd(hwnd, out)
    if rc != 0 or not os.path.isfile(out):
        return False, f"PrintWindow 抓图失败 rc={rc}"
    size = os.path.getsize(out)

    # 抓完就让它消失，别占着屏幕
    def bye():
        try:
            box["toast"].hide()
        except Exception:  # noqa: BLE001
            pass
    threading.Thread(target=bye, daemon=True).start()
    time.sleep(0.3)

    if err:
        return False, "线程异常：" + err[0].splitlines()[0]
    return True, f"{os.path.basename(out)}（{size} 字节）"


def main() -> int:
    print(f"外部 FindWindowW('PromptEnhancerToast') -> "
          f"{user32.FindWindowW(None, 'PromptEnhancerToast')}\n")
    ok_all = True
    for level, body in CASES:
        ok, msg = one_case(level, body)
        ok_all = ok_all and ok
        print(f"  {'✅' if ok else '❌'} {level}：{msg}\n")
    print("结论：" + ("三种样式都成功显示并抓到了画面" if ok_all else "有失败项"))
    return 0 if ok_all else 1


if __name__ == "__main__":
    raise SystemExit(main())
