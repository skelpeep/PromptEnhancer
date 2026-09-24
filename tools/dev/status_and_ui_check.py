#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""针对三处界面问题的回归自检（进程内起真实的 App + 设置窗口，不是模拟）。

覆盖：
  A. 增强失败后，设置窗口左下角不能还挂着"正在增强…"；
  B. 快捷键录入框按键即改（Ctrl → ctrl；再按 E → ctrl+e；Esc 清空）；
  C. 「恢复默认设置」把各项还原成出厂默认、且保留 API Key。

为什么要在进程内起真实 App：这三条都只在"界面状态"上体现，跨进程读不到 Tk 的
标签文字 / 输入框内容，端到端测试（tools/e2e_test.py）覆盖不到。配置目录用临时
APPDATA 隔离，不碰真实设置与日志。

用法（需要有桌面会话，会短暂出现一个托盘图标）：
    python tools/dev/status_and_ui_check.py
退出码 0 = 全通过。
"""

from __future__ import annotations

import os
import shutil
import socket
import sys
import tempfile
import threading
import time
import tkinter.messagebox as mbox

HERE = os.path.dirname(os.path.abspath(__file__))     # tools/dev
TOOLS = os.path.dirname(HERE)                         # tools
ROOT = os.path.dirname(TOOLS)                         # 项目根
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "desktop"))
sys.path.insert(0, HERE)
sys.path.insert(0, TOOLS)

from store import DEFAULT_SETTINGS, Store     # noqa: E402
import ui                                     # noqa: E402
import win_core                               # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    RESULTS.append((name, ok, detail))
    print(f"  {'✅' if ok else '❌'} {name}" + (f" — {detail}" if detail else ""), flush=True)
    return ok


# ---------------------------------------------------------------- 起一个"卡住不答"的上游
def start_hanging_server() -> tuple[socket.socket, int]:
    """accept 之后一个字节都不回：让增强请求一直挂到超时，方便观察进行中的状态。"""
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(5)
    port = srv.getsockname()[1]

    def loop():
        while True:
            try:
                conn, _ = srv.accept()
            except OSError:
                return
            threading.Thread(target=_hold, args=(conn,), daemon=True).start()

    def _hold(conn):
        try:
            conn.recv(65536)
            time.sleep(30)          # 故意不响应
        except OSError:
            pass
        finally:
            try:
                conn.close()
            except OSError:
                pass

    threading.Thread(target=loop, daemon=True).start()
    return srv, port


# ---------------------------------------------------------------- 按键
def press(root, entry, keysym: str) -> None:
    entry.event_generate("<KeyPress>", keysym=keysym)
    root.update()


def descendants(w) -> list:
    out = []
    for c in w.winfo_children():
        out.append(c)
        out.extend(descendants(c))
    return out


# 底部操作栏上的按钮。这些文字在窗口里唯一，用来把"底部栏"从一堆按钮里认出来。
BOTTOM_BUTTONS = ("恢复默认设置", "保存并应用", "关闭到后台", "退出程序")


def clipped_buttons(win) -> list:
    """找出底部操作栏里"看不到"的按钮，返回 [(文字, 实际底边 或 '未显示', 窗口高)]。

    为什么要专门查这个：pack 分配空间是"谁先 pack 谁先拿"，窗口比内容矮时，
    排在**后面**的控件会被整个裁掉 —— 底部操作栏（保存并应用 / 恢复默认设置）
    就是这么消失的。高 DPI 下字体放大、窗口没同步放大，特别容易踩到。

    ⚠️ 只查底部这四个按钮，不要泛泛地查"所有 TButton"：没被选中的页签里的按钮
    （测试连接、清空历史……）本来就是未映射状态，那样查会一直误报。
    判据两条：按钮必须已映射；底边不能超出窗口。
    """
    base_top = win.winfo_rooty()
    win_h = win.winfo_height()
    bad = []
    for c in descendants(win):
        if c.winfo_class() != "TButton":
            continue
        try:
            text = str(c.cget("text"))
        except Exception:  # noqa: BLE001
            continue
        if text not in BOTTOM_BUTTONS:
            continue
        if not c.winfo_ismapped():
            bad.append((text, "未显示（被裁掉）", win_h))
            continue
        bottom = c.winfo_rooty() - base_top + c.winfo_height()
        if bottom > win_h + 2:
            bad.append((text, bottom, win_h))
    return bad


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="pe_ui_")
    os.environ["APPDATA"] = tmp                      # 隔离配置目录
    srv, port = start_hanging_server()

    cfg = dict(DEFAULT_SETTINGS)
    cfg["base_url"] = f"http://127.0.0.1:{port}/v1"
    cfg["api_key"] = "test-placeholder-key"
    cfg["model"] = "custom-model-from-user"
    cfg["timeout"] = 2.0
    cfg["min_len"] = 1
    cfg["hotkey_enhance"] = "ctrl+shift+9"           # 故意不是默认值
    cfg["undo_window_sec"] = 300
    Store(os.path.join(tmp, "PromptEnhancer")).save_settings(cfg)

    app = None
    try:
        from main import EnhancerApp                  # 延迟导入：要先把 APPDATA 换掉
        app = EnhancerApp(show_window=False)
        app.show_settings()
        app.root.update()
        win, root = app.win, app.root
        print(f"设置窗口已就绪（临时配置目录 {os.path.dirname(Store().settings_path)}）\n")

        # ---------------- A. 失败后不能留着"正在增强…" ----------------
        print("A. 增强失败后的左下角状态")
        entry = win.vars["hotkey_enhance"]
        seen_busy = False
        app.start_enhance(preset_text="帮我写个爬虫，抓取网页标题", manual=True)
        deadline = time.time() + 20
        while time.time() < deadline:
            root.update()
            if app.busy and win.lbl_status.cget("text").startswith("正在增强"):
                seen_busy = True
            if not app.busy and time.time() > 0:      # busy 结束后再等状态栏落定
                break
            time.sleep(0.05)
        # busy 先于 _set_status 结束（_on_enhance_done 第一行就置 False），
        # 所以再抽一会儿事件循环，等跨线程的 notify（SendMessage）和状态写入完成
        for _ in range(60):
            root.update()
            time.sleep(0.05)
            if not win.lbl_status.cget("text").startswith("正在增强"):
                break
        status = win.lbl_status.cget("text")
        check("过程中确实出现过「正在增强…」（否则本项是空过）", seen_busy, f"状态栏={status!r}")
        check("失败后状态栏变成了失败信息（不再卡在「正在增强…」）",
              status.startswith("增强失败") and "正在增强" not in status, repr(status))

        # ---------------- B. 快捷键按键录入 ----------------
        print("\nB. 快捷键录入框：按什么存什么")
        entry = win.vars["hotkey_enhance"]
        # ⚠️ 必须先切到「快捷键」页。窗口默认停在「模型」页，没选中的页签里的控件
        # 处于未映射状态，Tk 会**直接丢弃**发给未映射窗口的 event_generate 事件 ——
        # 不切页的话下面所有按键都会"按了没反应"，白跑。
        win.nb.select(1)
        root.update()
        check("快捷键页已显示（否则按键事件会被 Tk 丢掉）",
              bool(entry.winfo_ismapped()), f"ismapped={entry.winfo_ismapped()}")
        entry.set_hotkey("ctrl+alt+e")
        root.update()
        entry.focus_force()
        root.update()
        seq = [
            ("Control_L", "ctrl", "只按修饰键：框里就该是 ctrl"),
            ("e", "ctrl+e", "接一个主键：ctrl+e"),
            ("k", "ctrl+k", "再按别的键换掉主键"),
            ("Shift_L", "ctrl+shift+k", "再按一个修饰键会累加上去"),
            ("Prior", "ctrl+shift+pageup", "PageUp 这类编辑键也认得"),
            ("Caps_Lock", "ctrl+shift+pageup", "不支持的键不改变内容（只给提示）"),
            ("Escape", "", "Esc 清空"),
            ("Control_L", "ctrl", "清空后重新按 Ctrl"),
            ("Alt_L", "ctrl+alt", "接着按 Alt"),
            ("F8", "ctrl+alt+f8", "最后接 F8：ctrl+alt+f8"),
        ]
        for keysym, expect, why in seq:
            press(root, entry, keysym)
            got = entry.get_hotkey()
            check(f"{why}（按 {keysym} → {expect or '空'}）", got == expect, f"实际 {got!r}")
        check("最后这个值能被解析成合法热键（能真的注册）",
              bool(win_core.parse_hotkey(entry.get_hotkey())),
              repr(entry.get_hotkey()))
        entry.set_hotkey("ctrl")
        root.update()
        bad = win._invalid_hotkey()
        check("只按修饰键就去保存会被拦下、并说清楚缺什么（不用等注册失败才报错）",
              bool(bad) and "缺少主键" in bad, bad)
        entry.set_hotkey("ctrl+alt+e")
        root.update()
        check("合法热键不会被误拦", win._invalid_hotkey() == "", win._invalid_hotkey())

        # ---------------- C. 恢复默认设置 ----------------
        print("\nC. 恢复默认设置")
        win.vars["model"].delete(0, "end")
        win.vars["model"].insert(0, "custom-model-from-user")
        win.vars["base_url"].delete(0, "end")
        win.vars["base_url"].insert(0, "http://example.invalid/v1")
        win.vars["undo_window_sec"].delete(0, "end")
        win.vars["undo_window_sec"].insert(0, "300")
        win.checks["fallback_clipboard"].set(True)
        win.checks["notify_success"].set(False)
        win.txt_user.delete("1.0", "end")
        win.txt_user.insert("1.0", "自定义模板 {input}")
        root.update()

        orig = mbox.askyesno
        mbox.askyesno = lambda *a, **k: True          # 自动确认对话框
        try:
            win.restore_defaults()
        finally:
            mbox.askyesno = orig
        root.update()
        s = win.collect()
        check("模型回到默认", s["model"] == DEFAULT_SETTINGS["model"], repr(s["model"]))
        check("API 地址回到默认", s["base_url"] == DEFAULT_SETTINGS["base_url"],
              repr(s["base_url"]))
        check("快捷键回到默认 ctrl+alt+e",
              s["hotkey_enhance"] == DEFAULT_SETTINGS["hotkey_enhance"],
              repr(s["hotkey_enhance"]))
        check("行为开关回到默认（fallback_clipboard 关、notify_success 开）",
              s["fallback_clipboard"] is False and s["notify_success"] is True,
              f"fallback={s['fallback_clipboard']} notify={s['notify_success']}")
        check("撤销有效窗口回到默认",
              int(s["undo_window_sec"]) == int(DEFAULT_SETTINGS["undo_window_sec"]),
              repr(s["undo_window_sec"]))
        dsys, dusr = app.default_templates()
        check("模板回到内置默认（界面里已不留自定义模板）",
              s["system_template"] == "" and s["user_template"] == "",
              f"system={len(s['system_template'])} user={len(s['user_template'])} 字")
        check("API Key 被保留（一键重置不该把有成本的东西抹掉）",
              s["api_key"] == "test-placeholder-key", repr(s["api_key"]))
        check("界面里也提示了还要点「保存并应用」",
              "保存并应用" in win.lbl_status.cget("text"), repr(win.lbl_status.cget("text")))

        # ---------------- D. 底部操作栏不能被裁掉 ----------------
        print("\nD. 底部操作栏（保存并应用 / 恢复默认设置）")
        win.geometry(win._default_geometry())
        root.update()
        bad = clipped_buttons(win)
        check("默认尺寸下底部按钮都在窗口内（含「恢复默认设置」）", not bad, str(bad))
        button_texts = [c.cget("text") for c in descendants(win)
                        if c.winfo_class() == "TButton" and c.winfo_ismapped()]
        check("四个按钮都在：恢复默认设置 / 保存并应用 / 关闭到后台 / 退出程序",
              all(t in button_texts for t in ("恢复默认设置", "保存并应用",
                                              "关闭到后台", "退出程序")),
              str(button_texts))
        # 把窗口压到最小尺寸：这时候页签区自己收缩，底部栏必须还在
        win.geometry(f"{int(700 * win.s)}x{int(520 * win.s)}")
        root.update()
        bad = clipped_buttons(win)
        check("窗口缩到最小尺寸时底部按钮也没被裁掉", not bad, str(bad))
    finally:
        try:
            srv.close()
        except OSError:
            pass
        if app is not None:
            try:
                app.quit_app()
            except Exception:  # noqa: BLE001
                pass
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    failed = [n for n, ok, _ in RESULTS if not ok]
    print(f"结果：{len(RESULTS) - len(failed)}/{len(RESULTS)} 通过")
    if failed:
        print("未通过：" + "；".join(failed))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
