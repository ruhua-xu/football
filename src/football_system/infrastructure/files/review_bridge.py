from __future__ import annotations

import os
from pathlib import Path
from tempfile import NamedTemporaryFile

from football_system.domain.review import MAX_CONTRACT_FILE_BYTES


def read_contract_file(path: Path) -> bytes:
    if not path.is_file():
        raise FileNotFoundError(f"contract file does not exist: {path}")
    if path.stat().st_size > MAX_CONTRACT_FILE_BYTES:
        raise ValueError(f"contract file exceeds the size limit: {path}")
    with path.open("rb") as stream:
        data = stream.read(MAX_CONTRACT_FILE_BYTES + 1)
    if len(data) > MAX_CONTRACT_FILE_BYTES:
        raise ValueError(f"contract file exceeds the size limit: {path}")
    return data


def write_contract_file(path: Path, content: str) -> None:
    encoded = (content + "\n").encode("utf-8")
    if len(encoded) > MAX_CONTRACT_FILE_BYTES:
        raise ValueError(f"contract file exceeds the size limit: {path}")
    if path.exists():
        if _has_content(path, encoded):
            return
        raise FileExistsError(f"refusing to overwrite different file: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
            temporary_path = Path(stream.name)
        os.link(temporary_path, path)
    except FileExistsError:
        if not _has_content(path, encoded):
            raise
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _has_content(path: Path, expected: bytes) -> bool:
    if not path.is_file() or path.stat().st_size != len(expected):
        return False
    with path.open("rb") as stream:
        return stream.read(len(expected) + 1) == expected
