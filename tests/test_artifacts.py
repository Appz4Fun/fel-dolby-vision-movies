import json
import os
from pathlib import Path
import stat

import artifacts
import pytest
from artifacts import publish_outputs, write_artifacts
from models import FelEvidence, FelRelease
from parser import parse_fel_releases


def release(title: str, date: str) -> FelRelease:
    return FelRelease(
        movie_title=title,
        release_date=date,
        studio="Unknown",
        audio_formats=["TrueHD Atmos"],
        english_audio="Yes",
        fel_evidence=FelEvidence(
            source_url=f"https://example.test/{title}",
            quote=f"{title} is Profile 7 FEL",
            evidence_type="fixture",
        ),
    )


def test_publish_outputs_writes_data_and_dashboard_from_releases(tmp_path: Path):
    sorted_releases = publish_outputs(
        [
            release("Older", "2020"),
            release("Newer", "2026-05-01"),
        ],
        output_dir=tmp_path,
    )

    assert [item.movie_title for item in sorted_releases] == ["Newer", "Older"]
    assert (tmp_path / "data/releases.json").exists()
    assert (tmp_path / "dist/index.html").exists()
    assert (tmp_path / "dist/releases.json").exists()


def test_publish_outputs_keeps_table_release_with_embedded_year_without_enrichment(
    tmp_path: Path,
):
    html = """
    <table>
      <tr><th>Title</th><th>DV</th></tr>
      <tr><td>Alpha (2023)</td><td>Profile 7 FEL</td></tr>
    </table>
    """
    releases = parse_fel_releases(html, "https://example.test/thread")

    published = publish_outputs(releases, output_dir=tmp_path)

    assert [(item.movie_title, item.release_date) for item in published] == [
        ("Alpha", "2023")
    ]
    data = json.loads((tmp_path / "data/releases.json").read_text(encoding="utf-8"))
    assert [(item["movie_title"], item["release_date"]) for item in data] == [
        ("Alpha", "2023")
    ]


def test_sort_key_places_unknown_dates_last():
    assert artifacts._sort_key(release("Unknown", "Unknown"))[0] == 1


def test_normalize_sort_date_pads_year_month():
    assert artifacts._normalize_sort_date("2023-06") == "2023-06-00"


def test_write_artifacts_quarantines_unmatched_unknown_and_writes_review(
    tmp_path: Path,
):
    review_path = tmp_path / "review.json"
    write_artifacts(
        [
            release("Unknown Date", "Unknown"),
            release("Newer", "2026-05-01"),
            release("Older", "2020"),
        ],
        output_dir=tmp_path,
        review_output_path=review_path,
    )

    data = json.loads((tmp_path / "data/releases.json").read_text(encoding="utf-8"))
    assert [item["movie_title"] for item in data] == [
        "Newer",
        "Older",
    ]
    review = json.loads(review_path.read_text(encoding="utf-8"))
    assert review["merged_count"] == 0
    assert review["addition_count"] == 2
    assert review["review_count"] == 1
    assert review["items"][0]["reason"] == "missing-year-no-match"


@pytest.mark.parametrize(
    "collision_kind",
    ["direct", "symlink", "symlinked-parent", "hard-link"],
)
def test_write_artifacts_rejects_review_path_referring_to_releases_json(
    tmp_path: Path,
    collision_kind: str,
):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    releases_path = data_dir / "releases.json"
    releases_path.write_text(
        json.dumps([release("Existing", "2020").to_dict()]) + "\n",
        encoding="utf-8",
    )
    original_bytes = releases_path.read_bytes()

    if collision_kind == "direct":
        review_path = releases_path
    elif collision_kind == "symlink":
        review_path = tmp_path / "review.json"
        review_path.symlink_to(releases_path)
    elif collision_kind == "symlinked-parent":
        linked_data_dir = tmp_path / "linked-data"
        linked_data_dir.symlink_to(data_dir, target_is_directory=True)
        review_path = linked_data_dir / "releases.json"
    else:
        review_path = tmp_path / "review.json"
        review_path.hardlink_to(releases_path)

    with pytest.raises(ValueError) as exc_info:
        write_artifacts(
            [release("Incoming", "2021")],
            output_dir=tmp_path,
            review_output_path=review_path,
        )

    assert str(exc_info.value) == ("review output must not refer to data/releases.json")
    assert releases_path.read_bytes() == original_bytes


def test_validate_review_output_rejects_existing_directory(tmp_path: Path):
    review_path = tmp_path / "review.json"
    review_path.mkdir()

    with pytest.raises(ValueError) as exc_info:
        artifacts.validate_review_output_path(tmp_path, review_path)

    assert str(exc_info.value) == "review output must be a regular file path"


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFO unsupported")
def test_validate_review_output_rejects_fifo(tmp_path: Path):
    review_path = tmp_path / "review.json"
    os.mkfifo(review_path)

    with pytest.raises(ValueError) as exc_info:
        artifacts.validate_review_output_path(tmp_path, review_path)

    assert str(exc_info.value) == "review output must be a regular file path"


def test_validate_review_output_rejects_symlink_loop_safely(tmp_path: Path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.symlink_to(second)
    second.symlink_to(first)

    with pytest.raises(ValueError) as exc_info:
        artifacts.validate_review_output_path(tmp_path, first)

    assert str(exc_info.value) == "review output must be a regular file path"


def test_validate_review_output_wraps_resolution_error(
    tmp_path: Path,
    monkeypatch,
):
    review_path = tmp_path / "review.json"
    original_resolve = Path.resolve

    def failing_resolve(path, *args, **kwargs):
        if path == review_path:
            raise OSError("private filesystem detail")
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", failing_resolve)

    with pytest.raises(ValueError) as exc_info:
        artifacts.validate_review_output_path(tmp_path, review_path)

    assert str(exc_info.value) == "review output must be a regular file path"


def test_validate_review_output_rejects_file_as_parent(tmp_path: Path):
    parent = tmp_path / "not-a-directory"
    parent.write_text("private body\n", encoding="utf-8")

    with pytest.raises(ValueError) as exc_info:
        artifacts.validate_review_output_path(tmp_path, parent / "review.json")

    assert str(exc_info.value) == "review output must be a regular file path"


def test_validate_review_output_rejects_symlink_loop_parent(tmp_path: Path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.symlink_to(second)
    second.symlink_to(first)

    with pytest.raises(ValueError) as exc_info:
        artifacts.validate_review_output_path(tmp_path, first / "review.json")

    assert str(exc_info.value) == "review output must be a regular file path"


def test_validate_review_output_wraps_samefile_error(
    tmp_path: Path,
    monkeypatch,
):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    releases_path = data_dir / "releases.json"
    releases_path.write_text("[]\n", encoding="utf-8")
    review_path = tmp_path / "review.json"
    review_path.write_text("{}\n", encoding="utf-8")

    def failing_samefile(_path, _other):
        raise OSError("private filesystem detail")

    monkeypatch.setattr(Path, "samefile", failing_samefile)

    with pytest.raises(ValueError) as exc_info:
        artifacts.validate_review_output_path(tmp_path, review_path)

    assert str(exc_info.value) == "review output must be a regular file path"


def test_validate_review_output_conservatively_rejects_casefold_collision(
    tmp_path: Path,
):
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    with pytest.raises(ValueError) as exc_info:
        artifacts.validate_review_output_path(
            tmp_path,
            data_dir / "RELEASES.JSON",
        )

    assert str(exc_info.value) == ("review output must not refer to data/releases.json")


def test_write_artifacts_rejects_directory_before_changing_releases(tmp_path: Path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    releases_path = data_dir / "releases.json"
    releases_path.write_text(
        json.dumps([release("Existing", "2020").to_dict()]) + "\n",
        encoding="utf-8",
    )
    original_bytes = releases_path.read_bytes()
    review_path = tmp_path / "review.json"
    review_path.mkdir()

    with pytest.raises(ValueError, match="regular file path"):
        write_artifacts(
            [release("Incoming", "2021")],
            output_dir=tmp_path,
            review_output_path=review_path,
        )

    assert releases_path.read_bytes() == original_bytes


def test_write_artifacts_rejects_symlink_loop_before_changing_releases(
    tmp_path: Path,
):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    releases_path = data_dir / "releases.json"
    releases_path.write_text(
        json.dumps([release("Existing", "2020").to_dict()]) + "\n",
        encoding="utf-8",
    )
    original_bytes = releases_path.read_bytes()
    review_path = tmp_path / "review.json"
    other_path = tmp_path / "other"
    review_path.symlink_to(other_path)
    other_path.symlink_to(review_path)

    with pytest.raises(ValueError, match="regular file path"):
        write_artifacts(
            [release("Incoming", "2021")],
            output_dir=tmp_path,
            review_output_path=review_path,
        )

    assert releases_path.read_bytes() == original_bytes


def test_write_artifacts_preserves_noncollision_with_missing_review_parents(
    tmp_path: Path,
):
    review_path = tmp_path / "nested" / "review" / "review.json"

    write_artifacts(
        [release("Incoming", "2021")],
        output_dir=tmp_path,
        review_output_path=review_path,
    )

    assert json.loads(review_path.read_text(encoding="utf-8"))["addition_count"] == 1
    assert (
        json.loads((tmp_path / "data" / "releases.json").read_text(encoding="utf-8"))[
            0
        ]["movie_title"]
        == "Incoming"
    )


def test_write_artifacts_stages_and_replaces_review_before_releases(
    tmp_path: Path,
    monkeypatch,
):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    releases_path = data_dir / "releases.json"
    review_path = tmp_path / "review.json"
    replace_calls: list[tuple[Path, Path]] = []
    commit_source_modes: list[int] = []
    real_replace = os.replace

    def tracking_replace(source, target):
        source_path = Path(source)
        target_path = Path(target)
        replace_calls.append((source_path, target_path))
        assert source_path.parent == target_path.parent
        if target_path in {review_path, releases_path}:
            commit_source_modes.append(stat.S_IMODE(source_path.stat().st_mode))
        return real_replace(source, target)

    monkeypatch.setattr(os, "replace", tracking_replace)

    write_artifacts(
        [release("Incoming", "2021")],
        output_dir=tmp_path,
        review_output_path=review_path,
    )

    assert [target for _, target in replace_calls] == [review_path, releases_path]
    assert commit_source_modes == [0o600, 0o600]
    staged_paths = [source for source, _ in replace_calls]
    assert len(set(staged_paths)) == 2
    assert all(not path.exists() for path in staged_paths)


def test_review_replace_failure_preserves_releases_and_cleans_staged_files(
    tmp_path: Path,
    monkeypatch,
):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    releases_path = data_dir / "releases.json"
    releases_path.write_text(
        json.dumps([release("Existing", "2020").to_dict()]) + "\n",
        encoding="utf-8",
    )
    original_bytes = releases_path.read_bytes()
    review_path = tmp_path / "review.json"
    before_paths = {path.relative_to(tmp_path) for path in tmp_path.rglob("*")}

    def failing_replace(_source, target):
        assert Path(target) == review_path
        raise OSError("review replace failed")

    monkeypatch.setattr(os, "replace", failing_replace)

    with pytest.raises(OSError, match="review replace failed"):
        write_artifacts(
            [release("Incoming", "2021")],
            output_dir=tmp_path,
            review_output_path=review_path,
        )

    assert releases_path.read_bytes() == original_bytes
    assert {path.relative_to(tmp_path) for path in tmp_path.rglob("*")} == before_paths


def test_review_replace_failure_restores_existing_review_entry(
    tmp_path: Path,
    monkeypatch,
):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    releases_path = data_dir / "releases.json"
    releases_path.write_text(
        json.dumps([release("Existing", "2020").to_dict()]) + "\n",
        encoding="utf-8",
    )
    original_release_bytes = releases_path.read_bytes()
    review_path = tmp_path / "review.json"
    original_review_bytes = b"exact prior review bytes\n"
    review_path.write_bytes(original_review_bytes)
    original_review_inode = review_path.lstat().st_ino
    before_paths = {path.relative_to(tmp_path) for path in tmp_path.rglob("*")}
    real_replace = os.replace
    review_commit_failed = False
    primary_error = OSError("review replace failed")

    def failing_review_commit(source, target):
        nonlocal review_commit_failed
        if Path(target) == review_path and not review_commit_failed:
            review_commit_failed = True
            raise primary_error
        return real_replace(source, target)

    monkeypatch.setattr(os, "replace", failing_review_commit)

    with pytest.raises(OSError, match="review replace failed") as exc_info:
        write_artifacts(
            [release("Incoming", "2021")],
            output_dir=tmp_path,
            review_output_path=review_path,
        )

    assert review_commit_failed is True
    assert exc_info.value is primary_error
    assert releases_path.read_bytes() == original_release_bytes
    assert review_path.read_bytes() == original_review_bytes
    assert review_path.lstat().st_ino == original_review_inode
    assert {path.relative_to(tmp_path) for path in tmp_path.rglob("*")} == before_paths


def test_restore_failure_raises_recovery_error_once_and_retains_original_backup(
    tmp_path: Path,
    monkeypatch,
):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    releases_path = data_dir / "releases.json"
    releases_path.write_text(
        json.dumps([release("Existing", "2020").to_dict()]) + "\n",
        encoding="utf-8",
    )
    original_release_bytes = releases_path.read_bytes()
    review_path = tmp_path / "review.json"
    original_review_bytes = b"exact prior review bytes\n"
    review_path.write_bytes(original_review_bytes)
    original_review_inode = review_path.lstat().st_ino
    primary_error = OSError("private primary commit detail")
    recovery_error = OSError("private restore detail")
    real_replace = os.replace
    review_target_calls = 0
    release_target_calls = 0

    def failing_commit_and_restore(source, target):
        nonlocal release_target_calls, review_target_calls
        target_path = Path(target)
        if target_path == releases_path:
            release_target_calls += 1
            if release_target_calls == 1:
                raise primary_error
        if target_path == review_path:
            review_target_calls += 1
            if review_target_calls > 1:
                raise recovery_error
        return real_replace(source, target)

    monkeypatch.setattr(os, "replace", failing_commit_and_restore)

    with pytest.raises(artifacts.ArtifactTransactionRecoveryError) as exc_info:
        write_artifacts(
            [release("Incoming", "2021")],
            output_dir=tmp_path,
            review_output_path=review_path,
        )

    error = exc_info.value
    assert error.__cause__ is primary_error
    assert error.recovery_error is recovery_error
    assert release_target_calls == 2
    assert review_target_calls == 2
    assert error.retained_backup_path is not None
    retained_backup = error.retained_backup_path
    assert error.retained_backup_paths == (retained_backup,)
    assert retained_backup.exists()
    assert retained_backup.read_bytes() == original_review_bytes
    assert retained_backup.lstat().st_ino == original_review_inode
    assert releases_path.read_bytes() == original_release_bytes
    assert "private" not in str(error)
    assert str(retained_backup) not in str(error)
    hidden_paths = {
        path for path in tmp_path.iterdir() if path.name.startswith(".review.json.")
    }
    assert hidden_paths == {retained_backup}


def test_absent_review_unlink_failure_raises_chained_recovery_error_once(
    tmp_path: Path,
    monkeypatch,
):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    releases_path = data_dir / "releases.json"
    releases_path.write_text(
        json.dumps([release("Existing", "2020").to_dict()]) + "\n",
        encoding="utf-8",
    )
    original_release_bytes = releases_path.read_bytes()
    review_path = tmp_path / "review.json"
    primary_error = OSError("private primary commit detail")
    recovery_error = OSError("private unlink detail")
    real_replace = os.replace
    real_unlink = Path.unlink
    unlink_attempts = 0
    release_target_calls = 0

    def failing_release_commit(source, target):
        nonlocal release_target_calls
        if Path(target) == releases_path:
            release_target_calls += 1
            if release_target_calls == 1:
                raise primary_error
        return real_replace(source, target)

    def failing_review_unlink(path, *args, **kwargs):
        nonlocal unlink_attempts
        if path == review_path:
            unlink_attempts += 1
            raise recovery_error
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(os, "replace", failing_release_commit)
    monkeypatch.setattr(Path, "unlink", failing_review_unlink)

    with pytest.raises(artifacts.ArtifactTransactionRecoveryError) as exc_info:
        write_artifacts(
            [release("Incoming", "2021")],
            output_dir=tmp_path,
            review_output_path=review_path,
        )

    error = exc_info.value
    assert error.__cause__ is primary_error
    assert error.recovery_error is recovery_error
    assert error.retained_backup_path is None
    assert release_target_calls == 2
    assert unlink_attempts == 1
    assert review_path.exists()
    assert releases_path.read_bytes() == original_release_bytes
    assert "private" not in str(error)
    assert not list(tmp_path.glob(".review.json.*.tmp"))
    assert not list(data_dir.glob(".releases.json.*.tmp"))


def test_backup_unlink_failure_after_success_is_best_effort_and_not_retried(
    tmp_path: Path,
    monkeypatch,
):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    releases_path = data_dir / "releases.json"
    releases_path.write_text(
        json.dumps([release("Existing", "2020").to_dict()]) + "\n",
        encoding="utf-8",
    )
    review_path = tmp_path / "review.json"
    original_review_bytes = b"exact prior review bytes\n"
    review_path.write_bytes(original_review_bytes)
    original_review_inode = review_path.lstat().st_ino
    real_unlink = Path.unlink
    backup_unlink_attempts = 0
    retained_backup: Path | None = None

    def failing_backup_unlink(path, *args, **kwargs):
        nonlocal backup_unlink_attempts, retained_backup
        if os.path.lexists(path) and path.lstat().st_ino == original_review_inode:
            backup_unlink_attempts += 1
            retained_backup = path
            raise OSError("private stale-backup cleanup detail")
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", failing_backup_unlink)

    result = write_artifacts(
        [release("Incoming", "2021")],
        output_dir=tmp_path,
        review_output_path=review_path,
    )

    assert [item.movie_title for item in result] == ["Incoming", "Existing"]
    assert backup_unlink_attempts == 1
    assert retained_backup is not None
    assert retained_backup.exists()
    assert retained_backup.read_bytes() == original_review_bytes
    assert json.loads(review_path.read_text(encoding="utf-8"))["addition_count"] == 1
    assert [
        item["movie_title"]
        for item in json.loads(releases_path.read_text(encoding="utf-8"))
    ] == ["Incoming", "Existing"]


def test_release_replace_failure_removes_new_review_and_preserves_canonical(
    tmp_path: Path,
    monkeypatch,
):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    releases_path = data_dir / "releases.json"
    releases_path.write_text(
        json.dumps([release("Existing", "2020").to_dict()]) + "\n",
        encoding="utf-8",
    )
    original_bytes = releases_path.read_bytes()
    review_path = tmp_path / "review.json"
    before_paths = {path.relative_to(tmp_path) for path in tmp_path.rglob("*")}
    real_replace = os.replace
    release_target_calls = 0

    def failing_replace(source, target):
        nonlocal release_target_calls
        if Path(target) == releases_path:
            release_target_calls += 1
            if release_target_calls == 1:
                raise OSError("release replace failed")
        return real_replace(source, target)

    monkeypatch.setattr(os, "replace", failing_replace)

    with pytest.raises(OSError, match="release replace failed"):
        write_artifacts(
            [release("Incoming", "2021")],
            output_dir=tmp_path,
            review_output_path=review_path,
        )

    assert releases_path.read_bytes() == original_bytes
    assert release_target_calls == 2
    assert not os.path.lexists(review_path)
    assert {path.relative_to(tmp_path) for path in tmp_path.rglob("*")} == before_paths


@pytest.mark.parametrize("review_kind", ["regular", "symlink", "hard-link"])
def test_release_replace_failure_restores_existing_review_directory_entry(
    tmp_path: Path,
    monkeypatch,
    review_kind: str,
):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    releases_path = data_dir / "releases.json"
    releases_path.write_text(
        json.dumps([release("Existing", "2020").to_dict()]) + "\n",
        encoding="utf-8",
    )
    original_release_bytes = releases_path.read_bytes()
    review_path = tmp_path / "review.json"
    backing_path = tmp_path / "review-backing.json"
    original_review_bytes = b"exact prior review bytes\n"
    if review_kind == "regular":
        review_path.write_bytes(original_review_bytes)
    else:
        backing_path.write_bytes(original_review_bytes)
        if review_kind == "symlink":
            review_path.symlink_to(backing_path)
        else:
            review_path.hardlink_to(backing_path)
    original_lstat = review_path.lstat()
    original_link_target = (
        os.readlink(review_path) if review_kind == "symlink" else None
    )
    before_paths = {path.relative_to(tmp_path) for path in tmp_path.rglob("*")}
    real_replace = os.replace
    release_target_calls = 0

    def failing_release_replace(source, target):
        nonlocal release_target_calls
        if Path(target) == releases_path:
            release_target_calls += 1
            if release_target_calls == 1:
                raise OSError("release replace failed")
        return real_replace(source, target)

    monkeypatch.setattr(os, "replace", failing_release_replace)

    with pytest.raises(OSError, match="release replace failed"):
        write_artifacts(
            [release("Incoming", "2021")],
            output_dir=tmp_path,
            review_output_path=review_path,
        )

    assert releases_path.read_bytes() == original_release_bytes
    assert release_target_calls == 2
    assert review_path.lstat().st_ino == original_lstat.st_ino
    assert review_path.read_bytes() == original_review_bytes
    if review_kind == "symlink":
        assert review_path.is_symlink()
        assert os.readlink(review_path) == original_link_target
        assert backing_path.read_bytes() == original_review_bytes
    elif review_kind == "hard-link":
        assert review_path.samefile(backing_path)
        assert backing_path.read_bytes() == original_review_bytes
    assert {path.relative_to(tmp_path) for path in tmp_path.rglob("*")} == before_paths


@pytest.mark.parametrize("alias_kind", ["symlink", "hard-link"])
def test_write_artifacts_revalidates_alias_swap_after_staging(
    tmp_path: Path,
    monkeypatch,
    alias_kind: str,
):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    releases_path = data_dir / "releases.json"
    releases_path.write_text(
        json.dumps([release("Existing", "2020").to_dict()]) + "\n",
        encoding="utf-8",
    )
    original_bytes = releases_path.read_bytes()
    review_path = tmp_path / "review.json"
    original_validate = artifacts.validate_review_output_path
    validation_count = 0

    def swapping_validate(output_dir, candidate_review_path):
        nonlocal validation_count
        validation_count += 1
        if validation_count == 2:
            if alias_kind == "symlink":
                review_path.symlink_to(releases_path)
            else:
                review_path.hardlink_to(releases_path)
        return original_validate(output_dir, candidate_review_path)

    monkeypatch.setattr(
        artifacts,
        "validate_review_output_path",
        swapping_validate,
    )

    with pytest.raises(
        ValueError,
        match="review output must not refer to data/releases.json",
    ):
        write_artifacts(
            [release("Incoming", "2021")],
            output_dir=tmp_path,
            review_output_path=review_path,
        )

    assert validation_count == 2
    assert releases_path.read_bytes() == original_bytes


@pytest.mark.parametrize("alias_kind", ["symlink", "hard-link"])
def test_atomic_review_replace_is_safe_if_alias_swaps_after_final_validation(
    tmp_path: Path,
    monkeypatch,
    alias_kind: str,
):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    releases_path = data_dir / "releases.json"
    releases_path.write_text("[]\n", encoding="utf-8")
    review_path = tmp_path / "review.json"
    real_replace = os.replace
    alias_injected = False

    def swapping_replace(source, target):
        nonlocal alias_injected
        if Path(target) == review_path and not alias_injected:
            alias_injected = True
            if alias_kind == "symlink":
                review_path.symlink_to(releases_path)
            else:
                review_path.hardlink_to(releases_path)
        return real_replace(source, target)

    monkeypatch.setattr(os, "replace", swapping_replace)

    write_artifacts(
        [release("Incoming", "2021")],
        output_dir=tmp_path,
        review_output_path=review_path,
    )

    assert alias_injected is True
    releases_payload = json.loads(releases_path.read_text(encoding="utf-8"))
    review_payload = json.loads(review_path.read_text(encoding="utf-8"))
    assert [item["movie_title"] for item in releases_payload] == ["Incoming"]
    assert review_payload["addition_count"] == 1
    releases_bytes = releases_path.read_bytes()
    review_path.write_text("changed review\n", encoding="utf-8")
    assert releases_path.read_bytes() == releases_bytes


def test_write_empty_review_output_is_atomic(tmp_path: Path, monkeypatch):
    review_path = tmp_path / "review.json"
    review_path.write_text("old review\n", encoding="utf-8")
    review_path.chmod(0o751)
    real_replace = os.replace
    replace_calls: list[tuple[Path, Path]] = []
    commit_source_modes: list[int] = []

    def tracking_replace(source, target):
        source_path = Path(source)
        target_path = Path(target)
        replace_calls.append((source_path, target_path))
        if target_path == review_path:
            commit_source_modes.append(stat.S_IMODE(source_path.stat().st_mode))
        return real_replace(source, target)

    monkeypatch.setattr(os, "replace", tracking_replace)

    artifacts.write_empty_review_output(review_path)

    commit_calls = [call for call in replace_calls if call[1] == review_path]
    assert len(commit_calls) == 1
    assert commit_source_modes == [0o600]
    staged_path, target_path = commit_calls[0]
    assert staged_path.parent == review_path.parent
    assert target_path == review_path
    assert not staged_path.exists()
    assert json.loads(review_path.read_text(encoding="utf-8")) == {
        "merged_count": 0,
        "addition_count": 0,
        "review_count": 0,
        "items": [],
    }


@pytest.mark.parametrize(
    "release_mode,review_mode",
    [(0o644, 0o644), (0o751, 0o640)],
)
def test_write_artifacts_preserves_existing_target_modes(
    tmp_path: Path,
    release_mode: int,
    review_mode: int,
):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    releases_path = data_dir / "releases.json"
    releases_path.write_text(
        json.dumps([release("Existing", "2020").to_dict()]) + "\n",
        encoding="utf-8",
    )
    releases_path.chmod(release_mode)
    review_path = tmp_path / "review.json"
    review_path.write_text("{}\n", encoding="utf-8")
    review_path.chmod(review_mode)

    write_artifacts(
        [release("Incoming", "2021")],
        output_dir=tmp_path,
        review_output_path=review_path,
    )

    assert stat.S_IMODE(releases_path.stat().st_mode) == release_mode
    assert stat.S_IMODE(review_path.stat().st_mode) == review_mode


def test_write_artifacts_uses_public_modes_for_new_outputs_and_private_staging(
    tmp_path: Path,
    monkeypatch,
):
    review_path = tmp_path / "review.json"
    stage_modes: list[int] = []
    original_stage = artifacts._stage_text

    def tracking_stage(path, text):
        staged_path = original_stage(path, text)
        stage_modes.append(stat.S_IMODE(staged_path.stat().st_mode))
        return staged_path

    monkeypatch.setattr(artifacts, "_stage_text", tracking_stage)

    write_artifacts(
        [release("Incoming", "2021")],
        output_dir=tmp_path,
        review_output_path=review_path,
    )

    releases_path = tmp_path / "data" / "releases.json"
    assert stage_modes == [0o600, 0o600]
    assert stat.S_IMODE(releases_path.stat().st_mode) == 0o644
    assert stat.S_IMODE(review_path.stat().st_mode) == 0o644


def test_stage_text_forces_private_mode_with_restrictive_umask(tmp_path: Path):
    previous_umask = os.umask(0o077)
    try:
        staged_path = artifacts._stage_text(tmp_path / "output.json", "payload\n")
    finally:
        os.umask(previous_umask)

    try:
        assert stat.S_IMODE(staged_path.stat().st_mode) == 0o600
    finally:
        staged_path.unlink()


def test_write_artifacts_creates_new_nested_outputs_with_restrictive_umask(
    tmp_path: Path,
):
    output_root = tmp_path / "output"
    review_path = output_root / "nested" / "review" / "review.json"
    releases_path = output_root / "data" / "releases.json"

    previous_umask = os.umask(0o077)
    try:
        write_artifacts(
            [release("Incoming", "2021")],
            output_dir=output_root,
            review_output_path=review_path,
        )
    finally:
        os.umask(previous_umask)

    assert json.loads(review_path.read_text(encoding="utf-8"))["addition_count"] == 1
    assert [
        item["movie_title"]
        for item in json.loads(releases_path.read_text(encoding="utf-8"))
    ] == ["Incoming"]
    assert stat.S_IMODE(review_path.stat().st_mode) == 0o644
    assert stat.S_IMODE(releases_path.stat().st_mode) == 0o644
    assert stat.S_IMODE(review_path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(releases_path.parent.stat().st_mode) == 0o700


def test_stage_text_fchmod_failure_preserves_primary_when_cleanup_fails(
    tmp_path: Path,
    monkeypatch,
):
    target = tmp_path / "output.json"
    primary_error = OSError("private fchmod detail")
    cleanup_error = OSError("private unlink detail")
    real_unlink = Path.unlink
    leaked_paths: list[Path] = []

    def failing_fchmod(_file_descriptor, _mode):
        raise primary_error

    def failing_temp_unlink(path, *args, **kwargs):
        if path.parent == target.parent and path.name.startswith(f".{target.name}."):
            leaked_paths.append(path)
            raise cleanup_error
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(os, "fchmod", failing_fchmod)
    monkeypatch.setattr(Path, "unlink", failing_temp_unlink)

    with pytest.raises(OSError, match="private fchmod detail") as exc_info:
        artifacts._stage_text(target, "payload\n")

    assert exc_info.value is primary_error
    assert len(leaked_paths) == 1
    leaked_path = leaked_paths[0]
    assert leaked_path.exists()
    assert leaked_path.parent == target.parent
    assert leaked_path.name.startswith(f".{target.name}.")
    assert leaked_path.name.endswith(".tmp")
    assert "private" not in leaked_path.name
    assert stat.S_IMODE(leaked_path.stat().st_mode) == 0o600
    real_unlink(leaked_path)


@pytest.mark.parametrize("failure_point", ["write", "close"])
def test_stage_text_stream_failure_preserves_primary_when_cleanup_fails(
    tmp_path: Path,
    monkeypatch,
    failure_point: str,
):
    target = tmp_path / "output.json"
    primary_error = OSError(f"private {failure_point} detail")
    cleanup_error = OSError("private unlink detail")
    real_fdopen = os.fdopen
    real_unlink = Path.unlink
    leaked_paths: list[Path] = []

    class FailingStream:
        def __init__(self, handle):
            self.handle = handle

        def __enter__(self):
            self.handle.__enter__()
            return self

        def __exit__(self, error_type, error, traceback):
            result = self.handle.__exit__(error_type, error, traceback)
            if failure_point == "close":
                raise primary_error
            return result

        def fileno(self):
            return self.handle.fileno()

        def write(self, text):
            if failure_point == "write":
                raise primary_error
            return self.handle.write(text)

    def failing_fdopen(file_descriptor, *args, **kwargs):
        return FailingStream(real_fdopen(file_descriptor, *args, **kwargs))

    def failing_temp_unlink(path, *args, **kwargs):
        if path.parent == target.parent and path.name.startswith(f".{target.name}."):
            leaked_paths.append(path)
            raise cleanup_error
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(os, "fdopen", failing_fdopen)
    monkeypatch.setattr(Path, "unlink", failing_temp_unlink)

    with pytest.raises(OSError, match=f"private {failure_point} detail") as exc_info:
        artifacts._stage_text(target, "payload\n")

    assert exc_info.value is primary_error
    assert len(leaked_paths) == 1
    leaked_path = leaked_paths[0]
    assert leaked_path.exists()
    assert leaked_path.parent == target.parent
    assert leaked_path.name.startswith(f".{target.name}.")
    assert leaked_path.name.endswith(".tmp")
    assert "private" not in leaked_path.name
    assert stat.S_IMODE(leaked_path.stat().st_mode) == 0o600
    real_unlink(leaked_path)


@pytest.mark.parametrize("existing_mode", [None, 0o751])
def test_write_empty_review_output_uses_public_or_existing_mode(
    tmp_path: Path,
    existing_mode: int | None,
):
    review_path = tmp_path / "review.json"
    if existing_mode is not None:
        review_path.write_text("old review\n", encoding="utf-8")
        review_path.chmod(existing_mode)

    artifacts.write_empty_review_output(review_path)

    expected_mode = existing_mode if existing_mode is not None else 0o644
    assert stat.S_IMODE(review_path.stat().st_mode) == expected_mode


def test_review_target_chmod_failure_restores_both_original_outputs(
    tmp_path: Path,
    monkeypatch,
):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    releases_path = data_dir / "releases.json"
    releases_path.write_text(
        json.dumps([release("Existing", "2020").to_dict()]) + "\n",
        encoding="utf-8",
    )
    releases_path.chmod(0o604)
    original_release_bytes = releases_path.read_bytes()
    original_release_stat = releases_path.stat()
    review_path = tmp_path / "review.json"
    review_path.write_bytes(b"exact prior review bytes\n")
    review_path.chmod(0o640)
    original_review_bytes = review_path.read_bytes()
    original_review_stat = review_path.stat()
    before_paths = {path.relative_to(tmp_path) for path in tmp_path.rglob("*")}
    primary_error = OSError("review chmod failed")
    real_chmod = Path.chmod
    chmod_attempts = 0

    def failing_review_chmod(path, mode, *args, **kwargs):
        nonlocal chmod_attempts
        if path == review_path:
            chmod_attempts += 1
            raise primary_error
        return real_chmod(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "chmod", failing_review_chmod)

    with pytest.raises(OSError, match="review chmod failed") as exc_info:
        write_artifacts(
            [release("Incoming", "2021")],
            output_dir=tmp_path,
            review_output_path=review_path,
        )

    assert exc_info.value is primary_error
    assert chmod_attempts == 1
    assert releases_path.read_bytes() == original_release_bytes
    assert releases_path.stat().st_ino == original_release_stat.st_ino
    assert stat.S_IMODE(releases_path.stat().st_mode) == 0o604
    assert review_path.read_bytes() == original_review_bytes
    assert review_path.stat().st_ino == original_review_stat.st_ino
    assert stat.S_IMODE(review_path.stat().st_mode) == 0o640
    assert {path.relative_to(tmp_path) for path in tmp_path.rglob("*")} == before_paths


def test_release_target_chmod_failure_restores_both_original_outputs(
    tmp_path: Path,
    monkeypatch,
):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    releases_path = data_dir / "releases.json"
    releases_path.write_text(
        json.dumps([release("Existing", "2020").to_dict()]) + "\n",
        encoding="utf-8",
    )
    releases_path.chmod(0o604)
    original_release_bytes = releases_path.read_bytes()
    original_release_stat = releases_path.stat()
    review_path = tmp_path / "review.json"
    review_path.write_bytes(b"exact prior review bytes\n")
    review_path.chmod(0o640)
    original_review_bytes = review_path.read_bytes()
    original_review_stat = review_path.stat()
    before_paths = {path.relative_to(tmp_path) for path in tmp_path.rglob("*")}
    primary_error = OSError("release chmod failed")
    real_chmod = Path.chmod
    chmod_attempts = 0

    def failing_release_chmod(path, mode, *args, **kwargs):
        nonlocal chmod_attempts
        if path == releases_path:
            chmod_attempts += 1
            raise primary_error
        return real_chmod(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "chmod", failing_release_chmod)

    with pytest.raises(OSError, match="release chmod failed") as exc_info:
        write_artifacts(
            [release("Incoming", "2021")],
            output_dir=tmp_path,
            review_output_path=review_path,
        )

    assert exc_info.value is primary_error
    assert chmod_attempts == 1
    assert releases_path.read_bytes() == original_release_bytes
    assert releases_path.stat().st_ino == original_release_stat.st_ino
    assert stat.S_IMODE(releases_path.stat().st_mode) == 0o604
    assert review_path.read_bytes() == original_review_bytes
    assert review_path.stat().st_ino == original_review_stat.st_ino
    assert stat.S_IMODE(review_path.stat().st_mode) == 0o640
    assert {path.relative_to(tmp_path) for path in tmp_path.rglob("*")} == before_paths


def test_empty_review_target_chmod_failure_restores_original_entry(
    tmp_path: Path,
    monkeypatch,
):
    review_path = tmp_path / "review.json"
    original_bytes = b"exact prior review bytes\n"
    review_path.write_bytes(original_bytes)
    review_path.chmod(0o751)
    original_stat = review_path.stat()
    before_paths = {path.relative_to(tmp_path) for path in tmp_path.rglob("*")}
    primary_error = OSError("empty review chmod failed")
    real_chmod = Path.chmod
    chmod_attempts = 0

    def failing_target_chmod(path, mode, *args, **kwargs):
        nonlocal chmod_attempts
        if path == review_path:
            chmod_attempts += 1
            raise primary_error
        return real_chmod(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "chmod", failing_target_chmod)

    with pytest.raises(OSError, match="empty review chmod failed") as exc_info:
        artifacts.write_empty_review_output(review_path)

    assert exc_info.value is primary_error
    assert chmod_attempts == 1
    assert review_path.read_bytes() == original_bytes
    assert review_path.stat().st_ino == original_stat.st_ino
    assert stat.S_IMODE(review_path.stat().st_mode) == 0o751
    assert {path.relative_to(tmp_path) for path in tmp_path.rglob("*")} == before_paths


def test_empty_review_restore_failure_retains_original_backup(
    tmp_path: Path,
    monkeypatch,
):
    review_path = tmp_path / "review.json"
    original_bytes = b"exact prior review bytes\n"
    review_path.write_bytes(original_bytes)
    original_inode = review_path.stat().st_ino
    primary_error = OSError("private empty review chmod detail")
    recovery_error = OSError("private empty review restore detail")
    real_replace = os.replace
    real_chmod = Path.chmod
    review_target_calls = 0

    def failing_restore(source, target):
        nonlocal review_target_calls
        if Path(target) == review_path:
            review_target_calls += 1
            if review_target_calls == 2:
                raise recovery_error
        return real_replace(source, target)

    def failing_target_chmod(path, mode, *args, **kwargs):
        if path == review_path:
            raise primary_error
        return real_chmod(path, mode, *args, **kwargs)

    monkeypatch.setattr(os, "replace", failing_restore)
    monkeypatch.setattr(Path, "chmod", failing_target_chmod)

    with pytest.raises(artifacts.ArtifactTransactionRecoveryError) as exc_info:
        artifacts.write_empty_review_output(review_path)

    error = exc_info.value
    assert error.__cause__ is primary_error
    assert error.recovery_error is recovery_error
    assert review_target_calls == 2
    assert error.retained_backup_path is not None
    retained_backup = error.retained_backup_path
    assert retained_backup.exists()
    assert retained_backup.read_bytes() == original_bytes
    assert retained_backup.stat().st_ino == original_inode
    assert "private" not in str(error)
    assert str(retained_backup) not in str(error)


def test_cleanup_failure_does_not_mask_primary_commit_error(
    tmp_path: Path,
    monkeypatch,
):
    review_path = tmp_path / "review.json"
    releases_path = tmp_path / "data" / "releases.json"
    primary_error = OSError("review commit failed")
    cleanup_error = OSError("private cleanup detail")
    real_unlink = Path.unlink
    cleanup_attempts = 0

    def failing_review_commit(_source, target):
        if Path(target) == review_path:
            raise primary_error
        raise AssertionError(f"unexpected commit target: {target}")

    def failing_review_stage_cleanup(path, *args, **kwargs):
        nonlocal cleanup_attempts
        if path.parent == review_path.parent and path.name.startswith(".review.json."):
            cleanup_attempts += 1
            raise cleanup_error
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(os, "replace", failing_review_commit)
    monkeypatch.setattr(Path, "unlink", failing_review_stage_cleanup)

    with pytest.raises(OSError, match="review commit failed") as exc_info:
        write_artifacts(
            [release("Incoming", "2021")],
            output_dir=tmp_path,
            review_output_path=review_path,
        )

    assert exc_info.value is primary_error
    assert cleanup_attempts == 1
    assert not os.path.lexists(review_path)
    assert not os.path.lexists(releases_path)
    retained_stage = next(tmp_path.glob(".review.json.*.tmp"))
    real_unlink(retained_stage)


@pytest.mark.parametrize("writer_kind", ["paired", "direct"])
@pytest.mark.parametrize(
    "transition",
    ["backup", "stage-existing", "stage-absent"],
)
def test_post_success_keyboard_interrupt_reconciles_atomic_output_state(
    tmp_path: Path,
    monkeypatch,
    writer_kind: str,
    transition: str,
):
    target_existed = transition != "stage-absent"
    primary_error = KeyboardInterrupt(f"post-success {transition}")
    real_replace = os.replace
    injected = False
    backup_destination_was_absent: bool | None = None

    if writer_kind == "paired":
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        target = data_dir / "releases.json"
        review_path = tmp_path / "review.json"
        review_path.write_bytes(b"exact prior review bytes\n")
        review_path.chmod(0o640)
        original_review_bytes = review_path.read_bytes()
        original_review_stat = review_path.stat()
        if target_existed:
            target.write_text(
                json.dumps([release("Existing", "2020").to_dict()]) + "\n",
                encoding="utf-8",
            )
            target.chmod(0o604)
    else:
        target = tmp_path / "review.json"
        review_path = None
        if target_existed:
            target.write_bytes(b"exact prior output bytes\n")
            target.chmod(0o604)

    original_target_bytes = target.read_bytes() if target_existed else None
    original_target_stat = target.stat() if target_existed else None
    before_paths = {path.relative_to(tmp_path) for path in tmp_path.rglob("*")}

    def interrupt_after_success(source, destination):
        nonlocal backup_destination_was_absent, injected
        source_path = Path(source)
        destination_path = Path(destination)
        is_selected_backup = transition == "backup" and source_path == target
        is_selected_stage = (
            transition.startswith("stage")
            and destination_path == target
            and not injected
        )
        if not injected and (is_selected_backup or is_selected_stage):
            if is_selected_backup:
                backup_destination_was_absent = not os.path.lexists(destination_path)
            real_replace(source, destination)
            injected = True
            raise primary_error
        return real_replace(source, destination)

    monkeypatch.setattr(os, "replace", interrupt_after_success)

    with pytest.raises(KeyboardInterrupt) as exc_info:
        if writer_kind == "paired":
            write_artifacts(
                [release("Incoming", "2021")],
                output_dir=tmp_path,
                review_output_path=review_path,
            )
        else:
            artifacts.write_empty_review_output(target)

    assert exc_info.value is primary_error
    assert injected is True
    if transition == "backup":
        assert backup_destination_was_absent is True
    if target_existed:
        assert target.read_bytes() == original_target_bytes
        assert target.stat().st_ino == original_target_stat.st_ino
        assert stat.S_IMODE(target.stat().st_mode) == 0o604
    else:
        assert not os.path.lexists(target)
    if writer_kind == "paired":
        assert review_path.read_bytes() == original_review_bytes
        assert review_path.stat().st_ino == original_review_stat.st_ino
        assert stat.S_IMODE(review_path.stat().st_mode) == 0o640
    assert {path.relative_to(tmp_path) for path in tmp_path.rglob("*")} == before_paths


def test_unknown_backup_identity_after_replace_is_recovery_error(
    tmp_path: Path,
    monkeypatch,
):
    target = tmp_path / "review.json"
    original_bytes = b"exact prior output bytes\n"
    target.write_bytes(original_bytes)
    original_stat = target.stat()
    real_lstat = Path.lstat
    identity_failure = OSError("private backup lstat detail")
    identity_failed = False

    def failing_backup_lstat(path, *args, **kwargs):
        nonlocal identity_failed
        if path.suffix == ".bak" and not identity_failed:
            identity_failed = True
            raise identity_failure
        return real_lstat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", failing_backup_lstat)

    with pytest.raises(artifacts.ArtifactTransactionRecoveryError) as exc_info:
        artifacts.write_empty_review_output(target)

    error = exc_info.value
    assert identity_failed is True
    assert isinstance(error.__cause__, RuntimeError)
    assert "private" not in str(error)
    assert target.read_bytes() == original_bytes
    assert target.stat().st_ino == original_stat.st_ino
    assert not list(tmp_path.glob(".review.json.*.bak"))


def test_unknown_stage_identity_after_replace_is_recovery_error(
    tmp_path: Path,
    monkeypatch,
):
    target = tmp_path / "review.json"
    real_lstat = Path.lstat
    identity_failure = OSError("private target lstat detail")
    identity_failed = False

    def failing_new_target_lstat(path, *args, **kwargs):
        nonlocal identity_failed
        if path == target and os.path.lexists(target) and not identity_failed:
            identity_failed = True
            raise identity_failure
        return real_lstat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", failing_new_target_lstat)

    with pytest.raises(artifacts.ArtifactTransactionRecoveryError) as exc_info:
        artifacts.write_empty_review_output(target)

    error = exc_info.value
    assert identity_failed is True
    assert isinstance(error.__cause__, RuntimeError)
    assert "private" not in str(error)
    assert not os.path.lexists(target)
    assert not list(tmp_path.glob(".review.json.*"))


def test_unknown_stage_identity_preserves_active_replace_error_as_cause(
    tmp_path: Path,
    monkeypatch,
):
    target = tmp_path / "review.json"
    primary_error = KeyboardInterrupt("post-success stage replace")
    identity_failure = OSError("private target lstat detail")
    real_replace = os.replace
    real_lstat = Path.lstat
    replace_completed = False
    identity_failed = False

    def interrupting_replace(source, destination):
        nonlocal replace_completed
        if Path(destination) == target:
            real_replace(source, destination)
            replace_completed = True
            raise primary_error
        return real_replace(source, destination)

    def failing_new_target_lstat(path, *args, **kwargs):
        nonlocal identity_failed
        if path == target and replace_completed and not identity_failed:
            identity_failed = True
            raise identity_failure
        return real_lstat(path, *args, **kwargs)

    monkeypatch.setattr(os, "replace", interrupting_replace)
    monkeypatch.setattr(Path, "lstat", failing_new_target_lstat)

    with pytest.raises(artifacts.ArtifactTransactionRecoveryError) as exc_info:
        artifacts.write_empty_review_output(target)

    error = exc_info.value
    assert error.__cause__ is primary_error
    assert replace_completed is True
    assert identity_failed is True
    assert "private" not in str(error)
    assert not os.path.lexists(target)


def test_unknown_restore_identity_after_replace_is_recovery_error(
    tmp_path: Path,
    monkeypatch,
):
    target = tmp_path / "review.json"
    original_bytes = b"exact prior output bytes\n"
    target.write_bytes(original_bytes)
    original_stat = target.stat()
    primary_error = OSError("private stage commit detail")
    identity_failure = OSError("private restore lstat detail")
    real_replace = os.replace
    real_lstat = Path.lstat
    target_replace_calls = 0
    restore_completed = False
    identity_failed = False

    def failing_commit_then_restoring(source, destination):
        nonlocal restore_completed, target_replace_calls
        if Path(destination) == target:
            target_replace_calls += 1
            if target_replace_calls == 1:
                raise primary_error
            real_replace(source, destination)
            restore_completed = True
            return None
        return real_replace(source, destination)

    def failing_restored_target_lstat(path, *args, **kwargs):
        nonlocal identity_failed
        if path == target and restore_completed and not identity_failed:
            identity_failed = True
            raise identity_failure
        return real_lstat(path, *args, **kwargs)

    monkeypatch.setattr(os, "replace", failing_commit_then_restoring)
    monkeypatch.setattr(Path, "lstat", failing_restored_target_lstat)

    with pytest.raises(artifacts.ArtifactTransactionRecoveryError) as exc_info:
        artifacts.write_empty_review_output(target)

    error = exc_info.value
    assert error.__cause__ is primary_error
    assert error.retained_backup_paths == ()
    assert error.retained_backup_path is None
    assert target_replace_calls == 2
    assert restore_completed is True
    assert identity_failed is True
    assert "private" not in str(error)
    assert target.read_bytes() == original_bytes
    assert target.stat().st_ino == original_stat.st_ino


def test_unknown_restore_retains_exact_existing_original_backup(
    tmp_path: Path,
    monkeypatch,
):
    target = tmp_path / "review.json"
    original_bytes = b"exact prior output bytes\n"
    target.write_bytes(original_bytes)
    original_inode = target.stat().st_ino
    primary_error = OSError("private stage commit detail")
    recovery_error = OSError("private restore detail")
    identity_failure = OSError("private restore lstat detail")
    real_replace = os.replace
    real_lstat = Path.lstat
    real_unlink = Path.unlink
    target_replace_calls = 0
    restore_attempted = False
    identity_failed = False

    def failing_commit_and_restore(_source, destination):
        nonlocal restore_attempted, target_replace_calls
        if Path(destination) == target:
            target_replace_calls += 1
            if target_replace_calls == 1:
                raise primary_error
            restore_attempted = True
            raise recovery_error
        return real_replace(_source, destination)

    def failing_restore_target_lstat(path, *args, **kwargs):
        nonlocal identity_failed
        if path == target and restore_attempted and not identity_failed:
            identity_failed = True
            raise identity_failure
        return real_lstat(path, *args, **kwargs)

    monkeypatch.setattr(os, "replace", failing_commit_and_restore)
    monkeypatch.setattr(Path, "lstat", failing_restore_target_lstat)

    with pytest.raises(artifacts.ArtifactTransactionRecoveryError) as exc_info:
        artifacts.write_empty_review_output(target)

    error = exc_info.value
    assert error.__cause__ is primary_error
    assert error.recovery_error is recovery_error
    assert target_replace_calls == 2
    assert restore_attempted is True
    assert identity_failed is True
    assert not os.path.lexists(target)
    assert len(error.retained_backup_paths) == 1
    retained_backup = error.retained_backup_paths[0]
    assert error.retained_backup_path == retained_backup
    assert retained_backup.exists()
    assert retained_backup.read_bytes() == original_bytes
    assert retained_backup.stat().st_ino == original_inode
    real_unlink(retained_backup)


def test_unknown_target_absence_after_chmod_failure_is_recovery_error(
    tmp_path: Path,
    monkeypatch,
):
    target = tmp_path / "review.json"
    primary_error = OSError("private target chmod detail")
    identity_failure = OSError("private removed target lstat detail")
    real_chmod = Path.chmod
    real_lstat = Path.lstat
    real_unlink = Path.unlink
    target_unlinked = False
    identity_failed = False

    def failing_target_chmod(path, mode, *args, **kwargs):
        if path == target:
            raise primary_error
        return real_chmod(path, mode, *args, **kwargs)

    def tracking_target_unlink(path, *args, **kwargs):
        nonlocal target_unlinked
        result = real_unlink(path, *args, **kwargs)
        if path == target:
            target_unlinked = True
        return result

    def failing_removed_target_lstat(path, *args, **kwargs):
        nonlocal identity_failed
        if path == target and target_unlinked and not identity_failed:
            identity_failed = True
            raise identity_failure
        return real_lstat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "chmod", failing_target_chmod)
    monkeypatch.setattr(Path, "unlink", tracking_target_unlink)
    monkeypatch.setattr(Path, "lstat", failing_removed_target_lstat)

    with pytest.raises(artifacts.ArtifactTransactionRecoveryError) as exc_info:
        artifacts.write_empty_review_output(target)

    error = exc_info.value
    assert error.__cause__ is primary_error
    assert target_unlinked is True
    assert identity_failed is True
    assert "private" not in str(error)
    assert not os.path.lexists(target)


def test_post_success_restore_interrupt_does_not_mask_primary_commit_error(
    tmp_path: Path,
    monkeypatch,
):
    target = tmp_path / "review.json"
    original_bytes = b"exact prior output bytes\n"
    target.write_bytes(original_bytes)
    original_stat = target.stat()
    primary_error = OSError("stage commit failed")
    recovery_interrupt = KeyboardInterrupt("post-success restore")
    real_replace = os.replace
    target_replace_calls = 0

    def failing_commit_and_interrupting_restore(source, destination):
        nonlocal target_replace_calls
        if Path(destination) == target:
            target_replace_calls += 1
            if target_replace_calls == 1:
                raise primary_error
            real_replace(source, destination)
            raise recovery_interrupt
        return real_replace(source, destination)

    monkeypatch.setattr(os, "replace", failing_commit_and_interrupting_restore)

    with pytest.raises(OSError, match="stage commit failed") as exc_info:
        artifacts.write_empty_review_output(target)

    assert exc_info.value is primary_error
    assert target_replace_calls == 2
    assert target.read_bytes() == original_bytes
    assert target.stat().st_ino == original_stat.st_ino


def test_backup_path_reservation_failure_preserves_primary_and_original(
    tmp_path: Path,
    monkeypatch,
):
    target = tmp_path / "review.json"
    original_bytes = b"exact prior output bytes\n"
    target.write_bytes(original_bytes)
    original_stat = target.stat()
    primary_error = OSError("private reservation unlink detail")
    real_unlink = Path.unlink
    reservation_unlink_attempts = 0
    reserved_path: Path | None = None

    def failing_reservation_unlink(path, *args, **kwargs):
        nonlocal reservation_unlink_attempts, reserved_path
        if path.suffix == ".bak":
            reservation_unlink_attempts += 1
            reserved_path = path
            raise primary_error
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", failing_reservation_unlink)

    with pytest.raises(OSError, match="private reservation unlink detail") as exc_info:
        artifacts.write_empty_review_output(target)

    assert exc_info.value is primary_error
    assert reservation_unlink_attempts == 2
    assert reserved_path is not None
    assert reserved_path.exists()
    assert reserved_path.name.startswith(".review.json.")
    assert reserved_path.name.endswith(".bak")
    assert stat.S_IMODE(reserved_path.stat().st_mode) == 0o600
    assert target.read_bytes() == original_bytes
    assert target.stat().st_ino == original_stat.st_ino
    real_unlink(reserved_path)


def test_empty_review_replace_failure_preserves_existing_and_cleans_temp(
    tmp_path: Path,
    monkeypatch,
):
    review_path = tmp_path / "review.json"
    original_bytes = b"old review\n"
    review_path.write_bytes(original_bytes)
    before_paths = {path.relative_to(tmp_path) for path in tmp_path.rglob("*")}

    def failing_replace(_source, _target):
        raise OSError("empty review replace failed")

    monkeypatch.setattr(os, "replace", failing_replace)

    with pytest.raises(OSError, match="empty review replace failed"):
        artifacts.write_empty_review_output(review_path)

    assert review_path.read_bytes() == original_bytes
    assert {path.relative_to(tmp_path) for path in tmp_path.rglob("*")} == before_paths


def test_write_artifacts_merges_into_existing_releases_json(tmp_path: Path):
    write_artifacts([release("First", "2020")], output_dir=tmp_path)
    write_artifacts([release("Second", "2021")], output_dir=tmp_path)

    data = json.loads((tmp_path / "data/releases.json").read_text(encoding="utf-8"))
    titles = sorted(item["movie_title"] for item in data)
    assert titles == ["First", "Second"]


def test_write_artifacts_normalizes_existing_release_titles(tmp_path: Path):
    write_artifacts([release("281 Nobody", "2021")], output_dir=tmp_path)
    write_artifacts([release("Nobody", "2021")], output_dir=tmp_path)

    data = json.loads((tmp_path / "data/releases.json").read_text(encoding="utf-8"))
    assert [item["movie_title"] for item in data] == ["Nobody"]


def test_write_artifacts_dedupes_same_title_same_bluray_url_across_years(
    tmp_path: Path,
):
    existing = release("Sisu", "2022")
    existing.tmdb_id = "840326"
    existing.bluray_url = "https://www.blu-ray.com/movies/Sisu-4K-Blu-ray/333344/"
    incoming = release("Sisu", "2023")
    incoming.tmdb_id = "935906"
    incoming.bluray_url = "https://www.blu-ray.com/movies/Sisu-4K-Blu-ray/333344/"

    write_artifacts([existing], output_dir=tmp_path)
    write_artifacts([incoming], output_dir=tmp_path)

    data = json.loads((tmp_path / "data/releases.json").read_text(encoding="utf-8"))
    assert [(item["movie_title"], item["tmdb_id"]) for item in data] == [
        ("Sisu", "840326")
    ]


def test_write_artifacts_replaces_stale_rows_from_refreshed_sources(tmp_path: Path):
    stale = release("Rango.2011.", "Unknown")
    stale.fel_evidence = FelEvidence(
        source_url="https://docs.example.test/sheet",
        quote="Rango.2011. BD FEL",
        evidence_type="google-sheet-row",
    )
    preserved = release("Preserved", "2020")
    fresh = release("Rango", "2011")
    fresh.fel_evidence = FelEvidence(
        source_url="https://docs.example.test/sheet",
        quote="Rango.2011. BD FEL",
        evidence_type="google-sheet-row",
    )

    write_artifacts([stale, preserved], output_dir=tmp_path)
    write_artifacts([fresh], output_dir=tmp_path)

    data = json.loads((tmp_path / "data/releases.json").read_text(encoding="utf-8"))
    titles = sorted(item["movie_title"] for item in data)
    assert titles == ["Preserved", "Rango"]


def test_write_artifacts_filters_stale_google_sheet_shapes_only(tmp_path: Path):
    stale_collection = release("Godfather Trilogy", "Unknown")
    stale_collection.fel_evidence = FelEvidence(
        source_url="https://docs.example.test/sheet",
        quote="Godfather Trilogy BD FEL",
        evidence_type="google-sheet-row",
    )
    stale_dotted = release("Rango.2011.", "2011")
    stale_dotted.fel_evidence = FelEvidence(
        source_url="https://docs.example.test/sheet",
        quote="Rango.2011. BD FEL",
        evidence_type="google-sheet-row",
    )
    forum_collection = release("Godfather Trilogy", "1972")
    forum_collection.fel_evidence = FelEvidence(
        source_url="https://forum.example.test/post",
        quote="Godfather Trilogy confirmed by post",
        evidence_type="forum-post",
    )

    write_artifacts(
        [stale_collection, stale_dotted, forum_collection],
        output_dir=tmp_path,
    )

    data = json.loads((tmp_path / "data/releases.json").read_text(encoding="utf-8"))
    assert [(item["movie_title"], item["release_date"]) for item in data] == [
        ("Godfather Trilogy", "1972")
    ]


def test_write_artifacts_dedupes_by_tmdb_id(tmp_path: Path):
    first = release("Spelling One", "2021")
    first.tmdb_id = "777"
    second = release("Spelling Two", "2021")
    second.tmdb_id = "777"

    write_artifacts([first, second], output_dir=tmp_path)

    data = json.loads((tmp_path / "data/releases.json").read_text(encoding="utf-8"))
    assert sum(1 for item in data if item["tmdb_id"] == "777") == 1


def test_write_artifacts_preserves_same_tmdb_distinct_bluray_releases(
    tmp_path: Path,
):
    first = release("Game of Thrones: The Complete First Season", "2011")
    first.tmdb_id = "1399"
    first.bluray_url = (
        "https://www.blu-ray.com/movies/"
        "Game-of-Thrones-The-Complete-First-Season-4K-Blu-ray/202472/"
    )
    seventh = release("Game of Thrones: The Complete Seventh Season", "2017-07-16")
    seventh.tmdb_id = "1399"
    seventh.bluray_url = (
        "https://www.blu-ray.com/movies/"
        "Game-of-Thrones-The-Complete-Seventh-Season-4K-Blu-ray/272494/"
    )

    write_artifacts([first, seventh], output_dir=tmp_path)

    data = json.loads((tmp_path / "data/releases.json").read_text(encoding="utf-8"))
    assert [(item["movie_title"], item["release_date"]) for item in data] == [
        ("Game of Thrones: The Complete Seventh Season", "2017-07-16"),
        ("Game of Thrones: The Complete First Season", "2011"),
    ]
    assert {
        (item["movie_title"], item["bluray_url"])
        for item in data
        if item["tmdb_id"] == "1399"
    } == {
        (first.movie_title, first.bluray_url),
        (seventh.movie_title, seventh.bluray_url),
    }


def test_write_artifacts_merges_same_title_year_different_bluray_urls(
    tmp_path: Path,
):
    # The disc URL comes from an unstable blu-ray.com search, so an
    # identically-titled row pointing at another pressing's page is the same
    # release and must not publish as a duplicate entry.
    first = release("Avatar", "2009")
    first.bluray_url = "https://www.blu-ray.com/movies/Avatar/1/"
    second = release("Avatar", "2009")
    second.bluray_url = "https://www.blu-ray.com/movies/Avatar/2/"

    write_artifacts([first, second], output_dir=tmp_path)

    data = json.loads((tmp_path / "data/releases.json").read_text(encoding="utf-8"))
    assert [(item["movie_title"], item["bluray_url"]) for item in data] == [
        ("Avatar", first.bluray_url)
    ]


def test_write_artifacts_merges_same_tmdb_when_existing_lacks_bluray(
    tmp_path: Path,
):
    existing = release("The Three Musketeers: Milady", "2023-12-13")
    existing.tmdb_id = "845111"
    existing.imdb_id = "tt12672620"
    existing.release_url = "https://www.themoviedb.org/movie/845111"

    incoming = release("Les Trois Mousquetaires: Milady", "2023-12-13")
    incoming.tmdb_id = "845111"
    incoming.imdb_id = "tt12672620"
    incoming.release_url = "https://www.themoviedb.org/movie/845111"
    incoming.bluray_url = (
        "https://www.blu-ray.com/movies/"
        "Les-Trois-Mousquetaires--Milady-4K-Blu-ray/347971/"
    )

    write_artifacts([existing], output_dir=tmp_path)
    write_artifacts([incoming], output_dir=tmp_path)

    data = json.loads((tmp_path / "data/releases.json").read_text(encoding="utf-8"))
    assert [(item["movie_title"], item["tmdb_id"]) for item in data] == [
        ("The Three Musketeers: Milady", "845111")
    ]
    assert data[0]["bluray_url"] == incoming.bluray_url


def test_write_artifacts_collapses_translated_aliases_across_refreshes(
    tmp_path: Path,
):
    english = release("The Movie", "2000")
    english.tmdb_id = "1"
    english.imdb_id = "tt0000001"
    english.bluray_url = "https://www.blu-ray.com/movies/The-Movie/1/"
    localized = release("Le Film", "2000")
    localized.tmdb_id = "1"
    localized.imdb_id = "tt0000001"
    localized.bluray_url = "https://www.blu-ray.com/movies/Le-Film/2/"
    localized.fel_evidence = FelEvidence(
        source_url=localized.source_url,
        quote="The Movie AKA Le Film [2000]",
        evidence_type="fixture",
    )

    write_artifacts([english], output_dir=tmp_path)
    published = write_artifacts([localized], output_dir=tmp_path)

    assert len(published) == 1
    assert published[0].movie_title == "The Movie"
    data = json.loads((tmp_path / "data/releases.json").read_text(encoding="utf-8"))
    assert [(item["movie_title"], item["tmdb_id"]) for item in data] == [
        ("The Movie", "1")
    ]


def test_write_artifacts_preserves_enriched_fields(tmp_path: Path):
    item = release("Enriched", "2024")
    item.tmdb_id = "111"
    item.poster_path = "data/posters/111.jpg"
    item.release_url = "https://www.themoviedb.org/movie/111"
    item.hdr_formats = ["Dolby Vision", "HDR10"]
    item.bluray_url = "https://www.blu-ray.com/movies/Enriched-4K-Blu-ray/9/"

    write_artifacts([item], output_dir=tmp_path)

    data = json.loads((tmp_path / "data/releases.json").read_text(encoding="utf-8"))
    assert len(data) == 1
    entry = data[0]
    assert entry["tmdb_id"] == "111"
    assert entry["poster_path"] == "data/posters/111.jpg"
    assert entry["hdr_formats"] == ["Dolby Vision", "HDR10"]
    assert entry["bluray_url"].endswith("/9/")


def test_write_artifacts_preserves_unreferenced_existing_poster_files(tmp_path: Path):
    poster_dir = tmp_path / "data/posters"
    poster_dir.mkdir(parents=True)
    referenced = poster_dir / "111.jpg"
    existing_unreferenced = poster_dir / "222.jpg"
    referenced.write_bytes(b"referenced")
    existing_unreferenced.write_bytes(b"existing")

    item = release("Enriched", "2024")
    item.tmdb_id = "111"
    item.poster_path = "data/posters/111.jpg"

    write_artifacts([item], output_dir=tmp_path)

    assert referenced.exists()
    assert existing_unreferenced.exists()


def test_prune_unreferenced_posters_removes_only_candidate_files(tmp_path: Path):
    poster_dir = tmp_path / "data/posters"
    poster_dir.mkdir(parents=True)
    referenced = poster_dir / "111.jpg"
    stale_candidate = poster_dir / "222.jpg"
    protected_unreferenced = poster_dir / "333.jpg"
    referenced.write_bytes(b"referenced")
    stale_candidate.write_bytes(b"stale")
    protected_unreferenced.write_bytes(b"protected")

    item = release("Enriched", "2024")
    item.poster_path = "data/posters/111.jpg"

    removed = artifacts.prune_unreferenced_posters(
        poster_dir,
        [item],
        candidate_names=["111.jpg", "222.jpg"],
    )

    assert removed == [stale_candidate]
    assert referenced.exists()
    assert not stale_candidate.exists()
    assert protected_unreferenced.exists()


def test_prune_unreferenced_posters_noops_when_poster_dir_is_missing(tmp_path: Path):
    removed = artifacts.prune_unreferenced_posters(
        tmp_path / "missing",
        [release("Enriched", "2024")],
        candidate_names=["222.jpg"],
    )

    assert removed == []
