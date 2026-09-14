"""System payout-state exports have their own bound; V3 wire limits stay fixed."""

import os
from pathlib import Path
from tempfile import NamedTemporaryFile

from football_system.infrastructure.files.review_bridge import _has_content

MAX_STRATEGY_FILE_BYTES = 64 * 1024 * 1024


def write_strategy_file(path: Path, content: str) -> None:
    encoded = (content + "\n").encode("utf-8")
    if len(encoded) > MAX_STRATEGY_FILE_BYTES:
        raise ValueError("strategy export exceeds its 64 MiB bound")
    if path.exists():
        if _has_content(path, encoded):
            return
        raise FileExistsError("strategy export refuses to overwrite different content")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with NamedTemporaryFile(
            mode="wb", dir=path.parent, prefix=".strategy-", suffix=".tmp", delete=False
        ) as stream:
            temporary = Path(stream.name)
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path)
    except FileExistsError:
        if not _has_content(path, encoded):
            raise
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
