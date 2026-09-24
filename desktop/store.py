#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""配置与历史持久化。

位置：%APPDATA%\\PromptEnhancer\\
    settings.json   设置（api_key 用 DPAPI 加密后存 api_key_enc）
    history.json    最近 100 条增强记录
    instance.json   运行中实例的 {pid, hwnd}，供第二个实例唤起窗口
"""

from __future__ import annotations

import base64
import json
import math
import os
import sys
import tempfile
import time

# 只在"源码直接运行"时补 sys.path；**冻结尾绝不能碰 sys.path**。
# 原因见 main.py 开头的长注释：冻结后 __file__ 在 _MEIPASS 里，
# os.path.dirname(os.path.dirname(__file__)) 会算成 %TEMP% 本身 —— 把整个临时目录
# 插到 sys.path 最前面，%TEMP% 里任何同名的 ui.pyc / toast.pyc 都会顶掉 exe 内的模块，
# 表现为"exe 明明是新的、行为却是旧的"，而且这个插入发生在 import 阶段，
# 比 main.py 里的清理更晚，所以每个模块都得自己守好。
if not getattr(sys, "frozen", False):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from win_core import dpapi_protect, dpapi_unprotect

APP_NAME = "PromptEnhancer"

DEFAULT_SETTINGS = {
    # 模型
    "base_url": "https://api.openai.com/v1",
    "api_key": "",
    "model": "gpt-4o-mini",
    "timeout": 25.0,
    "temperature": 0.4,
    "max_tokens": 1200,
    "min_len": 4,
    # 快捷键（ctrl+alt+s / ctrl+alt+p 在不少机器上已被占用，实测 1409，
    # 所以默认设置键用 ctrl+alt+o；被占用时设置页会提示，换一个即可）
    "hotkey_enhance": "ctrl+alt+e",
    "hotkey_undo": "ctrl+alt+z",
    "hotkey_settings": "ctrl+alt+o",
    # 行为
    "auto_paste": True,
    "notify_success": True,
    "undo_window_sec": 600,
    # ⚠️ 默认关闭。开启后，如果按热键时没有选中任何文本，程序会拿"上一次复制到
    # 剪贴板的内容"去增强 —— 用起来像是认错了对象（本次 bug 反馈的元凶之一）。
    "fallback_clipboard": False,
    # 模板（空 = 用 core.py 里的默认模板）
    "system_template": "",
    "user_template": "",
    # 窗口位置（自动记忆）
    "_geometry": "",
    # 配置格式版本。默认值发生变化且"旧默认值有害"时，靠它把老配置升上来。
    "_schema": 2,
}

SCHEMA_VERSION = 2


def _finite_number(value) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def app_dir() -> str:
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    path = os.path.join(base, APP_NAME)
    os.makedirs(path, exist_ok=True)
    return path


class Store:
    def __init__(self, directory: str | None = None):
        self.dir = directory or app_dir()
        os.makedirs(self.dir, exist_ok=True)
        self.settings_path = os.path.join(self.dir, "settings.json")
        self.history_path = os.path.join(self.dir, "history.json")
        self.instance_path = os.path.join(self.dir, "instance.json")

    # ------------------------------------------------------------ 设置
    def load_settings(self) -> dict:
        data = dict(DEFAULT_SETTINGS)
        try:
            with open(self.settings_path, "r", encoding="utf-8") as f:
                saved = json.load(f)
            if isinstance(saved, dict):
                enc = saved.pop("api_key_enc", "")
                plain = saved.pop("api_key_plain", "")
                if enc:
                    try:
                        saved["api_key"] = dpapi_unprotect(base64.b64decode(enc)).decode(
                            "utf-8", "replace")
                    except Exception:  # noqa: BLE001
                        saved["api_key"] = ""
                if plain and not saved.get("api_key"):
                    # 兼容旧版明文配置，下次保存时转换为 DPAPI 密文。
                    saved["api_key"] = str(plain)
                for k, v in saved.items():
                    if k in DEFAULT_SETTINGS and self._valid_value(k, v):
                        data[k] = v
                self._migrate(data, saved)
        except (OSError, ValueError, UnicodeError):
            pass
        return data

    @staticmethod
    def _valid_value(key: str, value) -> bool:
        default = DEFAULT_SETTINGS[key]
        if isinstance(default, bool):
            return isinstance(value, bool)
        if isinstance(default, str):
            return isinstance(value, str)
        if not _finite_number(value):
            return False
        if isinstance(default, int) and not isinstance(value, int):
            return False
        if key == "temperature":
            return 0 <= value <= 2
        return value > 0

    @staticmethod
    def _migrate(data: dict, saved: dict) -> None:
        """把老版本配置里"已经被判定为有害"的默认值升上来。

        只改 DEFAULT_SETTINGS 里**默认值变过**的项，而且仅当用户的配置里还是
        "那个旧默认值"时才动 —— 用户自己手动改过的值一律尊重。
        有老配置文件的人如果跳过这一步，会一直沿用有 bug 的行为（比如没有任何
        选中文本时悄悄拿剪贴板旧内容去增强），所以这步必须做。
        """
        schema = saved.get("_schema", 0)
        if isinstance(schema, int) and schema >= SCHEMA_VERSION:
            return
        # v2：这两个默认值变了，旧默认值会让"抓不到选中文本"时静默用剪贴板内容
        if saved.get("fallback_clipboard") is True:
            data["fallback_clipboard"] = False
        if saved.get("notify_success") is False:
            data["notify_success"] = True
        data["_schema"] = SCHEMA_VERSION

    def save_settings(self, settings: dict) -> None:
        data = {k: settings.get(k, v) for k, v in DEFAULT_SETTINGS.items()}
        for name, value in data.items():
            if not self._valid_value(name, value):
                raise OSError(f"Invalid setting: {name}")
        key = str(data.pop("api_key", "") or "")
        if key:
            try:
                data["api_key_enc"] = base64.b64encode(
                    dpapi_protect(key.encode("utf-8"))).decode("ascii")
            except Exception as exc:
                raise OSError("API Key 加密失败，配置未保存，请检查 Windows 用户凭据") from exc
        self._atomic_write(self.settings_path, data)

    @staticmethod
    def _atomic_write(path: str, obj) -> None:
        fd, tmp = tempfile.mkstemp(prefix=".pe-", suffix=".tmp",
                                   dir=os.path.dirname(path))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(obj, f, ensure_ascii=False, indent=2, allow_nan=False)
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)

    # ------------------------------------------------------------ 历史
    def load_history(self) -> list:
        try:
            with open(self.history_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, list):
                return []
            defaults = {"ts": 0, "time": "", "before": "", "after": "", "ok": False,
                        "elapsed_ms": 0, "model": "", "error": "", "source": "selection"}
            result = []
            for item in data:
                if not isinstance(item, dict):
                    continue
                # Keep the known schema only; unknown metadata can contain NaN too.
                entry = {key: item.get(key, value) for key, value in defaults.items()}
                if not all(isinstance(entry[key], str) for key in
                           ("before", "after", "time", "model", "error", "source")):
                    continue
                if not isinstance(entry["ok"], bool):
                    continue
                if not all(_finite_number(entry[key]) and entry[key] >= 0
                           for key in ("ts", "elapsed_ms")):
                    continue
                if not isinstance(entry["elapsed_ms"], int):
                    continue
                result.append(entry)
                if len(result) == 100:
                    break
            return result
        except (OSError, ValueError, UnicodeError):
            return []

    def add_history(self, *, before: str, after: str, ok: bool, elapsed_ms: int,
                    model: str, error: str = "", source: str = "selection") -> dict:
        entry = {
            "ts": time.time(),
            "time": time.strftime("%m-%d %H:%M:%S"),
            "before": before,
            "after": after,
            "ok": ok,
            "elapsed_ms": elapsed_ms,
            "model": model,
            "error": error,
            "source": source,
        }
        items = self.load_history()
        items.insert(0, entry)
        del items[100:]
        try:
            self._atomic_write(self.history_path, items)
        except OSError:
            pass
        return entry

    def clear_history(self) -> None:
        self._atomic_write(self.history_path, [])

    # ------------------------------------------------------------ 实例信息
    def write_instance(self, pid: int, hwnd: int) -> None:
        try:
            self._atomic_write(self.instance_path, {"pid": pid, "hwnd": hwnd})
        except OSError:
            pass

    def read_instance(self) -> dict:
        try:
            with open(self.instance_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError, UnicodeError):
            return {}

    def clear_instance(self) -> None:
        try:
            os.remove(self.instance_path)
        except OSError:
            pass
