from __future__ import annotations

import hashlib
import hmac
import os
import re
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile

from football_system.domain.archive import canonical_json
from football_system.domain.raw_data import RawArtifactMetadata

_SAFE_PROVIDER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,159}$")
_WINDOWS_RESERVED_NAMES = frozenset(
    {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{number}" for number in range(1, 10)),
        *(f"LPT{number}" for number in range(1, 10)),
    }
)


class RawArchiveCollisionError(FileExistsError):
    pass


@dataclass(frozen=True, slots=True)
class ArchivedRawArtifact:
    artifact_id: str
    payload_path: Path
    metadata_path: Path


class RawDataArchive:
    def __init__(self, root: str | Path) -> None:
        archive_root = Path(root).resolve()
        if archive_root.exists() and not archive_root.is_dir():
            raise NotADirectoryError(
                f"raw archive root is not a directory: {archive_root}"
            )
        archive_root.mkdir(parents=True, exist_ok=True)
        self._root = archive_root

    @property
    def root(self) -> Path:
        return self._root

    def write(
        self,
        payload: bytes,
        metadata: RawArtifactMetadata,
    ) -> ArchivedRawArtifact:
        if not isinstance(payload, bytes):
            raise TypeError("raw archive payload must be bytes")
        if not isinstance(metadata, RawArtifactMetadata):
            raise TypeError("raw archive metadata must be RawArtifactMetadata")
        provider = _safe_provider(metadata.provider)
        metadata = RawArtifactMetadata.model_validate(
            metadata.model_dump(mode="python")
        )
        payload_sha256 = hashlib.sha256(payload).hexdigest()
        if not hmac.compare_digest(payload_sha256, metadata.payload_sha256):
            raise ValueError("raw payload SHA-256 does not match metadata")

        date_component = metadata.requested_at_utc.date().isoformat()
        directory = self._contained_directory(provider, date_component)
        metadata_bytes = canonical_json(metadata).encode("utf-8")
        artifact_id = hashlib.sha256(metadata_bytes).hexdigest()
        payload_path = directory / f"{artifact_id}.raw"
        metadata_path = directory / f"{artifact_id}.metadata.json"
        lock_path = directory / f".{artifact_id}.lock"
        artifact = ArchivedRawArtifact(
            artifact_id=artifact_id,
            payload_path=payload_path,
            metadata_path=metadata_path,
        )

        if _complete_artifact_matches(
            payload_path,
            payload,
            metadata_path,
            metadata_bytes,
        ):
            return artifact

        lock_descriptor: int | None = None
        lock_owned = False
        try:
            try:
                lock_descriptor = os.open(
                    lock_path,
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                    0o600,
                )
                lock_owned = True
            except FileExistsError:
                if _complete_artifact_matches(
                    payload_path,
                    payload,
                    metadata_path,
                    metadata_bytes,
                ):
                    return artifact
                raise RawArchiveCollisionError(
                    f"raw artifact collision or write in progress: {artifact_id}"
                ) from None
            os.close(lock_descriptor)
            lock_descriptor = None

            _assert_existing_content(payload_path, payload)
            _assert_existing_content(metadata_path, metadata_bytes)
            _atomic_write_missing(payload_path, payload)
            _atomic_write_missing(metadata_path, metadata_bytes)
            return artifact
        finally:
            if lock_descriptor is not None:
                os.close(lock_descriptor)
            if lock_owned and lock_path.exists() and not lock_path.is_symlink():
                lock_path.unlink(missing_ok=True)

    def _contained_directory(self, provider: str, date_component: str) -> Path:
        provider_directory = self._root / provider
        if provider_directory.is_symlink():
            raise ValueError("raw archive provider directory cannot be a symlink")
        provider_directory.mkdir(exist_ok=True)
        if not provider_directory.resolve().is_relative_to(self._root):
            raise ValueError("raw archive provider path escapes its root")

        directory = provider_directory / date_component
        if directory.is_symlink():
            raise ValueError("raw archive date directory cannot be a symlink")
        directory.mkdir(exist_ok=True)
        if not directory.resolve().is_relative_to(self._root):
            raise ValueError("raw archive date path escapes its root")
        return directory


def _safe_provider(provider: str) -> str:
    if (
        not _SAFE_PROVIDER.fullmatch(provider)
        or provider in {".", ".."}
        or provider.endswith(".")
        or provider.upper() in _WINDOWS_RESERVED_NAMES
    ):
        raise ValueError("provider is not safe for a raw archive path")
    return provider


def _complete_artifact_matches(
    payload_path: Path,
    payload: bytes,
    metadata_path: Path,
    metadata: bytes,
) -> bool:
    payload_exists = payload_path.exists() or payload_path.is_symlink()
    metadata_exists = metadata_path.exists() or metadata_path.is_symlink()
    if not payload_exists and not metadata_exists:
        return False
    _assert_existing_content(payload_path, payload, allow_missing=True)
    _assert_existing_content(metadata_path, metadata, allow_missing=True)
    return payload_exists and metadata_exists


def _assert_existing_content(
    path: Path,
    expected: bytes,
    *,
    allow_missing: bool = False,
) -> None:
    exists = path.exists() or path.is_symlink()
    if not exists:
        if allow_missing:
            return
        return
    if path.is_symlink() or not path.is_file() or path.stat().st_size != len(expected):
        raise RawArchiveCollisionError(f"refusing to overwrite raw artifact: {path}")
    with path.open("rb") as stream:
        if stream.read(len(expected) + 1) != expected:
            raise RawArchiveCollisionError(
                f"refusing to overwrite different raw artifact: {path}"
            )


def _atomic_write_missing(path: Path, content: bytes) -> None:
    if path.exists() or path.is_symlink():
        _assert_existing_content(path, content)
        return
    temporary_path: Path | None = None
    try:
        with NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
            temporary_path = Path(stream.name)
        if path.exists() or path.is_symlink():
            _assert_existing_content(path, content)
            return
        os.replace(temporary_path, path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
