#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""极简 PNG 编码器（纯标准库）。

只做一件事：把 RGBA 字节写成 PNG。给图标生成和界面自绘小部件共用，
避免第三方依赖（Pillow）。
"""

from __future__ import annotations

import struct
import zlib


def to_png(w: int, h: int, rgba: bytes) -> bytes:
    """rgba 长度必须是 w*h*4，每像素 RGBA。"""
    if len(rgba) != w * h * 4:
        raise ValueError(f"rgba 长度不对：期望 {w * h * 4}，实际 {len(rgba)}")
    raw = b"".join(b"\x00" + bytes(rgba[y * w * 4:(y + 1) * w * 4]) for y in range(h))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9))
            + chunk(b"IEND", b""))


def to_ico(png_bytes: bytes, size: int) -> bytes:
    """把一张 PNG 包成单尺寸 ICO（Vista+ 支持 PNG 负载）。"""
    header = struct.pack("<HHH", 0, 1, 1)
    entry = struct.pack("<BBBBHHII",
                        0 if size >= 256 else size,
                        0 if size >= 256 else size,
                        0, 0, 1, 32, len(png_bytes), 22)
    return header + entry + png_bytes
