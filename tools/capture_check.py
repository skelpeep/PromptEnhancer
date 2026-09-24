#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""自检 hotkey.py（无界面版）的"抓选中"逻辑。

这是"增强用的却是上一次复制的内容"这个 bug 的回归测试。要卡住两件事：
  1. 有选中时，抓到的是**选中的草稿**，不是剪贴板里已有的旧内容；
  2. 没选中时，必须明确返回"没抓到"，**绝对不能**退回剪贴板旧内容。
同时确认旧剪贴板内容会被原样带出（后面好还原，不弄乱用户剪贴板）。

用法：python tools/capture_check.py
"""

from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import win_core                       # noqa: E402
from hotkey import Enhancer           # noqa: E402

PY = sys.executable
PYW = os.path.join(os.path.dirname(PY), "pythonw.exe")
if not os.path.isfile(PYW):
    PYW = PY
DRAFT = "帮我写个爬虫，抓取网页标题"
DECOY = "这是剪贴板里的旧内容，绝对不该被拿去增强"

MSG_SELECT_ALL = 0x8000 + 1
MSG_CLEAR_SEL = 0x8000 + 2

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    RESULTS.append((name, ok, detail))
    print(f"  {'✅' if ok else '❌'} {name}" + (f" — {detail}" if detail else ""),
          flush=True)
    return ok


def read_state(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return {}


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="pe_capture_")
    state = os.path.join(tmp, "state.json")
    proc = subprocess.Popen(
        [PYW, os.path.join(HERE, "target_window.py"),
         "--text", DRAFT, "--out", state, "--select", "all", "--keep-foreground"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        st: dict = {}
        deadline = time.time() + 10
        while time.time() < deadline:
            st = read_state(state)
            if st.get("is_foreground") and st.get("focus_hwnd") == st.get("edit"):
                break
            time.sleep(0.15)
        if not check("靶窗口抢到前台且键盘焦点在编辑框",
                     bool(st.get("is_foreground"))
                     and st.get("focus_hwnd") == st.get("edit"),
                     f"fg={st.get('is_foreground')} "
                     f"focus={st.get('focus_hwnd')} edit={st.get('edit')}"):
            return 1
        check("靶窗口已选中草稿", st.get("text") == DRAFT
              and st.get("sel_end", 0) > st.get("sel_start", 0),
              f"sel={st.get('sel_start')}..{st.get('sel_end')}")

        enh = Enhancer()

        # ---------- 场景 A：有选中，剪贴板里放一段"上一次复制的内容"当诱饵 ----------
        # 靶窗口每 200ms 会重抢一次前台，注入的 Ctrl+C 撞上就丢一次 —— 这是测试
        # 自身的时序问题，不是产品问题。所以「完全没抓到」时重试几次；
        # 但如果抓到的是别的东西，说明是真 bug，立刻跳出让它失败。
        text = source = old = ""
        for _ in range(3):
            win_core.set_clipboard_text(DECOY)
            time.sleep(0.25)
            text, source, old = enh.capture()
            if text.strip():
                break
            time.sleep(0.5)
        check("A 抓到的是选中的草稿（不是剪贴板旧内容）",
              text.strip() == DRAFT, repr(text)[:60])
        check("A 来源标记为 selection", source == "selection", source)
        check("A 旧剪贴板内容被原样带出（好还原）", old == DECOY, repr(old)[:40])

        # ---------- 场景 B：取消选中，剪贴板里仍然有诱饵 ----------
        win_core.user32.PostMessageW(ctypes.c_void_p(int(st["hwnd"])),
                                     ctypes.c_uint(MSG_CLEAR_SEL), 0, 0)
        time.sleep(0.8)
        st2 = read_state(state)
        check("B 靶窗口已取消选中",
              st2.get("sel_end", 0) == st2.get("sel_start", 0),
              f"sel={st2.get('sel_start')}..{st2.get('sel_end')}")

        win_core.set_clipboard_text(DECOY)
        time.sleep(0.2)
        text2, source2, _ = enh.capture()
        check("B 没选中时明确返回空（不退回剪贴板）",
              not text2.strip() and source2 == "none",
              f"text={text2!r} source={source2}")
        check("B 没有偷偷用剪贴板内容",
              "剪贴板里的旧内容" not in text2, repr(text2)[:40])

        print()
        failed = [n for n, ok, _ in RESULTS if not ok]
        print(f"结果：{len(RESULTS) - len(failed)}/{len(RESULTS)} 通过")
        if failed:
            print("未通过：" + "；".join(failed))
        return 1 if failed else 0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:  # noqa: BLE001
            proc.kill()


if __name__ == "__main__":
    raise SystemExit(main())
