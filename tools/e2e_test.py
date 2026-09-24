#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""端到端测试：真实验证「选中文本 → 按热键 → 原地替换 → 撤销」。

和 app_smoketest.py 的区别：这个测试**不注入热键**（Windows 会过滤注入输入，
触不到 RegisterHotKey），而是走 `main.py --enhance` 的跨进程命令通道；同时用
一个原生 Win32 靶窗口当"用户的编辑器"，因此真的会经过 Ctrl+C / Ctrl+V 注入。

配置目录用临时 APPDATA 隔离，不会动你真实的设置和日志。

用法：python tools/e2e_test.py
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "desktop"))

from store import Store, DEFAULT_SETTINGS   # noqa: E402
import win_core                             # noqa: E402
from toast import HOLD_MS as TOAST_HOLD_MS  # noqa: E402
from screenshot_corner import grab_hwnd_rgba  # noqa: E402

PY = sys.executable
PYW = os.path.join(os.path.dirname(PY), "pythonw.exe")
if not os.path.isfile(PYW):
    PYW = PY
MOCK_PORT = 18099
DRAFT = "帮我写个爬虫，抓取网页标题"
EXPECTED_MARK = "MOCK增强"       # mock 上游固定返回的内容里含这个标记
# 浮层平时只停 0.6s（TOAST_HOLD_MS）。这里要连着抓两次画面做像素比对，0.6s 内
# 抓不完，所以给被测实例一个环境变量把停留时长拉长 —— 本测试关心的是"浮层画得
# 对不对"，停留时长本身由 tools/dev/toast_duration_check.py 单独量。
E2E_TOAST_MS = 4000

RESULTS: list[tuple[str, bool, str]] = []


def log(msg: str) -> None:
    print(f"[e2e] {msg}", flush=True)


def check(name: str, ok: bool, detail: str = "") -> bool:
    RESULTS.append((name, ok, detail))
    print(f"  {'✅' if ok else '❌'} {name}" + (f" — {detail}" if detail else ""), flush=True)
    return ok


def wait_port(port: int, timeout: float = 8.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), 0.4):
                return True
        except OSError:
            time.sleep(0.15)
    return False


def read_json(path: str) -> dict:
    for _ in range(30):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            time.sleep(0.05)
    return {}


def send(*pairs) -> None:
    seq = [win_core._key(vk, up) for vk, up in pairs]
    arr = (win_core.INPUT * len(seq))(*seq)
    win_core.user32.SendInput(len(seq), arr, ctypes.sizeof(win_core.INPUT))


def wait_history(store: Store, since: float, timeout: float = 25.0) -> dict | None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        hist = store.load_history()
        if hist and hist[0].get("ts", 0) > since:
            return hist[0]
        time.sleep(0.2)
    return None


def inspect_toast_window() -> dict:
    """找右下角浮层窗口，报告是否存在、可见、是否落在屏幕里。

    比截图可靠：截图可能因为 DPI 虚拟化/坐标换算出偏差，而窗口状态是直接问系统。
    """
    u = win_core.user32
    u.FindWindowW.restype = ctypes.c_void_p
    u.FindWindowW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p]
    u.GetWindowRect.argtypes = [ctypes.c_void_p, ctypes.POINTER(wt.RECT)]
    u.IsWindowVisible.argtypes = [ctypes.c_void_p]
    h = u.FindWindowW(None, "PromptEnhancerToast")
    if not h:
        return {"visible": False, "on_screen": False, "rect": None, "found": False}
    rect = wt.RECT()
    u.GetWindowRect(ctypes.c_void_p(h), ctypes.byref(rect))
    sw, sh = u.GetSystemMetrics(0), u.GetSystemMetrics(1)
    return {
        "found": True,
        "hwnd": int(h),
        "visible": bool(u.IsWindowVisible(ctypes.c_void_p(h))),
        "on_screen": rect.left >= 0 and rect.top >= 0 and rect.right <= sw
                     and rect.bottom <= sh and (rect.right - rect.left) > 50,
        "rect": (rect.left, rect.top, rect.right, rect.bottom),
        "screen": (sw, sh),
    }


def inspect_toast_pixels(hwnd: int) -> dict:
    """把浮层窗口自己的画面抓下来，数一数它到底画了什么。

    只看"窗口可见"是不够的 —— 窗口可见但内容是空白的（比如正文被推到可视区
    之外）同样会让用户觉得"没有提示"。所以这里直接统计像素：
      * 浅色卡片背景占多数     → 底被刷上了
      * 左侧有色条             → 主题色画上了（busy 是灰色）
      * 存在深色文字像素       → 标题/正文真的渲染出来了
      * 纯黑像素应当为 0       → 出现成片纯黑说明有区域没被绘制（露底）
    """
    got = grab_hwnd_rgba(hwnd)
    if got is None:
        return {"ok": False, "reason": "抓不到窗口像素"}
    w, h, px = got
    total, light, dark, accent, black = w * h, 0, 0, 0, 0
    bar = max(2, w // 74)                           # 最左边那条 5px 色条
    for y in range(h):
        row = y * w * 4
        for x in range(w):
            i = row + x * 4
            r, g, b = px[i], px[i + 1], px[i + 2]
            if r > 235 and g > 235 and b > 235:
                light += 1
            elif max(r, g, b) < 150:
                dark += 1
            if r < 24 and g < 24 and b < 24:
                black += 1
            if x < bar and (max(r, g, b) - min(r, g, b) > 12 or max(r, g, b) < 200):
                accent += 1
    return {
        "ok": True, "size": (w, h),
        "light_ratio": round(light / total, 3),
        "dark": dark, "accent": accent, "black": black,
    }


def main() -> int:
    global MOCK_PORT
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        MOCK_PORT = probe.getsockname()[1]
    tmp = tempfile.mkdtemp(prefix="pe_e2e_")
    data_dir = os.path.join(tmp, "PromptEnhancer")
    os.makedirs(data_dir, exist_ok=True)
    store = Store(data_dir)

    env = dict(os.environ)
    env["APPDATA"] = tmp                      # 隔离配置目录
    env["PROMPT_ENHANCER_TOAST_MS"] = str(E2E_TOAST_MS)   # 见文件头注释
    # 隔离单实例互斥体：否则本机要是正好开着正式实例，被唤起的是它，
    # 测试实例连 instance.json 都写不出来（表现为"增强器后台启动"失败）。
    env["PROMPT_ENHANCER_INSTANCE_NAME"] = f"PromptEnhancer.SingleInstance.e2e.{uuid.uuid4().hex}"
    cfg = dict(DEFAULT_SETTINGS)
    cfg["base_url"] = f"http://127.0.0.1:{MOCK_PORT}/v1"
    cfg["api_key"] = "dummy-key"
    cfg["model"] = "mock-model"
    cfg["timeout"] = 10.0
    cfg["notify_success"] = True
    store.save_settings(cfg)

    state_path = os.path.join(tmp, "target_state.json")
    procs: list[subprocess.Popen] = []
    exe = None
    if len(sys.argv) > 2 and sys.argv[1] == "--exe":
        exe = os.path.abspath(sys.argv[2])

    def app_cmd(*extra: str) -> list[str]:
        """启动主实例（--exe 时用打包后的 exe，否则用源码）。"""
        if exe:
            return [exe, *extra]
        return [PYW, os.path.join(ROOT, "desktop", "main.py"), *extra]

    def signal_cmd(*extra: str) -> list[str]:
        """投递信号（--enhance / --undo / --quit）。

        故意固定用源码跑：第二实例只做一件事 —— 读 instance.json、
        PostMessage 给主实例、退出。用 exe 的话每次都要解压 11MB 的 onefile
        包（好几秒），会把测试时序搅乱，而这段代码跟 exe 本身无关。
        """
        return [PYW, os.path.join(ROOT, "desktop", "main.py"), *extra]

    try:
        log(f"配置目录（临时）: {data_dir}")
        check("浮层停留时长默认 0.6s（用户要求）", TOAST_HOLD_MS == 600,
              f"toast.HOLD_MS={TOAST_HOLD_MS}；本测试实例临时用 {E2E_TOAST_MS}ms")

        # ---------- 起假上游 ----------
        procs.append(subprocess.Popen(
            [PY, os.path.join(HERE, "mock_llm.py"), str(MOCK_PORT), "1.2"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        if not wait_port(MOCK_PORT):
            check("假上游启动", False)
            return 1
        check("假上游启动", True, f"127.0.0.1:{MOCK_PORT}（延迟 1.2s，便于观察过程提示）")

        # ---------- 起增强器 ----------
        procs.append(subprocess.Popen(
            app_cmd("--hidden"), env=env, cwd=ROOT,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        inst = {}
        deadline = time.time() + 12
        while time.time() < deadline:
            inst = store.read_instance()
            if inst.get("hwnd"):
                break
            time.sleep(0.2)
        if not check("增强器后台启动", bool(inst.get("hwnd")), str(inst)):
            return 1

        # ---------- 起靶窗口（保持前台）----------
        procs.append(subprocess.Popen(
            [PYW, os.path.join(HERE, "target_window.py"),
             "--text", DRAFT, "--out", state_path,
             "--select", "all", "--keep-foreground"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        st = {}
        deadline = time.time() + 10
        while time.time() < deadline:
            st = read_json(state_path)
            if st.get("is_foreground"):
                break
            time.sleep(0.15)
        if not check("靶窗口抢到前台", bool(st.get("is_foreground")), str(st.get("hwnd"))):
            return 1
        target_hwnd = int(st.get("hwnd") or 0)
        check("靶窗口已选中草稿", st.get("text") == DRAFT
              and st.get("sel_end", 0) > st.get("sel_start", 0),
              f"sel={st.get('sel_start')}..{st.get('sel_end')}")
        check("键盘焦点在编辑框上（否则 Ctrl+C 无人响应）",
              st.get("focus_hwnd") == st.get("edit"),
              f"focus={st.get('focus_hwnd')} edit={st.get('edit')}")

        # ---------- 前置自检：注入链路本身是否有效 ----------
        # 先独立验证"注入 Ctrl+C 能让选中内容进剪贴板"，这样万一后面失败，
        # 能一眼分清是注入链路的问题还是增强器逻辑的问题。
        # 靶窗口每 200ms 会重抢一次前台，注入偶尔会正好撞上，所以这里允许重试 ——
        # 这条检查是环境探针，目的是"区分故障归属"，不是被测功能本身。
        copied_ok, copied_detail = False, ""
        for attempt in range(1, 4):
            win_core.set_clipboard_text("__precheck__")
            time.sleep(0.25)
            p0 = win_core.clipboard_sequence()
            win_core.send_ctrl(win_core.VK_C)
            time.sleep(0.9)
            p1 = win_core.clipboard_sequence()
            copied = win_core.get_clipboard_text()
            if p1 != p0 and DRAFT in copied:
                copied_ok = True
                copied_detail = f"第 {attempt} 次成功：seq {p0}->{p1}"
                break
            copied_detail = (f"第 {attempt} 次未成功：seq {p0}->{p1}, "
                             f"剪贴板={copied[:24]!r}")
        check("注入链路自检：Ctrl+C 能把选中内容复制出来",
              copied_ok, copied_detail)
        if target_hwnd:
            win_core.user32.PostMessageW(ctypes.c_void_p(target_hwnd),
                                         ctypes.c_uint(0x8000 + 1), 0, 0)
        time.sleep(0.6)

        # ---------- 场景 1：正常增强并替换 ----------
        log("场景 1：选中草稿 → 触发增强 → 应当原地替换")
        # 先等启动时那条「已在后台运行」提示自己消失。不等的话它会和"正在增强"
        # 的浮层撞在一起 —— 两者高度不同，抓到的画面会一会儿一变。
        deadline = time.time() + 8
        while time.time() < deadline:
            if not inspect_toast_window().get("visible"):
                break
            time.sleep(0.2)
        t0 = time.time()
        subprocess.run(signal_cmd("--enhance"), env=env, cwd=ROOT,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        # 过程提示：确认"正在增强…"的浮层窗口真的被创建、显示、并在屏幕可见范围内
        # （第二个实例启动要花时间，所以这里等而不是固定 sleep）
        toast: dict = {}
        deadline = time.time() + 6
        while time.time() < deadline:
            toast = inspect_toast_window()
            if toast.get("visible"):
                break
            time.sleep(0.15)
        check("右下角浮层窗口已创建且可见", toast.get("visible") is True, str(toast))
        check("浮层落在屏幕可见范围内", toast.get("on_screen") is True,
              f"rect={toast.get('rect')} screen={toast.get('screen')}")
        # 光"窗口可见"说明不了问题：窗口在、但内容是空白的，用户一样看不到提示。
        # 所以把窗口自己的画面抓下来数像素，确认卡片底、左侧色条、文字都画上了。
        pix = inspect_toast_pixels(toast["hwnd"]) if toast.get("hwnd") else {"ok": False}
        check("浮层画面不是空白（卡片底已刷上）",
              pix.get("ok") and pix.get("light_ratio", 0) > 0.35, str(pix))
        check("浮层左侧主题色条已画上",
              pix.get("ok") and pix.get("accent", 0) > 0, str(pix.get("accent")))
        check("浮层里真的有文字（存在深色文字像素）",
              pix.get("ok") and pix.get("dark", 0) > 80, f"dark={pix.get('dark')}")
        # 隔一会儿再抓一次：确认画面是稳定的，不是"刚显示时还没画完"。
        # 顺带把露底（成片纯黑）也卡住 —— 有黑块说明有区域根本没被绘制。
        time.sleep(0.8)
        pix2 = inspect_toast_pixels(toast["hwnd"])
        check("浮层画面稳定（0.8 秒后再抓，尺寸与内容一致）",
              pix2.get("ok") and pix2.get("size") == pix.get("size")
              and abs(pix2.get("light_ratio", 0) - pix.get("light_ratio", 1)) < 0.05,
              f"{pix.get('size')} -> {pix2.get('size')}, "
              f"{pix.get('light_ratio')} -> {pix2.get('light_ratio')}")
        check("浮层没有未绘制的黑块（露底）",
              pix2.get("ok") and pix2.get("black", 1) == 0,
              f"纯黑像素={pix2.get('black')}, 尺寸={pix2.get('size')}")
        # 截图存到系统临时目录 —— 以前往项目根目录写 shot_toast.png，每跑一次留一个文件
        shot = os.path.join(tempfile.gettempdir(), "pe_e2e_shot_toast.png")
        subprocess.run([PYW, os.path.join(HERE, "screenshot_corner.py"),
                        "--hwnd", hex(int(toast["hwnd"])), shot],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        check("已截取浮层画面", os.path.isfile(shot), shot)

        entry = wait_history(store, t0)
        if not check("产生增强记录", entry is not None):
            return 1
        check("增强成功", bool(entry.get("ok")), str(entry.get("error") or ""))
        check("取到的是选中的草稿（不是上一次剪贴板内容）",
              entry.get("before", "").strip() == DRAFT, repr(entry.get("before", ""))[:60])
        check("来源标记为 selection", entry.get("source") == "selection",
              str(entry.get("source")))
        time.sleep(0.7)
        st = read_json(state_path)
        check("增强结果已替换掉选中的文本",
              EXPECTED_MARK in st.get("text", ""), repr(st.get("text", ""))[:70])

        # ---------- 场景 2：没有选中文本时，不应乱用剪贴板 ----------
        log("场景 2：没有选中任何文本 → 应当明确报错，而不是拿剪贴板内容去增强")
        win_core.set_clipboard_text("这是剪贴板里的旧内容，绝对不该被拿去增强")
        time.sleep(0.15)
        if target_hwnd:
            win_core.user32.PostMessageW(ctypes.c_void_p(target_hwnd),
                                         ctypes.c_uint(0x8000 + 2), 0, 0)
        time.sleep(0.8)
        st2 = read_json(state_path)
        check("靶窗口已取消选中", st2.get("sel_end", 0) == st2.get("sel_start", 0),
              f"sel={st2.get('sel_start')}..{st2.get('sel_end')}")

        t0 = time.time()
        subprocess.run(signal_cmd("--enhance"), env=env, cwd=ROOT,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        entry2 = wait_history(store, t0, timeout=12)
        if check("产生了记录", entry2 is not None):
            check("明确拒绝了这次增强", entry2.get("ok") is False,
                  str(entry2.get("error"))[:70])
            check("没有偷偷用剪贴板内容",
                  "剪贴板里的旧内容" not in entry2.get("before", ""),
                  repr(entry2.get("before", ""))[:40])

        # ---------- 场景 3：用户还按着热键修饰键 ----------
        log("场景 3：模拟用户按住 Ctrl+Alt 期间触发（复现真实按键场景）")
        if target_hwnd:
            win_core.user32.PostMessageW(ctypes.c_void_p(target_hwnd),
                                         ctypes.c_uint(0x8000 + 1), 0, 0)
        time.sleep(0.6)
        proc = None
        try:
            send((win_core.VK_CTRL, False), (win_core.VK_ALT, False))
            time.sleep(0.1)
            t0 = time.time()
            proc = subprocess.Popen(signal_cmd("--enhance"), env=env, cwd=ROOT,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            time.sleep(0.35)
            send((win_core.VK_ALT, True), (win_core.VK_CTRL, True))   # 松手
            proc.wait(timeout=15)
        finally:
            send((win_core.VK_ALT, True), (win_core.VK_CTRL, True))
        entry3 = wait_history(store, t0)
        if check("产生了记录", entry3 is not None):
            check("按住修饰键时仍然抓对了草稿",
                  entry3.get("before", "").strip() == DRAFT,
                  repr(entry3.get("before", ""))[:60])
            check("按住修饰键时增强成功", bool(entry3.get("ok")),
                  str(entry3.get("error") or "")[:70])

        # ---------- 场景 4：撤销 ----------
        log("场景 4：按撤销热键，内容应还原成草稿")
        time.sleep(0.4)
        t_before_undo = time.time()
        subprocess.run(signal_cmd("--undo"), env=env, cwd=ROOT,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(1.2)
        st4 = read_json(state_path)
        check("撤销后内容回到草稿或已把原文放进剪贴板",
              st4.get("text", "").strip() == DRAFT
              or win_core.get_clipboard_text().strip() == DRAFT,
              repr(st4.get("text", ""))[:50])

        # ---------- 日志检查 ----------
        log_path = os.path.join(data_dir, "app.log")
        tail = ""
        if os.path.isfile(log_path):
            with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                tail = f.read()[-8000:]
        errs = [ln for ln in tail.splitlines()
                if " ERROR " in ln or "Traceback" in ln or "OverflowError" in ln]
        check("运行日志无异常", not errs, (errs[0][:110] if errs else ""))
        check("日志确认已注入 Ctrl+V 替换", "[paste] 已注入 Ctrl+V" in tail)
        check("日志确认抓到了选中内容", "[enhance] 开始" in tail)
        if "_tw_state" in str(state_path):
            pass
        for ln in tail.splitlines():
            if any(k in ln for k in ("[start]", "[grab]", "[enhance] 开始", "[paste]", "[undo]")):
                print("     " + ln[:150])

    finally:
        # A onefile executable has a bootloader parent and an application child.
        # Signal the application before terminating processes so neither survives.
        if store.read_instance().get("hwnd"):
            try:
                subprocess.run(signal_cmd("--quit"), env=env, cwd=ROOT,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               timeout=15)
                if len(procs) > 1:
                    procs[1].wait(timeout=10)
            except (OSError, subprocess.SubprocessError):
                pass
        for p in procs:
            try:
                p.terminate()
            except Exception:  # noqa: BLE001
                pass
        time.sleep(0.5)
        for p in procs:
            if p.poll() is None:
                try:
                    p.kill()
                except Exception:  # noqa: BLE001
                    pass
        try:
            shutil.rmtree(tmp, ignore_errors=True)
        except Exception:  # noqa: BLE001
            pass

    print()
    failed = [n for n, ok, _ in RESULTS if not ok]
    log(f"结果：{len(RESULTS) - len(failed)}/{len(RESULTS)} 通过")
    if failed:
        log("未通过：" + "；".join(failed))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
