#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""全局热键提示词增强器（无界面版，Windows，纯标准库）。

Codex Desktop / Claude Desktop 都是闭源客户端，没有"改写输入框内容"的扩展点。
但它们都跑在同一个 Windows 桌面上，所以用系统级剪贴板 + 模拟按键就能做出和
WorkBuddy 完全一样的体验：

    选中输入框里的草稿 -> 按 Ctrl+Alt+E -> 原地变成增强后的提示词

适用于任何客户端：Codex、Claude Desktop、ChatGPT 桌面版、浏览器、IDE……
想要托盘图标 + 图形化设置界面，请用 desktop/main.py（打包后是 exe）。

用法：
    python hotkey.py                       # 默认热键 ctrl+alt+e
    python hotkey.py --hotkey ctrl+shift+e
    python hotkey.py --undo-hotkey ctrl+alt+z
    python hotkey.py --no-paste            # 只把结果放进剪贴板，不自动粘贴
    python hotkey.py --selftest            # 自检：注册热键 + 剪贴板读写

Win32 底层（剪贴板 / SendInput / 热键解析）统一放在 win_core.py，与桌面版共用。
"""

from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as wt
import datetime
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from core import enhance, load_dotenv  # noqa: E402
from win_core import (MOD_NOREPEAT, VK_C, VK_V, VK_Z, WM_HOTKEY,  # noqa: E402
                      clipboard_sequence, format_hotkey, get_clipboard_text,
                      get_input_target, last_input_tick,
                      modifier_down, parse_hotkey, send_ctrl,
                      set_clipboard_text, user32, wait_modifiers_released)

load_dotenv()

LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "enhancer.log")
HK_ENHANCE, HK_UNDO = 1, 2


def log(msg: str) -> None:
    line = f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def _beep(kind: str = "info") -> None:
    try:
        freq = {"ok": 1200, "fail": 400}.get(kind, 800)
        ctypes.windll.kernel32.Beep(freq, 90 if kind == "ok" else (200 if kind == "fail" else 60))
    except Exception:  # noqa: BLE001
        pass


class Enhancer:
    """把"抓选中 → 增强 → 粘贴回去 → 可撤销"这套流程封装起来。"""

    def __init__(self, paste: bool = True, undo_window: float = 600.0):
        self.paste = paste
        self.undo_window = undo_window
        self.last: dict | None = None

    def capture(self) -> tuple[str, str, str]:
        """返回 (文本, 来源, 原剪贴板)。没抓到选区时返回 ("", "none", 原剪贴板)。

        两个坑都在这里，改之前会表现为"增强的却是上一次复制的内容"：

        1. 按 Ctrl+Alt+E 触发时，WM_HOTKEY 是**按下瞬间**送到的，此刻 Ctrl/Alt
           物理上还按着。这时注入 Ctrl+C，目标程序收到的是 **Ctrl+Alt+C**，复制
           根本不会发生 —— 于是抓不到选区。所以必须先等修饰键松开。
        2. 不能靠"清空剪贴板再看有没有内容"来判断（会把用户的图片/富文本一起丢掉）。
           用剪贴板序号判断复制到底有没有发生。
        """
        wait_modifiers_released(1.0)
        stuck = modifier_down()
        if stuck:
            log("请松开快捷键后重试")
            return "", "none", get_clipboard_text()

        old_clip = get_clipboard_text()
        seq0 = clipboard_sequence()
        prev = old_clip
        send_ctrl(VK_C)
        deadline = time.time() + 1.6
        while time.time() < deadline:
            time.sleep(0.03)
            if seq0:
                if clipboard_sequence() != seq0:
                    break
            elif get_clipboard_text() not in ("", prev):
                break
        else:
            # 关键：**不要**悄悄退化成"用剪贴板里已有的内容"。
            # 那样用户会以为程序认错了对象（本次 bug 反馈的元凶）。
            log("没抓到选中的文本 —— 请先选中要增强的文字，再按热键")
            return "", "none", old_clip

        for _ in range(10):                  # 等目标程序把剪贴板数据写完
            text = get_clipboard_text()
            if text.strip():
                return text, "selection", old_clip
            time.sleep(0.04)
        return "", "none", old_clip

    def enhance_once(self) -> str:
        target = get_input_target()
        text, source, old_clip = self.capture()
        if not text.strip():
            _beep("fail")
            return "empty"
        if get_input_target() != target:
            log("窗口焦点已改变，请重新选中文字后增强")
            return "focus_changed"
        captured_seq = clipboard_sequence()
        captured_input = last_input_tick()
        log(f"来源={source} 原长={len(text)}")

        r = enhance(text)
        if not r.ok:
            log(f"增强失败({r.error})，保持原文不动")
            if old_clip and captured_seq and clipboard_sequence() == captured_seq:
                set_clipboard_text(old_clip)
            _beep("fail")
            return "failed"
        if r.text.strip() == text.strip():
            log("增强结果与原文相同，未做修改")
            if old_clip and captured_seq and clipboard_sequence() == captured_seq:
                set_clipboard_text(old_clip)
            return "unchanged"

        log(f"增强成功 耗时={r.elapsed_ms}ms 原长={len(text)} -> 新长={len(r.text)}")
        if captured_seq and clipboard_sequence() != captured_seq:
            log("剪贴板已有新内容，增强结果输出到控制台，请手动复制")
            print(r.text, flush=True)
            return "clipboard_changed"
        if not set_clipboard_text(r.text):
            log("写入剪贴板失败，增强结果输出到控制台，请手动复制")
            print(r.text, flush=True)
            _beep("fail")
            return "failed"
        self.last = {"before": text, "after": r.text, "ts": time.time(),
                     "pasted": False, "target": target}

        if self.paste:
            seq_written = clipboard_sequence()
            # 同样先等修饰键松开，否则 Ctrl+V 变成 Ctrl+Alt+V，粘不进去
            wait_modifiers_released(0.6)
            if (all(target) and get_input_target() == target and not modifier_down()
                    and captured_input is not None and last_input_tick() == captured_input
                    and seq_written and clipboard_sequence() == seq_written):
                send_ctrl(VK_V)
                self.last["pasted"] = True
                self.last["ts"] = time.time()
                time.sleep(0.4)
                if old_clip and clipboard_sequence() == seq_written:
                    set_clipboard_text(old_clip)
        if self.last["pasted"]:
            log(f"已替换选中内容；撤销窗口 {int(self.undo_window)} 秒内按撤销键可还原")
        else:
            log("未自动粘贴，增强结果已复制到剪贴板，请手动粘贴")
        _beep("ok")
        return "ok"

    def undo(self) -> str:
        if not self.last:
            log("没有可撤销的记录")
            _beep("fail")
            return "none"
        age = time.time() - self.last["ts"]
        wait_modifiers_released(1.0)
        if not set_clipboard_text(self.last["before"]):
            log("写入剪贴板失败，请稍后重试撤销")
            return "failed"
        target = self.last.get("target", (0, 0))
        if (age <= self.undo_window and self.last.get("pasted") and all(target)
                and get_input_target() == target and not modifier_down()):
            send_ctrl(VK_Z)
            status = "ok"
            log("已发送撤销，原文也已复制到剪贴板")
        else:
            status = "expired" if age > self.undo_window else "copied"
            log("原文已复制到剪贴板，请回到原位置手动粘贴")
        self.last = None
        _beep("ok")
        return status


def main() -> int:
    ap = argparse.ArgumentParser(description="全局热键提示词增强器")
    ap.add_argument("--hotkey", default=os.environ.get("ENHANCER_HOTKEY", "ctrl+alt+e"))
    ap.add_argument("--undo-hotkey", default=os.environ.get("ENHANCER_UNDO_HOTKEY", "ctrl+alt+z"))
    ap.add_argument("--no-paste", action="store_true", help="只把结果写进剪贴板，不自动替换")
    ap.add_argument("--undo-window", type=float, default=600.0, help="撤销有效窗口（秒）")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if sys.platform != "win32":
        print("本脚本依赖 Win32 API，仅支持 Windows", file=sys.stderr)
        return 2

    try:
        pairs = {HK_ENHANCE: parse_hotkey(args.hotkey), HK_UNDO: parse_hotkey(args.undo_hotkey)}
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2

    if args.selftest:
        ok = True
        for hid, (mods, vk) in pairs.items():
            spec = args.hotkey if hid == HK_ENHANCE else args.undo_hotkey
            got = bool(user32.RegisterHotKey(None, hid, mods | MOD_NOREPEAT, vk))
            if got:
                user32.UnregisterHotKey(None, hid)
            print(f"RegisterHotKey {format_hotkey(spec)}: {'OK' if got else 'FAIL'}")
            ok = ok and got
        probe = "剪贴板往返测试 abc 123"
        set_clipboard_text(probe)
        back = get_clipboard_text()
        print(f"Clipboard round-trip: {'OK' if back == probe else 'FAIL'} -> {back!r}")
        return 0 if (ok and back == probe) else 1

    for hid, (mods, vk) in pairs.items():
        if not user32.RegisterHotKey(None, hid, mods | MOD_NOREPEAT, vk):
            print(f"热键 #{hid} 注册失败（错误码 {ctypes.get_last_error()}），"
                  f"换个 --hotkey / --undo-hotkey 试试。", file=sys.stderr)
            return 1

    engine = Enhancer(paste=not args.no_paste, undo_window=args.undo_window)
    log(f"提示词增强器已启动：增强 {format_hotkey(args.hotkey)} / "
        f"撤销 {format_hotkey(args.undo_hotkey)}，粘贴={'关闭' if args.no_paste else '开启'}。"
        f"Ctrl+C 退出。")

    msg = wt.MSG()
    try:
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message != WM_HOTKEY:
                continue
            try:
                if msg.wParam == HK_ENHANCE:
                    engine.enhance_once()
                elif msg.wParam == HK_UNDO:
                    engine.undo()
            except Exception as e:  # noqa: BLE001
                log(f"处理异常: {type(e).__name__}: {e}")
                _beep("fail")
    except KeyboardInterrupt:
        pass
    finally:
        for hid in pairs:
            user32.UnregisterHotKey(None, hid)
        log("已退出")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
