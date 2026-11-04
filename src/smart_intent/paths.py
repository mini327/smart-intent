"""Conservative local-file reads, including post-open handle checks on Windows."""

import os
import stat
from pathlib import Path

from .errors import DomainError


def checked_path(path: Path, root: Path) -> Path:
    for item in (path, root):
        if not item.is_absolute() or ".." in item.parts:
            raise DomainError("access_denied", "源路径不在允许范围内。")
        if os.name == "nt" and (str(item).startswith("\\\\") or ":" in str(item)[2:]):
            raise DomainError("access_denied", "不支持网络、设备或备用数据流路径。")
        for part in (item, *item.parents):
            info = part.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                raise DomainError("access_denied", "不允许经链接或重解析点读取数据。")
    resolved = path.resolve(strict=True)
    if not resolved.is_relative_to(root.resolve(strict=True)):
        raise DomainError("access_denied", "源路径不在允许范围内。")
    return resolved


def handle_path(fd: int) -> Path:
    if os.name == "nt":
        import ctypes
        import msvcrt
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        fn = kernel.GetFinalPathNameByHandleW
        fn.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD]
        fn.restype = wintypes.DWORD
        buffer = ctypes.create_unicode_buffer(32768)
        n = fn(msvcrt.get_osfhandle(fd), buffer, len(buffer), 0)
        if not n or n >= len(buffer):
            raise DomainError("access_denied", "无法核验打开的源文件。")
        value = buffer.value
        if value.startswith("\\\\?\\UNC\\"):
            raise DomainError("access_denied", "不支持网络数据源。")
        return Path(value.removeprefix("\\\\?\\"))
    proc = Path(f"/proc/self/fd/{fd}")
    if proc.exists():
        return Path(os.readlink(proc))
    raise DomainError("platform_unsupported", "当前平台没有实现打开文件后的范围核验。")


def read_source(source, project_root, max_bytes, check_deadline):
    try:
        path = checked_path(source.path, source.allowed_root)
        checked_path(source.allowed_root, project_root)
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(path, flags)
        with os.fdopen(fd, "rb") as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode):
                raise DomainError("access_denied", "数据源必须为普通文件。")
            actual = handle_path(stream.fileno()).resolve(strict=True)
            if actual != path or not actual.is_relative_to(
                source.allowed_root.resolve(strict=True)
            ):
                raise DomainError("access_denied", "实际打开文件与授权源不一致。")
            if before.st_size > max_bytes:
                raise DomainError("source_too_large", "数据源超过大小限制。")
            chunks, size = [], 0
            while True:
                check_deadline()
                chunk = stream.read(min(65536, max_bytes + 1 - size))
                if not chunk:
                    break
                size += len(chunk)
                if size > max_bytes:
                    raise DomainError("source_too_large", "数据源超过大小限制。")
                chunks.append(chunk)
            after = os.fstat(stream.fileno())
            current = path.stat()

            def signature(info):
                return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns

            # Windows fstat and stat can disagree about creation/change time (ctime).
            # Compare ctime only between calls to the same API, plus identity/size/mtime
            # across the opened handle and the directory entry.
            if (
                signature(before) != signature(after)
                or before.st_ctime_ns != after.st_ctime_ns
                or signature(after) != signature(current)
            ):
                raise DomainError(
                    "source_changed", "读取期间源文件发生变化，请重新查询。", retryable=True
                )
            return b"".join(chunks), after
    except PermissionError as exc:
        raise DomainError("access_denied", "无权读取该数据源。") from exc
    except FileNotFoundError as exc:
        raise DomainError("source_unavailable", "配置的数据源不存在或不可用。") from exc
    except OSError as exc:
        raise DomainError("source_unavailable", "无法读取配置的数据源。") from exc
