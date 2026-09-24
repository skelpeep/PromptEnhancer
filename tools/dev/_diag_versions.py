# -*- coding: utf-8 -*-
"""一次性诊断：把"候选 exe"各自的哈希/时间戳、TEMP 里的残留 .pyc、探针日志摆在一起。"""
import hashlib
import os
import time
import zipfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
TEMP = os.environ.get("TEMP") or os.environ.get("TMP") or ""


def digest(path):
    if not os.path.isfile(path):
        return None
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    st = os.stat(path)
    return (h.hexdigest()[:12], st.st_size,
            time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(st.st_mtime)))


print("=== 候选 exe ===")
cands = [
    os.path.join(ROOT, "dist", "PromptEnhancer.exe"),
    os.path.join(ROOT, "release", "PromptEnhancer-1.0.1-win64", "PromptEnhancer.exe"),
]
for c in cands:
    d = digest(c)
    print(f"{d[0] if d else '(缺失)':12} {d[1] if d else 0:>10} {d[2] if d else '':20} {c}")

zpath = os.path.join(ROOT, "release", "PromptEnhancer-1.0.1-win64.zip")
print(f"\n=== zip: {zpath} {'存在' if os.path.isfile(zpath) else '缺失'} ===")
if os.path.isfile(zpath):
    with zipfile.ZipFile(zpath) as z:
        for i in z.infolist():
            h = hashlib.sha256(z.read(i.filename)).hexdigest()[:12]
            print(f"  {h}  {i.file_size:>10}  {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(i.date_time and time.mktime(i.date_time + (0, 0, -1))))}  {i.filename}")

print("\n=== TEMP 根目录下的 .pyc（可疑残留） ===")
import glob
pycs = sorted(glob.glob(os.path.join(TEMP, "*.pyc")))
if not pycs:
    print("  (无)")
for f in pycs:
    st = os.stat(f)
    print(f"  {st.st_size:>8}  {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(st.st_mtime))}  {os.path.basename(f)}")

print("\n=== TEMP 里与探针有关的东西 ===")
for pat in ("pe_probe*", "_MEI*", "BNZ.*"):
    hits = sorted(glob.glob(os.path.join(TEMP, pat)))
    print(f"  {pat}: {len(hits)} 个")
    for h in hits[:12]:
        try:
            st = os.stat(h)
            kind = "dir" if os.path.isdir(h) else "file"
            print(f"    [{kind}] {time.strftime('%m-%d %H:%M:%S', time.localtime(st.st_mtime))}  {h}")
        except OSError as e:
            print(f"    (读不到 {e})  {h}")

print("\n=== 探针隔离配置里的 app.log ===")
log = os.path.join(TEMP, "pe_probe_appdata", "PromptEnhancer", "app.log")
print(f"  {log}  {'存在' if os.path.isfile(log) else '缺失'}")
if os.path.isfile(log):
    with open(log, encoding="utf-8", errors="replace") as f:
        lines = f.read().splitlines()
    print(f"  (共 {len(lines)} 行，打印前 25 行)")
    for ln in lines[:25]:
        print("   ", ln)
