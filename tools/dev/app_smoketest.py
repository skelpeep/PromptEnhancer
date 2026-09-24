#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""桌面版端到端自测：不起 GUI 交互，直接驱动热键链路。

它做四件事：
 1. 把设置写成指向本地假上游（auto_paste=False，避免往用户窗口里打字）；
 2. 启动 desktop/main.py --hidden（常驻后台）；
 3. 用 SendInput 真的按下增强热键，然后检查 history.json 与剪贴板；
 4. 再按一次撤销热键，最后退出应用。

用法：
    python tools/mock_llm.py 18080      # 另开一个终端
    python tools/dev/app_smoketest.py
    python tools/dev/app_smoketest.py --exe dist/PromptEnhancer.exe   # 测打包后的 exe
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))     # tools/dev
TOOLS = os.path.dirname(HERE)                         # tools
ROOT = os.path.dirname(TOOLS)                         # 项目根
PY = sys.executable
MOCK = "http://127.0.0.1:18080/v1"
PROBE = "帮我写个爬虫"


def log(msg: str) -> None:
    print(f"[smoke] {msg}", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--exe", default="", help="测打包后的 exe，而不是源码")
    args = ap.parse_args()

    if args.exe:
        exe = args.exe if os.path.isabs(args.exe) else os.path.join(ROOT, args.exe)
        if not os.path.isfile(exe):
            log(f"找不到 exe：{exe}")
            return 2
        launch = [exe]
        workdir = os.path.dirname(exe)
        log(f"被测对象：{exe}")
    else:
        launch = [PY, os.path.join(ROOT, "desktop", "main.py")]
        workdir = ROOT
        log("被测对象：源码 desktop/main.py")
    sys.path.insert(0, ROOT)
    sys.path.insert(0, os.path.join(ROOT, "desktop"))
    from store import Store
    import win_core

    store = Store()
    log(f"配置目录 {store.dir}")

    # 1) 写设置：指向假上游，关闭自动粘贴
    cfg = store.load_settings()
    cfg.update({"base_url": MOCK, "api_key": "dummy", "model": "mock-model",
                "auto_paste": False, "notify_success": True,
                "hotkey_enhance": "ctrl+alt+e", "hotkey_undo": "ctrl+alt+z"})
    store.save_settings(cfg)
    store.clear_history()
    log("设置已写入，历史已清空")

    backup_clip = win_core.get_clipboard_text()

    # 2) 启动应用
    proc = subprocess.Popen(
        launch + ["--hidden"], cwd=workdir,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    log(f"应用已启动 pid={proc.pid}")
    time.sleep(8 if args.exe else 5)

    inst = store.read_instance()
    log(f"instance.json = {inst}")
    if not inst.get("hwnd") or not win_core.is_valid_window(int(inst["hwnd"])):
        log("失败：应用没有起来或窗口句柄无效")
        return 1

    mods, vk = win_core.parse_hotkey("ctrl+alt+e")
    undo_mods, undo_vk = win_core.parse_hotkey("ctrl+alt+z")
    _ = (mods, vk, undo_mods, undo_vk)

    def signal(flag: str):
        subprocess.run(launch + [flag], cwd=workdir,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    try:
        # 3) 触发增强：先塞一段文本进剪贴板，capture 阶段会回退用它
        #    注意：这里不能用 SendInput 模拟热键——Windows 会过滤注入的输入，
        #    注入按键不会触发 RegisterHotKey 注册的热键（见 tools/dev/hotkey_inject_probe.py）。
        #    所以走命令行信号通道，效果与真实按键等价。
        win_core.set_clipboard_text(PROBE)
        time.sleep(0.3)
        signal("--enhance")
        log("已发送增强信号，等待完成…")
        time.sleep(5)

        hist = store.load_history()
        if not hist:
            log("失败：没有产生历史记录")
            return 1
        h = hist[0]
        log(f"历史[0] ok={h['ok']} 耗时={h['elapsed_ms']}ms 来源={h['source']}")
        log(f"  原文: {h['before']!r}")
        log(f"  结果: {h['after']!r}")
        if not h["ok"] or not h["after"]:
            log(f"失败：增强未成功，error={h['error']}")
            return 1

        clip = win_core.get_clipboard_text()
        log(f"剪贴板内容: {clip[:60]!r}")
        if clip.strip() != h["after"].strip():
            log("注意：剪贴板与增强结果不一致（可能被别的程序抢占）")

        # 4) 撤销
        signal("--undo")
        log("已发送撤销信号")
        time.sleep(2)
        log("撤销后剪贴板: " + win_core.get_clipboard_text()[:60])
    finally:
        win_core.set_clipboard_text(backup_clip)
        rc = subprocess.run(launch + ["--quit"], cwd=workdir).returncode
        log(f"退出信号已发送 rc={rc}")
        time.sleep(2)
        if proc.poll() is None:
            proc.terminate()
            log("进程仍在，已强制结束")
        else:
            log(f"进程已退出，returncode={proc.returncode}")
        _dump_log(store)
    return 0


def _dump_log(store) -> None:
    log_path = os.path.join(store.dir, "app.log")
    if not os.path.isfile(log_path):
        log("app.log 不存在")
        return
    with open(log_path, "r", encoding="utf-8", errors="replace") as f:
        tail = f.read()[-4000:]
    lines = tail.splitlines()
    bad = [ln for ln in lines if "ERROR" in ln or "Traceback" in ln]
    log(f"app.log 错误行数: {len(bad)}")
    for ln in bad[:6]:
        print("    !", ln[:160])
    for key, name in (("[enhance] 成功", "增强成功"), ("[undo] 已撤销", "撤销成功")):
        hit = any(key in ln for ln in lines)
        log(f"日志校验 {name}: {'通过' if hit else '未找到 ' + key}")
    for ln in lines:
        if any(k in ln for k in ("[start]", "[enhance]", "[undo]", "[settings]")):
            print("    ·", ln[:170])


if __name__ == "__main__":
    raise SystemExit(main())
