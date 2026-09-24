#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""核对一个 PyInstaller 打出来的 exe 里，到底装的是**哪一版源码**。

为什么需要它：onefile exe 把 Python 模块压进内部 PYZ（zlib），所以在 exe 上
`grep "恢复默认设置"` 是搜不到的；比对文件时间戳又只能证明"重建过"，不能证明
"装进去了"。于是经常出现两种误判 —— 明明是新版却被当成旧的，或者构建静默
失败、拿老 exe 当新的发出去。

这里的做法是把 PYZ 解开，取回字节码，然后按**符号名**核对每项改动在不在。
符号名（函数名、类名、模块级常量）会出现在 code object 的 co_names 里，
界面文案会出现在 co_consts 里，两者都查，命中即算通过。

    # 核对单个文件
    python tools/dev/exe_contains.py dist/PromptEnhancer.exe

    # 新旧对比（旧的那个一般能在 %TEMP% 的验证残留里翻到）
    python tools/dev/exe_contains.py new.exe old.exe

需要装了 PyInstaller 的解释器（用它自带的 reader，不额外解析二进制格式）。
本项目构建用的是 Python 3.12，注意用 3.12 的解释器跑本脚本，字节码版本才对得上。
"""

from __future__ import annotations

import marshal
import os
import sys
import types

# 每项改动 → 该改动的"指纹"。模块名用扁平名（项目里是 sys.path 注入式导入，
# 没有 desktop 包前缀），符号名随便挑一个这版新增、旧版没有的。
FEATURES = [
    ("浮层停留时长固定 0.6s", "toast", "HOLD_MS"),
    ("win_shell 用统一停留时长", "win_shell", "TOAST_HOLD_MS"),
    ("状态栏统一出口（失败不残留）", "main", "_set_status"),
    ("快捷键按键录入控件", "widgets", "HotkeyEntry"),
    ("复选框关掉原生指示器", "widgets", "indicatoron"),
    ("恢复默认设置按钮", "ui", "恢复默认设置"),
    ("恢复默认设置的处理函数", "ui", "restore_defaults"),
    # 冻结尾不把 %TEMP% 塞进 sys.path（否则 %TEMP% 里的同名 pyc 会顶掉 exe 内的模块）
    ("运行时模块来源自检", "main", "module_sources"),
]


def _as_code(res):
    """extract() 可能给 code object，也可能给 pyc 字节流；其余（DLL 之类）返回 None。

    容易踩的坑：PyInstaller 6.x 的 `ZlibArchiveReader.extract()` **直接返回
    code object**，不是 (typecode, data) 元组 —— 照着老文档写成 `res[1]` 会拿到
    code 对象再喂给 marshal.loads()，抛异常后被自己的 except 吞掉，最后表现为
    "PYZ 里有 136 个模块，但一个都没读到"。
    """
    if isinstance(res, tuple) and len(res) == 2:
        return _as_code(res[1])                  # CArchive.extract() 给 (typecode, data)
    if isinstance(res, types.CodeType):
        return res
    if isinstance(res, (bytes, bytearray)):
        try:
            co = marshal.loads(bytes(res))
        except Exception:  # noqa: BLE001
            return None
        return co if isinstance(co, types.CodeType) else None
    return None


def load_code_objects(exe: str) -> dict:
    """解开 onefile exe，返回 {模块名: code object}。"""
    from PyInstaller.archive.readers import CArchiveReader

    arc = CArchiveReader(exe)
    out = {}

    # 1) CArchive 顶层：入口脚本 main 和少数模块以未压缩条目放在这里，不在 PYZ 内
    for name in arc.toc:
        if "." in name or name.upper() == name:      # 跳过 DLL / VERSION 之类
            continue
        try:
            co = _as_code(arc.extract(name))
        except Exception:  # noqa: BLE001
            continue
        # 入口脚本的 co_name 是 '<module>'，不是文件名 —— 只认 == name 会把它漏掉
        if co is not None and co.co_name in (name, "<module>"):
            out[name] = co

    # 2) PYZ：其余被 import 的模块（含本项目自己的包内模块）
    pyz_names = [n for n in arc.toc if "PYZ" in n]
    if pyz_names:
        pyz = arc.open_embedded_archive(pyz_names[0])
        for name in pyz.toc:
            try:
                co = _as_code(pyz.extract(name))
            except Exception:  # noqa: BLE001
                continue
            if co is not None:
                out[name] = co
    return out


def collect(code, strings: set, names: set) -> None:
    for c in code.co_consts:
        if isinstance(c, str):
            strings.add(c)
        elif isinstance(c, types.CodeType):
            collect(c, strings, names)
    names.update(code.co_names)
    names.update(code.co_varnames)


PROJECT_MODULES = ("main", "toast", "win_shell", "widgets", "ui",
                   "win_core", "store", "core", "png_util")


def probe(exe: str) -> dict:
    codes = load_code_objects(exe)
    per = {}
    for mod in PROJECT_MODULES:
        if mod not in codes:
            continue
        strings, names = set(), set()
        collect(codes[mod], strings, names)
        per[mod] = {"strings": strings, "names": names}
    return {"modules": sorted(codes), "per": per}


def hit(r: dict, mod: str, sym: str) -> bool:
    m = r["per"].get(mod)
    if not m:
        return False
    return sym in m["names"] or any(sym in s for s in m["strings"])


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    if not args:
        print(__doc__)
        return 2

    results = []
    for exe in args:
        if not os.path.isfile(exe):
            print(f"找不到文件：{exe}")
            return 2
        r = probe(exe)
        r["exe"] = exe
        results.append(r)
        print(f"\n=== {exe} ===")
        print(f"  PYZ 模块数 {len(r['modules'])}；读到本项目模块：{sorted(r['per'])}")
        if "ui" not in r["per"]:
            print("  ⚠️ 没读到 ui 模块，下面结论不可信")

    print("\n" + "=" * 72)
    head = "改动项".ljust(34) + "".join(f"  {os.path.basename(r['exe'])[:14]:>14}" for r in results)
    print(head)
    print("-" * 72)
    passed = 0
    for label, mod, sym in FEATURES:
        hits = [hit(r, mod, sym) for r in results]
        if all(hits):
            passed += 1
        cells = [f"  {'✅' if h else '❌':>14}" for h in hits]
        print(f"{label.ljust(34)}" + "".join(cells))

    total = len(FEATURES)
    print(f"\n说明：上表每列对应一个 exe，✅ = 该改动已打进去，❌ = 没有（就是旧版）。")
    # 机器可读的结论行 —— 打包脚本只认它。别去 grep 表格里的 ✅/❌：
    # 上面那行说明文字本身就带这两个字符，grep 必然误判（踩过）。
    ok = passed == total
    print(f"RESULT={'OK' if ok else 'FAIL'} {passed}/{total}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
