#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""提示词增强 MCP 服务（stdio，纯标准库，无需 pip 安装）。

给 Claude Desktop / 任何支持 MCP 的客户端用。暴露两样东西：
  * tool  `enhance_prompt` ：把草稿增强为更明确的提示词，走你自己的模型。
  * prompt `enhance`       ：Claude Desktop 里会显示成 /enhance 斜杠命令，
                             输入 `/enhance 帮我写个爬虫`，模型会先调上面那个工具，
                             再把增强结果原样贴给你。

必须说清的边界：MCP 只能给客户端"加工具"，不能改写客户端输入框里的内容。
所以这条路是"让模型帮你调一次增强"，而不是 WorkBuddy 那种原地替换。
想要原地替换请用 hotkey.py。

自测（不需要客户端）：
    echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' | python mcp_server.py

Claude Desktop 配置（注意 3P 版路径不同）：
    标准版：%APPDATA%\\Claude\\claude_desktop_config.json
    3P 版：%LOCALAPPDATA%\\Claude-3p\\claude_desktop_config.json
    {
      "mcpServers": {
        "prompt-enhancer": {
          "command": "C:\\\\Users\\\\你\\\\.workbuddy\\\\binaries\\\\python\\\\versions\\\\3.13.12\\\\python.exe",
          "args": ["D:\\\\path\\\\to\\\\prompt-enhancer\\\\mcp_server.py"],
          "env": {
            "ENHANCER_BASE_URL": "https://你的中转站/v1",
            "ENHANCER_API_KEY": "sk-xxx",
            "ENHANCER_MODEL": "gpt-4o-mini"
          }
        }
      }
    }
改完配置必须重启 Claude Desktop（完全退出，不是关窗口）。
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from core import VERSION, enhance  # noqa: E402

SERVER_NAME = "prompt-enhancer"
SERVER_VERSION = VERSION
DEFAULT_PROTOCOL = "2024-11-05"

TOOL_DESC = (
    "把一段潦草的用户草稿增强成更明确、更具体的提示词，保持原语言。"
    "当用户想优化/润色自己的提问、需求描述或任务指令时调用。"
    "返回的就是可直接使用的提示词正文，不要改写它。"
)

TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "text": {"type": "string", "description": "需要增强的原始草稿"},
        "model": {"type": "string", "description": "可选，覆盖默认模型"},
    },
    "required": ["text"],
    "additionalProperties": False,
}

PROMPT_TEMPLATE = (
    "请调用 enhance_prompt 工具，把下面这段草稿增强成更明确的提示词，"
    "然后把工具返回的结果原文贴给我，不要自己改写或补充。\n\n草稿：\n$ARGUMENTS"
)


def _write(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _result(req_id, result) -> None:
    _write({"jsonrpc": "2.0", "id": req_id, "result": result})


def _error(req_id, code: int, message: str) -> None:
    _write({"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}})


def handle(msg: dict):
    if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0" \
            or not isinstance(msg.get("method"), str):
        _error(None, -32600, "invalid JSON-RPC request")
        return
    method = msg.get("method")
    req_id = msg.get("id")
    if "id" not in msg:
        return  # JSON-RPC notifications never receive a response.
    if isinstance(req_id, bool) or not isinstance(req_id, (str, int, type(None))):
        _error(None, -32600, "invalid request id")
        return
    params = msg.get("params", {})
    if not isinstance(params, dict):
        _error(req_id, -32602, "params must be an object")
        return

    if method == "initialize":
        _result(req_id, {
            "protocolVersion": DEFAULT_PROTOCOL,
            "capabilities": {"tools": {"listChanged": False},
                             "prompts": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        })
    elif method in ("notifications/initialized", "initialized"):
        return
    elif method == "ping":
        _result(req_id, {})
    elif method == "tools/list":
        _result(req_id, {"tools": [{
            "name": "enhance_prompt",
            "description": TOOL_DESC,
            "inputSchema": TOOL_SCHEMA,
        }]})
    elif method == "tools/call":
        name = params.get("name")
        args = params.get("arguments", {})
        if name != "enhance_prompt":
            _result(req_id, {"content": [{"type": "text", "text": f"未知工具: {name}"}],
                             "isError": True})
            return
        if not isinstance(args, dict):
            _error(req_id, -32602, "arguments must be an object")
            return
        text = args.get("text")
        if not isinstance(text, str) or not text.strip() \
                or ("model" in args and not isinstance(args["model"], str)) \
                or set(args) - {"text", "model"}:
            _result(req_id, {"content": [{"type": "text", "text": "text must be a non-empty string; model must be a string"}],
                             "isError": True})
            return
        r = enhance(text, model=args.get("model"))
        if not r.ok:
            _result(req_id, {
                "content": [{"type": "text",
                             "text": f"增强失败（{r.error}）。保留原始草稿：\n\n{text}"}],
                "isError": True,
            })
            return
        _result(req_id, {"content": [{"type": "text", "text": r.text}]})
    elif method == "prompts/list":
        _result(req_id, {"prompts": [{
            "name": "enhance",
            "description": "把草稿增强成更明确的提示词",
            "arguments": [{"name": "draft", "description": "原始草稿", "required": True}],
        }]})
    elif method == "prompts/get":
        if params.get("name") != "enhance":
            _error(req_id, -32602, "未知 prompt")
            return
        args = params.get("arguments", {})
        if not isinstance(args, dict) or not isinstance(args.get("draft"), str) \
                or not args["draft"].strip():
            _error(req_id, -32602, "draft must be a non-empty string")
            return
        draft = args["draft"]
        _result(req_id, {
            "description": "提示词增强",
            "messages": [{"role": "user", "content": {
                "type": "text", "text": PROMPT_TEMPLATE.replace("$ARGUMENTS", draft)}}],
        })
    elif method in ("resources/list", "resources/templates/list"):
        _result(req_id, {"resources": []} if method == "resources/list" else {"resourceTemplates": []})
    else:
        _error(req_id, -32601, f"method not found: {method}")


def main() -> int:
    # MCP stdio is UTF-8 even when Windows uses a legacy console code page.
    for stream in (sys.stdin, sys.stdout):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            _error(None, -32700, "parse error")
            continue
        try:
            handle(msg)
        except Exception as e:  # noqa: BLE001
            if isinstance(msg, dict) and "id" in msg:
                _error(msg["id"], -32603, f"{type(e).__name__}: {e}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
