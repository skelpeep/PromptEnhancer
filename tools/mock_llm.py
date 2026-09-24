#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""本地假上游：用于不花钱验证 enhancer / proxy 的链路是否通。

启动：
    python mock_llm.py            # 监听 127.0.0.1:18080
（默认端口刻意避开 8899 等 Windows 保留段，那些端口 bind 会报 WinError 10013）
它实现了 OpenAI 兼容的：
    POST /v1/chat/completions
    POST /v1/responses
返回内容是固定的“增强结果”，并把收到的最后一条 user 消息回显到日志里，
方便确认代理有没有真的改写请求体。
"""

from __future__ import annotations

import json
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ENHANCED = "【MOCK增强】请详细说明需求的目标、范围、约束条件和期望输出格式，并给出一份可执行的步骤清单。"
DELAY = 0.0        # 人为延迟，方便观察"正在增强…"的过程提示


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *a):  # 静音默认日志
        pass

    def _send(self, obj, code=200):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path.rstrip("/").endswith("/models"):
            self._send({"object": "list", "data": [{"id": "mock-model"}]})
        else:
            self._send({"error": "not found"}, 404)

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n).decode("utf-8", "replace")
        try:
            body = json.loads(raw)
        except Exception:
            body = {}
        if DELAY:
            time.sleep(DELAY)

        path = self.path.rstrip("/")
        if path.endswith("/chat/completions"):
            msgs = body.get("messages") or []
            user_msgs = [m for m in msgs if m.get("role") == "user"]
            print(f"[mock] chat/completions model={body.get('model')} "
                  f"user_tail={str(user_msgs[-1].get('content'))[-200:]!r}", flush=True)
            self._send({
                "id": "chatcmpl-mock", "object": "chat.completion",
                "model": body.get("model", "mock-model"),
                "choices": [{"index": 0, "finish_reason": "stop",
                             "message": {"role": "assistant", "content": ENHANCED}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
            })
        elif path.endswith("/responses"):
            print(f"[mock] responses model={body.get('model')} "
                  f"input={json.dumps(body.get('input'), ensure_ascii=False)[-300:]}", flush=True)
            self._send({
                "id": "resp-mock", "object": "response",
                "model": body.get("model", "mock-model"), "status": "completed",
                "output": [{"type": "message", "role": "assistant",
                            "content": [{"type": "output_text", "text": ENHANCED}]}],
            })
        else:
            self._send({"error": {"message": "unsupported path " + self.path}}, 404)


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 18080
    DELAY = float(sys.argv[2]) if len(sys.argv) > 2 else 0.0
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"mock upstream on http://127.0.0.1:{port} (delay={DELAY}s)", flush=True)
    srv.serve_forever()
