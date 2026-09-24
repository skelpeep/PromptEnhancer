#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""自绘 / 自管理的小部件。

1) 复选框图标：ttk 的 clam 主题下，勾选态的勾会被画成一个粗十字，看起来像
   "未选中 / 禁用"，容易被误读。自己生成 4x 超采样的 PNG 再用
   tk.Checkbutton(image=, selectimage=) 显示，形状和颜色都完全可控，而且能按
   当前 DPI 缩放，在高分屏上不糊。图标在内存里生成后 base64 塞给
   tk.PhotoImage(data=...)，不落任何临时文件。

2) HotkeyEntry：快捷键录入框。点一下它，然后直接按组合键，不用手打
   "ctrl+alt+e"（见类文档）。
"""

from __future__ import annotations

import base64
import math
import os
import sys
import tkinter as tk
from tkinter import ttk

# 冻结尾不碰 sys.path：dirname(dirname(__file__)) 会算成 %TEMP%，见 main.py 开头的注释
if not getattr(sys, "frozen", False):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from png_util import to_png  # noqa: E402

BORDER = (198, 201, 214)
FILL_ON = (79, 70, 229)
WHITE = (255, 255, 255)
SS = 4


def _seg_dist(px: float, py: float, ax: float, ay: float, bx: float, by: float) -> float:
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def _render_hi(size: int, on: bool, box: float) -> bytearray:
    """在 size*SS 的画布上渲染（二值覆盖，靠降采样得到抗锯齿）。"""
    W = size * SS
    side = W * box
    off = (W - side) / 2
    radius = side * 0.24
    border_w = max(SS * 1.0, side * 0.075)
    c = off + side / 2

    p1 = (off + side * 0.26, off + side * 0.535)
    p2 = (off + side * 0.435, off + side * 0.715)
    p3 = (off + side * 0.755, off + side * 0.315)
    tick_w = max(SS * 1.4, side * 0.11)

    buf = bytearray(W * W * 4)
    for y in range(W):
        row = y * W * 4
        for x in range(W):
            cx, cy = x + 0.5, y + 0.5
            dx = max(abs(cx - c) - (side / 2 - radius), 0.0)
            dy = max(abs(cy - c) - (side / 2 - radius), 0.0)
            box_d = math.hypot(dx, dy) - radius
            if box_d > 0:
                continue                       # 框外透明

            if on:
                r, g, b = FILL_ON
                t = min(_seg_dist(cx, cy, *p1, *p2), _seg_dist(cx, cy, *p2, *p3))
                if t <= tick_w / 2:
                    r, g, b = WHITE
            else:
                if box_d > -border_w:
                    r, g, b = BORDER               # 边框
                else:
                    r, g, b = WHITE                # 空心

            i = row + x * 4
            buf[i], buf[i + 1], buf[i + 2], buf[i + 3] = r, g, b, 255
    return buf


def _downsample(buf: bytearray, size: int) -> bytes:
    W = size * SS
    out = bytearray(size * size * 4)
    area = SS * SS
    for y in range(size):
        for x in range(size):
            tr = tg = tb = ta = 0
            for dy in range(SS):
                base = (y * SS + dy) * W
                for dx in range(SS):
                    j = (base + x * SS + dx) * 4
                    a = buf[j + 3]
                    tr += buf[j] * a
                    tg += buf[j + 1] * a
                    tb += buf[j + 2] * a
                    ta += a
            k = (y * size + x) * 4
            if ta == 0:
                out[k:k + 4] = b"\x00\x00\x00\x00"
            else:
                out[k] = tr // ta
                out[k + 1] = tg // ta
                out[k + 2] = tb // ta
                out[k + 3] = ta // area
    return bytes(out)


def check_png(size: int, on: bool, box: float = 0.84) -> bytes:
    """生成一个复选框图标（PNG 字节）。size 是最终像素尺寸。"""
    return to_png(size, size, _downsample(_render_hi(size, on, box), size))


_cache: dict[tuple[int, int, bool], tk.PhotoImage] = {}


def check_photo(size: int, on: bool, master=None) -> tk.PhotoImage:
    key = (id(master.tk) if master is not None else 0, size, on)
    if key not in _cache:
        png = check_png(size, on)
        _cache[key] = tk.PhotoImage(master=master, data=base64.b64encode(png).decode("ascii"))
    return _cache[key]


def make_checkbutton(parent, text: str, variable: tk.BooleanVar, *,
                    dpi_scale: float = 1.0, bg: str = "#FFFFFF", fg: str = "#1F2233",
                    font=None, wraplength: int = 0) -> tk.Checkbutton:
    """自绘复选框（图标 + 文字），点击文字也能切换。"""
    px = max(12, int(round(17 * dpi_scale)))
    cb = tk.Checkbutton(
        parent, text=text, variable=variable,
        image=check_photo(px, False, parent), selectimage=check_photo(px, True, parent),
        compound="left", anchor="w", justify="left",
        # ⚠️ indicatoron 必须关掉。默认（1）时 Tk 会**再画一个原生小方框**在我们
        # 的图标左边，看起来像两个复选框并排；关掉之后只剩我们自绘的那一个。
        indicatoron=0,
        bg=bg, fg=fg, activebackground=bg, activeforeground=fg,
        selectcolor=bg, bd=0, highlightthickness=1, highlightbackground=bg,
        highlightcolor="#4F46E5", takefocus=1,
    )
    if font:
        cb.configure(font=font)
    if wraplength:
        cb.configure(wraplength=wraplength)
    return cb


class ScrollableFrame(ttk.Frame):
    """Scrollable settings page that keeps its content fitted to the viewport."""

    def __init__(self, parent, *, padding=20):
        super().__init__(parent)
        self.canvas = tk.Canvas(self, highlightthickness=0, bg="#FFFFFF",
                                width=1, height=1)
        self.scrollbar = ttk.Scrollbar(self, orient="vertical",
                                       command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.scrollbar.pack(side="right", fill="y")
        self.body = ttk.Frame(self.canvas, padding=padding)
        self._window = self.canvas.create_window((0, 0), window=self.body, anchor="nw")
        self.body.bind("<Configure>", self._content_changed)
        self.canvas.bind("<Configure>", self._resized)
        self._top = self.winfo_toplevel()
        self._wheel_binding = self._top.bind("<MouseWheel>", self._wheel, add="+")
        self.bind("<Destroy>", self._destroyed, add="+")

    def _content_changed(self, _event=None):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _resized(self, event):
        self.canvas.itemconfigure(self._window, width=event.width)

    def _wheel(self, event):
        widget = event.widget
        if isinstance(widget, (tk.Text, ttk.Spinbox)):
            return
        while widget is not None and widget is not self:
            widget = getattr(widget, "master", None)
        if widget is self and self.body.winfo_reqheight() > self.canvas.winfo_height():
            units = -int(event.delta / 120) if abs(event.delta) >= 120 else (-1 if event.delta > 0 else 1)
            self.canvas.yview_scroll(units, "units")
            return "break"

    def reveal(self, widget):
        self.update_idletasks()
        top = widget.winfo_rooty() - self.body.winfo_rooty()
        bottom = top + widget.winfo_height()
        visible_top = self.canvas.canvasy(0)
        visible_height = self.canvas.winfo_height()
        content_height = max(1, self.body.winfo_reqheight())
        if top < visible_top:
            self.canvas.yview_moveto(max(0, top - 12) / content_height)
        elif bottom > visible_top + visible_height:
            self.canvas.yview_moveto((bottom - visible_height + 12) / content_height)

    def _destroyed(self, event):
        if event.widget is self:
            try:
                self._top.unbind("<MouseWheel>", self._wheel_binding)
            except tk.TclError:
                pass


# ---------------------------------------------------------------- 快捷键录入
# Win 键在 Tk 里既可能报 win_l / win_r，也可能报 super_* / meta_*，全都归到 win
_MOD_KEYSYMS = {
    "control_l": "ctrl", "control_r": "ctrl",
    "shift_l": "shift", "shift_r": "shift",
    "alt_l": "alt", "alt_r": "alt",
    "win_l": "win", "win_r": "win",
    "super_l": "win", "super_r": "win",
    "meta_l": "win", "meta_r": "win",
}

# Tk keysym（小写）→ win_core.KEYS 里认得的键名。两边只有交集能当快捷键。
_KEYSYM_MAP = {
    "return": "enter", "kp_enter": "enter", "escape": "esc", "esc": "esc",
    "tab": "tab", "space": "space",
    "grave": "`", "minus": "-", "equal": "=", "bracketleft": "[",
    "bracketright": "]", "semicolon": ";", "apostrophe": "'",
    "comma": ",", "period": ".", "slash": "/", "backslash": "\\",
    "backspace": "backspace", "delete": "delete", "insert": "insert",
    "home": "home", "end": "end", "prior": "pageup", "next": "pagedown",
    "up": "up", "down": "down", "left": "left", "right": "right",
    "exclam": "1", "at": "2", "numbersign": "3", "dollar": "4",
    "percent": "5", "asciicircum": "6", "ampersand": "7", "asterisk": "8",
    "parenleft": "9", "parenright": "0", "underscore": "-", "plus": "=",
    "braceleft": "[", "braceright": "]", "colon": ";", "quotedbl": "'",
    "less": ",", "greater": ".", "question": "/", "bar": "\\", "asciitilde": "`",
}
# F1–F12：Tk 报 "F8"，配置里写 "f8"
_KEYSYM_MAP.update({f"f{i}": f"f{i}" for i in range(1, 13)})
_PLAIN_KEYS = frozenset("0123456789abcdefghijklmnopqrstuvwxyz")


def keysym_to_key(keysym: str) -> str | None:
    """Tk 的 keysym → 配置里用的键名；不认识的键返回 None。"""
    low = str(keysym).lower()
    if low in _KEYSYM_MAP:
        return _KEYSYM_MAP[low]
    # 单字符键：按住 Shift 按 E 时 Tk 报的是 "E"，统一按小写记
    # （Shift 本身由修饰键列表单独管，不会丢）
    if len(low) == 1 and low in _PLAIN_KEYS:
        return low
    return None


class HotkeyEntry(ttk.Entry):
    """按键录入框：点一下它，然后直接按组合键。

    规则（按实际使用习惯定的）：
      * 只按修饰键 → 框里就显示这个修饰键（点一下框再按 Ctrl，框里变成 ctrl）；
      * 接着按 E → ctrl+e；再按别的键会换掉主键（ctrl+k）；
      * 修饰键是**累加**的：先按 Ctrl、再按 Shift、再按 E → ctrl+shift+e；
      * 想重来：按 Esc 清空；或点别的输入框再点回来（重新获得焦点后，下一次
        按键会整体覆盖原值）。

    框里显示的字符串就是最终存进配置的值（"ctrl+e" 这种），不做美化 ——
    所见即所得，省得用户猜"我按的和它存的是不是一个东西"。
    """

    def __init__(self, parent, on_change=None, on_message=None, **kw):
        super().__init__(parent, **kw)
        self._mods: list[str] = []
        self._main: str | None = None
        self._fresh = True          # 下一次按键是否整体覆盖（刚拿到焦点时为真）
        self._on_change = on_change
        self._on_message = on_message
        self.bind("<KeyPress>", self._on_press)
        self.bind("<KeyRelease>", self._on_release)
        self.bind("<FocusIn>", self._on_focus_in)

    # ------------------------------------------------------------ 对外
    def set_hotkey(self, spec: str) -> None:
        """外部写值（加载配置 / 恢复默认设置），同时把录入状态清干净。"""
        self._mods, self._main, self._fresh = [], None, True
        self.delete(0, "end")
        if spec:
            self.insert(0, spec)

    def get_hotkey(self) -> str:
        return self.get().strip()

    # ------------------------------------------------------------ 内部
    def _say(self, msg: str, level: str = "hint") -> None:
        if self._on_message:
            self._on_message(msg, level)

    def _on_focus_in(self, _evt=None) -> None:
        self._mods, self._main, self._fresh = [], None, True
        self._say("现在按你要用的组合键，例如按住 Ctrl 再按 E")

    def _on_press(self, ev):
        """拦下按键自己处理，并 return "break" —— 不让 ttk 往框里插字符。"""
        keysym = str(ev.keysym)
        state = int(getattr(ev, "state", 0))
        if keysym.lower() == "tab" and not (state & 0x2000C):
            target = self.tk_focusPrev() if state & 0x0001 else self.tk_focusNext()
            if target:
                target.focus_set()
            return "break"
        if keysym.lower() in ("escape", "esc"):
            self._mods, self._main, self._fresh = [], None, False
            self._render()
            self._say("已清空。留空表示不注册这个热键")
            return "break"

        mod = _MOD_KEYSYMS.get(keysym.lower())
        if mod:
            if self._fresh:
                self._mods, self._main = [], None
                self._fresh = False
            if mod not in self._mods:
                self._mods.append(mod)
            self._render()
            self._say("已按下 " + "+".join(self._mods) + "，再加一个字母/数字键就完整了")
            return "break"

        key = keysym_to_key(keysym)
        if key is None:
            self._say(f"「{keysym}」不能当快捷键，换一个（字母、数字、F1–F12、方向键等）",
                      "warn")
            return "break"
        if self._fresh:
            self._mods = [name for name, mask in (("ctrl", 0x4), ("alt", 0x20008), ("shift", 0x1)) if state & mask]
            self._fresh = False
        self._main = key
        self._render()
        return "break"

    def _on_release(self, event):
        mod = _MOD_KEYSYMS.get(str(event.keysym).lower())
        if mod in self._mods:
            self._mods.remove(mod)
            if not self._mods:
                self._fresh = True

    def _render(self) -> None:
        spec = "+".join(self._mods + ([self._main] if self._main else []))
        self.delete(0, "end")
        if spec:
            self.insert(0, spec)
        if self._on_change:
            self._on_change(spec)
