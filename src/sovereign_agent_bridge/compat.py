"""Cross-platform compatibility utilities for Sovereign Agent Bridge.

Provides robust cross-platform helpers for atomic file operations, safe path
normalization, platform detection (Linux, macOS, Windows, Termux, WSL), and
resilient encoding-aware file I/O using only the Python standard library.
"""

from __future__ import annotations

import contextlib
import os
import platform
import shutil
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Generator, Sequence

# ---------------------------------------------------------------------------
# Platform Detection Constants
# ---------------------------------------------------------------------------

IS_WINDOWS: bool = sys.platform.startswith("win") or sys.platform == "cygwin"
IS_MACOS: bool = sys.platform == "darwin"
IS_LINUX: bool = sys.platform.startswith("linux")
IS_POSIX: bool = os.name == "posix"
IS_TERMUX: bool = (
    "TERMUX_VERSION" in os.environ
    or os.path.exists("/data/data/com.termux")
    or "com.termux" in os.environ.get("PREFIX", "")
)
IS_WSL: bool = IS_LINUX and (
    "microsoft" in platform.release().lower()
    or "wsl" in platform.release().lower()
    or "WSL_DISTRO_NAME" in os.environ
)

# ---------------------------------------------------------------------------
# Path Normalization & Resolution
# ---------------------------------------------------------------------------


def normalize_path(path: str | Path) -> Path:
    """Normalize and resolve a filesystem path across all supported operating systems.

    Handles environment variable expansion ($HOME, %APPDATA%), tilde expansion (~),
    symlink resolution where possible, redundant separators, and Termux prefixes.

    Args:
        path: Path string or Path object to normalize.

    Returns:
        Fully resolved, absolute Path object.
    """
    if isinstance(path, str):
        # Expand environment variables (handles both $VAR and %VAR%)
        expanded = os.path.expandvars(os.path.expanduser(path))
    else:
        expanded = os.path.expandvars(os.path.expanduser(str(path)))

    p = Path(expanded)
    try:
        return p.resolve()
    except (OSError, RuntimeError):
        # Fallback if path doesn't exist yet or has permissions constraint
        return p.absolute()


def ensure_dir(path: str | Path, mode: int = 0o755) -> Path:
    """Ensure a directory and its parents exist with safe permissions.

    Args:
        path: Directory path to create.
        mode: Permissions mode for created directories (POSIX only).

    Returns:
        The normalized Path of the directory.
    """
    norm_path = normalize_path(path)
    norm_path.mkdir(parents=True, exist_ok=True, mode=mode)
    return norm_path


def get_app_dir(app_name: str = "sovereign-agent-bridge") -> Path:
    """Get the standard OS-specific application data directory.

    Locations:
        - Termux: $PREFIX/var/lib/<app_name> or ~/.<app_name>
        - Windows: %LOCALAPPDATA%/<app_name> or %APPDATA%/<app_name>
        - macOS: ~/Library/Application Support/<app_name>
        - Linux/POSIX: $XDG_DATA_HOME/<app_name> or ~/.local/share/<app_name>

    Args:
        app_name: Application name subfolder.

    Returns:
        Normalized Path to the application data directory.
    """
    if IS_TERMUX:
        prefix = os.environ.get("PREFIX", "/data/data/com.termux/files/usr")
        base = Path(prefix) / "var" / "lib"
        if not os.access(str(base.parent), os.W_OK):
            base = Path.home() / f".{app_name}"
        target = base / app_name
    elif IS_WINDOWS:
        local_app_data = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        if local_app_data:
            target = Path(local_app_data) / app_name
        else:
            target = Path.home() / f".{app_name}"
    elif IS_MACOS:
        target = Path.home() / "Library" / "Application Support" / app_name
    else:
        xdg_data = os.environ.get("XDG_DATA_HOME")
        if xdg_data:
            target = Path(xdg_data) / app_name
        else:
            target = Path.home() / ".local" / "share" / app_name

    return ensure_dir(target)


def get_temp_dir(subfolder: str = "sovereign-agent-bridge") -> Path:
    """Get a safe, writable temporary directory for the application.

    Args:
        subfolder: Subdirectory name within the system temp folder.

    Returns:
        Normalized Path to the temp directory.
    """
    base_temp = Path(tempfile.gettempdir())
    target = base_temp / subfolder
    return ensure_dir(target)


# ---------------------------------------------------------------------------
# Resilient File I/O (Read / Write)
# ---------------------------------------------------------------------------


def safe_read_text(
    path: str | Path,
    encodings: Sequence[str] = ("utf-8", "utf-8-sig", "latin-1", "cp1252"),
    errors: str = "replace",
) -> str:
    """Read a text file with multi-encoding fallback and graceful error handling.

    Args:
        path: Path to the file.
        encodings: Ordered sequence of encodings to attempt.
        errors: Error handling scheme for decoding errors ('replace', 'ignore', etc.).

    Returns:
        String content of the file.

    Raises:
        FileNotFoundError: If the file does not exist.
        OSError: If an unrecoverable I/O error occurs.
    """
    target = normalize_path(path)
    if not target.is_file():
        raise FileNotFoundError(f"File not found: {target}")

    raw_bytes = target.read_bytes()
    for enc in encodings:
        try:
            return raw_bytes.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue

    # Final fallback using primary encoding with requested error replacement
    return raw_bytes.decode("utf-8", errors=errors)


def safe_read_bytes(path: str | Path) -> bytes:
    """Read raw binary content from a file.

    Args:
        path: Path to the file.

    Returns:
        Binary bytes content.
    """
    target = normalize_path(path)
    return target.read_bytes()


def atomic_write_bytes(
    path: str | Path,
    content: bytes,
    sync: bool = True,
    max_retries: int = 5,
    retry_delay: float = 0.05,
) -> None:
    """Atomically write binary data to a file using temporary file replacement.

    Writes to a temporary file in the same directory (preventing cross-device
    move errors) and replaces the destination file atomically via os.replace.
    Includes retry backoff for Windows file locks.

    Args:
        path: Destination file path.
        content: Binary data to write.
        sync: If True, flushes and calls fsync before replacement.
        max_retries: Maximum retries for atomic replacement if locked.
        retry_delay: Base delay between retries in seconds.

    Raises:
        OSError: If writing or renaming fails after all retries.
    """
    dest = normalize_path(path)
    parent = ensure_dir(dest.parent)

    # Unique temporary file in the same filesystem directory
    tmp_name = f".tmp_{dest.name}_{uuid.uuid4().hex[:8]}_{os.getpid()}"
    tmp_path = parent / tmp_name

    try:
        with open(tmp_path, "wb") as f:
            f.write(content)
            if sync:
                f.flush()
                try:
                    os.fsync(f.fileno())
                except OSError:
                    pass  # fsync may fail on some virtual / memory filesystems

        # Atomic replacement with retry loop for Windows file locking
        for attempt in range(max_retries):
            try:
                os.replace(tmp_path, dest)
                return
            except PermissionError:
                if attempt == max_retries - 1:
                    raise
                time.sleep(retry_delay * (2**attempt))
    finally:
        if tmp_path.exists():
            with contextlib.suppress(OSError):
                tmp_path.unlink()


def atomic_write_text(
    path: str | Path,
    content: str,
    encoding: str = "utf-8",
    newline: str | None = None,
    sync: bool = True,
    max_retries: int = 5,
    retry_delay: float = 0.05,
) -> None:
    """Atomically write string data to a text file.

    Args:
        path: Destination file path.
        content: String content to write.
        encoding: Text encoding (default: 'utf-8').
        newline: Controls how line breaks are translated.
        sync: If True, executes fsync to guarantee disk persistence.
        max_retries: Maximum retries on file locking errors.
        retry_delay: Base delay between retries in seconds.
    """
    encoded_bytes = content.encode(encoding)
    if newline is not None and newline != "\n":
        # Handle custom newline translation if needed
        encoded_bytes = content.replace("\n", newline).encode(encoding)
    atomic_write_bytes(
        path=path,
        content=encoded_bytes,
        sync=sync,
        max_retries=max_retries,
        retry_delay=retry_delay,
    )


def safe_delete(path: str | Path) -> bool:
    """Safely delete a file or directory without raising unhandled errors.

    Args:
        path: Path to delete.

    Returns:
        True if deleted or did not exist, False if deletion failed.
    """
    try:
        p = normalize_path(path)
        if not p.exists():
            return True
        if p.is_dir() and not p.is_symlink():
            shutil.rmtree(p, ignore_errors=True)
        else:
            p.unlink(missing_ok=True)
        return True
    except OSError:
        return False


# ---------------------------------------------------------------------------
# Cross-Platform Advisory File Lock
# ---------------------------------------------------------------------------


class FileLock:
    """Cross-platform advisory file lock for cross-process synchronization.

    Uses `fcntl` on POSIX systems (Linux, macOS, Termux) and a robust lockfile
    polling mechanism with exponential backoff on Windows.
    """

    def __init__(
        self,
        lock_path: str | Path,
        timeout: float = 10.0,
        retry_interval: float = 0.05,
    ) -> None:
        self.lock_path = normalize_path(lock_path)
        self.timeout = timeout
        self.retry_interval = retry_interval
        self._fd: int | None = None
        self._is_locked = False

    def acquire(self) -> bool:
        """Acquire the file lock within the configured timeout window."""
        ensure_dir(self.lock_path.parent)
        start_time = time.monotonic()

        while True:
            try:
                if IS_POSIX:
                    import fcntl

                    self._fd = os.open(
                        str(self.lock_path),
                        os.O_CREAT | os.O_RDWR | os.O_TRUNC,
                        0o600,
                    )
                    fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    self._is_locked = True
                    return True
                else:
                    # Windows atomic create check (O_CREAT | O_EXCL)
                    self._fd = os.open(
                        str(self.lock_path),
                        os.O_CREAT | os.O_EXCL | os.O_RDWR,
                        0o600,
                    )
                    self._is_locked = True
                    return True
            except (OSError, BlockingIOError, PermissionError):
                if self._fd is not None:
                    with contextlib.suppress(OSError):
                        os.close(self._fd)
                    self._fd = None

                elapsed = time.monotonic() - start_time
                if elapsed >= self.timeout:
                    return False
                time.sleep(self.retry_interval)

    def release(self) -> None:
        """Release the acquired file lock."""
        if not self._is_locked:
            return

        try:
            if IS_POSIX and self._fd is not None:
                import fcntl

                fcntl.flock(self._fd, fcntl.LOCK_UN)
                os.close(self._fd)
            elif self._fd is not None:
                os.close(self._fd)

            if self.lock_path.exists():
                with contextlib.suppress(OSError):
                    self.lock_path.unlink(missing_ok=True)
        finally:
            self._fd = None
            self._is_locked = False

    def __enter__(self) -> FileLock:
        if not self.acquire():
            raise TimeoutError(
                f"Failed to acquire file lock on {self.lock_path} within {self.timeout}s"
            )
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.release()
