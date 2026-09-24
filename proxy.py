#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""提示词增强中间层代理（OpenAI 兼容）。

思路：在客户端和上游网关之间插一层 HTTP 代理，拦截请求体里第一条 user 消息，
用增强后的版本替换掉，再原样转发。客户端完全无感，不用改任何配置以外的东西。

链路：
    Codex / 任意 OpenAI 兼容客户端
        -> 本代理 127.0.0.1:18765/v1
            -> 你的上游（CC Switch 本地代理 / 中转站 / 官方）
    增强用的模型走 ENHANCER_* 配置，与上游解耦（建议用一个便宜快的小模型）。

为什么把上游设成本地 CC Switch 代理特别合适：
你本来就有 CC Switch 的本地代理接管 Codex 请求，把 base_url 改成这一层即可，
不用动网关和密钥，出问题把端口换回来就回滚了。

用法：
    set ENHANCER_UPSTREAM_BASE=http://127.0.0.1:15721/v1
    set ENHANCER_BASE_URL=https://你的中转站/v1
    set ENHANCER_API_KEY=sk-xxx
    set ENHANCER_MODEL=gpt-4o-mini
    python proxy.py                    # 监听 127.0.0.1:18765

关闭改写（临时放行某次请求）：请求头加  X-Enhancer: off

风险提示（务必了解）：
1. 每条消息多一次 LLM 调用，首字延迟增加 1~3 秒；
2. 改写会破坏上游的 prompt cache（前缀变了）；
3. proxy.log 记录改写长度、耗时和错误，不记录提示词正文；
4. 对代码类客户端，Codex 的 input 数组里带大量环境上下文项，
   本代理只会改“第一条纯文本 user 项”，其余原样透传。
"""

from __future__ import annotations

import http.client
import json
import os
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from core import Config, VERSION, enhance, load_dotenv  # noqa: E402

load_dotenv()  # 允许用脚本同目录的 config.env 配置

UPSTREAM = os.environ.get("ENHANCER_UPSTREAM_BASE", "").rstrip("/")
LISTEN_HOST = os.environ.get("ENHANCER_PROXY_HOST", "127.0.0.1")
LISTEN_PORT = int(os.environ.get("ENHANCER_PROXY_PORT", "18765"))
MIN_ENHANCE_LEN = int(os.environ.get("ENHANCER_MIN_LEN", "4"))
LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "proxy.log")
MAX_BODY_BYTES = 16 * 1024 * 1024

HOP_HEADERS = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailer", "trailers", "transfer-encoding", "upgrade", "host",
    "content-length",
}


def log(msg: str) -> None:
    import datetime
    line = f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def _target_path(base_path: str, request_path: str) -> str:
    """Join API paths without repeating an overlapping prefix such as /v1."""
    incoming = urllib.parse.urlsplit(request_path)
    base = [part for part in base_path.split("/") if part]
    parts = incoming.path.lstrip("/").split("/")
    overlap = 0
    for size in range(1, min(len(base), len(parts)) + 1):
        if base[-size:] == parts[:size]:
            overlap = size
    path = "/" + "/".join(base + parts[overlap:])
    return urllib.parse.urlunsplit(("", "", path, incoming.query, ""))


def _hop_headers(headers) -> set[str]:
    nominated = ",".join(value for name, value in headers
                         if name.lower() == "connection")
    return HOP_HEADERS | {name.strip().lower() for name in nominated.split(",")}


# ------------------------------------------------------------------ 改写逻辑
def _enhance_text(text: str) -> str:
    """对一段文本做增强；任何情况下都返回可用的文本。"""
    if len(text.strip()) < MIN_ENHANCE_LEN:
        return text
    r = enhance(text)
    if not r.ok or not r.text.strip():
        log(f"  增强失败，保持原文: {r.error}")
        return text
    if r.text.strip() == text.strip():
        return text
    log(f"  改写 {len(text)} -> {len(r.text)} 字符 / {r.elapsed_ms}ms")
    return r.text


def _rewrite_chat(body: dict) -> bool:
    """改写 /chat/completions 的第一条 user 消息。返回是否发生改写。"""
    msgs = body.get("messages")
    if not isinstance(msgs, list):
        return False
    for m in msgs:
        if not isinstance(m, dict) or m.get("role") != "user":
            continue
        content = m.get("content")
        if isinstance(content, str):
            new = _enhance_text(content)
            if new != content:
                m["content"] = new
                return True
            return False
        if isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text" \
                        and isinstance(part.get("text"), str):
                    new = _enhance_text(part["text"])
                    if new != part["text"]:
                        part["text"] = new
                        return True
                    return False
        return False
    return False


def _rewrite_responses(body: dict) -> bool:
    """改写 /responses 的第一条纯文本 user 输入项。"""
    inp = body.get("input")
    if isinstance(inp, str):
        new = _enhance_text(inp)
        if new != inp:
            body["input"] = new
            return True
        return False
    if not isinstance(inp, list):
        return False
    for item in inp:
        if not isinstance(item, dict) or item.get("role") != "user":
            continue
        content = item.get("content")
        if isinstance(content, str):
            new = _enhance_text(content)
            if new != content:
                item["content"] = new
                return True
            return False
        if isinstance(content, list):
            for part in content:
                if not isinstance(part, dict):
                    continue
                if part.get("type") in ("input_text", "text") \
                        and isinstance(part.get("text"), str):
                    new = _enhance_text(part["text"])
                    if new != part["text"]:
                        part["text"] = new
                        return True
                    return False
        return False
    return False


# ------------------------------------------------------------------ HTTP 服务
class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = f"prompt-enhancer-proxy/{VERSION}"

    def log_message(self, fmt, *a):
        pass

    # ---- 转发 ----
    def _proxy(self, method: str) -> None:
        if not UPSTREAM:
            self._json_error(500, "ENHANCER_UPSTREAM_BASE 未配置")
            return

        if self.headers.get("Transfer-Encoding"):
            self._json_error(501, "chunked request bodies are not supported; use Content-Length")
            return
        lengths = self.headers.get_all("Content-Length", [])
        if len(lengths) > 1 or (lengths and not lengths[0].strip().isascii()) \
                or (lengths and not lengths[0].strip().isdigit()):
            self._json_error(400, "invalid Content-Length")
            return
        try:
            length = int(lengths[0]) if lengths else 0
        except ValueError:
            self._json_error(400, "invalid Content-Length")
            return
        if length > MAX_BODY_BYTES:
            self._json_error(413, "request body exceeds 16 MiB")
            return
        try:
            self.connection.settimeout(30)
            raw = self.rfile.read(length) if length else b""
        except OSError:
            self._json_error(408, "request body timeout")
            return
        if len(raw) != length:
            self._json_error(400, "incomplete request body")
            return
        body_obj = None
        path = urllib.parse.urlsplit(self.path).path
        do_enhance = self.headers.get("X-Enhancer", "").lower() != "off"

        if method == "POST" and raw and path.rstrip("/").endswith(("/chat/completions", "/responses")):
            try:
                body_obj = json.loads(raw)
            except Exception:
                body_obj = None
            if isinstance(body_obj, dict) and do_enhance:
                try:
                    changed = (_rewrite_responses(body_obj)
                               if path.rstrip("/").endswith("/responses")
                               else _rewrite_chat(body_obj))
                    if changed:
                        raw = json.dumps(body_obj, ensure_ascii=False).encode("utf-8")
                        log(f"{method} {path} 已改写首条 user 消息")
                    else:
                        log(f"{method} {path} 无需改写")
                except Exception as e:  # noqa: BLE001
                    log(f"  改写异常，按原文转发: {type(e).__name__}: {e}")

        try:
            parsed = urllib.parse.urlsplit(UPSTREAM)
            if parsed.scheme not in ("http", "https") or not parsed.hostname \
                    or parsed.username is not None or parsed.password is not None \
                    or parsed.query or parsed.fragment:
                raise ValueError("invalid upstream URL")
            port = parsed.port or (443 if parsed.scheme == "https" else 80)
        except ValueError:
            self._json_error(500, "ENHANCER_UPSTREAM_BASE must be an http(s) base URL")
            return
        target = _target_path(parsed.path, self.path)

        conn_cls = http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection
        conn = None
        try:
            conn = conn_cls(parsed.hostname, port, timeout=300)
            blocked = _hop_headers(self.headers.items()) | {"x-enhancer"}
            headers = {k: v for k, v in self.headers.items()
                       if k.lower() not in blocked}
            headers["Host"] = parsed.netloc
            if raw:
                headers["Content-Length"] = str(len(raw))
            conn.request(method, target, body=raw or None, headers=headers)
            resp = conn.getresponse()
        except Exception as e:  # noqa: BLE001
            if conn is not None:
                conn.close()
            log(f"上游连接失败: {type(e).__name__}: {e}")
            self._json_error(502, f"upstream connect failed: {e}")
            return

        try:
            blocked = _hop_headers(resp.getheaders())
            no_body = method == "HEAD" or resp.status in (204, 304) or resp.status < 200
            clen = resp.getheader("Content-Length") if not resp.chunked else None
            if clen is not None and (not clen.isascii() or not clen.isdigit()):
                clen = None
            self.send_response(resp.status)
            for k, v in resp.getheaders():
                if k.lower() not in blocked:
                    self.send_header(k, v)
            if clen is not None and resp.status != 204:
                self.send_header("Content-Length", clen)
            elif not no_body:
                self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            if not no_body:
                if clen is not None:
                    self._copy(resp)
                else:
                    self._copy_chunked(resp)
        except (OSError, http.client.HTTPException):
            self.close_connection = True
        finally:
            resp.close()
            conn.close()

    def _copy(self, resp, size: int = 8192) -> None:
        try:
            while True:
                chunk = resp.read1(size)
                if not chunk:
                    if resp.length:
                        self.close_connection = True
                    break
                self.wfile.write(chunk)
                self.wfile.flush()
        except (OSError, http.client.HTTPException):
            self.close_connection = True

    def _copy_chunked(self, resp, size: int = 4096) -> None:
        try:
            while True:
                # read1 returns available bytes, keeping SSE events responsive.
                chunk = resp.read1(size)
                if not chunk:
                    break
                self.wfile.write(b"%X\r\n" % len(chunk) + chunk + b"\r\n")
                self.wfile.flush()
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()
        except (OSError, http.client.HTTPException):
            self.close_connection = True

    def _json_error(self, code: int, msg: str) -> None:
        data = json.dumps({"error": {"message": msg, "type": "proxy_error"}},
                          ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Connection", "close")
        self.close_connection = True
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def do_POST(self):
        self._proxy("POST")

    def do_GET(self):
        self._proxy("GET")

    def do_DELETE(self):
        self._proxy("DELETE")

    def do_HEAD(self):
        self._proxy("HEAD")

    def do_PUT(self):
        self._proxy("PUT")

    def do_PATCH(self):
        self._proxy("PATCH")

    def do_OPTIONS(self):
        self._proxy("OPTIONS")


def main() -> int:
    if not UPSTREAM:
        print("请先设置 ENHANCER_UPSTREAM_BASE，例如 http://127.0.0.1:15721/v1",
              file=sys.stderr)
        return 2
    cfg = Config.resolve()
    log(f"提示词增强代理启动: http://{LISTEN_HOST}:{LISTEN_PORT}  ->  {UPSTREAM}")
    log(f"增强模型: {cfg.model} @ {cfg.base_url}")
    try:
        with ThreadingHTTPServer((LISTEN_HOST, LISTEN_PORT), Handler) as server:
            server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
