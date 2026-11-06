"""Read-only file and tar-member primitives shared by the packed readers.

Every source file is opened read-only, without following a final symlink, and
checked to be the same regular file as its path. The helpers raise OSError or
the neutral errors below; each caller maps them to its own exception and text.
"""

from __future__ import annotations

import os
import stat
import tarfile
import threading
from contextlib import nullcontext
from pathlib import Path

TAR_BLOCK_SIZE = 512
_OS_PREAD = getattr(os, "pread", None)


# The neutral errors deliberately derive from neither OSError nor ValueError,
# so a caller's existing handlers for those never capture them by accident.
class NotRegularFileError(Exception):
    """Raised when a descriptor or its path is not a regular file."""


class ShortReadError(Exception):
    """Raised when a positional read ends before the requested size."""

    def __init__(self, expected: int, received: int) -> None:
        super().__init__(f"expected {expected}, got {received}")
        self.expected = expected
        self.received = received


def open_flags(base: int = os.O_RDONLY) -> int:
    """Add the binary, close-on-exec and no-follow flags this platform has."""
    for name in ("O_BINARY", "O_CLOEXEC", "O_NOFOLLOW"):
        base |= getattr(os, name, 0)
    return base


def same_file_revision(first: os.stat_result, second: os.stat_result) -> bool:
    """Whether two stat results describe the same file content revision."""
    return (first.st_dev, first.st_ino, first.st_mtime_ns, first.st_size) == (
        second.st_dev,
        second.st_ino,
        second.st_mtime_ns,
        second.st_size,
    )


def regular_stats(fd: int, path: Path) -> tuple[os.stat_result, os.stat_result]:
    """Return the descriptor's and the unfollowed path's stat results.

    Raises NotRegularFileError when either is not a regular file.
    """
    descriptor_stat = os.fstat(fd)
    path_stat = os.stat(path, follow_symlinks=False)
    if not stat.S_ISREG(descriptor_stat.st_mode) or not stat.S_ISREG(path_stat.st_mode):
        raise NotRegularFileError(f"not a regular file: {path}")
    return descriptor_stat, path_stat


def read_exact_at(
    fd: int,
    offset: int,
    size: int,
    *,
    position_lock: threading.Lock | None = None,
) -> bytes:
    """Read exactly ``size`` bytes at ``offset``.

    Uses ``os.pread`` where available. Otherwise it seeks and reads, holding
    ``position_lock`` when the descriptor is shared between threads.
    """
    output = bytearray()
    if _OS_PREAD is not None:
        while len(output) < size:
            chunk = _OS_PREAD(fd, size - len(output), offset + len(output))
            if not chunk:
                break
            output.extend(chunk)
    else:
        with position_lock if position_lock is not None else nullcontext():
            os.lseek(fd, offset, os.SEEK_SET)
            while len(output) < size:
                chunk = os.read(fd, size - len(output))
                if not chunk:
                    break
                output.extend(chunk)
    if len(output) != size:
        raise ShortReadError(size, len(output))
    return bytes(output)


def parse_ustar_header(header: bytes) -> tarfile.TarInfo:
    """Parse one POSIX USTAR header block that must describe a regular file.

    Raises ValueError, tarfile.HeaderError or UnicodeError for a bad header.
    """
    if len(header) != TAR_BLOCK_SIZE:
        raise ValueError(f"header has {len(header)} bytes")
    if header[257:265] != tarfile.POSIX_MAGIC:
        raise ValueError("header does not contain POSIX USTAR magic")
    info = tarfile.TarInfo.frombuf(header, "utf-8", "surrogateescape")
    if not info.isreg():
        raise ValueError(f"member is not a regular file: {info.name!r}")
    return info
