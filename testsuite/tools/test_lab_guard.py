#!/usr/bin/env python3
"""Monkeypatch-vakt: tester får inte nå ~/lab/.change-freeze eller skriva i ~/lab.

Installeras i setUpModule för grindtesterna. Jämför sökvägar med
os.path.abspath (ingen resolve/stat) så vakten inte själv rör filen.
"""

from __future__ import annotations

import builtins
import os
import pwd
from pathlib import Path

REAL_HOME = os.path.abspath(pwd.getpwuid(os.getuid()).pw_dir)
REAL_LAB = os.path.join(REAL_HOME, "lab")
REAL_FREEZE = os.path.join(REAL_LAB, ".change-freeze")

_installed = False
_orig: dict = {}


class LabReachError(AssertionError):
    """A test touched the real passwd-home freeze/lab path."""


def _abspath(p) -> str:
    return os.path.abspath(os.fspath(p))


def is_real_freeze(p) -> bool:
    return _abspath(p) == REAL_FREEZE


def is_under_real_lab(p) -> bool:
    s = _abspath(p)
    return s == REAL_LAB or s.startswith(REAL_LAB + os.sep)


def check_read(p) -> None:
    if is_real_freeze(p):
        raise LabReachError(f"test nådde riktiga freeze-vägen: {_abspath(p)}")


def check_write(p) -> None:
    check_read(p)
    if is_under_real_lab(p):
        raise LabReachError(f"test skrev under riktiga ~/lab: {_abspath(p)}")


def install_lab_guard() -> None:
    global _installed
    if _installed:
        return
    _orig["Path.is_file"] = Path.is_file
    _orig["Path.exists"] = Path.exists
    _orig["Path.read_text"] = Path.read_text
    _orig["Path.read_bytes"] = Path.read_bytes
    _orig["Path.write_text"] = Path.write_text
    _orig["Path.write_bytes"] = Path.write_bytes
    _orig["Path.mkdir"] = Path.mkdir
    _orig["Path.touch"] = Path.touch
    _orig["Path.unlink"] = Path.unlink
    _orig["Path.open"] = Path.open
    _orig["open"] = builtins.open
    _orig["os.open"] = os.open
    _orig["os.stat"] = os.stat
    _orig["os.mkdir"] = os.mkdir
    _orig["os.makedirs"] = os.makedirs

    def is_file(self, *a, **k):
        check_read(self)
        return _orig["Path.is_file"](self, *a, **k)

    def exists(self, *a, **k):
        check_read(self)
        return _orig["Path.exists"](self, *a, **k)

    def read_text(self, *a, **k):
        check_read(self)
        return _orig["Path.read_text"](self, *a, **k)

    def read_bytes(self, *a, **k):
        check_read(self)
        return _orig["Path.read_bytes"](self, *a, **k)

    def write_text(self, *a, **k):
        check_write(self)
        return _orig["Path.write_text"](self, *a, **k)

    def write_bytes(self, *a, **k):
        check_write(self)
        return _orig["Path.write_bytes"](self, *a, **k)

    def mkdir(self, *a, **k):
        check_write(self)
        return _orig["Path.mkdir"](self, *a, **k)

    def touch(self, *a, **k):
        check_write(self)
        return _orig["Path.touch"](self, *a, **k)

    def unlink(self, *a, **k):
        check_write(self)
        return _orig["Path.unlink"](self, *a, **k)

    def path_open(self, *a, **k):
        mode = a[0] if a else k.get("mode", "r")
        if isinstance(mode, str) and any(c in mode for c in "wax+"):
            check_write(self)
        else:
            check_read(self)
        return _orig["Path.open"](self, *a, **k)

    def builtin_open(file, mode="r", *a, **k):
        try:
            if isinstance(mode, str) and any(c in mode for c in "wax+"):
                check_write(file)
            else:
                check_read(file)
        except TypeError:
            pass
        return _orig["open"](file, mode, *a, **k)

    def os_open(path, flags, *a, **k):
        if flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND):
            check_write(path)
        else:
            check_read(path)
        return _orig["os.open"](path, flags, *a, **k)

    def os_stat(path, *a, **k):
        check_read(path)
        return _orig["os.stat"](path, *a, **k)

    def os_mkdir(path, *a, **k):
        check_write(path)
        return _orig["os.mkdir"](path, *a, **k)

    def os_makedirs(path, *a, **k):
        check_write(path)
        return _orig["os.makedirs"](path, *a, **k)

    Path.is_file = is_file  # type: ignore[method-assign]
    Path.exists = exists  # type: ignore[method-assign]
    Path.read_text = read_text  # type: ignore[method-assign]
    Path.read_bytes = read_bytes  # type: ignore[method-assign]
    Path.write_text = write_text  # type: ignore[method-assign]
    Path.write_bytes = write_bytes  # type: ignore[method-assign]
    Path.mkdir = mkdir  # type: ignore[method-assign]
    Path.touch = touch  # type: ignore[method-assign]
    Path.unlink = unlink  # type: ignore[method-assign]
    Path.open = path_open  # type: ignore[method-assign]
    builtins.open = builtin_open  # type: ignore[assignment]
    os.open = os_open  # type: ignore[assignment]
    os.stat = os_stat  # type: ignore[assignment]
    os.mkdir = os_mkdir  # type: ignore[assignment]
    os.makedirs = os_makedirs  # type: ignore[assignment]
    _installed = True


def uninstall_lab_guard() -> None:
    global _installed
    if not _installed:
        return
    Path.is_file = _orig["Path.is_file"]  # type: ignore[method-assign]
    Path.exists = _orig["Path.exists"]  # type: ignore[method-assign]
    Path.read_text = _orig["Path.read_text"]  # type: ignore[method-assign]
    Path.read_bytes = _orig["Path.read_bytes"]  # type: ignore[method-assign]
    Path.write_text = _orig["Path.write_text"]  # type: ignore[method-assign]
    Path.write_bytes = _orig["Path.write_bytes"]  # type: ignore[method-assign]
    Path.mkdir = _orig["Path.mkdir"]  # type: ignore[method-assign]
    Path.touch = _orig["Path.touch"]  # type: ignore[method-assign]
    Path.unlink = _orig["Path.unlink"]  # type: ignore[method-assign]
    Path.open = _orig["Path.open"]  # type: ignore[method-assign]
    builtins.open = _orig["open"]
    os.open = _orig["os.open"]
    os.stat = _orig["os.stat"]
    os.mkdir = _orig["os.mkdir"]
    os.makedirs = _orig["os.makedirs"]
    _orig.clear()
    _installed = False
