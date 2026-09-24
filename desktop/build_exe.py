#!/usr/bin/env python3
"""Build the Windows executable and record the exact inputs used for it."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import struct
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent.parent
FINAL = ROOT / "dist" / "PromptEnhancer.exe"
MANIFEST = FINAL.with_suffix(".build.json")
VERSION_PATTERN = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_version() -> str:
    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    if not VERSION_PATTERN.fullmatch(version):
        raise ValueError("VERSION must contain a release version such as 1.1.0")
    if any(int(part) > 65535 for part in version.split(".")):
        raise ValueError("VERSION components must fit Windows version metadata")
    return version


def build_inputs() -> dict[str, str]:
    paths = list(ROOT.glob("*.py")) + list((ROOT / "desktop").glob("*.py"))
    paths += [ROOT / "VERSION", ROOT / "requirements-build.txt",
              ROOT / "desktop" / "app.ico", ROOT / "desktop" / "app.png"]
    return {path.relative_to(ROOT).as_posix(): sha256(path) for path in sorted(paths)}


def replace_file(src: Path, dst: Path) -> None:
    """A failed copy or locked destination must leave the old executable intact."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=dst.name + ".", dir=dst.parent)
    os.close(descriptor)
    try:
        shutil.copy2(src, temporary)
        os.replace(temporary, dst)
    finally:
        Path(temporary).unlink(missing_ok=True)


def main() -> int:
    if sys.platform != "win32" or struct.calcsize("P") != 8:
        print("Build requires 64-bit Python on Windows.", file=sys.stderr)
        return 2
    try:
        import PyInstaller
        import tkinter  # noqa: F401
    except ImportError as exc:
        print(f"Missing build dependency: {exc}. Use Python with Tcl/Tk and run "
              "python -m pip install -r requirements-build.txt", file=sys.stderr)
        return 2

    try:
        version = read_version()
        inputs = build_inputs()
        (ROOT / "build").mkdir(exist_ok=True)
        work = Path(tempfile.mkdtemp(prefix="windows-", dir=ROOT / "build"))
        version_tuple = tuple(int(part) for part in version.split(".")) + (0,)
        version_file = work / "version_info.txt"
        version_file.write_text(
            "VSVersionInfo(ffi=FixedFileInfo("
            f"filevers={version_tuple!r}, prodvers={version_tuple!r}, "
            "mask=0x3f, flags=0, OS=0x40004, fileType=1, subtype=0, date=(0, 0)), "
            "kids=[StringFileInfo([StringTable('040904B0', ["
            "StringStruct('ProductName', 'Prompt Enhancer'), "
            f"StringStruct('ProductVersion', '{version}'), "
            f"StringStruct('FileVersion', '{version}'), "
            "StringStruct('FileDescription', 'Prompt Enhancer'), "
            "StringStruct('OriginalFilename', 'PromptEnhancer.exe')])]), "
            "VarFileInfo([VarStruct('Translation', [1033, 1200])])])\n",
            encoding="utf-8")
        command = [
            sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
            "--onefile", "--windowed", "--noupx", "--name", "PromptEnhancer",
            "--icon", str(ROOT / "desktop" / "app.ico"),
            "--version-file", str(version_file),
            "--paths", str(ROOT), "--paths", str(ROOT / "desktop"),
            "--distpath", str(work / "dist"), "--workpath", str(work / "work"),
            "--specpath", str(work / "spec"),
        ]
        for resource in (ROOT / "desktop" / "app.ico", ROOT / "desktop" / "app.png",
                         ROOT / "VERSION"):
            command += ["--add-data", f"{resource}{os.pathsep}."]
        command += [str(ROOT / "desktop" / "main.py")]
        result = subprocess.run(command, cwd=ROOT, check=False)
        if result.returncode:
            return result.returncode
        if inputs != build_inputs():
            raise ValueError("Source files changed during the build; rebuild before releasing")
        executable = work / "dist" / FINAL.name
        metadata = {
            "schema": 1, "version": version, "architecture": "win64",
            "python": platform.python_version(), "pyinstaller": PyInstaller.__version__,
            "exe_sha256": sha256(executable), "inputs": inputs,
        }
        metadata_file = work / MANIFEST.name
        metadata_file.write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n",
                                 encoding="utf-8")
        replace_file(executable, FINAL)
        replace_file(metadata_file, MANIFEST)
    except (OSError, ValueError) as exc:
        print(f"Build failed: {exc}", file=sys.stderr)
        return 1
    print(f"Built {FINAL} ({FINAL.stat().st_size / 1024 / 1024:.1f} MiB)")
    print(f"Build manifest: {MANIFEST}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
