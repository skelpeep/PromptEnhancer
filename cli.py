#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""命令行入口：把一段文字增强成更明确的提示词。

用法：
    python cli.py "帮我写个爬虫"
    echo "帮我写个爬虫" | python cli.py
    python cli.py --file draft.txt
    python cli.py --json "..."          # 输出结构化结果（含耗时/错误）
    python cli.py --strict "..."        # 失败时返回非 0 退出码，而不是静默用原文
    python cli.py --model gpt-4o-mini "..."

环境变量：ENHANCER_BASE_URL / ENHANCER_API_KEY / ENHANCER_MODEL
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from core import enhance  # noqa: E402


def _positive_timeout(value: str) -> float:
    try:
        timeout = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("timeout must be a positive number") from exc
    if not math.isfinite(timeout) or timeout <= 0:
        raise argparse.ArgumentTypeError("timeout must be a positive finite number")
    return timeout


def main() -> int:
    ap = argparse.ArgumentParser(description="提示词增强 CLI")
    ap.add_argument("text", nargs="*", help="要增强的文本；省略则从 stdin 读取")
    ap.add_argument("--file", help="从文件读取输入")
    ap.add_argument("--model", help="覆盖模型")
    ap.add_argument("--base-url", help="覆盖 base_url")
    ap.add_argument("--timeout", type=_positive_timeout, help="超时秒数")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--strict", action="store_true", help="失败时返回退出码 1")
    args = ap.parse_args()

    if args.file and args.text:
        ap.error("--file cannot be combined with positional text")
    try:
        if args.file:
            with open(args.file, "r", encoding="utf-8-sig") as f:
                text = f.read()
        elif args.text:
            text = " ".join(args.text)
        elif sys.stdin.isatty():
            ap.print_usage(sys.stderr)
            print("provide text, --file, or piped input", file=sys.stderr)
            return 2
        else:
            text = sys.stdin.read()
    except (OSError, UnicodeError) as exc:
        print(f"cannot read input: {exc}", file=sys.stderr)
        return 2

    if not text.strip():
        print("empty input", file=sys.stderr)
        return 2

    r = enhance(text, base_url=args.base_url, model=args.model, timeout=args.timeout)

    if args.json:
        print(json.dumps({
            "ok": r.ok, "text": r.text, "error": r.error,
            "model": r.model, "elapsed_ms": r.elapsed_ms,
        }, ensure_ascii=False, indent=2))
    else:
        print(r.text)
        if not r.ok:
            print(f"[warn] enhance failed: {r.error}", file=sys.stderr)

    if args.strict and not r.ok:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
