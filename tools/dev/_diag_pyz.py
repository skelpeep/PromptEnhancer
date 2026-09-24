# -*- coding: utf-8 -*-
"""把 exe 的 PYZ 打开，把 `ui` / `main` 两个模块里跟"版本号/标题"有关的字符串常量全部列出来。

目的：判断 exe 里装的 ui 模块到底是新版还是旧版 —— 别靠"符号存在与否"猜，
直接把常量打出来看。
"""
import glob
import marshal
import os
import sys

from PyInstaller.archive.readers import CArchiveReader

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))


def as_code(obj):
    if hasattr(obj, "co_code"):
        return obj
    if isinstance(obj, (bytes, bytearray)):
        return marshal.loads(bytes(obj))
    if isinstance(obj, tuple):
        for part in obj:
            c = as_code(part)
            if c is not None:
                return c
    return None


def main():
    exe = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "dist", "PromptEnhancer.exe")
    print(f"=== {exe} ===")
    print("mtime:", __import__("time").strftime(
        "%Y-%m-%d %H:%M:%S", __import__("time").localtime(os.stat(exe).st_mtime)))

    car = CArchiveReader(exe)
    toc = car.toc
    pyz_name = next((k for k in toc if k.endswith(".pyz")), None)
    print("PYZ:", pyz_name, "| CArchive 条目数:", len(toc))
    # PyInstaller 6：内嵌 PYZ 要用 CArchive.open_embedded_archive(name)，
    # 直接 ZlibArchiveReader(exe, name) 会把 name 当成 start_offset → TypeError
    pyz = car.open_embedded_archive(pyz_name)

    names = sorted(pyz.toc.keys())
    interesting = [n for n in names if not n.startswith(("encodings", "importlib", "collections"))]
    print("\n模块数:", len(names))
    print("与项目有关的模块:", [n for n in names if n.split(".")[0] in
                          ("ui", "main", "toast", "widgets", "core", "store", "win_core",
                           "win_shell", "png_util", "hotkey", "cli", "mcp_server", "proxy")])

    for mod in ("ui", "main", "toast"):
        print(f"\n--- 模块 {mod} ---")
        if mod not in pyz.toc:
            print("  不在 PYZ 里！")
            continue
        code = as_code(pyz.extract(mod))
        if code is None:
            print("  取不出 code object")
            continue
        print(f"  co_filename={code.co_filename!r}")
        keys = ("设置窗口标题", "· 设置", "提示词增强器", "版本 ", "构建", "初始化", "恢复默认",
                "停留", "toast", "快捷键", "键盘", "初始状态")
        stack = [code]
        found = []
        seen = 0
        while stack:
            c = stack.pop()
            seen += 1
            for k in c.co_consts:
                if hasattr(k, "co_code"):
                    stack.append(k)
                elif isinstance(k, str):
                    if any(s in k for s in keys) and len(k) < 120:
                        found.append(k)
        print(f"  扫描 {seen} 个 code object，命中常量 {len(found)} 条：")
        for s in dict.fromkeys(found):
            print("   ", repr(s))

    # 版本资源文件
    print("\n--- CArchive 顶层条目（含数据文件）---")
    for k in sorted(toc):
        print("  ", k)


if __name__ == "__main__":
    main()
