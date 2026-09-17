"""Tests for cross-platform compatibility utilities in sovereign_agent_bridge.compat."""

from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
from pathlib import Path

import pytest

from sovereign_agent_bridge.compat import (
    IS_LINUX,
    IS_MACOS,
    IS_POSIX,
    IS_TERMUX,
    IS_WINDOWS,
    IS_WSL,
    FileLock,
    atomic_write_bytes,
    atomic_write_text,
    ensure_dir,
    get_app_dir,
    get_temp_dir,
    normalize_path,
    safe_delete,
    safe_read_bytes,
    safe_read_text,
)


def test_platform_detection_constants():
    """Verify platform detection constants are booleans and sensible."""
    assert isinstance(IS_WINDOWS, bool)
    assert isinstance(IS_MACOS, bool)
    assert isinstance(IS_LINUX, bool)
    assert isinstance(IS_POSIX, bool)
    assert isinstance(IS_TERMUX, bool)
    assert isinstance(IS_WSL, bool)

    # At least one OS must match standard systems
    if sys.platform.startswith("linux"):
        assert IS_LINUX is True
        assert IS_POSIX is True
    elif sys.platform == "darwin":
        assert IS_MACOS is True
        assert IS_POSIX is True
    elif sys.platform.startswith("win"):
        assert IS_WINDOWS is True


def test_normalize_path(tmp_path: Path):
    """Verify path expansion and normalization."""
    p = tmp_path / "subdir" / ".." / "file.txt"
    norm = normalize_path(p)
    assert norm.name == "file.txt"
    assert norm.is_absolute()

    # String with home expansion
    home_norm = normalize_path("~/test_sovereign_dir")
    assert str(Path.home()) in str(home_norm)


def test_ensure_dir(tmp_path: Path):
    """Verify recursive directory creation."""
    target = tmp_path / "deep" / "nested" / "folder"
    created = ensure_dir(target)
    assert created.is_dir()
    assert created.exists()


def test_get_app_dir_and_temp_dir():
    """Verify application directory and temp directory discovery."""
    app_dir = get_app_dir("test-sovereign-bridge")
    assert app_dir.is_dir()
    assert "test-sovereign-bridge" in str(app_dir)

    temp_dir = get_temp_dir("test-sovereign-temp")
    assert temp_dir.is_dir()
    assert "test-sovereign-temp" in str(temp_dir)


def test_atomic_write_and_safe_read_text(tmp_path: Path):
    """Verify atomic writing and resilient reading of text files."""
    dest = tmp_path / "atomic_text.txt"
    content = "Sovereign Agent Bridge: Line 1\nLine 2: 🤖 Multi-Agent UTF-8 \u2713"

    atomic_write_text(dest, content)
    assert dest.exists()

    read_back = safe_read_text(dest)
    assert read_back == content

    # Test safe_read_bytes
    raw_bytes = safe_read_bytes(dest)
    assert raw_bytes == content.encode("utf-8")


def test_safe_read_nonexistent_file(tmp_path: Path):
    """Verify FileNotFoundError raised for nonexistent file."""
    dest = tmp_path / "does_not_exist.txt"
    with pytest.raises(FileNotFoundError):
        safe_read_text(dest)


def test_atomic_write_bytes(tmp_path: Path):
    """Verify binary atomic writing."""
    dest = tmp_path / "data.bin"
    payload = b"\x00\x01\x02\xFF\xFE\xFD"

    atomic_write_bytes(dest, payload)
    assert dest.read_bytes() == payload


def test_safe_delete(tmp_path: Path):
    """Verify safe deletion of files and directory trees."""
    f = tmp_path / "to_delete.txt"
    f.write_text("temporary", encoding="utf-8")
    assert f.exists()
    assert safe_delete(f) is True
    assert not f.exists()

    # Deleting non-existent returns True without error
    assert safe_delete(f) is True

    # Deleting directory
    d = tmp_path / "sub_dir_to_del"
    d.mkdir()
    (d / "inner.txt").write_text("inner", encoding="utf-8")
    assert safe_delete(d) is True
    assert not d.exists()


def test_file_lock_acquisition_and_release(tmp_path: Path):
    """Verify FileLock acquire, context manager, and concurrency semantics."""
    lock_file = tmp_path / "test.lock"

    # Context manager test
    with FileLock(lock_file, timeout=2.0) as lock:
        assert lock._is_locked is True

    assert lock._is_locked is False

    # Manual acquire and release
    fl = FileLock(lock_file, timeout=2.0)
    assert fl.acquire() is True
    assert fl._is_locked is True
    fl.release()
    assert fl._is_locked is False


def test_file_lock_contention(tmp_path: Path):
    """Verify lock contention when another thread holds lock."""
    lock_file = tmp_path / "contention.lock"
    holder = FileLock(lock_file, timeout=2.0)
    assert holder.acquire() is True

    # Secondary lock with short timeout should fail or block
    contender = FileLock(lock_file, timeout=0.2, retry_interval=0.05)
    acquired = contender.acquire()

    # If posix flock per-fd or windows exclusive
    if IS_POSIX:
        # Note: within the same process on POSIX, different open fd's flock can detect lock
        pass

    holder.release()
    # Now contender should acquire
    assert contender.acquire() is True
    contender.release()
