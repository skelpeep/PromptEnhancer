# -*- coding: utf-8 -*-
r"""清理 %TEMP% 里两类会让程序"跑成旧版"的残留。

第一类 —— 劫持导入的 .pyc：
    onefile exe 解包后 ROOT = %TEMP%，旧版代码会把 %TEMP% 插进 sys.path 最前面，
    于是这些同名 .pyc 会在运行时顶掉 exe 里打进 PYZ 的模块。

第二类 —— zip 直接双击留下的解压缓存 %TEMP%\BNZ.*：
    在压缩包里双击 exe 时，Windows 会把 zip 内容解到 %TEMP%\BNZ.<hash>\ 再运行，
    而且**会一直复用**这份缓存 —— 之后即使换了新 zip，双击还是跑旧副本。

两类残留都不会报错，只会让"新包跑出旧行为"，所以打包/排查前先看一眼。

    python tools/dev/temp_pyc_guard.py --list      # 只列
    python tools/dev/temp_pyc_guard.py --move      # 挪到备份目录（不删）
    python tools/dev/temp_pyc_guard.py --restore   # 从备份目录挪回（做回归实验用）

注意：普通解压之后的 exe 运行留下的 %TEMP%\\_MEI* 目录不在此列 —— 那是 PyInstaller
每次运行自己的解包目录，属正常现象，不用（也不该）清理。
"""

from __future__ import annotations

import argparse
import glob
import os
import shutil
import time

TEMP = os.environ.get("TEMP") or os.environ.get("TMP") or ""
BACKUP = os.path.join(TEMP, "_stale_pyc_backup")
ZIP_BACKUP = os.path.join(TEMP, "_stale_zip_cache_backup")


def listing():
    return sorted(glob.glob(os.path.join(TEMP, "*.pyc")))


def bnz_listing():
    return sorted(d for d in glob.glob(os.path.join(TEMP, "BNZ.*")) if os.path.isdir(d))


def dump(title, items, note="") -> None:
    print(f"{title}：{len(items)} 个" + (f"  {note}" if note else ""))
    for f in items:
        try:
            st = os.stat(f)
            kind = "dir " if os.path.isdir(f) else "file"
            print(f"  [{kind}] {st.st_size:>9}  "
                  f"{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(st.st_mtime))}  "
                  f"{os.path.basename(f)}")
        except OSError as e:
            print(f"  (读不到 {e})  {os.path.basename(f)}")


def move(items, dest) -> int:
    os.makedirs(dest, exist_ok=True)
    n = 0
    for f in items:
        dst = os.path.join(dest, os.path.basename(f))
        try:
            if os.path.exists(dst):
                if os.path.isdir(dst):
                    shutil.rmtree(dst, ignore_errors=True)
                else:
                    os.remove(dst)
            shutil.move(f, dst)
            n += 1
        except OSError as e:
            # 正在被占用（那份 exe 还开着）时挪不动，如实说，不静默跳过
            print(f"  挪不动 {os.path.basename(f)}：{e}")
    return n


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--move", action="store_true")
    ap.add_argument("--restore", action="store_true")
    a = ap.parse_args()

    if not TEMP:
        print("读不到 %TEMP%")
        return 2

    if a.move:
        print(f"已挪走 {move(listing(), BACKUP)} 个 .pyc -> {BACKUP}")
        print(f"已挪走 {move(bnz_listing(), ZIP_BACKUP)} 个 zip 解压缓存 -> {ZIP_BACKUP}")
        return 0

    if a.restore:
        n = 0
        for f in sorted(glob.glob(os.path.join(BACKUP, "*.pyc"))):
            shutil.move(f, os.path.join(TEMP, os.path.basename(f)))
            n += 1
        print(f"已从备份挪回 {n} 个 .pyc -> {TEMP}")
        return 0

    print(f"%TEMP% = {TEMP}")
    dump("劫持导入的 .pyc（根目录）", listing())
    dump("zip 直接双击留下的解压缓存", bnz_listing(),
         "（里面是某次在压缩包里双击时缓存的 exe）" if bnz_listing() else "")
    for d, name in ((BACKUP, "pyc 备份"), (ZIP_BACKUP, "zip 缓存备份")):
        if os.path.isdir(d):
            print(f"{name}：{len(os.listdir(d))} 个 -> {d}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
