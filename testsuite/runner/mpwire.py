"""Minimal msgpack codec + rtx-ctlproto framing (stdlib only).

qw-fasttrack is stdlib-only, so this hand-rolls exactly the msgpack subset the
a293067 rtx control protocol (`crates/rtx-ctlproto`) uses: nil, bool, int
(fixint / int|uint 8..64), float32/float64, str (fixstr / str8..32), bin,
array, map. The wire is `[u32 little-endian length][msgpack body]`.

Encoding note: every float field in this protocol is an f32 (glam Vec3, v_req,
health, ...), so `packb` emits Python floats as msgpack float32 to match the
Rust `f32` deserializer exactly. `unpackb` decodes both float32 and float64.
"""
from __future__ import annotations

import struct

# Mirror of proto::MAX_FRAME (a guard against a garbage length prefix).
MAX_FRAME = 64 * 1024 * 1024


# ---------------------------------------------------------------------------
# Encode
# ---------------------------------------------------------------------------

def _pack_int(n: int, out: bytearray) -> None:
    if 0 <= n <= 0x7F:
        out.append(n)
    elif -32 <= n < 0:
        out.append(n & 0xFF)
    elif 0 <= n <= 0xFF:
        out += b"\xcc" + struct.pack(">B", n)
    elif 0 <= n <= 0xFFFF:
        out += b"\xcd" + struct.pack(">H", n)
    elif 0 <= n <= 0xFFFFFFFF:
        out += b"\xce" + struct.pack(">I", n)
    elif 0 <= n <= 0xFFFFFFFFFFFFFFFF:
        out += b"\xcf" + struct.pack(">Q", n)
    elif -0x80 <= n < 0:
        out += b"\xd0" + struct.pack(">b", n)
    elif -0x8000 <= n < 0:
        out += b"\xd1" + struct.pack(">h", n)
    elif -0x80000000 <= n < 0:
        out += b"\xd2" + struct.pack(">i", n)
    elif -0x8000000000000000 <= n < 0:
        out += b"\xd3" + struct.pack(">q", n)
    else:
        raise ValueError(f"int out of msgpack range: {n}")


def _pack(obj, out: bytearray) -> None:
    if obj is None:
        out.append(0xC0)
    elif obj is True:
        out.append(0xC3)
    elif obj is False:
        out.append(0xC2)
    elif isinstance(obj, bool):  # unreachable, kept for clarity
        out.append(0xC3 if obj else 0xC2)
    elif isinstance(obj, int):
        _pack_int(obj, out)
    elif isinstance(obj, float):
        # This protocol is all-f32; emit float32 so the Rust f32 fields decode.
        out += b"\xca" + struct.pack(">f", obj)
    elif isinstance(obj, str):
        b = obj.encode("utf-8")
        n = len(b)
        if n <= 31:
            out.append(0xA0 | n)
        elif n <= 0xFF:
            out += b"\xd9" + struct.pack(">B", n)
        elif n <= 0xFFFF:
            out += b"\xda" + struct.pack(">H", n)
        else:
            out += b"\xdb" + struct.pack(">I", n)
        out += b
    elif isinstance(obj, (bytes, bytearray)):
        n = len(obj)
        if n <= 0xFF:
            out += b"\xc4" + struct.pack(">B", n)
        elif n <= 0xFFFF:
            out += b"\xc5" + struct.pack(">H", n)
        else:
            out += b"\xc6" + struct.pack(">I", n)
        out += bytes(obj)
    elif isinstance(obj, (list, tuple)):
        n = len(obj)
        if n <= 15:
            out.append(0x90 | n)
        elif n <= 0xFFFF:
            out += b"\xdc" + struct.pack(">H", n)
        else:
            out += b"\xdd" + struct.pack(">I", n)
        for item in obj:
            _pack(item, out)
    elif isinstance(obj, dict):
        n = len(obj)
        if n <= 15:
            out.append(0x80 | n)
        elif n <= 0xFFFF:
            out += b"\xde" + struct.pack(">H", n)
        else:
            out += b"\xdf" + struct.pack(">I", n)
        for key, val in obj.items():
            _pack(key, out)
            _pack(val, out)
    else:
        raise TypeError(f"cannot msgpack-encode {type(obj).__name__}")


def packb(obj) -> bytes:
    out = bytearray()
    _pack(obj, out)
    return bytes(out)


def pack_frame(obj) -> bytes:
    """`[u32 little-endian length][msgpack body]` — one rtx control frame."""
    body = packb(obj)
    return struct.pack("<I", len(body)) + body


# ---------------------------------------------------------------------------
# Decode
# ---------------------------------------------------------------------------

def _unpack(data: bytes, i: int):
    c = data[i]
    i += 1
    # positive fixint
    if c <= 0x7F:
        return c, i
    # negative fixint
    if c >= 0xE0:
        return c - 0x100, i
    # fixstr
    if 0xA0 <= c <= 0xBF:
        n = c & 0x1F
        return data[i:i + n].decode("utf-8", "replace"), i + n
    # fixmap
    if 0x80 <= c <= 0x8F:
        return _unpack_map(data, i, c & 0x0F)
    # fixarray
    if 0x90 <= c <= 0x9F:
        return _unpack_array(data, i, c & 0x0F)
    if c == 0xC0:
        return None, i
    if c == 0xC2:
        return False, i
    if c == 0xC3:
        return True, i
    if c == 0xCC:
        return data[i], i + 1
    if c == 0xCD:
        return struct.unpack_from(">H", data, i)[0], i + 2
    if c == 0xCE:
        return struct.unpack_from(">I", data, i)[0], i + 4
    if c == 0xCF:
        return struct.unpack_from(">Q", data, i)[0], i + 8
    if c == 0xD0:
        return struct.unpack_from(">b", data, i)[0], i + 1
    if c == 0xD1:
        return struct.unpack_from(">h", data, i)[0], i + 2
    if c == 0xD2:
        return struct.unpack_from(">i", data, i)[0], i + 4
    if c == 0xD3:
        return struct.unpack_from(">q", data, i)[0], i + 8
    if c == 0xCA:
        return struct.unpack_from(">f", data, i)[0], i + 4
    if c == 0xCB:
        return struct.unpack_from(">d", data, i)[0], i + 8
    if c == 0xD9:
        n = data[i]; i += 1
        return data[i:i + n].decode("utf-8", "replace"), i + n
    if c == 0xDA:
        n = struct.unpack_from(">H", data, i)[0]; i += 2
        return data[i:i + n].decode("utf-8", "replace"), i + n
    if c == 0xDB:
        n = struct.unpack_from(">I", data, i)[0]; i += 4
        return data[i:i + n].decode("utf-8", "replace"), i + n
    if c == 0xC4:
        n = data[i]; i += 1
        return data[i:i + n], i + n
    if c == 0xC5:
        n = struct.unpack_from(">H", data, i)[0]; i += 2
        return data[i:i + n], i + n
    if c == 0xC6:
        n = struct.unpack_from(">I", data, i)[0]; i += 4
        return data[i:i + n], i + n
    if c == 0xDC:
        n = struct.unpack_from(">H", data, i)[0]; i += 2
        return _unpack_array(data, i, n)
    if c == 0xDD:
        n = struct.unpack_from(">I", data, i)[0]; i += 4
        return _unpack_array(data, i, n)
    if c == 0xDE:
        n = struct.unpack_from(">H", data, i)[0]; i += 2
        return _unpack_map(data, i, n)
    if c == 0xDF:
        n = struct.unpack_from(">I", data, i)[0]; i += 4
        return _unpack_map(data, i, n)
    raise ValueError(f"unknown msgpack marker 0x{c:02x} at offset {i - 1}")


def _unpack_array(data, i, n):
    out = []
    for _ in range(n):
        val, i = _unpack(data, i)
        out.append(val)
    return out, i


def _unpack_map(data, i, n):
    out = {}
    for _ in range(n):
        key, i = _unpack(data, i)
        val, i = _unpack(data, i)
        out[key] = val
    return out, i


def unpackb(data: bytes):
    """Decode one msgpack object from `data` (a full frame body)."""
    val, _ = _unpack(data, 0)
    return val
