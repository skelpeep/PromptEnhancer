#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""浮层（toast）停留时长自检：连着显示 3 次，逐次量"可见了多久"。

为什么需要这个脚本：
  曾经出过一个只有"第二次之后"才出现的 bug —— 每次浮层都在约定的停留时长
  （0.6s）之前就消失，用户完全来不及看清。根因是淡出定时器没被清掉：
  `hide()` 只 KillTimer(1)，残留的淡出定时器会在下一轮 show() 之后立刻把
  新浮层淡出。第一轮正常、后面全是"一闪而过"，光看代码很难发现，必须实测。

用法（需要有桌面会话，浮层会真的出现在右下角）：
    python tools/dev/toast_duration_check.py
    python tools/dev/toast_duration_check.py --ms 600      # 指定期望停留时长
退出码 0 = 每次都停够了；非 0 = 有哪一轮提前消失。
"""

from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as wt
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))     # tools/dev
TOOLS = os.path.dirname(HERE)                         # tools
ROOT = os.path.dirname(TOOLS)                         # 项目根
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "desktop"))

from toast import Toast, HOLD_MS                      # noqa: E402

user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.PeekMessageW.argtypes = [ctypes.POINTER(wt.MSG), ctypes.c_void_p,
                                wt.UINT, wt.UINT, wt.UINT]
user32.PeekMessageW.restype = wt.BOOL
user32.TranslateMessage.argtypes = [ctypes.POINTER(wt.MSG)]
user32.DispatchMessageW.argtypes = [ctypes.POINTER(wt.MSG)]
user32.IsWindowVisible.argtypes = [ctypes.c_void_p]
user32.IsWindowVisible.restype = wt.BOOL

PM_REMOVE = 0x0001
FADE_MS = 6 * 26        # FADE_STEPS * FADE_INTERVAL，淡出本身占的时间


def pump(deadline: float) -> None:
    """把消息队列抽干 —— WM_TIMER 只有被 Dispatch 到窗口才会走 _on_timer。"""
    msg = wt.MSG()
    while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, PM_REMOVE):
        user32.TranslateMessage(ctypes.byref(msg))
        user32.DispatchMessageW(ctypes.byref(msg))
        if time.time() > deadline:
            return


def measure_once(t: Toast, body: str, duration: int) -> tuple[float, float]:
    """显示一次浮层，返回 (首次可见的时刻, 最后可见的时刻)，单位秒（相对本次起点）。"""
    t0 = time.time()
    t.show("提示词增强器", body, "busy", duration)
    shown: float | None = None
    last: float | None = None
    while time.time() - t0 < 8.0:
        pump(t0 + 8.0)
        vis = bool(user32.IsWindowVisible(ctypes.c_void_p(t.hwnd)))
        now = time.time() - t0
        if vis:
            if shown is None:
                shown = now
            last = now
        elif shown is not None:
            break                   # 可见 -> 不可见，本轮结束
        time.sleep(0.005)
    return (shown if shown is not None else -1.0,
            last if last is not None else -1.0)


def main() -> int:
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument("--ms", type=int, default=HOLD_MS,
                    help=f"期望停留时长（毫秒），默认用 toast.HOLD_MS={HOLD_MS}")
    ap.add_argument("--rounds", type=int, default=3)
    args, _unknown = ap.parse_known_args()

    print(f"toast.HOLD_MS = {HOLD_MS} ms，本轮按 {args.ms} ms 检查（含淡出约 {FADE_MS} ms）")
    t = Toast(96)
    if not t._ensure_window():
        print("❌ 浮层窗口创建失败（需要桌面会话）")
        return 2

    floor = args.ms / 1000.0 * 0.85        # 允许定时器精度带来的少量误差
    bad: list[int] = []
    try:
        for i in range(1, args.rounds + 1):
            shown, last = measure_once(t, f"第 {i} 轮：正在增强：帮我写个爬虫", args.ms)
            span = last - shown
            ok = shown >= 0 and span >= floor
            if not ok:
                bad.append(i)
            print(f"  {'✅' if ok else '❌'} 第 {i} 轮：可见 {span * 1000:.0f} ms"
                  f"（期望 ≥ {floor * 1000:.0f} ms）")
            time.sleep(0.35)               # 轮次之间留点间隔，跟真实使用节奏接近
    finally:
        t.destroy()

    if bad:
        print(f"结论：第 {bad} 轮提前消失 —— 淡出定时器没被清干净（hide() 里 "
              f"KillTimer 漏了淡出用的那个）")
        return 1
    print(f"结论：{args.rounds} 轮都停够了，浮层时长正常")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
