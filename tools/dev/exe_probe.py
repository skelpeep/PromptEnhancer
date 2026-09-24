# -*- coding: utf-8 -*-
"""启动一个 PromptEnhancer.exe，把它真实弹出的窗口抓下来，用来确认「这个 exe 里到底是哪版代码」。

为什么需要它：exe 是 PyInstaller onefile，内部 PYZ 被 zlib 压缩，直接对 exe
做字符串搜索找不到源码里的中文常量（"恢复默认设置" 这类），所以「有没有带上
某次修复」没法靠 grep 判断。比对时间戳也只是间接证据 —— 唯一直接的办法是让
它跑起来，看它画出来的界面长什么样。

用法：
    python tools/dev/exe_probe.py dist/PromptEnhancer.exe
    python tools/dev/exe_probe.py <exe> --out a.png --cycle-tabs 2

抓图走**屏幕 BitBlt**（先把窗口置顶），而不是 PrintWindow：Tk 窗口在
PrintWindow 下常只画出上半部分，底部的按钮栏（保存并应用 / 恢复默认设置）
正好会缺掉，而"按钮在不在"恰恰是判断版本的关键。

注意：它用**隔离的 APPDATA** 和**独立的单实例互斥体名**启动，不会碰你自己的
配置，也不会被你已经开着的实例挡住（否则新进程会直接退出、什么窗口都没有）。
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(HERE)
ROOT = os.path.dirname(TOOLS)
sys.path.insert(0, ROOT)
sys.path.insert(0, TOOLS)

from screenshot_corner import _blit_to_png, capture_hwnd   # noqa: E402
import win_core                                            # noqa: E402

user32 = ctypes.WinDLL("user32", use_last_error=True)

HWND_TOPMOST = -1
SWP_NOSIZE = 0x0001
SWP_SHOWWINDOW = 0x0040
VK_CONTROL = 0x11
VK_TAB = 0x09


class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


def proc_path(pid: int) -> str:
    """查某个进程的映像路径。

    ⚠️ 别用"新出现的窗口"来认自己启动的进程：PyInstaller onefile 的 GUI 跑在
    bootloader 的**子进程**里（`Popen` 拿到的是父进程 pid，窗口属于另一个 pid），
    而且屏幕上随时可能有别人的同名窗口。用进程路径核对最靠得住。
    """
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = ctypes.c_ulong(1024)
        if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return buf.value
    finally:
        k32.CloseHandle(h)
    return ""


def enum_windows():
    out = []
    proc = ctypes.WINFUNCTYPE(
        ctypes.c_bool, ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong * 3))

    def cb(hwnd, lparam):
        pid = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        buf = ctypes.create_unicode_buffer(512)
        user32.GetWindowTextW(hwnd, buf, 512)
        cls = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, cls, 256)
        r = RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(r))
        out.append({
            "hwnd": int(hwnd), "pid": int(pid.value), "title": buf.value,
            "cls": cls.value, "visible": bool(user32.IsWindowVisible(hwnd)),
            "rect": (r.left, r.top, r.right, r.bottom),
        })
        return True

    user32.EnumWindows(proc(cb), None)
    return out


def bring_front(hwnd: int) -> None:
    """把窗口挪到 (0,0) 并置顶。

    置顶是关键：屏幕 BitBlt 抓的是"屏幕上看到的东西"，不置顶就可能抓到压在
    上面的别的窗口。置顶后即使没拿到前台焦点，内容也一定在最上层。
    """
    scr_w = user32.GetSystemMetrics(0)
    scr_h = user32.GetSystemMetrics(1)
    r = RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    w, h = r.right - r.left, r.bottom - r.top
    if h > scr_h or w > scr_w:            # 比屏幕还大就先缩到能看全，否则抓不全
        user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, min(w, scr_w), min(h, scr_h),
                            SWP_SHOWWINDOW)
    else:
        user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0,
                            SWP_NOSIZE | SWP_SHOWWINDOW)
    user32.SetForegroundWindow(hwnd)
    time.sleep(0.5)


def shot(hwnd: int, path: str, front: bool = True) -> int:
    if not front:
        return capture_hwnd(hwnd, path)
    bring_front(hwnd)
    r = RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    return _blit_to_png(r.left, r.top, r.right - r.left, r.bottom - r.top, path)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("exe")
    ap.add_argument("--out", default="")
    ap.add_argument("--wait", type=float, default=30.0)
    ap.add_argument("--settle", type=float, default=1.5)
    ap.add_argument("--cycle-tabs", type=int, default=0,
                    help="抓完首页后，用 Ctrl+Tab 切 N 次页签，各抓一张。"
                         "⚠️ 只对能拿到前台键盘焦点的窗口有效；实测对 Tk 的 ttk.Notebook "
                         "常常切不动（注入的按键到不了控件），可靠做法是在进程内 "
                         "notebook.select(i) 之后再抓图（见 tools/dev/ui_walkthrough.py）")
    ap.add_argument("--no-front", action="store_true",
                    help="改用 PrintWindow（不抢焦点，但 Tk 窗口常画不全）")
    args = ap.parse_args()

    exe = os.path.abspath(args.exe)
    if not os.path.isfile(exe):
        print(f"[probe] exe 不存在：{exe}")
        return 2
    out = args.out or os.path.join(tempfile.gettempdir(), "pe_exe_probe.png")

    appdata = os.path.join(tempfile.gettempdir(), "pe_probe_appdata")
    shutil.rmtree(appdata, ignore_errors=True)
    conf = os.path.join(appdata, "PromptEnhancer")
    os.makedirs(conf, exist_ok=True)
    with open(os.path.join(conf, "settings.json"), "w", encoding="utf-8") as f:
        json.dump({"base_url": "http://127.0.0.1:9/v1", "api_key": "probe-dummy",
                   "model": "probe"}, f)
    # 不要把上次的窗口尺寸/历史带进来：老配置里的 _geometry 会掩盖「按内容自适应」
    for stale in os.listdir(conf):
        if stale != "settings.json":
            try:
                os.remove(os.path.join(conf, stale))
            except OSError:
                pass

    env = dict(os.environ, APPDATA=appdata,
               PROMPT_ENHANCER_INSTANCE_NAME="pe_probe_%d" % random.randint(10 ** 8, 10 ** 9))

    before = {w["hwnd"] for w in enum_windows()}
    print(f"[probe] 启动 {exe}")
    proc = subprocess.Popen([exe], env=env, cwd=os.path.dirname(exe))
    print(f"[probe] pid={proc.pid}")

    deadline = time.time() + args.wait
    want = os.path.normcase(os.path.abspath(exe))
    mine, foreign = [], []
    while time.time() < deadline:
        if proc.poll() is not None:
            print(f"[probe] 进程已退出，returncode={proc.returncode}")
            return 3
        for w in enum_windows():
            if w["hwnd"] in before or not w["visible"] or not w["title"]:
                continue
            if os.path.normcase(proc_path(w["pid"])) == want:
                if w not in mine:
                    mine.append(w)
            elif w not in foreign:
                foreign.append(w)
        if mine:
            break
        time.sleep(0.4)

    if foreign:
        print("[probe] 以下新窗口**不属于**目标 exe，已忽略：")
        for w in foreign:
            print(f"  pid={w['pid']}  title={w['title']!r}  路径={proc_path(w['pid']) or '(读不到)'}")

    if not mine:
        print("[probe] 没等到任何窗口")
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                       capture_output=True)
        return 4

    time.sleep(args.settle)
    print("[probe] 该 exe 的窗口：")
    for w in mine:
        l, t, r, b = w["rect"]
        print(f"  hwnd={w['hwnd']}  pid={w['pid']}  {r - l}x{b - t}  "
              f"class={w['cls']}  title={w['title']!r}")

    target = max(mine, key=lambda w: (w["rect"][2] - w["rect"][0]) * (w["rect"][3] - w["rect"][1]))
    hwnd = target["hwnd"]
    base, ext = os.path.splitext(out)
    rc = 0
    try:
        shots = [(out, "页签 1（默认）")]
        for i in range(1, args.cycle_tabs + 1):
            shots.append((f"{base}-tab{i + 1}{ext}", f"Ctrl+Tab x{i}"))
        for i, (path, label) in enumerate(shots):
            if i:                          # 切到下一个页签
                win_core.send_combo([VK_CONTROL], VK_TAB)
                time.sleep(0.9)
            r = shot(hwnd, path, front=not args.no_front)
            ok = r == 0 and os.path.isfile(path)
            size = os.path.getsize(path) if ok else 0
            print(f"[probe] {label}: {'✅' if ok else '❌'} rc={r} {size} 字节 -> {path}")
            if not ok:
                rc = 5
    finally:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                       capture_output=True)
        print("[probe] 已结束该进程")

    # 把应用自己的启动日志里那几行关键信息打出来 —— 尤其是"模块来源"。
    # 只看窗口标题是不够的：标题、按钮都可能来自 exe 内部，而某个被 import 的模块
    # （ui / toast / widgets…）却可能被 %TEMP% 里的同名 .pyc 顶掉，只有程序自报
    # __file__ 才看得见。发现异常就让本脚本以非 0 退出。
    log_path = os.path.join(appdata, "PromptEnhancer", "app.log")
    print(f"[probe] 应用启动日志（{log_path}）：")
    hijack = None
    try:
        with open(log_path, encoding="utf-8", errors="replace") as f:
            for line in f.read().splitlines():
                key = ("[start]", "[ui]", "[toast]", "模块来源", "⚠")
                if any(k in line for k in key):
                    print("   ", line)
                    if "模块来源" in line and "⚠" in line:
                        hijack = line
    except OSError as e:
        print(f"    (读不到日志：{e})")
        rc = rc or 6
    if hijack:
        print("[probe] ❌ 运行时加载了 exe 之外的模块 —— 跑的代码不是包里那份")
        rc = 7
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
