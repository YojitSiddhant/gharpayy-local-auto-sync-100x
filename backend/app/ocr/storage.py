"""Private, content-addressed screenshot evidence storage.

Files live outside any public/static directory, in an owner-only (0700) folder,
written owner-read/write only (0600). No HTTP route serves them in Phase 2.
Retention: there is no automatic purge in Phase 2; deletion is manual.
"""
from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_EXTENSIONS = {"png", "jpg", "webp"}


def store_evidence(root: str | Path, sha256: str, extension: str, data: bytes) -> str:
    if not _SHA256_RE.fullmatch(sha256) or extension not in _EXTENSIONS:
        raise ValueError("Invalid evidence key")
    directory = Path(root).resolve()
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(directory, 0o700)
    key = f"{sha256}.{extension}"
    target = directory / key
    if target.is_file():
        return key
    fd, temp_path = tempfile.mkstemp(dir=directory, prefix=".incoming-")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.chmod(temp_path, 0o600)
        os.replace(temp_path, target)
    except BaseException:
        Path(temp_path).unlink(missing_ok=True)
        raise
    return key
