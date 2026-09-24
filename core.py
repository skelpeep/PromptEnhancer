#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""提示词增强核心模块。

设计取自 WorkBuddy 的实现思路，并做了工程化加固：

1. 独立一次调用：不带主对话上下文，由 system + user 双模板驱动，
   走 OpenAI 兼容的 /chat/completions。
2. 输出即结果：禁止解释、禁止 markdown 围栏、禁止 meta 说明，
   返回后再剥掉可能出现的包裹引号。
3. 语言一致性优先：中进中出、英进英出，混合输入保持混合。
4. 失败静默降级：任何异常都返回原文 ok=False，绝不阻塞用户输入。
5. 模板可外置：通过环境变量指向自定义模板文件，方便你自己迭代 prompt。

用法（库）：
    from core import enhance
    r = enhance("帮我写个爬虫")
    print(r.text if r.ok else "增强失败，沿用原文")
"""

from __future__ import annotations

import json
import math
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass


def get_version() -> str:
    for directory in (getattr(sys, "_MEIPASS", ""), os.path.dirname(os.path.abspath(__file__))):
        if not directory:
            continue
        try:
            with open(os.path.join(directory, "VERSION"), encoding="utf-8-sig") as file:
                version = file.read().strip()
            if re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version):
                return version
        except (OSError, UnicodeError):
            pass
    return "0.0.0"


VERSION = get_version()

# --------------------------------------------------------------------------
# 默认模板（可被 ENHANCER_SYSTEM_FILE / ENHANCER_USER_FILE 指向的文件覆盖）
# --------------------------------------------------------------------------

DEFAULT_SYSTEM_TEMPLATE = """You are a Prompt Engineering Expert specializing in improving user prompts for an AI assistant that can write code, operate local files and produce documents.

TASK: analyze the user's prompt and rewrite it into a more effective version while preserving its core purpose.

ANALYSIS PROCESS:
1. Identify the main objective, the ambiguities, the missing context and the unclear constraints.
2. Apply prompt engineering principles: make the scope concrete, state explicit constraints, specify the expected output format, add only the context that is genuinely implied, and remove redundancy.
3. Produce the enhanced version: keep the original goal, stay realistic, never invent facts, files, data or requirements the user did not provide.

HARD CONSTRAINTS (highest priority first):
1. LANGUAGE: respond in the exact same language as the user's input. Chinese in -> Chinese out. English in -> English out. Mixed input stays naturally mixed. Never translate the user's intent into another language.
2. OUTPUT ONLY the enhanced prompt. No preface, no explanation, no markdown code fences, no wrapping quotes, no labels such as "Enhanced prompt:", no language meta-notes.
3. Do NOT answer the user's question, do NOT provide how-to steps, do NOT ask follow-up questions.
4. Do NOT suggest specific technologies, frameworks or tools unless the user mentioned them.
5. Focus on WHAT should be achieved, not HOW to implement it.
6. Keep the result under about 800 characters. It must read as one coherent prompt and must never end with a dangling colon, an unfinished list or a trailing conjunction.

EXAMPLE
input: "A website for my dog"
output: "Create a website dedicated to my dog. Present the information and photos I provide in a clear, visually appealing layout that is easy to use on desktop and mobile. Use placeholders for any missing details instead of inventing facts about my dog."
"""

DEFAULT_USER_TEMPLATE = """Improve the user prompt below while preserving its intent and language.

USER INPUT:
{input}

TASK:
Rewrite the user input into a clearer, more specific prompt for the target AI assistant.

CRITICAL PRIORITY - LANGUAGE CONSISTENCY:
1. Detect the language of the user input and write the enhanced prompt in that same language.
2. Chinese input -> entirely Chinese output. English input -> entirely English output.
3. Any other language -> that same language.
4. Mixed-language input -> keep a natural matching mix; do not collapse it into one language.
5. These language rules are behavior instructions only; never output language analysis or language labels.

ENHANCEMENT REQUIREMENTS:
1. Return only the enhanced prompt text. No explanations, prefaces, markdown fences, labels or analysis.
2. Never output meta notes such as "User input is in Chinese" or "Response must be in Chinese".
3. Preserve the user's original intent, topic, constraints and target output type. Do not answer the request.
4. Always make a substantive enhancement when possible: clarify the task, the scope, the constraints and the expected output.
5. If the original prompt is already clear, lightly polish it instead of returning it unchanged.
6. Keep it complete and concise. Never end with an unfinished list, dangling conjunction or trailing colon.
7. Do not add unrelated requirements, unsupported facts or unnecessary sections.

EXAMPLES:
User input (Chinese): "请帮我解释这段代码的功能"
Enhanced prompt: "请解释这段代码的主要功能、执行流程和关键逻辑，并指出可能需要注意的边界情况。"

User input (English): "Please explain what this code does"
Enhanced prompt: "Explain what this code does, including its main purpose, key control flow and any important edge cases."

User input (Mixed): "这段代码有 bug，can you help me fix it?"
Enhanced prompt: "请分析这段代码中的 bug，explain the root cause, and provide a minimal fix with the necessary verification steps."

BAD OUTPUT EXAMPLE (never do this):
User input is in Chinese -> Response must be in Chinese.
请解释这段代码的主要功能

GOOD OUTPUT EXAMPLE:
请解释这段代码的主要功能、执行流程和关键逻辑，并指出可能需要注意的边界情况。
"""


# --------------------------------------------------------------------------
# 配置
# --------------------------------------------------------------------------

@dataclass
class EnhanceResult:
    ok: bool
    text: str          # ok=True 时是增强结果；ok=False 时是原文
    error: str = ""
    model: str = ""
    elapsed_ms: int = 0


def _first_env(*names: str, default: str = "") -> str:
    for n in names:
        v = os.environ.get(n, "").strip()
        if v:
            return v
    return default


_CONFIG_ENV = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.env")
_config_loaded = False


def load_dotenv() -> None:
    """把脚本同目录的 config.env 读进环境变量（已存在的环境变量优先，不覆盖）。

    格式：KEY=VALUE，# 开头为注释，值两边的引号会被剥掉。
    """
    global _config_loaded
    if _config_loaded:
        return
    _config_loaded = True
    if not os.path.isfile(_CONFIG_ENV):
        return
    try:
        with open(_CONFIG_ENV, "r", encoding="utf-8-sig") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                key = key.strip()
                val = val.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = val
    except (OSError, UnicodeError):
        pass


def _load_template(env_key: str, default: str) -> str:
    path = os.environ.get(env_key, "").strip()
    if path and os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8-sig") as f:
                content = f.read().strip()
            if content:
                return content
        except (OSError, UnicodeError):
            pass
    return default


def load_templates() -> tuple[str, str]:
    """返回 (system_template, user_template)。"""
    return (
        _load_template("ENHANCER_SYSTEM_FILE", DEFAULT_SYSTEM_TEMPLATE),
        _load_template("ENHANCER_USER_FILE", DEFAULT_USER_TEMPLATE),
    )


@dataclass
class Config:
    base_url: str
    api_key: str
    model: str
    timeout: float = 25.0
    temperature: float = 0.4
    max_tokens: int = 1200

    @staticmethod
    def resolve(
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
    ) -> "Config":
        load_dotenv()
        cfg = Config(
            base_url=(base_url or _first_env(
                "ENHANCER_BASE_URL", "OPENAI_BASE_URL", "OPENAI_API_BASE",
                default="https://api.openai.com/v1")),
            api_key=(api_key if api_key is not None else _first_env(
                "ENHANCER_API_KEY", "OPENAI_API_KEY", default="")),
            model=(model or _first_env(
                "ENHANCER_MODEL", "OPENAI_MODEL", default="gpt-4o-mini")),
        )
        return cfg


def normalize_base(base_url: str) -> str:
    """把 base_url 归一化成不带结尾斜杠、且带 /v1 的形式。"""
    parsed = urllib.parse.urlsplit((base_url or "").strip() or "https://api.openai.com/v1")
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("base_url must be an http(s) URL")
    if parsed.username is not None or parsed.password is not None or parsed.fragment:
        raise ValueError("base_url must not contain credentials or a fragment")
    parsed.port  # Validate malformed port numbers before sending a request.
    path = parsed.path.rstrip("/") or "/v1"
    return urllib.parse.urlunsplit(parsed._replace(path=path))


def chat_completions_url(base_url: str) -> str:
    parsed = urllib.parse.urlsplit(normalize_base(base_url))
    path = parsed.path
    if not path.endswith("/chat/completions"):
        path += "/chat/completions"
    return urllib.parse.urlunsplit(parsed._replace(path=path))


# --------------------------------------------------------------------------
# 清洗
# --------------------------------------------------------------------------

_FENCE_RE = re.compile(r"^```[a-zA-Z0-9_-]*\s*|\s*```$")
_QUOTE_PAIRS = {"\"": "\"", "'": "'", "“": "”", "‘": "’", "「": "」", "《": "》"}


def strip_wrapping_quotes(text: str) -> str:
    """去掉首尾包裹的引号 / 代码围栏，等价于 WorkBuddy 的 stripWrappingQuotes。"""
    t = (text or "").strip()
    if t.startswith("```"):
        t = _FENCE_RE.sub("", t).strip()
    t = _FENCE_RE.sub("", t).strip()
    # 成对包裹的引号才剥
    if len(t) >= 2 and _QUOTE_PAIRS.get(t[0]) == t[-1]:
        t = t[1:-1].strip()
    # 常见的前缀噪音
    t = re.sub(r"^(Enhanced prompt|增强后的提示词|优化后的提示词)\s*[:：]\s*", "", t).strip()
    return t


def render_user_prompt(template: str, text: str) -> str:
    if "{input}" in template:
        return template.replace("{input}", text)
    return template + "\n\n" + text


# --------------------------------------------------------------------------
# 调用
# --------------------------------------------------------------------------

_LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1", "0.0.0.0"}


def build_opener(url: str):
    """构造 opener。

    重要：很多 IDE / 沙箱会在环境变量里注入 http_proxy，如果无脑走系统代理，
    连 127.0.0.1 的本地端口都会被转发出去而失败。所以本地地址一律直连，
    同时尊重 NO_PROXY，另外提供 ENHANCER_NO_PROXY=1 强制全直连。
    """
    host = (urllib.parse.urlparse(url).hostname or "").lower()
    no_proxy = (os.environ.get("NO_PROXY") or os.environ.get("no_proxy") or "")
    listed = {h.strip().lower() for h in no_proxy.split(",") if h.strip()}
    bypass = (
        host in _LOCAL_HOSTS
        or host in listed
        or os.environ.get("ENHANCER_NO_PROXY", "") == "1"
    )
    if bypass:
        return urllib.request.build_opener(urllib.request.ProxyHandler({}))
    return urllib.request.build_opener()


def _post_json(url: str, payload: dict, api_key: str, timeout: float) -> dict:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": f"prompt-enhancer/{VERSION}",
    }
    if api_key:
        headers["Authorization"] = "Bearer " + api_key
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with build_opener(url).open(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8", "replace")
    return json.loads(raw)


def enhance(
    text: str,
    *,
    base_url: str | None = None,
    api_key: str | None = None,
    model: str | None = None,
    timeout: float | None = None,
    system_template: str | None = None,
    user_template: str | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
) -> EnhanceResult:
    """把 text 增强一次。失败时返回原文 + ok=False，调用方可静默降级。

    system_template / user_template 非空时优先于配置文件与默认模板，
    方便桌面 App 把模板放在设置里直接传进来。
    """
    original = text if isinstance(text, str) else ""
    if not isinstance(text, str):
        return EnhanceResult(False, original, "invalid_input: text must be a string")
    if not original.strip():
        return EnhanceResult(False, original, "empty_input")

    started = time.monotonic()
    cfg = None
    try:
        cfg = Config.resolve(base_url, api_key, model)
        if timeout is not None:
            cfg.timeout = timeout
        if temperature is not None:
            cfg.temperature = temperature
        if max_tokens is not None:
            cfg.max_tokens = max_tokens
        if not math.isfinite(cfg.timeout) or cfg.timeout <= 0:
            raise ValueError("timeout must be a positive finite number")
        if not math.isfinite(cfg.temperature) or not 0 <= cfg.temperature <= 2:
            raise ValueError("temperature must be between 0 and 2")
        if isinstance(cfg.max_tokens, bool) or not isinstance(cfg.max_tokens, int) or cfg.max_tokens <= 0:
            raise ValueError("max_tokens must be a positive integer")
        default_system, default_user = load_templates()
        system_tpl = (system_template or "").strip() or default_system
        user_tpl = (user_template or "").strip() or default_user
        url = chat_completions_url(cfg.base_url)
    except Exception as e:  # noqa: BLE001 - Configuration errors also preserve the draft.
        return EnhanceResult(False, original, f"{type(e).__name__}: {e}",
                             cfg.model if cfg else "", int((time.monotonic() - started) * 1000))

    base_payload = {
        "model": cfg.model,
        "messages": [
            {"role": "system", "content": system_tpl},
            {"role": "user", "content": render_user_prompt(user_tpl, original)},
        ],
        "stream": False,
    }
    full_payload = dict(base_payload, temperature=cfg.temperature,
                        max_tokens=cfg.max_tokens)

    attempts = [full_payload, base_payload]  # 某些模型拒收 temperature/max_tokens，退一步重试
    last_err = ""
    for payload in attempts:
        try:
            remaining = cfg.timeout - (time.monotonic() - started)
            if remaining <= 0:
                raise TimeoutError("enhancement timeout exceeded")
            data = _post_json(url, payload, cfg.api_key, remaining)
            content = ""
            try:
                content = data["choices"][0]["message"]["content"] or ""
            except (KeyError, IndexError, TypeError):
                # 兼容 Responses 风格或异常结构
                content = data.get("output_text", "") if isinstance(data, dict) else ""
            if isinstance(content, list):
                content = "".join(part["text"] for part in content
                                  if isinstance(part, dict) and isinstance(part.get("text"), str))
            if not isinstance(content, str):
                content = ""
            cleaned = strip_wrapping_quotes(content)
            if not cleaned:
                last_err = "empty_result"
                continue
            return EnhanceResult(True, cleaned, "", cfg.model,
                                 int((time.monotonic() - started) * 1000))
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read(4096).decode("utf-8", "replace")[:300]
            except Exception:
                pass
            finally:
                e.close()
            last_err = f"HTTP {e.code}: {detail}"
            if e.code in (400, 415, 422):
                continue  # 参数不兼容，换精简 payload 重试
            break
        except Exception as e:  # noqa: BLE001 - 任何异常都降级
            last_err = f"{type(e).__name__}: {e}"
            break

    return EnhanceResult(False, original, last_err, cfg.model,
                         int((time.monotonic() - started) * 1000))
