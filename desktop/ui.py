#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""设置窗口（tkinter / ttk）。

五个页签：模型、快捷键、提示词模板、历史、关于。
所有耗时操作（测试连接）都丢到后台线程，界面不卡。
"""

from __future__ import annotations

import math
import queue
import re
import threading
import tkinter as tk
from tkinter import messagebox, ttk
from urllib.parse import urlsplit

from widgets import HotkeyEntry, ScrollableFrame, make_checkbutton

BG = "#FFFFFF"
PANEL = "#F7F8FA"       # 极淡中性底，仅用于只读输出区（历史详情），不再做卡片
BORDER = "#ECEDF2"      # 更淡的发丝线，替代原先偏重的边框
TEXT = "#1F2233"
MUTED = "#6B7280"
ACCENT = "#4F46E5"
ACCENT_DARK = "#4338CA"
HOVER = "#F2F3F7"       # 次要按钮的浅填充
OK_C = "#059669"
ERR_C = "#DC2626"
WARN_C = "#B45309"
FONT = ("Microsoft YaHei UI", 9)
FONT_B = ("Microsoft YaHei UI", 9, "bold")
FONT_H = ("Microsoft YaHei UI", 12, "bold")
FONT_MONO = ("Consolas", 9)

NUMBER_FIELDS = {
    "timeout": ("超时", float, 1, 300),
    "temperature": ("温度", float, 0, 2),
    "max_tokens": ("最大输出 token", int, 1, 32768),
    "min_len": ("最短触发长度", int, 1, 10000),
    "undo_window_sec": ("撤销有效窗口", int, 10, 86400),
}


class SettingsError(ValueError):
    def __init__(self, field: str, message: str):
        super().__init__(message)
        self.field = field


def parse_number(field: str, raw: str):
    label, cast, low, high = NUMBER_FIELDS[field]
    try:
        number = float(raw)
    except (TypeError, ValueError):
        raise SettingsError(field, f"「{label}」请输入数字。") from None
    if not math.isfinite(number) or not low <= number <= high:
        raise SettingsError(field, f"「{label}」必须在 {low} 到 {high} 之间。")
    if cast is int and not number.is_integer():
        raise SettingsError(field, f"「{label}」请输入整数。")
    return cast(number)


def validate_connection(settings: dict) -> None:
    try:
        parsed = urlsplit(settings.get("base_url", ""))
        valid_url = (parsed.scheme in ("http", "https") and bool(parsed.hostname)
                     and not parsed.username and not parsed.password
                     and not parsed.fragment)
        parsed.port
    except ValueError:
        valid_url = False
    if not valid_url or any(c.isspace() for c in settings.get("base_url", "")):
        raise SettingsError("base_url", "API 地址需要完整的 http:// 或 https:// 地址，且不能包含账号或片段。")
    if not settings.get("model", "").strip():
        raise SettingsError("model", "请填写模型名称。")

# 快捷键三项：(配置键, 界面名, 一行说明)
HOTKEY_FIELDS = (
    ("hotkey_enhance", "增强选中文本", "选中草稿后按它，原地替换成增强版"),
    ("hotkey_undo", "撤销上一次增强", "把刚被替换掉的原文还原回去"),
    ("hotkey_settings", "打开本设置窗口", "应用在后台跑时也能随时唤起"),
)


def _fmt_hotkey(spec: str) -> str:
    """'ctrl+e' -> 'Ctrl + E'。拿不到 win_core 就原样返回，别让界面因此崩掉。"""
    try:
        from win_core import format_hotkey
        return format_hotkey(spec)
    except Exception:  # noqa: BLE001
        return spec


def apply_style(root: tk.Misc) -> ttk.Style:
    st = ttk.Style(root)
    try:
        st.theme_use("clam")
    except tk.TclError:
        pass
    root.option_add("*Font", FONT)
    st.configure(".", background=BG, foreground=TEXT, font=FONT)
    st.configure("TFrame", background=BG)
    st.configure("Card.TFrame", background=BG, relief="flat")
    st.configure("TLabel", background=BG, foreground=TEXT)
    st.configure("Card.TLabel", background=BG, foreground=TEXT)
    st.configure("Muted.TLabel", background=BG, foreground=MUTED)
    st.configure("Hint.TLabel", background=BG, foreground=MUTED, font=("Microsoft YaHei UI", 8))
    st.configure("H.TLabel", background=BG, foreground=TEXT, font=FONT_H)

    # 页签：去掉灰底，做成扁平文字标签，选中态用强调色文字，无边框无凸起
    st.configure("TNotebook", background=BG, borderwidth=0)
    st.configure("TNotebook.Tab", padding=(16, 9), font=FONT,
                 background=BG, foreground=MUTED, borderwidth=0)
    st.map("TNotebook.Tab",
           background=[("selected", BG), ("active", BG)],
           foreground=[("selected", ACCENT), ("active", TEXT)])

    # 输入框：白底 + 发丝线，聚焦时描边强调色（去掉原来的淡紫填充，更干净）
    st.configure("TEntry", fieldbackground="#FFFFFF", bordercolor=BORDER,
                 lightcolor=BORDER, darkcolor=BORDER, borderwidth=1, padding=6)
    st.map("TEntry", bordercolor=[("focus", ACCENT)],
           lightcolor=[("focus", ACCENT)], darkcolor=[("focus", ACCENT)])
    st.configure("TSpinbox", fieldbackground="#FFFFFF", bordercolor=BORDER,
                 lightcolor=BORDER, darkcolor=BORDER, borderwidth=1, padding=5)
    st.map("TSpinbox", bordercolor=[("focus", ACCENT)],
           lightcolor=[("focus", ACCENT)], darkcolor=[("focus", ACCENT)])

    st.configure("TCheckbutton", background=BG, foreground=TEXT)
    st.map("TCheckbutton", background=[("active", BG)])

    # 次要按钮：扁平浅填充、无边框；只有主操作用实心强调色，层级更干净
    st.configure("TButton", padding=(13, 7), background=HOVER, foreground=TEXT,
                 borderwidth=0, focusthickness=0)
    st.map("TButton",
           background=[("active", "#E6E8EF"), ("pressed", "#E6E8EF"),
                       ("disabled", PANEL)],
           foreground=[("disabled", MUTED)])
    st.configure("Accent.TButton", background=ACCENT, foreground="#FFFFFF")
    st.map("Accent.TButton",
           background=[("active", ACCENT_DARK), ("pressed", ACCENT_DARK),
                       ("disabled", "#C7C9DA")],
           foreground=[("disabled", "#FFFFFF")])

    # 分区容器去掉方框，只留一行加粗小标题 + 留白来分组，观感更扁平
    st.configure("TLabelframe", background=BG, borderwidth=0, relief="flat",
                 bordercolor=BG, lightcolor=BG, darkcolor=BG)
    st.configure("TLabelframe.Label", background=BG, foreground=TEXT, font=FONT_B)

    st.configure("Treeview", background="#FFFFFF", fieldbackground="#FFFFFF",
                 foreground=TEXT, rowheight=26, borderwidth=0)
    st.configure("Treeview.Heading", background=BG, foreground=MUTED,
                 font=FONT_B, relief="flat", padding=6)
    st.map("Treeview.Heading", background=[("active", BG)])
    st.map("Treeview", background=[("selected", "#EEF0FF")],
           foreground=[("selected", TEXT)])
    st.configure("TCombobox", padding=4)
    st.configure("TSeparator", background=BORDER)
    return st


class SettingsWindow(tk.Toplevel):
    def __init__(self, app, icon_png: str = "", on_save=None, dpi_scale: float = 1.0):
        super().__init__(app.root)
        self.app = app
        self.on_save = on_save
        self.s = max(1.0, float(dpi_scale or 1.0))
        self._loading = False
        self._baseline = None
        self._dirty = False
        self._test_running = False
        self._test_results = queue.Queue()
        self._pages = {}
        self._field_vars = {}
        # 标题带版本号：用户手里常同时有几份包，问他"哪一版"时，看一眼标题栏就能答
        _ver = getattr(app, "version", "dev")
        self.title(f"提示词增强器 {_ver} · 设置")
        # 把"实际生效的标题"落进日志。窗口标题是用户判断版本的第一眼依据，
        # 一旦这里和预期不一致，日志能直接说明是取值问题还是显示问题。
        if hasattr(app, "log"):
            app.log(f"[ui] 设置窗口标题={self.title()!r} 版本={_ver!r} "
                    f"构建={getattr(app, 'build', '')!r}")
        self.configure(bg=BG)
        saved_geo = app.settings.get("_geometry") or ""
        screen_w, screen_h = self.winfo_screenwidth(), self.winfo_screenheight()
        self.minsize(min(int(640 * self.s), screen_w - 40),
                     min(int(430 * self.s), screen_h - 100))
        self.protocol("WM_DELETE_WINDOW", self.hide)
        if icon_png:
            try:
                self._icon = tk.PhotoImage(file=icon_png)
                self.iconphoto(True, self._icon)
            except tk.TclError:
                pass

        self.vars = {}
        self._build()
        self._load_values()
        self._watch_changes()
        self._mark_saved()
        self.refresh_history()
        self.geometry(self._fit_geometry(saved_geo))
        self.bind("<Control-s>", self._save_shortcut)

    def _default_geometry(self) -> str:
        """Keep the first window inside the screen; long pages scroll independently."""
        w = min(int(840 * self.s), max(480, self.winfo_screenwidth() - 60))
        h = min(int(720 * self.s), max(360, self.winfo_screenheight() - 100))
        return f"{w}x{h}"

    def _fit_geometry(self, saved: str) -> str:
        match = re.fullmatch(r"(\d+)x(\d+)([+-]\d+)?([+-]\d+)?", saved)
        if not match:
            return self._default_geometry()
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        min_w, min_h = self.minsize()
        width = max(min_w, min(int(match[1]), sw - 40))
        height = max(min_h, min(int(match[2]), sh - 100))
        if match[3] is None or match[4] is None:
            return f"{width}x{height}"
        x = max(0, min(int(match[3]), sw - width - 20))
        y = max(0, min(int(match[4]), sh - height - 80))
        return f"{width}x{height}+{x}+{y}"

    # ------------------------------------------------------------ 构建
    def _build(self):
        header = ttk.Frame(self, padding=(24, 18, 24, 12))
        header.pack(fill="x")
        title_row = ttk.Frame(header)
        title_row.pack(fill="x")
        ttk.Label(title_row, text="提示词增强器", style="H.TLabel").pack(side="left")
        self.lbl_dirty = ttk.Label(title_row, text="设置已同步", style="Muted.TLabel")
        self.lbl_dirty.pack(side="right")
        self.lbl_state = ttk.Label(header, text="", style="Muted.TLabel")
        self.lbl_state.pack(anchor="w", pady=(4, 0))
        header.bind("<Configure>", lambda e: self.lbl_state.configure(wraplength=max(100, e.width - 48)))

        # ⚠️ 先 pack 底部操作栏，再 pack 页签区。
        # pack 是按调用顺序分配空间的，排在后面的控件在空间不够时**直接被裁掉**。
        # 「保存并应用」「恢复默认设置」都在底部栏，窗口一旦被拖小（页面内容请求
        # 的高度比窗口还大，这是常量）它们就会整个消失 —— 之前就是这个顺序，
        # 小窗口下底部按钮全看不见。现在底部栏先占位，页签区自己收缩。
        bar = ttk.Frame(self, padding=(20, 10, 20, 14))
        bar.pack(side="bottom", fill="x")
        self.lbl_status = ttk.Label(bar, text="Ctrl+S 保存设置 · 关闭窗口后继续在系统托盘运行", style="Muted.TLabel")
        self.lbl_status.pack(fill="x", pady=(0, 8))
        bar.bind("<Configure>", lambda e: self.lbl_status.configure(wraplength=max(100, e.width - 40)))
        actions = ttk.Frame(bar)
        actions.pack(fill="x")
        self.btn_save = ttk.Button(actions, text="保存并应用", style="Accent.TButton", command=self.save)
        self.btn_save.pack(side="right")
        ttk.Button(actions, text="关闭到后台", command=self.hide).pack(side="right", padx=(0, 8))
        ttk.Button(actions, text="退出程序", command=self.request_quit).pack(side="left")
        ttk.Button(actions, text="恢复默认设置", command=self.restore_defaults).pack(side="left", padx=(8, 0))
        # 按钮变扁平后，用一条发丝线把操作栏和内容区分开，替代原先靠灰底按钮的"重量"
        ttk.Separator(self, orient="horizontal").pack(side="bottom", fill="x")

        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=16, pady=(0, 8))
        self.nb = nb
        nb.add(self._tab_model(nb), text="  模型  ")
        nb.add(self._tab_hotkey(nb), text="  快捷键  ")
        nb.add(self._tab_template(nb), text="  提示词模板  ")
        nb.add(self._tab_history(nb), text="  历史  ")
        nb.add(self._tab_about(nb), text="  关于  ")

    def _scroll_page(self, parent, name):
        page = ScrollableFrame(parent, padding=20)
        self._pages[name] = page
        return page, page.body

    @staticmethod
    def _wrap_label(parent, text, *, style="Hint.TLabel", **kwargs):
        label = ttk.Label(parent, text=text, style=style, justify="left", wraplength=400, **kwargs)
        parent.bind("<Configure>", lambda e: label.configure(wraplength=max(160, e.width - 40)), add="+")
        return label

    # ------------------------------------------------------------ 模型页
    def _tab_model(self, parent):
        page, f = self._scroll_page(parent, "model")
        self._wrap_label(f, "01  连接你的模型", style="H.TLabel").grid(row=0, column=0, sticky="w", pady=(0, 4))
        self._wrap_label(f, "填写兼容 OpenAI 的接口信息，测试成功后保存即可开始使用。", style="Muted.TLabel").grid(row=1, column=0, sticky="we", pady=(0, 10))
        rows = [
            ("base_url", "API 地址（Base URL）", "例如 https://api.openai.com/v1 或你的中转站地址"),
            ("api_key", "API Key", "本地用 Windows DPAPI 加密存储，仅当前用户的账号能解密"),
            ("model", "模型名称", "建议用便宜快的小模型，增强任务不需要推理模型"),
        ]
        for i, (key, label, hint) in enumerate(rows):
            # 提示文字放在输入框下方：放右边容易被窗口边缘裁掉
            ttk.Label(f, text=label).grid(row=2 + i * 3, column=0, sticky="w",
                                          pady=(12 if i else 0, 3))
            e = ttk.Entry(f)
            e.grid(row=3 + i * 3, column=0, sticky="we")
            self._wrap_label(f, hint).grid(row=4 + i * 3, column=0, sticky="we", pady=(2, 0))
            self.vars[key] = e
        self.vars["api_key"].configure(show="•")

        show = tk.BooleanVar(value=False)
        show_cb = make_checkbutton(f, "显示 API Key", show, dpi_scale=self.s,
                                   font=FONT)
        show_cb.configure(command=lambda: self.vars["api_key"].configure(
            show="" if show.get() else "•"))
        show_cb.grid(row=11, column=0, sticky="w", pady=(6, 10))

        test_row = ttk.Frame(f)
        test_row.grid(row=12, column=0, sticky="we", pady=(4, 2))
        self.btn_test = ttk.Button(test_row, text="测试连接", command=self.test_connection)
        self.btn_test.pack(side="left")
        ttk.Label(test_row, text="使用当前表单，发送一次真实请求", style="Hint.TLabel").pack(side="left", padx=(12, 0))
        self.lbl_test = self._wrap_label(f, "尚未测试", style="Muted.TLabel")
        self.lbl_test.grid(row=13, column=0, sticky="we", pady=(4, 14))

        adv = ttk.LabelFrame(f, text="高级参数", padding=(0, 6, 0, 0))
        adv.grid(row=14, column=0, sticky="we")
        nums = [("timeout", "超时（秒）", 1, 300, 1.0),
                ("temperature", "温度（0–2）", 0, 2, 0.05),
                ("max_tokens", "最大输出 token", 1, 32768, 100),
                ("min_len", "最短触发长度（字符）", 1, 10000, 1)]
        for i, (key, label, lo, hi, step) in enumerate(nums):
            col, row = i % 2, i // 2
            cell = ttk.Frame(adv)
            cell.grid(row=row, column=col, sticky="we", padx=(0, 24), pady=6)
            ttk.Label(cell, text=label).pack(anchor="w")
            sp = ttk.Spinbox(cell, from_=lo, to=hi, increment=step, width=12)
            sp.pack(anchor="w", pady=(2, 0))
            self.vars[key] = sp

        adv.columnconfigure((0, 1), weight=1)
        f.columnconfigure(0, weight=1)
        return page

    # ------------------------------------------------------------ 快捷键页
    def _tab_hotkey(self, parent):
        page, f = self._scroll_page(parent, "hotkey")
        ttk.Label(f, text="02  设置触发方式", style="H.TLabel").pack(anchor="w", pady=(0, 6))
        self._wrap_label(f, "点击快捷键输入框后直接按组合键；保存后立即生效。", style="Muted.TLabel").pack(fill="x", pady=(0, 16))
        hk = ttk.LabelFrame(f, text="全局快捷键", padding=(0, 6, 0, 0))
        hk.pack(fill="x")
        for i, (key, label, hint) in enumerate(HOTKEY_FIELDS):
            ttk.Label(hk, text=label).grid(row=i * 2, column=0, sticky="w", pady=(8, 0))
            e = HotkeyEntry(hk, width=18, on_change=self._on_hotkey_change,
                            on_message=self._on_hotkey_message)
            e.grid(row=i * 2, column=1, rowspan=2, sticky="e", padx=(12, 0), pady=5)
            ttk.Label(hk, text=hint, style="Hint.TLabel").grid(row=i * 2 + 1, column=0, sticky="w", pady=(2, 8))
            self.vars[key] = e
        hk.columnconfigure(0, weight=1)
        # 录入框是"按什么存什么"，所以这里要把交互讲清楚，不然用户还是去手打
        self._wrap_label(hk, "Esc 清空并停用该快捷键，Tab 移到下一项。组合键必须带 Ctrl / Alt / Shift / Win 之一，三项不能重复。").grid(row=len(HOTKEY_FIELDS) * 2, column=0, columnspan=2, sticky="we", pady=(8, 0))

        beh = ttk.LabelFrame(f, text="行为", padding=(0, 6, 0, 0))
        beh.pack(fill="x", pady=(20, 0))
        checks = [("auto_paste", "增强后自动替换掉选中的文本（关闭则只写进剪贴板）", True),
                  ("notify_success", "完成后在右下角给个提示", True),
                  ("fallback_clipboard",
                   "没选中文本时，改用剪贴板里已有的内容（容易认错对象，慎开）", False)]
        self.checks = {}
        for i, (key, label, _d) in enumerate(checks):
            v = tk.BooleanVar()
            cb = make_checkbutton(beh, label, v, dpi_scale=self.s, font=FONT, wraplength=520)
            cb.grid(row=i, column=0, sticky="we", pady=5)
            beh.bind("<Configure>", lambda e, widget=cb: widget.configure(wraplength=max(160, e.width - 30)), add="+")
            self.checks[key] = v

        row = ttk.Frame(beh)
        row.grid(row=3, column=0, sticky="w", pady=(10, 0))
        ttk.Label(row, text="撤销有效窗口（秒）").pack(side="left")
        sp = ttk.Spinbox(row, from_=10, to=86400, increment=10, width=10)
        sp.pack(side="left", padx=(10, 0))
        self.vars["undo_window_sec"] = sp
        self._wrap_label(beh, "超过有效时间或切换了窗口后，撤销会把原文放进剪贴板，供手动粘贴。").grid(row=4, column=0, sticky="we", pady=(6, 0))
        beh.columnconfigure(0, weight=1)
        return page

    # ------------------------------------------------------------ 快捷键录入反馈
    def _on_hotkey_message(self, msg: str, level: str = "hint") -> None:
        self.set_status(msg, ERR_C if level == "warn" else MUTED)

    def _on_hotkey_change(self, spec: str) -> None:
        self._update_dirty()
        if not spec:
            self.set_status("快捷键已清空 —— 留空表示不注册这个热键", MUTED)
            return
        self.set_status(f"快捷键已改为 {_fmt_hotkey(spec)}，点「保存并应用」才生效", ACCENT)

    # ------------------------------------------------------------ 模板页
    def _tab_template(self, parent):
        f = ttk.Frame(parent, padding=16)
        ttk.Label(f, text="03  定义增强方式", style="H.TLabel").pack(anchor="w", pady=(0, 8))
        row = ttk.Frame(f)
        row.pack(side="bottom", fill="x", pady=(8, 0))
        ttk.Button(row, text="恢复内置默认模板", command=self.restore_templates).pack(side="left")
        ttk.Label(row, text="留空使用默认模板", style="Hint.TLabel").pack(side="left", padx=(12, 0))
        ttk.Label(f, text="system 模板（角色与硬约束）").pack(anchor="w")
        self.txt_system = self._text_area(f, height=5, editable=True)

        ttk.Label(f, text="user 模板（必须包含 {input} 占位符）").pack(anchor="w")
        self.txt_user = self._text_area(f, height=7, editable=True)
        return f

    def _text_area(self, parent, *, height, editable=False):
        frame = ttk.Frame(parent)
        frame.pack(fill="both", expand=True, pady=(4, 8))
        text = tk.Text(frame, height=height, width=1, wrap="word", font=FONT,
                       relief="flat", padx=10, pady=8, undo=editable,
                       highlightthickness=1, highlightbackground=BORDER,
                       highlightcolor=ACCENT, bg=BG if editable else PANEL,
                       fg=TEXT, insertbackground=TEXT)
        scrollbar = ttk.Scrollbar(frame, orient="vertical", command=text.yview)
        text.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        text.pack(side="left", fill="both", expand=True)
        if not editable:
            text.configure(state="disabled")
        return text

    # ------------------------------------------------------------ 历史页
    def _tab_history(self, parent):
        f = ttk.Frame(parent, padding=16)
        heading = ttk.Frame(f)
        heading.pack(fill="x", pady=(0, 10))
        self.lbl_history = ttk.Label(heading, text="最近记录", style="H.TLabel")
        self.lbl_history.pack(side="left")
        ttk.Button(heading, text="刷新", command=self.refresh_history).pack(side="right")
        self._wrap_label(f, "重新增强使用已保存的设置，完成后将结果复制到剪贴板。").pack(fill="x", pady=(0, 8))

        row = ttk.Frame(f)
        row.pack(side="bottom", fill="x", pady=(8, 0))
        self.btn_reenhance = ttk.Button(row, text="重新增强", command=self.reenhance)
        self.btn_reenhance.pack(side="left")
        self.btn_copy_before = ttk.Button(row, text="复制原文", command=lambda: self._copy("before"))
        self.btn_copy_before.pack(side="left", padx=6)
        self.btn_copy_after = ttk.Button(row, text="复制结果", command=lambda: self._copy("after"))
        self.btn_copy_after.pack(side="left")
        self.btn_clear_history = ttk.Button(row, text="清空历史", command=self.clear_history)
        self.btn_clear_history.pack(side="right")

        table = ttk.Frame(f)
        table.pack(fill="both", expand=True)
        cols = ("time", "status", "elapsed", "before")
        self.tree = ttk.Treeview(table, columns=cols, show="headings", height=6, selectmode="browse")
        for c, t, w in (("time", "时间", 128), ("status", "状态", 58),
                        ("elapsed", "耗时", 80), ("before", "输入摘要", 340)):
            self.tree.heading(c, text=t)
            self.tree.column(c, width=int(w * self.s), minwidth=48, anchor="w", stretch=c == "before")
        scrollbar = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview)
        scrollbar.pack(side="right", fill="y")
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.pack(side="left", fill="both", expand=True)
        self.tree.tag_configure("failed", foreground=ERR_C)
        self.tree.bind("<<TreeviewSelect>>", self._on_history_select)

        ttk.Label(f, text="详情").pack(anchor="w", pady=(12, 4))
        self.txt_detail = self._text_area(f, height=7)
        return f

    # ------------------------------------------------------------ 关于页
    def _tab_about(self, parent):
        page, f = self._scroll_page(parent, "about")
        ttk.Label(f, text="提示词增强器", style="H.TLabel").pack(anchor="w")
        # 版本 + 构建时间：同一个版本号可能被打包多次（改完 bug 重出一版），
        # 只显示 "1.0.0" 根本分不清手里是哪份。构建时间对得上 exe 的文件修改时间。
        _ver = getattr(self.app, "version", "dev")
        _build = getattr(self.app, "build", "")
        _verline = f"版本 {_ver}" + (f" · 构建 {_build}" if _build else "")
        ttk.Label(f, text=_verline, style="Muted.TLabel").pack(anchor="w", pady=(2, 0))
        if hasattr(self.app, "log"):
            self.app.log(f"[ui] 关于页版本文本={_verline!r}")
        ttk.Label(f, text="把 WorkBuddy 的「提示词增强」搬到你自己的客户端上。",
                  style="Muted.TLabel").pack(anchor="w", pady=(4, 14))
        lines = [
            "· 选中草稿 → 按热键 → 原地替换成更明确的提示词；",
            "· 撤销：按撤销热键，或直接在编辑框里 Ctrl+Z；",
            "· 数据完全本地：只有请求会发到你自己配置的 API 地址；",
            "· API Key 用 Windows DPAPI 加密，存于 %APPDATA%\\PromptEnhancer\\；",
            "· 历史记录只保存在本地 history.json，最多 100 条；",
            "· 托盘图标右键可以暂停增强、撤销、退出。",
        ]
        for t in lines:
            self._wrap_label(f, t, style="Muted.TLabel").pack(fill="x", pady=2)
        ttk.Label(f, text="热键冲突或按了没反应？先在托盘菜单里确认没被暂停；"
                          "若目标程序以管理员权限运行，请同样以管理员权限启动本程序。",
                  style="Hint.TLabel", wraplength=int(620 * self.s),
                  justify="left").pack(anchor="w", pady=(16, 0))
        return page

    # ------------------------------------------------------------ 取值/赋值
    def _set_entry(self, key: str, value) -> None:
        w = self.vars[key]
        w.delete(0, "end")
        w.insert(0, str(value))

    def _fill_fields(self, s: dict) -> None:
        """把一份设置铺到界面上。加载配置（_load_values）和恢复默认共用这一段。"""
        self._loading = True
        for key in ("base_url", "api_key", "model"):
            self._set_entry(key, s.get(key, ""))
        for key in ("timeout", "temperature", "max_tokens", "min_len", "undo_window_sec"):
            self._set_entry(key, s.get(key, ""))
        for key, _label, _hint in HOTKEY_FIELDS:
            w = self.vars[key]
            if hasattr(w, "set_hotkey"):
                w.set_hotkey(str(s.get(key, "")))
            else:
                self._set_entry(key, s.get(key, ""))
        for key, var in self.checks.items():
            var.set(bool(s.get(key, False)))

        dsys, dusr = self.app.default_templates()
        self.txt_system.delete("1.0", "end")
        self.txt_system.insert("1.0", s.get("system_template") or dsys)
        self.txt_user.delete("1.0", "end")
        self.txt_user.insert("1.0", s.get("user_template") or dusr)
        self._loading = False
        self._update_dirty()

    def _load_values(self):
        self._fill_fields(self.app.settings)

    def _watch_changes(self):
        for key, widget in self.vars.items():
            variable = tk.StringVar(master=self, value=widget.get())
            widget.configure(textvariable=variable)
            variable.trace_add("write", self._update_dirty)
            self._field_vars[key] = variable
            page = self._pages["hotkey" if key.startswith("hotkey_") or key == "undo_window_sec" else "model"]
            widget.bind("<FocusIn>", lambda _e, p=page, w=widget: p.reveal(w), add="+")
        for variable in self.checks.values():
            variable.trace_add("write", self._update_dirty)
        for widget in (self.txt_system, self.txt_user):
            widget.edit_modified(False)
            widget.bind("<<Modified>>", lambda _e, w=widget: self._text_changed(w))

    def _text_changed(self, widget):
        if widget.edit_modified():
            widget.edit_modified(False)
            self._update_dirty()

    def _snapshot(self):
        return ({key: widget.get() for key, widget in self.vars.items()},
                {key: variable.get() for key, variable in self.checks.items()},
                self.txt_system.get("1.0", "end-1c"), self.txt_user.get("1.0", "end-1c"))

    def _update_dirty(self, *_args):
        if self._loading or self._baseline is None:
            return
        self._dirty = self._snapshot() != self._baseline
        self.lbl_dirty.configure(text="有未保存的修改" if self._dirty else "设置已同步",
                                 foreground=WARN_C if self._dirty else MUTED)

    def _mark_saved(self):
        self._baseline = self._snapshot()
        self._update_dirty()

    def _save_shortcut(self, event):
        if not isinstance(event.widget, HotkeyEntry):
            self.save()
            return "break"

    def collect(self) -> dict:
        s = dict(self.app.settings)
        for key in ("base_url", "api_key", "model", "hotkey_enhance",
                    "hotkey_undo", "hotkey_settings"):
            s[key] = self.vars[key].get().strip()
        for key in NUMBER_FIELDS:
            s[key] = parse_number(key, self.vars[key].get().strip())
        for key, var in self.checks.items():
            s[key] = bool(var.get())

        dsys, dusr = self.app.default_templates()
        s["system_template"] = self.txt_system.get("1.0", "end").strip()
        s["user_template"] = self.txt_user.get("1.0", "end").strip()
        if s["system_template"] == dsys.strip():
            s["system_template"] = ""
        if s["user_template"] == dusr.strip():
            s["user_template"] = ""
        s["_geometry"] = self.geometry()
        validate_connection(s)
        if s["user_template"] and "{input}" not in s["user_template"]:
            raise SettingsError("user_template", "user 模板里必须有 {input} 占位符，否则原文会被丢掉。")
        return s

    # ------------------------------------------------------------ 动作
    def _invalid_hotkey(self) -> str:
        """保存前校验快捷键，返回错误说明；全部合法返回空串。

        录入框是"按什么存什么"，用户很可能只按了修饰键就去点保存（框里是
        "ctrl"）。与其等注册失败后报一个错误码，不如在这里说清楚缺什么。
        """
        try:
            from win_core import parse_hotkey
        except Exception:  # noqa: BLE001
            return ""
        seen = {}
        for key, label, _hint in HOTKEY_FIELDS:
            spec = self.vars[key].get().strip()
            if not spec:
                continue            # 留空是合法的：表示不注册这个热键
            try:
                parsed = parse_hotkey(spec)
            except ValueError as e:
                return f"「{label}」填的是「{spec}」—— {e}"
            if parsed in seen:
                return f"「{seen[parsed]}」与「{label}」用了同一个快捷键，请为它们设置不同的组合键。"
            seen[parsed] = label
        return ""

    def _show_validation_error(self, error):
        field = error.field
        if field == "user_template":
            self.nb.select(2)
            self.txt_user.focus_set()
        else:
            hotkey_page = field.startswith("hotkey_") or field == "undo_window_sec"
            self.nb.select(1 if hotkey_page else 0)
            self.vars[field].focus_set()
            self._pages["hotkey" if hotkey_page else "model"].reveal(self.vars[field])
        self.set_status(str(error), ERR_C)
        messagebox.showerror("请检查设置", str(error), parent=self)

    def save(self) -> bool:
        try:
            s = self.collect()
        except SettingsError as error:
            self._show_validation_error(error)
            return False
        bad = self._invalid_hotkey()
        if bad:
            self.nb.select(1)
            messagebox.showerror("快捷键有问题", bad + "\n\n修改之后再点「保存并应用」。",
                                 parent=self)
            self.set_status("快捷键不合法，什么都没保存", ERR_C)
            return False
        try:
            ok, msg = (self.on_save or self.app.save_settings)(s)
        except (OSError, ValueError) as error:
            self.set_status(f"保存失败：{error}", ERR_C)
            messagebox.showerror("保存失败", str(error), parent=self)
            return False
        if ok or all(self.app.settings.get(key) == value for key, value in s.items() if key != "_geometry"):
            self._mark_saved()
        self.set_status(msg, OK_C if ok else ERR_C)
        if not ok:
            messagebox.showwarning("设置未完全生效", msg, parent=self)
        return ok

    def restore_defaults(self):
        """把设置还原成出厂默认值（API Key 除外）。

        为什么保留 API Key：它是要有成本才拿到的东西，一键重置把它抹掉得不偿失；
        真想清掉，手动删输入框里的内容即可。
        实际写入发生在「保存并应用」那一步，所以点错也不用怕 —— 关掉窗口不保存，
        配置文件一个字都不会动。
        """
        if not messagebox.askyesno(
                "恢复默认设置",
                "下面这些会变回出厂默认值：\n\n"
                "· 模型、API 地址、超时 / 温度 / 最大输出 / 最短触发长度\n"
                "· 三个全局快捷键（Ctrl + Alt + E / Z / O）\n"
                "· 行为开关、撤销有效窗口\n"
                "· 提示词模板（system / user）\n\n"
                "API Key 会保留。\n"
                "还要点「保存并应用」才会真正写进配置文件。\n\n要继续吗？",
                parent=self):
            return
        from store import DEFAULT_SETTINGS
        values = dict(DEFAULT_SETTINGS)
        values["api_key"] = self.vars["api_key"].get()      # 唯一保留的一项
        self._fill_fields(values)
        self.set_status("已恢复默认设置，记得点「保存并应用」让它生效。", MUTED)

    def restore_templates(self):
        if not messagebox.askyesno("恢复默认模板", "将替换当前编辑的两个模板，保存后生效。继续吗？", parent=self):
            return
        dsys, dusr = self.app.default_templates()
        self.txt_system.delete("1.0", "end")
        self.txt_system.insert("1.0", dsys)
        self.txt_user.delete("1.0", "end")
        self.txt_user.insert("1.0", dusr)
        self._update_dirty()
        self.set_status("已恢复内置默认模板，记得点「保存并应用」。", MUTED)

    def test_connection(self):
        if self._test_running:
            return
        try:
            s = self.collect()
        except SettingsError as error:
            self._show_validation_error(error)
            return
        self._test_running = True
        self._tested_settings = s
        self.btn_test.configure(state="disabled")
        self.lbl_test.configure(text="正在测试当前表单，请稍候…", foreground=ACCENT)

        def work():
            try:
                from core import enhance
                result = enhance("帮我写一段清晰的产品介绍", base_url=s["base_url"], api_key=s["api_key"],
                                 model=s["model"], timeout=min(float(s["timeout"]), 30.0),
                                 temperature=float(s["temperature"]), max_tokens=min(int(s["max_tokens"]), 256),
                                 system_template=s["system_template"] or None,
                                 user_template=s["user_template"] or None)
                self._test_results.put((result, ""))
            except Exception as error:  # Keep all Tk calls on the UI thread, including failures.
                self._test_results.put((None, f"测试未完成（{type(error).__name__}），请重试。"))

        threading.Thread(target=work, daemon=True, name="ConnectionTest").start()
        self.after(80, self._poll_test)

    def _poll_test(self):
        try:
            result, error = self._test_results.get_nowait()
        except queue.Empty:
            self.after(80, self._poll_test)
            return
        self._test_running = False
        self.btn_test.configure(state="normal")
        try:
            current = self.collect()
            fields = ("base_url", "api_key", "model", "timeout", "temperature", "max_tokens", "system_template", "user_template")
            stale = any(current[key] != self._tested_settings[key] for key in fields)
        except SettingsError:
            stale = True
        suffix = "\n表单已修改，请重新测试当前设置。" if stale else "\n测试不会自动保存设置。"
        if result is not None and result.ok:
            self.lbl_test.configure(text=f"连接正常 · {result.elapsed_ms} ms\n返回：{result.text[:120]}" + suffix, foreground=OK_C)
        else:
            self.lbl_test.configure(text="连接失败：" + (error or result.error or "未收到有效响应")[:250] + suffix, foreground=ERR_C)

    # ------------------------------------------------------------ 历史
    def refresh_history(self):
        selected = self._current() if hasattr(self, "_history") else None
        self._history = self.app.store.load_history()
        self.tree.delete(*self.tree.get_children())
        for i, h in enumerate(self._history):
            summary = " ".join(str(h.get("before") or "").split())[:90]
            self.tree.insert("", "end", iid=str(i),
                             values=(h.get("time", ""),
                                     "成功" if h.get("ok") else "失败",
                                     f"{h.get('elapsed_ms', 0)} ms",
                                     summary), tags=() if h.get("ok") else ("failed",))
            if selected and h == selected:
                self.tree.selection_set(str(i))
                self.tree.see(str(i))
        if not self.tree.selection() and self._history:
            self.tree.selection_set("0")
        self.lbl_history.configure(text=f"最近记录 · {len(self._history)} 条" if self._history else "还没有增强记录")
        self.btn_clear_history.configure(state="normal" if self._history else "disabled")
        self._on_history_select()

    def _current(self):
        sel = self.tree.selection()
        if not sel:
            return None
        try:
            return self._history[int(sel[0])]
        except (ValueError, IndexError):
            return None

    def _update_history_actions(self):
        h = self._current()
        self.btn_reenhance.configure(state="normal" if h and h.get("before") and not getattr(self.app, "busy", False) else "disabled")
        self.btn_copy_before.configure(state="normal" if h and h.get("before") else "disabled")
        self.btn_copy_after.configure(state="normal" if h and h.get("after") else "disabled")

    def _on_history_select(self, _evt=None):
        h = self._current()
        self._update_history_actions()
        self.txt_detail.configure(state="normal")
        self.txt_detail.delete("1.0", "end")
        if not h:
            self.txt_detail.insert("1.0", "增强完成后，原文、结果和失败原因会出现在这里。\n选中一条记录可以复制内容或重新增强。")
            self.txt_detail.configure(state="disabled")
            return
        source = {"selection": "选中文本", "clipboard": "剪贴板", "manual": "历史重试"}.get(h.get("source"), h.get("source", "未知"))
        self.txt_detail.insert("1.0",
                               f"时间：{h.get('time')}    来源：{source}    "
                               f"耗时：{h.get('elapsed_ms')} ms    模型：{h.get('model')}\n"
                               + (f"错误：{h.get('error')}\n" if h.get("error") else "")
                               + "\n【原文】\n" + (h.get("before") or "")
                               + "\n\n【增强后】\n" + (h.get("after") or ""))
        self.txt_detail.configure(state="disabled")

    def _copy(self, field):
        h = self._current()
        if not h:
            return
        content = h.get(field) or ""
        if not content:
            return
        if self.app.copy_to_clipboard(content):
            self.set_status("原文已复制到剪贴板" if field == "before" else "结果已复制到剪贴板", OK_C)
        else:
            self.set_status("剪贴板正被其他程序占用，请重试。", ERR_C)

    def reenhance(self):
        h = self._current()
        if not h:
            return
        self.app.reenhance(h.get("before") or "")
        self._on_history_select()

    def clear_history(self):
        if not messagebox.askyesno("清空历史", "确定要清空全部历史记录吗？", parent=self):
            return
        try:
            self.app.store.clear_history()
        except OSError as error:
            self.set_status("清空失败，历史记录已保留。", ERR_C)
            messagebox.showerror("无法清空历史", str(error), parent=self)
            return
        self.refresh_history()
        self.set_status("历史已清空", MUTED)

    # ------------------------------------------------------------
    def set_status(self, text: str, color: str = MUTED):
        self.lbl_status.configure(text=text, foreground=color)
        if hasattr(self, "_history"):
            self._update_history_actions()

    def set_state_text(self, text: str):
        self.lbl_state.configure(text=text)

    def show(self):
        self.deiconify()
        self.lift()
        self.focus_force()
        self.refresh_history()

    def confirm_discard_changes(self) -> bool:
        """Return whether a user-initiated close can continue without losing a draft."""
        self._update_dirty()
        if not self._dirty:
            return True
        answer = messagebox.askyesnocancel("有未保存的修改", "是否先保存当前修改？\n\n是：保存并继续\n否：放弃修改\n取消：返回设置", parent=self)
        if answer is None:
            return False
        if answer:
            return self.save()
        self._load_values()
        self._mark_saved()
        self.set_status("已放弃未保存的修改", MUTED)
        return True

    def request_quit(self):
        if self.confirm_discard_changes():
            self.app.quit_app()

    def hide(self):
        if not self.confirm_discard_changes():
            return
        try:
            self.app.settings["_geometry"] = self.geometry()
        except tk.TclError:
            pass
        self.withdraw()
        self.app.notify_once_hidden()
