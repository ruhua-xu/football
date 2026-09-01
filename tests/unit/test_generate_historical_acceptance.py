from pathlib import Path

import pytest

from scripts import generate_historical_acceptance as generator


ROOT = Path(__file__).resolve().parents[2]
FIXED_CORPUS = ROOT / "data" / "fixtures" / "historical_acceptance"


def _file_bytes(directory: Path) -> dict[str, bytes]:
    return {
        path.relative_to(directory).as_posix(): path.read_bytes()
        for path in directory.rglob("*")
        if path.is_file()
    }


def test_regeneration_is_byte_identical_and_drops_unknown_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output_directory = tmp_path / "historical_acceptance"
    output_directory.mkdir()
    (output_directory / "unknown.json").write_text("stale", encoding="utf-8")
    monkeypatch.setattr(generator, "OUTPUT_DIRECTORY", output_directory)

    generator.main()

    assert _file_bytes(output_directory) == _file_bytes(FIXED_CORPUS)


def test_generation_failure_leaves_existing_corpus_untouched(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output_directory = tmp_path / "historical_acceptance"
    output_directory.mkdir()
    (output_directory / "existing.json").write_bytes(b"existing corpus\n")
    before = _file_bytes(output_directory)
    monkeypatch.setattr(generator, "OUTPUT_DIRECTORY", output_directory)

    def fail_generation(staged_directory: Path) -> None:
        (staged_directory / "partial.json").write_bytes(b"partial corpus\n")
        raise RuntimeError("injected generation failure")

    monkeypatch.setattr(generator, "_generate_corpus", fail_generation)

    with pytest.raises(RuntimeError, match="injected generation failure"):
        generator.main()

    assert _file_bytes(output_directory) == before
    assert not tuple(tmp_path.glob(".historical_acceptance.staging-*"))


def test_publish_failure_restores_previous_corpus(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output_directory = tmp_path / "historical_acceptance"
    output_directory.mkdir()
    (output_directory / "existing.json").write_bytes(b"existing corpus\n")
    staged_directory = tmp_path / ".historical_acceptance.staging-test"
    staged_directory.mkdir()
    (staged_directory / "generated.json").write_bytes(b"generated corpus\n")
    original_rename = Path.rename

    def fail_staged_rename(self: Path, target: Path) -> Path:
        if self == staged_directory and Path(target) == output_directory:
            raise OSError("injected publish failure")
        return original_rename(self, target)

    monkeypatch.setattr(Path, "rename", fail_staged_rename)

    with pytest.raises(OSError, match="injected publish failure"):
        generator._publish_corpus(staged_directory, output_directory)

    assert _file_bytes(output_directory) == {"existing.json": b"existing corpus\n"}
    assert _file_bytes(staged_directory) == {"generated.json": b"generated corpus\n"}
    assert not tuple(tmp_path.glob("*.previous"))
