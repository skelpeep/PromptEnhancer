#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""最小实验：SendInput 注入的按键，到底能不能到达前台窗口？

做法：起一个原生靶窗口（EDIT 控件、保持前台、焦点在编辑框），
先注入一个普通字符 X，看编辑框内容有没有变；
再注入 Ctrl+C，看剪贴板序号有没有变。

如果连 X 都打不进去，说明是环境层面拦住了注入（UIPI / 沙箱），
跟"增强器逻辑"无关 —— 那就要换实现路线。

用法：python tools/dev/inject_check.py
"""

from __future__ import annotations

import ctypes
import json
import os
import socket
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))     # tools/dev
TOOLS = os.path.dirname(HERE)                         # tools
ROOT = os.path.dirname(TOOLS)                         # 项目根
sys.path.insert(0, ROOT)

import win_core  # noqa: E402

PY = sys.executable
PYW = os.path.join(os.path.dirname(PY), "pythonw.exe")
if not os.path.isfile(PYW):
    PYW = PY


def log(m: str) -> None:
    print(m, flush=True)


def read_state(p: str) -> dict:
    for _ in range(20):
        try:
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            time.sleep(0.05)
    return {}


def inject(*pairs) -> int:
    seq = [win_core._key(vk, up) for vk, up in pairs]
    arr = (win_core.INPUT * len(seq))(*seq)
    ctypes.set_last_error(0)
    n = win_core.user32.SendInput(len(seq), arr, ctypes.sizeof(win_core.INPUT))
    err = ctypes.get_last_error()
    if n != len(seq):
        log(f"    ⚠️ SendInput 返回 {n}/{len(seq)}，err={err}")
    return n


def reselect(hwnd) -> None:
    """让靶窗口恢复原文并重新全选（Ctrl+C 要有选区才会写剪贴板）。"""
    if not hwnd:
        return
    win_core.user32.PostMessageW(ctypes.c_void_p(int(hwnd)),
                                ctypes.c_uint(0x8000 + 1), 0, 0)
    time.sleep(0.6)


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="pe_inj_")
    state = os.path.join(tmp, "s.json")
    proc = subprocess.Popen(
        [PYW, os.path.join(TOOLS, "target_window.py"),
         "--text", "abcdefghijklmn", "--out", state, "--select", "all",
         "--keep-foreground"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        st = {}
        deadline = time.time() + 10
        while time.time() < deadline:
            st = read_state(state)
            if st.get("is_foreground") and st.get("focus_hwnd") == st.get("edit"):
                break
            time.sleep(0.15)
        log(f"靶窗口 hwnd={st.get('hwnd')} edit={st.get('edit')} "
            f"foreground={st.get('is_foreground')} "
            f"focus_on_edit={st.get('focus_hwnd') == st.get('edit')}")
        if not (st.get("is_foreground") and st.get("focus_hwnd") == st.get("edit")):
            log("❌ 靶窗口没能拿到前台+键盘焦点，实验不成立")
            return 1

        # --- 实验 1：直接 SendInput（确认它是否被环境拦截）---
        before = read_state(state).get("text", "")
        log(f"实验 1：直接 SendInput 注入字符 X（当前内容 {before!r}，选区已全选）")
        inject((0x58, False), (0x58, True))     # VK_X
        time.sleep(0.6)
        after = read_state(state).get("text", "")
        log(f"  注入后内容 = {after!r}  "
            + ("SendInput 正常" if after != before else "SendInput 被环境拦截（根因）"))

        # --- 实验 2：走 win_core.send_combo（内置 keybd_event 回退）---
        before2 = read_state(state).get("text", "")
        log("实验 2：改用 win_core.send_combo 注入字符 X（带自动回退）")
        win_core.send_combo([], 0x58)
        time.sleep(0.6)
        after2 = read_state(state).get("text", "")
        log(f"  注入后内容 = {after2!r}  "
            + ("✅ 回退链路生效，按键到达了窗口" if after2 != before2 else "❌ 仍然没到达"))

        # --- 实验 3：直接 SendInput Ctrl+C ---
        reselect(st.get("hwnd"))
        win_core.set_clipboard_text("__marker__")
        time.sleep(0.15)
        seq0 = win_core.clipboard_sequence()
        log(f"实验 3：直接 SendInput 注入 Ctrl+C（剪贴板序号 {seq0}）")
        inject((win_core.VK_CTRL, False), (win_core.VK_C, False),
               (win_core.VK_C, True), (win_core.VK_CTRL, True))
        time.sleep(0.7)
        seq1 = win_core.clipboard_sequence()
        log(f"  序号 {seq0} -> {seq1}  "
            + ("复制生效" if seq1 != seq0 else "复制没发生（被拦）"))

        # --- 实验 4：走 win_core.send_ctrl ---
        reselect(st.get("hwnd"))
        win_core.set_clipboard_text("__marker2__")
        time.sleep(0.15)
        seq2 = win_core.clipboard_sequence()
        log("实验 4：改用 win_core.send_ctrl 注入 Ctrl+C（带自动回退）")
        win_core.send_ctrl(win_core.VK_C)
        time.sleep(0.7)
        seq3 = win_core.clipboard_sequence()
        txt3 = win_core.get_clipboard_text()
        log(f"  序号 {seq2} -> {seq3}，剪贴板 = {txt3[:40]!r}  "
            + ("✅ 复制成功" if seq3 != seq2 else "❌ 复制没发生"))
    finally:
        proc.terminate()
        time.sleep(0.4)
        if proc.poll() is None:
            proc.kill()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
