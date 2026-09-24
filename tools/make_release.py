#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Create a verified Windows release archive, checksums, and release notes."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
import zipfile

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from desktop.build_exe import FINAL, MANIFEST, build_inputs, read_version, sha256

README_NAME = "使用说明.txt"


def verify_build(version: str) -> None:
    if not FINAL.is_file() or not MANIFEST.is_file():
        raise ValueError("Executable or build manifest missing; run with --build")
    metadata = json.loads(MANIFEST.read_text(encoding="utf-8"))
    if not isinstance(metadata, dict):
        raise ValueError("Build manifest must be a JSON object")
    if metadata.get("schema") != 1 or metadata.get("architecture") != "win64":
        raise ValueError("Unsupported build manifest; rebuild with desktop/build_exe.py")
    if metadata.get("version") != version:
        raise ValueError("Executable version differs from VERSION; rebuild before releasing")
    if metadata.get("inputs") != build_inputs():
        raise ValueError("Source files differ from the build manifest; rebuild before releasing")
    if metadata.get("exe_sha256") != sha256(FINAL):
        raise ValueError("Executable checksum differs from the build manifest")
    with FINAL.open("rb") as stream:
        if stream.read(2) != b"MZ":
            raise ValueError("Executable does not have a Windows PE header")


def readme(version: str) -> bytes:
    text = f"""提示词增强器 {version} · Windows 10 / 11 64 位

1. 请先完整解压压缩包，再运行 PromptEnhancer.exe。
2. 首次运行填写 API 地址、API Key 和模型名称，测试连接后保存并应用。
3. 在可编辑输入框选中文字，按 Ctrl+Alt+E 增强。

默认快捷键：Ctrl+Alt+E 增强；Ctrl+Alt+Z 撤销；Ctrl+Alt+O 打开设置。
快捷键可在设置中修改。关闭设置窗口后，程序继续在托盘运行。
如需退出，请使用设置窗口或托盘菜单的退出功能。

增强会把选中的文本发送到你配置的 API 服务，可能产生 API 费用。
请先在普通输入框测试；不同应用对复制、粘贴和撤销的支持可能不同。
等待期间切换窗口或发生键鼠操作时，结果改为复制到剪贴板，由你手动粘贴。
若期间复制了新内容，会保留新剪贴板，请从历史记录复制增强结果。
增强后继续编辑可能改变 Ctrl+Z 的撤销对象；需要时从历史记录复制原文。

配置、历史和日志默认位于 %APPDATA%\\PromptEnhancer\\。
API Key 使用当前 Windows 用户的 DPAPI 保护；历史记录包含明文提示词。
升级前退出旧版本，再运行新版本；配置保留在上述目录。
卸载时退出程序并删除解压目录；需要清除本地数据时再删除上述数据目录。

本发行包未提供代码签名。Windows 可能显示来源或信誉提示。
运行前请确认下载来源，并对照 Release 页的 SHA256SUMS.txt 校验压缩包。

包内 LICENSE 为 MIT 许可证，CHANGELOG.md 记录版本变化。
BUILD_INFO.json 记录 Python、构建工具版本与源文件校验和。
"""
    return text.replace("\n", "\r\n").encode("utf-8-sig")


def archive_timestamp() -> tuple[int, int, int, int, int, int]:
    # Stable ZIP metadata when packaging the same executable and documentation.
    epoch = int(os.environ.get("SOURCE_DATE_EPOCH", "315532800"))
    date = datetime.fromtimestamp(epoch, timezone.utc)
    if not 1980 <= date.year <= 2107:
        raise ValueError("SOURCE_DATE_EPOCH must be within the ZIP range 1980..2107")
    return date.year, date.month, date.day, date.hour, date.minute, date.second


def make_zip(destination: Path, version: str) -> dict[str, bytes]:
    top = destination.stem
    payload = {
        "PromptEnhancer.exe": FINAL.read_bytes(),
        README_NAME: readme(version),
        "LICENSE": (ROOT / "LICENSE").read_bytes(),
        "CHANGELOG.md": (ROOT / "CHANGELOG.md").read_bytes(),
        "BUILD_INFO.json": MANIFEST.read_bytes(),
    }
    with zipfile.ZipFile(destination, "w") as archive:
        for name, data in sorted(payload.items()):
            info = zipfile.ZipInfo(f"{top}/{name}", date_time=archive_timestamp())
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            info.compress_type = (zipfile.ZIP_STORED if name.endswith(".exe")
                                  else zipfile.ZIP_DEFLATED)
            archive.writestr(info, data)
    return payload


def verify_zip(archive_path: Path, payload: dict[str, bytes], destination: Path) -> Path:
    top = archive_path.stem
    with zipfile.ZipFile(archive_path) as archive:
        expected = {f"{top}/{name}" for name in payload}
        if set(archive.namelist()) != expected or len(archive.namelist()) != len(expected):
            raise ValueError("Unexpected or duplicate archive entries")
        for name, data in payload.items():
            if archive.read(f"{top}/{name}") != data:
                raise ValueError(f"Archive content mismatch: {name}")
        archive.extractall(destination)
    return destination / top


def smoke_run(command: list[str], cwd: Path) -> None:
    """Exercise startup and clean shutdown without touching the user's settings."""
    if sys.platform != "win32":
        raise ValueError("Desktop smoke tests require Windows")
    with tempfile.TemporaryDirectory(prefix="prompt-enhancer-smoke-") as temporary:
        data = Path(temporary)
        configuration = data / "PromptEnhancer"
        configuration.mkdir()
        settings = {
            "api_key": "local-smoke-placeholder", "base_url": "http://127.0.0.1:1/v1",
            "model": "smoke", "auto_paste": False,
            "hotkey_enhance": "", "hotkey_undo": "", "hotkey_settings": "",
        }
        (configuration / "settings.json").write_text(json.dumps(settings), encoding="utf-8")
        environment = dict(os.environ, APPDATA=str(data), LOCALAPPDATA=str(data),
                           PROMPT_ENHANCER_INSTANCE_NAME=f"PromptEnhancer.Smoke.{uuid.uuid4().hex}")
        process = subprocess.Popen(command + ["--hidden"], cwd=cwd, env=environment,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            deadline = time.monotonic() + 60
            instance_file = configuration / "instance.json"
            log_file = configuration / "app.log"
            while True:
                if process.poll() is not None:
                    raise ValueError(f"Smoke startup exited early ({process.returncode})")
                log = (log_file.read_text(encoding="utf-8", errors="replace")
                       if log_file.is_file() else "")
                if "Traceback" in log or "有模块不是从 exe 内部加载" in log:
                    raise ValueError("Smoke startup reported a runtime error")
                if instance_file.is_file() and "模块来源全部正常" in log:
                    break
                if time.monotonic() >= deadline:
                    raise ValueError("Smoke startup timed out waiting for instance and module checks")
                time.sleep(0.2)
            instance = json.loads(instance_file.read_text(encoding="utf-8"))
            if not instance.get("pid") or not instance.get("hwnd"):
                raise ValueError("Smoke startup produced invalid instance metadata")
            subprocess.run(command + ["--quit"], cwd=cwd, env=environment,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           check=True, timeout=60)
            result = process.wait(timeout=30)
            if result != 0:
                raise ValueError(f"Smoke shutdown failed ({result})")
            if "Traceback" in log_file.read_text(encoding="utf-8", errors="replace"):
                raise ValueError("Smoke run logged an unhandled exception")
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=15)
    print("Desktop smoke test passed (isolated startup and shutdown).")


def release_notes(version: str, archive: Path, digest: str) -> str:
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    marker = f"## [{version}]"
    if marker not in changelog:
        raise ValueError(f"CHANGELOG.md has no entry for {version}")
    changes = changelog.split(marker, 1)[1].split("\n## [", 1)[0]
    changes = changes.split("\n", 1)[1].strip()
    return f"""# 提示词增强器 v{version}

{changes}

## 下载与使用

下载 `{archive.name}`，完整解压后运行 `PromptEnhancer.exe`。
适用于 Windows 10 / 11 64 位；首次运行需要配置自己的 API 服务、Key 和模型。
GitHub 自动提供的 Source code 附件是源码，不包含可执行程序。

本发行包未提供代码签名。请确认下载来源；可用 PowerShell 校验压缩包：

```powershell
Get-FileHash .\\{archive.name} -Algorithm SHA256
```

预期 SHA256：`{digest}`。也可对照附件 `SHA256SUMS.txt`。

增强文本会发送到你配置的 API 服务；历史记录保存在本机。
升级前请退出旧版本。详细使用与数据说明见压缩包中的 `{README_NAME}`。
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build", action="store_true", help="Build the executable first")
    parser.add_argument("--version", help="Expected VERSION (optional v prefix); never overrides it")
    parser.add_argument("--out", type=Path, default=ROOT / "release")
    parser.add_argument("--no-smoke", action="store_true", help="Skip executable startup/shutdown")
    parser.add_argument("--no-extract", action="store_true", help="Do not keep an unpacked copy")
    parser.add_argument("--smoke-source", action="store_true", help="Only smoke-test the source app")
    args = parser.parse_args()
    try:
        if args.smoke_source:
            smoke_run([sys.executable, str(ROOT / "desktop" / "main.py")], ROOT)
            return 0
        version = read_version()
        if args.version and args.version.removeprefix("v") != version:
            raise ValueError("Requested version or tag does not match VERSION")
        if args.build:
            subprocess.run([sys.executable, str(ROOT / "desktop" / "build_exe.py")],
                           cwd=ROOT, check=True)
        verify_build(version)
        output = args.out.resolve()
        output.mkdir(parents=True, exist_ok=True)
        name = f"PromptEnhancer-{version}-win64.zip"
        with tempfile.TemporaryDirectory(prefix="package-", dir=output) as temporary:
            stage = Path(temporary)
            archive = stage / name
            payload = make_zip(archive, version)
            unpacked = verify_zip(archive, payload, stage / "verify")
            if not args.no_smoke:
                smoke_run([str(unpacked / FINAL.name)], unpacked)
            digest = sha256(archive)
            (stage / "RELEASE_NOTES.md").write_text(release_notes(version, archive, digest),
                                                   encoding="utf-8", newline="\n")
            (stage / "SHA256SUMS.txt").write_text(f"{digest}  {name}\n", encoding="ascii")
            if not args.no_extract:
                destination = output / archive.stem
                if destination.exists():
                    destination = output / f"{archive.stem}-{uuid.uuid4().hex[:8]}"
                shutil.copytree(unpacked, destination)
            for filename in (name, "RELEASE_NOTES.md", "SHA256SUMS.txt"):
                os.replace(stage / filename, output / filename)
        print(f"Release archive: {output / name}")
        print(f"SHA256: {digest}")
        print(f"Release notes and checksums: {output}")
        if args.no_smoke:
            print("Startup/shutdown smoke test was skipped (--no-smoke).")
        return 0
    except (OSError, ValueError, subprocess.SubprocessError, zipfile.BadZipFile) as exc:
        print(f"Release failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
