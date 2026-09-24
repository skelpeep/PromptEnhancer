#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""提示词增强器 · 桌面版（托盘常驻 + 全局热键 + 原地替换 + 可撤销 + 图形化设置）

    python main.py              正常启动（已有实例则唤起设置窗口）
    python main.py --show       启动并直接打开设置窗口
    python main.py --quit       退出正在运行的实例

设计要点：
* tkinter 跑主线程，Win32 消息循环（热键 + 托盘）跑独立线程，两者靠队列通信；
* 所有网络请求、按键模拟都在工作线程，界面与热键永不卡住；
* 失败一律静默降级 + 气泡提示，绝不在用户输入框里留垃圾。
"""

from __future__ import annotations

import argparse
import ctypes
import logging
import os
import queue
import sys
import threading
import time
import tkinter as tk
from tkinter import messagebox

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
FROZEN = bool(getattr(sys, "frozen", False))

# ⚠️⚠️ 冻结尾的 sys.path 只许"减法"，绝不许把 ROOT 加进去 —— 这个坑很隐蔽，记在这：
#
# onefile 解包后，入口脚本的 __file__ 是 %TEMP%\_MEIxxxx\main.py，于是
#     HERE = %TEMP%\_MEIxxxx        （正常，就是 _MEIPASS）
#     ROOT = %TEMP%                 （**是 %TEMP% 本身**）
# 原来的写法无条件 sys.path.insert(0, p)，等于把 %TEMP% 放到了 sys.path 最前面。
# 而 sys.meta_path 里 PyInstaller 的 FrozenImporter 排在标准 PathFinder **之后**，
# 所以文件系统优先：只要 %TEMP% 根目录恰好存在同名的 ui.pyc / toast.pyc / core.pyc
# （任何编译产物、任何工具留下的残留都行），运行时就会加载那一份，而不是 exe 里
# 打进 PYZ 的那份。
#
# 症状特别能骗人：exe 是刚打的、版本号/构建时间都对（那些由入口脚本 main 提供），
# 但界面与交互全是旧版 —— 用 exe_contains 解包核对 PYZ 内容也一切正常，因为
# exe 里确实是新的，是**运行时**换了一个来源。只有看模块的 __file__ 才抓得住。
#
# 所以：冻结尾把 ROOT（=%TEMP%）以及 %TEMP% / %TMP% 本身清出 sys.path，只有源码
# 运行才注入 HERE / ROOT。
#
# 注意 HERE（=_MEIPASS）要留着：它是 bootloader 自己新建的解包目录，里面只会有本次
# 打包进 exe 的东西，属于"自己的地盘"；去掉它反而会偏离 PyInstaller 的默认布局，
# 让 importlib.resources / pkgutil 这类按 sys.path 找东西的库出怪问题。
if FROZEN:
    bad = {os.path.normcase(os.path.abspath(p)) for p in (ROOT,)}
    for _tmp_key in ("TEMP", "TMP"):
        _tmp = os.environ.get(_tmp_key)
        if _tmp:
            bad.add(os.path.normcase(os.path.abspath(_tmp)))
    sys.path[:] = [p for p in sys.path
                   if os.path.normcase(os.path.abspath(p or os.getcwd())) not in bad]
    if HERE not in sys.path:          # 兜底：_MEIPASS 得留着（PyInstaller 本来也会加）
        sys.path.append(HERE)
else:
    for p in (HERE, ROOT):
        if p not in sys.path:
            sys.path.insert(0, p)

from core import enhance, load_templates                      # noqa: E402
from store import Store                                        # noqa: E402
from ui import SettingsWindow, apply_style, ACCENT, ERR_C, MUTED, OK_C  # noqa: E402
import win_core                                                # noqa: E402
import win_shell                                               # noqa: E402

# 二次净化：import 阶段可能是**别人**动手脚 —— store / widgets / win_shell 为了
# "直接跑单个文件也能 import 兄弟模块"，各自在模块顶层插过 sys.path；旧写法在冻结尾
# 会把这些目录算成 %TEMP% / _MEIPASS 再插回来。等兄弟模块都加载完再擦一遍，
# 让运行期任何后续 import（延迟导入的模块、第三方库）也拿不到被污染的路径。
if FROZEN:
    sys.path[:] = [p for p in sys.path
                   if os.path.normcase(os.path.abspath(p or os.getcwd())) not in bad]

APP_TITLE = "提示词增强器"
HK_ENHANCE, HK_UNDO, HK_SETTINGS = 1, 2, 3
HK_NAMES = {HK_ENHANCE: "增强", HK_UNDO: "撤销", HK_SETTINGS: "打开设置"}
RESOURCE_DIR = getattr(sys, "_MEIPASS", HERE)
PROJECT_MODULES = ("ui", "toast", "widgets", "core", "store", "win_core", "win_shell", "png_util")


def import_diag() -> str:
    """一行日志说清"这些模块各自是从哪儿加载的"。

    排查"exe 是新的、行为是旧的"这类问题时，这一行比什么都管用：能同时看到
    sys.path 的实际内容、meta_path 的查找顺序，以及每个模块由哪个 loader、
    从哪个文件加载 —— 2026-09 那次 %TEMP% 同名 .pyc 劫持就是靠它定位的。
    """
    parts = [f"frozen={FROZEN}",
             f"meipass={os.path.basename(getattr(sys, '_MEIPASS', ''))}",
             f"path={[os.path.normcase(p) for p in sys.path[:6]]}",
             f"meta={[type(m).__name__ for m in sys.meta_path]}"]
    for name in PROJECT_MODULES:
        mod = sys.modules.get(name)
        if mod is None:
            continue
        parts.append(f"{name}={type(getattr(mod, '__loader__', None)).__name__}"
                     f"@{getattr(mod, '__file__', '')}")
    return " | ".join(parts)

# 单实例互斥体名字。默认全局唯一，所以"同一台机器上再跑一份"会去唤起已有实例；
# 用 PROMPT_ENHANCER_INSTANCE_NAME 可以另起一个命名空间（自动化测试要在一台正在
# 跑正式实例的机器上起一份隔离实例，就靠它；将来做"多份并存"也用得上）。
MUTEX_NAME = (os.environ.get("PROMPT_ENHANCER_INSTANCE_NAME")
              or "PromptEnhancer.SingleInstance.v1")


def resource(name: str) -> str:
    """exe 打包后资源在 _MEIPASS，源码运行则在脚本目录。"""
    return os.path.join(RESOURCE_DIR, name)


def app_version() -> str:
    """版本号的唯一来源是仓库根的 VERSION 文件。

    打包后它被 PyInstaller 放进 _MEIPASS；源码直接跑时在仓库根目录，
    所以两个位置都试一下。读不到就返回 "dev"，绝不让界面因此崩掉。
    """
    for p in (os.path.join(RESOURCE_DIR, "VERSION"), os.path.join(ROOT, "VERSION")):
        try:
            with open(p, "r", encoding="utf-8") as f:
                v = f.read().strip()
            if v:
                return v
        except OSError:
            continue
    return "dev"


def build_stamp() -> str:
    """这个 exe 是什么时候构建的。

    光有版本号不够用：同一个版本号重复打包（改完 bug 重出一版、版本号忘了升）
    会产生两份长得一模一样、行为却不同的包，用户根本分不清手里是哪份。把 exe
    自身的修改时间显示在「关于」页，就能对上文件属性里的时间。
    """
    if not getattr(sys, "frozen", False):
        return "源码运行"
    try:
        ts = os.path.getmtime(sys.executable)
    except OSError:
        return "未知"
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))


PROJECT_MODULES = ("ui", "toast", "widgets", "core", "store", "win_core", "win_shell", "png_util")


def module_sources() -> str:
    """返回"不该这么加载"的项目模块清单（空串 = 全部正常）。

    为什么要查这个：exe 是不是新版，光看版本号/构建时间**不够**。版本号由入口脚本
    main 提供，而 main 是直接从 exe 里跑起来的，永远是新的；真正的界面代码（ui、
    toast、widgets…）是 import 进来的，只要 sys.path 上有个同名 .pyc 挡在前面，
    就会静默加载旧的那份 —— 表现为"exe 换新了，界面还是旧的"。

    冻结时正确来源只能是 _MEIPASS 里；源码运行时只能落在仓库目录里。
    """
    base = os.path.normcase(os.path.abspath(RESOURCE_DIR))
    repo = os.path.normcase(os.path.abspath(ROOT))
    bad = []
    for name in PROJECT_MODULES:
        mod = sys.modules.get(name)
        path = getattr(mod, "__file__", "") or ""
        if not path:                       # 冻结后有些模块没有 __file__，跳过即可
            continue
        p = os.path.normcase(os.path.abspath(path))
        if FROZEN:
            ok = p.startswith(base)
        else:
            ok = p.startswith(repo)
        if not ok:
            bad.append(f"{name}<-{path}")
    return "；".join(bad)


def enable_dpi_awareness() -> None:
    """声明 DPI 感知。

    不声明的话，在 125%/150% 缩放的屏幕上 Windows 会把整个窗口位图拉伸，
    文字发虚。必须在创建 Tk 之前调用。
    """
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)   # PROCESS_SYSTEM_DPI_AWARE
        return
    except Exception:  # noqa: BLE001 - 老系统没有 shcore
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:  # noqa: BLE001
        pass


class EnhancerApp:
    def __init__(self, show_window: bool):
        self.store = Store()
        self.settings = self.store.load_settings()
        first_run = not os.path.isfile(self.store.settings_path)
        self.version = app_version()
        self.build = build_stamp()

        self.queue: "queue.Queue[tuple]" = queue.Queue()
        self.busy = False
        self._quitting = False
        self.last: dict | None = None        # 最近一次增强，用于撤销
        self._hidden_notified = False

        # tkinter：主窗口隐藏，只当事件循环用
        self.root = tk.Tk()
        self.root.title(APP_TITLE)
        self.root.withdraw()
        try:
            self.dpi_scale = max(1.0, self.root.winfo_fpixels("1i") / 96.0)
        except tk.TclError:
            self.dpi_scale = 1.0
        apply_style(self.root)
        self.icon_png = resource("app.png")
        self.icon_ico = resource("app.ico")
        try:
            self._icon_img = tk.PhotoImage(file=self.icon_png)
            self.root.iconphoto(True, self._icon_img)
        except tk.TclError:
            pass

        # Win32 shell：热键 + 托盘
        tip = self._tray_tip()
        self.shell = win_shell.ShellHost(
            icon_path=self.icon_ico, tip=tip,
            on_hotkey=lambda hid: self.queue.put(("hotkey", hid)),
            on_menu=lambda cmd: self.queue.put(("menu", cmd)),
            on_show=lambda: self.queue.put(("show_settings",)),
            on_quit=lambda: self.queue.put(("quit",)),
            on_signal=lambda kind: self.queue.put(("signal", kind)),
            dpi_scale=self.dpi_scale,
        )
        self.shell.start()
        if not self.shell.wait_ready(6.0) or self.shell.error:
            err = self.shell.error or "启动超时"
            messagebox.showerror(APP_TITLE, f"后台服务启动失败：{err}")
            raise SystemExit(1)

        self.store.write_instance(os.getpid(), self.shell.hwnd)
        self.failed_hotkeys = self.shell.apply_hotkeys(self._hotkey_map())
        self.log(f"[start] pid={os.getpid()} 界面缩放={self.dpi_scale:.2f} 热键 " + " / ".join(
            f"{HK_NAMES[hid]} {win_core.format_hotkey(self.settings.get(key, ''))}"
            for hid, key in ((HK_ENHANCE, "hotkey_enhance"), (HK_UNDO, "hotkey_undo"),
                             (HK_SETTINGS, "hotkey_settings"))))
        # 把自己是哪份 exe 写进日志。踩过的坑：用户从 zip 里直接双击 exe 运行时，
        # Windows 会跑 %TEMP%\BNZ.* 里的解压缓存副本，于是"明明更新了包，界面还是旧的"。
        # 日志里有这一行，下次对着哈希/路径一看就知道跑的是哪份。
        self.log(f"[start] 版本={self.version} 构建={self.build} 程序={sys.executable}")
        # 再记一行"每个模块实际是从哪加载的"。这一行是上面那个 %TEMP% 劫持坑的对策：
        # 版本号只能证明入口脚本是新的，证不了 ui / toast 这批模块没被同名 pyc 顶掉。
        # 哪个模块来源不在 _MEIPASS 里，这里会直接把它点名。
        hijacked = module_sources()
        self.log(f"[start] 导入诊断：{import_diag()}")
        if hijacked:
            self.log(f"[start] ⚠️ 有模块不是从 exe 内部加载：{hijacked}", logging.ERROR)
        else:
            self.log("[start] 模块来源全部正常" + ("（均为 exe 内部）" if FROZEN else "（源码）"))

        if self.failed_hotkeys:
            self.log(f"[start] 热键注册失败：{self.failed_hotkeys}", logging.WARNING)
            self.notify(APP_TITLE,
                        "有热键被其它程序占用了：" + "；".join(
                            f"{HK_NAMES[k]}" for k in self.failed_hotkeys)
                        + "。请到设置里换一个（托盘菜单也能打开设置）。", "warn")

        self.win = SettingsWindow(self, icon_png=self.icon_png, on_save=self.save_settings,
                                  dpi_scale=self.dpi_scale)
        self.win.withdraw()
        self._update_state_label()

        self.root.after(60, self._pump)
        self.root.protocol("WM_DELETE_WINDOW", self.quit_app)
        self.root.createcommand("tk::mac::ReopenApplication", self.show_settings)

        if show_window or first_run or not self.settings.get("api_key"):
            self.show_settings()
            if not self.settings.get("api_key"):
                self.win.set_status("还没填 API Key，填好后点「测试连接」确认能通。", ERR_C)
        else:
            self.notify(f"{APP_TITLE}已在后台运行", self._tray_tip())

    # ------------------------------------------------------------ 辅助
    # ------------------------------------------------------------ 日志
    def log(self, msg: str, level: int = logging.INFO) -> None:
        logging.log(level, msg)

    def _hotkey_map(self) -> dict:
        return {
            HK_ENHANCE: self.settings.get("hotkey_enhance", ""),
            HK_UNDO: self.settings.get("hotkey_undo", ""),
            HK_SETTINGS: self.settings.get("hotkey_settings", ""),
        }

    def _tray_tip(self) -> str:
        s = self.settings
        return (f"{APP_TITLE} · 增强 {win_core.format_hotkey(s.get('hotkey_enhance', ''))}"
                f" / 设置 {win_core.format_hotkey(s.get('hotkey_settings', ''))}")[:127]

    def default_templates(self) -> tuple[str, str]:
        return load_templates()

    def copy_to_clipboard(self, text: str) -> bool:
        return win_core.set_clipboard_text(text or "")

    def notify(self, title: str, text: str, level: str = "info") -> None:
        self.shell.notify(title, text, level)

    def notify_once_hidden(self) -> None:
        if self._hidden_notified:
            return
        self._hidden_notified = True
        self.notify(f"{APP_TITLE}仍在后台运行", self._tray_tip())

    def _set_status(self, text: str, color: str = MUTED) -> None:
        """更新设置窗口左下角的状态文字（窗口已销毁就忽略）。

        ⚠️ 每次增强都必须在**所有**出口调用它。之前只在几条路径上调，失败时
        "正在增强…"就一直挂在左下角，看起来像程序卡住了、也看不出失败原因。
        """
        try:
            self.win.set_status(text, color)
        except tk.TclError:
            pass

    def _update_state_label(self):
        key_map = {HK_ENHANCE: "hotkey_enhance", HK_UNDO: "hotkey_undo",
                   HK_SETTINGS: "hotkey_settings"}
        parts = []
        for hid, name in HK_NAMES.items():
            if hid in getattr(self, "failed_hotkeys", {}):
                parts.append(f"{name} ×")
            else:
                spec = self.settings.get(key_map[hid], "")
                parts.append(f"{name} {win_core.format_hotkey(spec)}")
        status = "运行中" if self.shell.enabled else "已暂停"
        text = f"{status} · " + "　".join(parts)
        if getattr(self, "failed_hotkeys", None):
            text += "　（带 × 的热键注册失败，去「快捷键」页换一个）"
        try:
            self.win.set_state_text(text)
        except tk.TclError:
            pass

    def show_settings(self):
        self.win.show()

    # ------------------------------------------------------------ 事件泵
    def _pump(self):
        try:
            while True:
                item = self.queue.get_nowait()
                self._dispatch(item)
        except queue.Empty:
            pass
        except Exception:  # noqa: BLE001
            logging.exception("事件处理异常")
        finally:
            if not self._quitting:
                self.root.after(60, self._pump)

    def _dispatch(self, item):
        kind = item[0]
        if kind == "hotkey":
            hid = item[1]
            if hid == HK_ENHANCE:
                self.start_enhance()
            elif hid == HK_UNDO:
                self.start_undo()
            elif hid == HK_SETTINGS:
                self.show_settings()
        elif kind == "menu":
            cmd = item[1]
            if cmd == win_shell.MENU_OPEN:
                self.show_settings()
            elif cmd == win_shell.MENU_TOGGLE:
                self.toggle_enabled()
            elif cmd == win_shell.MENU_UNDO:
                self.start_undo()
            elif cmd == win_shell.MENU_QUIT:
                self.win.request_quit()
        elif kind == "signal":
            if item[1] == "enhance":
                self.start_enhance()
            else:
                self.start_undo()
        elif kind == "show_settings":
            self.show_settings()
        elif kind == "enhancing":
            self._on_enhancing(item[1])
        elif kind == "enhance_done":
            self._on_enhance_done(*item[1:])
        elif kind == "undo_done":
            self._on_undo_done(*item[1:])
        elif kind == "delivery_done":
            self._on_delivery_done(*item[1:])
        elif kind == "quit":
            self.quit_app()

    # ------------------------------------------------------------ 增强
    def toggle_enabled(self):
        enabled = not self.shell.enabled
        self.shell.set_enabled(enabled)
        self.notify(APP_TITLE, "已恢复增强" if enabled else "已暂停增强")
        if enabled:
            self.failed_hotkeys = self.shell.apply_hotkeys(self._hotkey_map())
        self._update_state_label()

    def start_enhance(self, preset_text: str | None = None, manual: bool = False):
        if not self.shell.enabled:
            self.notify(APP_TITLE, "当前已暂停增强（托盘菜单里可恢复）", "warn")
            return
        if self.busy:
            self.notify(APP_TITLE, "上一次增强还在进行中…")
            return
        self.busy = True
        self._job_settings = dict(self.settings)
        self._input_target = win_core.get_input_target()
        threading.Thread(target=self._enhance_worker, args=(preset_text, manual),
                         daemon=True, name="EnhanceWorker").start()

    def _enhance_worker(self, preset_text: str | None, manual: bool):
        s = dict(getattr(self, "_job_settings", self.settings))
        source = "manual" if preset_text is not None else "selection"
        text = preset_text or ""
        old_clip = ""
        try:
            old_clip = win_core.get_clipboard_text()
            min_len = int(s.get("min_len", 4))

            if preset_text is None:
                text, err = self._grab_selection()
                if win_core.get_input_target() != self._input_target:
                    self.queue.put(("enhance_done", None, "", source, old_clip, manual,
                                    "窗口焦点已改变，请重新选中文字后增强"))
                    return
                if err and s.get("fallback_clipboard") and old_clip.strip():
                    # 默认关闭。开着的话，"没选中文本"时会拿上一次复制的内容去增强，
                    # 用户会觉得程序认错了对象 —— 所以默认不这么干，只在明确开启时才回退。
                    self.log("[grab] 未抓到选中内容，按设置回退到剪贴板已有内容", logging.WARNING)
                    text, err, source = old_clip, "", "clipboard"
            else:
                text, err = preset_text, ""
            self._captured_seq = win_core.clipboard_sequence()
            self._captured_input = win_core.last_input_tick()

            if err:
                self.queue.put(("enhance_done", None, text, source, old_clip, manual, err))
                return

            # 抓到了：先让界面给出"正在增强"的反馈，再去调模型
            # （否则用户按完热键要盯着屏幕等好几秒，不知道到底有没有触发）
            self.queue.put(("enhancing", text))

            if len(text.strip()) < min_len:
                self.queue.put(("enhance_done", None, text, source, old_clip, manual,
                                f"内容太短（少于 {min_len} 字），未增强"))
                return

            r = enhance(
                text,
                base_url=s.get("base_url"),
                api_key=s.get("api_key"),
                model=s.get("model"),
                timeout=float(s.get("timeout") or 25),
                temperature=float(s.get("temperature", 0.4)),
                max_tokens=int(s.get("max_tokens") or 1200),
                system_template=s.get("system_template") or None,
                user_template=s.get("user_template") or None,
            )
            self.queue.put(("enhance_done", r, text, source, old_clip, manual, ""))
        except Exception as e:  # noqa: BLE001 - 兜底：worker 绝不能静默退出，否则 busy 卡死
            self.log(f"[enhance] worker 异常：{type(e).__name__}: {e}", logging.ERROR)
            self.queue.put(("enhance_done", None, text, source, old_clip, manual,
                            f"内部错误：{type(e).__name__}: {e}"))

    def _grab_selection(self) -> tuple[str, str]:
        """抓取当前选中的文本。返回 (文本, 错误信息)，错误信息非空表示没抓到。"""
        # ⚠️ 这一步是"增强用的却是上一次剪贴板内容"的根因所在。
        # 用户按 Ctrl+Alt+E 触发时，WM_HOTKEY 在按下的瞬间就送到了，此刻 Ctrl 和
        # Alt 在物理上还按着。如果这时立刻注入 Ctrl+C，目标程序收到的是
        # **Ctrl+Alt+C** 而不是 Ctrl+C —— 复制根本不会发生。等用户松手再注入，
        # 才能得到干净的 Ctrl+C。
        waited = win_core.wait_modifiers_released(1.0)
        if waited > 0.02:
            self.log(f"[grab] 等热键修饰键释放 {waited * 1000:.0f} ms")
        stuck = win_core.modifier_down()
        if stuck:
            return "", "请松开快捷键后重试"
        target = getattr(self, "_input_target", None)
        if target is not None and win_core.get_input_target() != target:
            return "", "窗口焦点已改变，请重新选中文字后增强"

        # 用剪贴板序号判断"复制到底有没有发生"。不去预先清空剪贴板，因为清空会
        # 连带丢掉用户原本复制的图片 / 富文本。
        seq0 = win_core.clipboard_sequence()
        prev_text = win_core.get_clipboard_text()
        win_core.send_ctrl(win_core.VK_C)
        deadline = time.time() + 1.6
        changed = False
        while time.time() < deadline:
            time.sleep(0.03)
            if seq0:
                changed = win_core.clipboard_sequence() != seq0
            else:
                # 序号读不到（个别权限受限场景会返回 0）时退化成内容比对
                cur = win_core.get_clipboard_text()
                changed = bool(cur.strip()) and cur != prev_text
            if changed:
                break
        if not changed:
            # 把现场信息记下来：前台窗口是不是目标应用、剪贴板序号有没有动。
            # 用户反馈"没反应"时，这条日志能立刻区分是焦点跑了还是注入被拦了。
            fg = win_core.get_foreground_window()
            self.log(f"[grab] 抓取失败：前台窗口=0x{fg:x}，剪贴板序号 {seq0} -> "
                     f"{win_core.clipboard_sequence()}，"
                     f"修饰键={win_core.modifier_down() or '无'}", logging.WARNING)
            return "", "没抓到选中的文本。请先选中要增强的文字，再按热键"

        for _ in range(10):          # 等目标程序把剪贴板数据写完
            text = win_core.get_clipboard_text()
            if text.strip():
                return text, ""
            time.sleep(0.04)
        return "", "选中的内容是空白的，没有可增强的文字"

    def _on_enhancing(self, text: str):
        preview = " ".join(text.split())
        if len(preview) > 28:
            preview = preview[:28] + "…"
        self.notify(APP_TITLE, f"正在增强：{preview}", "busy")
        self.log(f"[enhance] 开始，输入 {len(text)} 字")
        self._set_status("正在增强…", ACCENT)

    def _on_enhance_done(self, r, before: str, source: str, old_clip: str,
                         manual: bool, pre_error: str):
        self.busy = False
        s = self.settings

        if r is None:
            self.store.add_history(before=before, after="", ok=False, elapsed_ms=0,
                                   model=s.get("model", ""), error=pre_error, source=source)
            self.log(f"[enhance] 跳过：{pre_error}（来源={source}）", logging.WARNING)
            self.notify(APP_TITLE, pre_error, "warn")
            self._set_status(pre_error, ERR_C)
            self.win.refresh_history()
            return

        self.store.add_history(before=before, after=r.text, ok=r.ok,
                               elapsed_ms=r.elapsed_ms, model=r.model,
                               error=r.error, source=source)
        self.win.refresh_history()

        if not r.ok:
            self.log(f"[enhance] 失败：{r.error}（耗时 {r.elapsed_ms}ms）", logging.ERROR)
            self.notify(APP_TITLE, f"增强失败：{(r.error or '')[:180]}", "error")
            self._set_status(f"增强失败：{(r.error or '')[:80]}", ERR_C)
            return
        if r.text.strip() == before.strip():
            self.log(f"[enhance] 结果与原文相同，未修改（耗时 {r.elapsed_ms}ms）")
            self.notify(APP_TITLE, "增强结果与原文相同，已保持原样", "warn")
            self._set_status("增强结果与原文相同，未修改", MUTED)
            return

        self.log(f"[enhance] 成功 {len(before)}->{len(r.text)} 字，"
                 f"耗时 {r.elapsed_ms}ms，来源={source}，模型={r.model}")
        self.busy = True
        threading.Thread(target=self._deliver_worker,
                         args=(r.text, before, source, old_clip, manual),
                         daemon=True, name="PasteWorker").start()

    def _deliver_worker(self, result: str, before: str, source: str,
                        old_clip: str, manual: bool):
        s = dict(getattr(self, "_job_settings", self.settings))
        target = getattr(self, "_input_target", (0, 0))
        last = None
        status = "copied"
        try:
            if self._quitting:
                return
            seq = getattr(self, "_captured_seq", 0)
            if seq and win_core.clipboard_sequence() != seq:
                status = "clipboard_changed"
            elif not win_core.set_clipboard_text(result):
                status = "clipboard_failed"
            else:
                seq_written = win_core.clipboard_sequence()
                last = {"before": before, "after": result, "ts": time.time(),
                        "pasted": False, "target": target}
                if not manual and source == "selection" and s.get("auto_paste", True):
                    win_core.wait_modifiers_released(0.6)
                    if self._quitting:
                        return
                    if win_core.modifier_down():
                        status = "modifiers"
                    elif not all(target) or win_core.get_input_target() != target:
                        status = "focus_changed"
                    elif (getattr(self, "_captured_input", None) is not None
                          and win_core.last_input_tick() != self._captured_input):
                        status = "input_changed"
                    elif not seq_written or win_core.clipboard_sequence() != seq_written:
                        status = "clipboard_changed"
                    else:
                        win_core.send_ctrl(win_core.VK_V)
                        last["pasted"] = True
                        last["ts"] = time.time()
                        status = "pasted"
                        time.sleep(0.4)
                        if old_clip and win_core.clipboard_sequence() == seq_written:
                            win_core.set_clipboard_text(old_clip)
        except Exception:
            logging.exception("粘贴结果失败")
            status = "clipboard_failed"
        self.queue.put(("delivery_done", status, last))

    def _on_delivery_done(self, status: str, last):
        self.busy = False
        if last:
            self.last = last
        messages = {
            "pasted": "已增强并替换，可使用撤销快捷键恢复",
            "copied": "增强结果已复制到剪贴板，请手动粘贴",
            "focus_changed": "窗口焦点已改变，结果已复制，请回到原位置手动粘贴",
            "input_changed": "等待期间检测到键鼠操作，结果已复制，请手动粘贴",
            "modifiers": "修饰键尚未松开，结果已复制，请手动粘贴",
            "clipboard_changed": "剪贴板已有新内容，结果已保存在「历史」页",
            "clipboard_failed": "写入剪贴板失败，结果已保存在「历史」页",
        }
        message = messages[status]
        if status == "pasted":
            self.log("[paste] 已注入 Ctrl+V 替换选中内容")
        self.log(f"[paste] {message}")
        self._set_status(message, ERR_C if status == "clipboard_failed" else OK_C)
        if status != "pasted" or self.settings.get("notify_success"):
            self.notify(APP_TITLE, message, "error" if status == "clipboard_failed" else "ok")

    # ------------------------------------------------------------ 撤销
    def start_undo(self):
        if self.busy:
            self.notify(APP_TITLE, "正在处理上一项操作，请稍后撤销", "warn")
            return
        if not self.last:
            self.notify(APP_TITLE, "没有可撤销的增强记录", "warn")
            return
        self.busy = True
        threading.Thread(target=self._undo_worker, args=(dict(self.last),),
                         daemon=True, name="UndoWorker").start()

    def _undo_worker(self, last: dict):
        status = "error"
        try:
            age = time.time() - last["ts"]
            limit = int(self.settings.get("undo_window_sec", 600))
            win_core.wait_modifiers_released(1.0)
            if self._quitting:
                return
            if win_core.set_clipboard_text(last["before"]):
                target = last.get("target", (0, 0))
                if (last.get("pasted") and age <= limit and all(target)
                        and win_core.get_input_target() == target
                        and not win_core.modifier_down()):
                    win_core.send_ctrl(win_core.VK_Z)
                    status = "ok"
                else:
                    status = "copied"
        except Exception:
            logging.exception("撤销失败")
        finally:
            self.queue.put(("undo_done", status, None))

    def _on_undo_done(self, status: str, _unused):
        self.busy = False
        if status != "error":
            self.last = None
        if status == "ok":
            self.log("[undo] 已撤销上一次增强")
            message = "已发送撤销，原文也已复制到剪贴板"
        elif status == "copied":
            message = "原文已复制到剪贴板，请回到原位置手动粘贴"
        else:
            message = "撤销失败，原文仍可在「历史」页复制"
        self.notify(APP_TITLE, message, "error" if status == "error" else "info")
        self._set_status(message, ERR_C if status == "error" else OK_C)

    def reenhance(self, text: str):
        if not text.strip():
            return
        self.start_enhance(preset_text=text, manual=True)

    # ------------------------------------------------------------ 设置保存
    def save_settings(self, new_settings: dict) -> tuple[bool, str]:
        geometry = new_settings.get("_geometry") or self.settings.get("_geometry", "")
        candidate = dict(self.settings, **new_settings)
        candidate["_geometry"] = geometry
        try:
            self.store.save_settings(candidate)
        except OSError as e:
            return False, f"写入配置失败：{e}"
        self.settings = candidate

        self.failed_hotkeys = self.shell.apply_hotkeys(self._hotkey_map())
        self.shell.set_tray_tip(self._tray_tip())
        if self.failed_hotkeys:
            msg = "；".join(f"{HK_NAMES[k]} 热键：{v}" for k, v in self.failed_hotkeys.items())
            self.log(f"[settings] 热键注册失败：{msg}", logging.WARNING)
            self._update_state_label()
            return False, msg + "（其它设置已保存）"

        self.log(f"[settings] 已保存：模型={self.settings.get('model')} "
                 f"地址={self.settings.get('base_url')} 增强键={self.settings.get('hotkey_enhance')}")
        self._update_state_label()
        return True, "已保存并生效"

    # ------------------------------------------------------------ 退出
    def quit_app(self):
        if self._quitting:
            return
        self._quitting = True
        try:
            geo = self.win.geometry()
            if geo:
                self.settings["_geometry"] = geo
        except tk.TclError:
            pass
        try:
            self.store.save_settings(self.settings)
        except OSError:
            pass
        self.store.clear_instance()
        try:
            self.shell.stop()
        except Exception:  # noqa: BLE001
            pass
        try:
            self.root.destroy()
        except tk.TclError:
            pass


def _setup_logging() -> None:
    log_path = os.path.join(Store().dir, "app.log")
    logging.basicConfig(
        filename=log_path, level=logging.INFO, filemode="a",
        format="%(asctime)s %(levelname)s %(name)s: %(message)s", encoding="utf-8")
    # 无控制台运行时 stdout/stderr 为 None，包装一层避免 print 抛异常
    class _Tee:
        def __init__(self, stream, logger, level):
            self._stream, self._logger, self._level = stream, logger, level

        def write(self, s):
            if s and s.strip():
                self._logger.log(self._level, s.rstrip())
            if self._stream:
                try:
                    self._stream.write(s)
                except Exception:  # noqa: BLE001
                    pass

        def flush(self):
            if self._stream:
                try:
                    self._stream.flush()
                except Exception:  # noqa: BLE001
                    pass

    logging.getLogger("stdout").info("---- app start ----")
    sys.stdout = _Tee(sys.stdout, logging.getLogger("stdout"), logging.INFO)
    sys.stderr = _Tee(sys.stderr, logging.getLogger("stderr"), logging.ERROR)


def main() -> int:
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument("--show", action="store_true")
    ap.add_argument("--hidden", action="store_true")
    ap.add_argument("--quit", action="store_true")
    ap.add_argument("--enhance", action="store_true",
                    help="让正在运行的实例执行一次增强（可绑到键盘宏）")
    ap.add_argument("--undo", action="store_true",
                    help="让正在运行的实例撤销上一次增强")
    args, _unknown = ap.parse_known_args()

    _setup_logging()
    enable_dpi_awareness()
    mutex = win_core.acquire_single_instance(MUTEX_NAME)
    if mutex is None:
        # 已有实例在跑：把请求投递给它，然后自己退出
        inst = Store().read_instance()
        hwnd = int(inst.get("hwnd") or 0)
        if args.quit:
            msg = win_shell.MSG_QUIT
        elif args.enhance:
            msg = win_shell.MSG_ENHANCE
        elif args.undo:
            msg = win_shell.MSG_UNDO
        else:
            msg = win_shell.MSG_SHOW
        if hwnd and win_core.is_valid_window(hwnd):
            win_core.post_message(hwnd, msg)
        else:
            logging.warning("已有实例但拿不到窗口句柄，忽略本次启动")
        return 0

    if args.enhance or args.undo or args.quit:
        # 没有实例在跑时，这些信号没有意义
        print("提示词增强器没有在运行，先启动它再发这个命令。")
        win_core.release_single_instance(mutex)
        return 0

    try:
        app = EnhancerApp(show_window=args.show or not args.hidden)
        app.root.mainloop()
    except SystemExit:
        return 0
    except Exception:  # noqa: BLE001
        logging.exception("未捕获异常")
        try:
            messagebox.showerror(APP_TITLE, "程序异常退出，详情见 app.log")
        except Exception:  # noqa: BLE001
            pass
        return 1
    finally:
        win_core.release_single_instance(mutex)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
