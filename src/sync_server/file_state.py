"""Portable catalog signatures and stricter same-handle change detection."""
import os

_WINDOWS = os.name == "nt"


def file_signature(info):
    # CPython on Windows may expose creation time through stat/lstat ctime,
    # but metadata-change time through fstat ctime. Birthtime is consistent
    # across the two APIs on supported Windows Python versions (3.12+).
    timestamp = info.st_birthtime_ns if _WINDOWS else info.st_ctime_ns
    return info.st_size, info.st_mtime_ns, timestamp, info.st_ino


def descriptor_signature(info):
    # Compare raw ctime only with another fstat from the same open handle.
    # This retains Windows ChangeTime protection for in-place writes even
    # when size and mtime have been restored by the writer.
    return info.st_size, info.st_mtime_ns, info.st_ctime_ns, info.st_ino, info.st_dev
