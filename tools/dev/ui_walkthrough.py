#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""界面走查 + 文档截图：起一份隔离实例，逐页抓图，直接更新 docs/ 里的界面图。

    python tools/dev/ui_walkthrough.py

产物（主 README「界面实拍」引用的就是这几张）：
    docs/ui-1-model.png              模型页
    docs/ui-2-hotkeys.png            快捷键页
    docs/ui-3-prompt-template.png    提示词模板页

为什么是"自己起一份实例"而不是去截正在运行的那个：
  * 按窗口标题找窗口会找到本机正在跑的正式实例（截出来是**老界面**，越改越不对）；
  * 不能碰用户自己的配置。所以用临时 APPDATA + 独立互斥体名起一份隔离实例，
    预填一份假配置，改完代码重跑就能拿到最新界面图。

抓图走 PrintWindow（`grab_hwnd_rgba`）：不需要窗口在前台、不抢焦点、
被别的窗口盖住也能抓到。用 winfo_id() 直接拿到 Tk 窗口句柄，不按标题找。

用法提示：跑完自己看一眼这三张图 —— 页面内容被裁掉、控件挤在一起，图上立刻能看出来。
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))     # tools/dev
TOOLS = os.path.dirname(HERE)                         # tools
ROOT = os.path.dirname(TOOLS)                         # 项目根
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "desktop"))
sys.path.insert(0, HERE)
sys.path.insert(0, TOOLS)

from screenshot_corner import capture_hwnd      # noqa: E402
from store import DEFAULT_SETTINGS, Store       # noqa: E402

# (页签序号, 输出文件名) —— 只截主 README 引用的那三张，别往 docs/ 里堆散图
TABS = [(0, "ui-1-model.png"), (1, "ui-2-hotkeys.png"), (2, "ui-3-prompt-template.png")]


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="pe_shots_")
    os.environ["APPDATA"] = tmp                            # 隔离配置目录
    os.environ["PROMPT_ENHANCER_INSTANCE_NAME"] = "PromptEnhancer.SingleInstance.shots"

    cfg = dict(DEFAULT_SETTINGS)
    cfg["api_key"] = "demo-placeholder"                    # 假值，只为图上不空着
    cfg["model"] = "gpt-4o-mini"
    Store(os.path.join(tmp, "PromptEnhancer")).save_settings(cfg)

    app = None
    try:
        from main import EnhancerApp, enable_dpi_awareness
        # 和正式启动一样先声明 DPI 感知：不声明的话本进程按 100% 布局，系统再把
        # 整张位图拉伸 150%，出图发虚，而且量出来的窗口尺寸也不是用户看到的那套。
        enable_dpi_awareness()
        app = EnhancerApp(show_window=False)
        app.show_settings()
        win, root = app.win, app.root
        # 用程序自己的默认尺寸出图（别自己另设一个更小的，否则截到的是"窗口被拖小
        # 之后"的非常规样子，图上看不出真实排版）
        win.geometry(win._default_geometry())
        root.update()

        failed = []
        for index, name in TABS:
            win.nb.select(index)
            root.update()
            out = os.path.join(ROOT, "docs", name)
            # winfo_id() 就是 Tk 窗口的 HWND，不用按标题去猜（本机可能有别的同名窗口）
            rc = capture_hwnd(int(win.winfo_id()), out)
            ok = rc == 0 and os.path.isfile(out)
            n = os.path.getsize(out) if ok else 0
            print(f"  {'✅' if ok else '❌'} 页签 {index} -> {name}"
                  + (f"（{n} 字节）" if ok else f"（rc={rc}）"))
            if not ok:
                failed.append(name)
        return 1 if failed else 0
    finally:
        if app is not None:
            try:
                app.quit_app()
            except Exception:  # noqa: BLE001
                pass
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
