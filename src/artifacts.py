from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum, auto
import json
import os
from pathlib import Path
import re
import stat
import tempfile
from typing import Iterable

from merge import dedupe_releases, title_bluray_key
from models import UNKNOWN, FelRelease, release_from_dict
from normalize import normalize_fel_title
from reconcile import ReconciliationResult, reconcile_releases


STALE_SHEET_COLLECTION_RE = re.compile(
    r"\b(?:collection|trilogy|duology|quadrilogy|tetralogy|saga|box\s*set|boxset)$",
    re.IGNORECASE,
)
STALE_DOTTED_YEAR_TITLE_RE = re.compile(r"[._-](?:19|20)\d{2}[.\s_-]*$")
REVIEW_OUTPUT_COLLISION_MESSAGE = "review output must not refer to data/releases.json"
REVIEW_OUTPUT_INVALID_MESSAGE = "review output must be a regular file path"
TRANSACTION_RECOVERY_MESSAGE = "artifact transaction recovery did not complete"
IDENTITY_RECONCILIATION_MESSAGE = "artifact filesystem state could not be verified"
PUBLIC_OUTPUT_MODE = 0o644
PRIVATE_STAGE_MODE = 0o600


class ReviewOutputError(ValueError):
    """Raised when a reconciliation review target is unsafe."""


class ArtifactTransactionRecoveryError(RuntimeError):
    """Raised when an in-process artifact rollback cannot be completed."""

    def __init__(
        self,
        recovery_errors: tuple[BaseException, ...],
        retained_backup_paths: tuple[Path, ...],
    ) -> None:
        super().__init__(TRANSACTION_RECOVERY_MESSAGE)
        self.recovery_errors = recovery_errors
        self.recovery_error = recovery_errors[0] if recovery_errors else None
        self.retained_backup_paths = retained_backup_paths
        self.retained_backup_path = (
            retained_backup_paths[0] if retained_backup_paths else None
        )


class _IdentityOutcome(Enum):
    MATCH = auto()
    MISMATCH = auto()
    UNKNOWN = auto()


class _IdentityReconciliationError(RuntimeError):
    """Records an indeterminate filesystem transition without leaking its path."""


@dataclass
class _AtomicOutputState:
    target: Path
    staged_path: Path
    output_mode: int
    committed: bool = False
    backup_path: Path | None = None
    backup_holds_original: bool = False
    original_identity: tuple[int, int] | None = None
    staged_identity: tuple[int, int] | None = None
    identity_errors: list[BaseException] = field(default_factory=list)


def _safe_resolve(path: Path) -> Path:
    try:
        return path.resolve(strict=False)
    except (OSError, RuntimeError):
        raise ReviewOutputError(REVIEW_OUTPUT_INVALID_MESSAGE) from None


def _existing_target_stat(path: Path) -> os.stat_result | None:
    if not os.path.lexists(path):
        return None
    try:
        return path.stat()
    except (OSError, RuntimeError):
        raise ReviewOutputError(REVIEW_OUTPUT_INVALID_MESSAGE) from None


def _validate_output_parent(path: Path) -> None:
    candidate = path.parent
    while not os.path.lexists(candidate):
        parent = candidate.parent
        if parent == candidate:  # pragma: no cover - filesystem root always exists
            break
        candidate = parent
    try:
        parent_stat = candidate.stat()
    except (OSError, RuntimeError):
        raise ReviewOutputError(REVIEW_OUTPUT_INVALID_MESSAGE) from None
    if not stat.S_ISDIR(parent_stat.st_mode):
        raise ReviewOutputError(REVIEW_OUTPUT_INVALID_MESSAGE)


def _validate_regular_review_target(path: Path) -> os.stat_result | None:
    target_stat = _existing_target_stat(path)
    if target_stat is not None and not stat.S_ISREG(target_stat.st_mode):
        raise ReviewOutputError(REVIEW_OUTPUT_INVALID_MESSAGE)
    _validate_output_parent(path)
    return target_stat


def validate_review_output_path(
    output_dir: Path | str,
    review_path: Path | str | None,
) -> None:
    """Reject review output aliases of the canonical releases database."""
    if review_path is None:
        return

    releases_path = Path(output_dir) / "data" / "releases.json"
    review_output_path = Path(review_path)
    releases_resolved = _safe_resolve(releases_path)
    review_resolved = _safe_resolve(review_output_path)
    releases_stat = _existing_target_stat(releases_path)
    review_stat = _validate_regular_review_target(review_output_path)

    refers_to_releases = releases_resolved == review_resolved
    if not refers_to_releases and releases_stat is not None and review_stat is not None:
        try:
            refers_to_releases = releases_path.samefile(review_output_path)
        except OSError:
            raise ReviewOutputError(REVIEW_OUTPUT_INVALID_MESSAGE) from None
    if (
        not refers_to_releases
        and (releases_stat is None or review_stat is None)
        and releases_resolved.parent == review_resolved.parent
        and releases_resolved.name.casefold() == review_resolved.name.casefold()
    ):
        refers_to_releases = True
    if refers_to_releases:
        raise ReviewOutputError(REVIEW_OUTPUT_COLLISION_MESSAGE)


def publish_outputs(
    releases: list[FelRelease],
    output_dir: Path | str = ".",
    review_output_path: Path | str | None = None,
) -> list[FelRelease]:
    from dashboard import build_dashboard

    root = Path(output_dir)
    sorted_releases = write_artifacts(
        releases, output_dir=root, review_output_path=review_output_path
    )
    build_dashboard(
        sorted_releases,
        output_dir=root / "dist",
        poster_src=root / "data" / "posters",
    )
    return sorted_releases


def write_empty_review_output(path: Path | str) -> None:
    """Write the canonical empty reconciliation review document."""
    target = Path(path)
    _validate_regular_review_target(target)
    _atomic_write_text(
        target,
        json.dumps(
            {"merged_count": 0, "addition_count": 0, "review_count": 0, "items": []},
            indent=2,
        )
        + "\n",
    )


def write_artifacts(
    releases: list[FelRelease],
    output_dir: Path | str = ".",
    review_output_path: Path | str | None = None,
) -> list[FelRelease]:
    """Write outputs with rollback for exceptions raised in this process.

    This is not hard-exit or crash atomicity. A successful replacement replaces
    the target directory entry, including a final-component symlink or hard link.
    New parent directories honor the process umask; restrictive 0077 is supported
    when their existing ancestor is writable.
    """
    root = Path(output_dir)
    data_dir = root / "data"
    releases_path = data_dir / "releases.json"
    validate_review_output_path(root, review_output_path)

    existing: list[FelRelease] = []
    if releases_path.exists():
        existing = [
            release_from_dict(item)
            for item in json.loads(releases_path.read_text(encoding="utf-8"))
        ]

    existing = [
        release for release in existing if not _is_stale_google_sheet_release(release)
    ]
    existing = _normalize_release_titles(existing)
    releases = _normalize_release_titles(releases)
    releases = [
        release for release in releases if not _is_stale_google_sheet_release(release)
    ]

    reconciliation = reconcile_releases(existing, releases)
    # Reconciliation is edition-aware; only collapse exact title/URL duplicates
    # defensively. A title/year-only dedupe would erase distinct physical cuts.
    merged = dedupe_releases(reconciliation.releases, title_bluray_key)
    _enforce_ai_source_label(merged)
    sorted_releases = sorted(merged, key=_sort_key)

    releases_payload = (
        json.dumps(
            [release.to_dict() for release in sorted_releases],
            indent=2,
            ensure_ascii=False,
        )
        + "\n"
    )
    review_path = Path(review_output_path) if review_output_path is not None else None
    review_payload = (
        _review_output_text(reconciliation) if review_path is not None else None
    )

    staged_paths: list[Path] = []
    states: list[_AtomicOutputState] = []
    try:
        review_stage = None
        if review_path is not None and review_payload is not None:
            review_stage = _stage_text(review_path, review_payload)
            staged_paths.append(review_stage)
        releases_stage = _stage_text(releases_path, releases_payload)
        staged_paths.append(releases_stage)

        validate_review_output_path(root, review_path)
        releases_mode = _output_mode(releases_path)
        review_mode = _output_mode(review_path) if review_path is not None else None

        review_state = None
        if (
            review_stage is not None
            and review_path is not None
            and review_mode is not None
        ):
            review_state = _AtomicOutputState(
                target=review_path,
                staged_path=review_stage,
                output_mode=review_mode,
            )
            states.append(review_state)
        releases_state = _AtomicOutputState(
            target=releases_path,
            staged_path=releases_stage,
            output_mode=releases_mode,
        )
        states.append(releases_state)

        try:
            if review_state is not None:
                _commit_output(review_state)
            _commit_output(releases_state)
        except BaseException as primary_error:
            recovery_errors = _recover_outputs(states)
            transaction_errors = recovery_errors + _identity_errors(states)
            if transaction_errors:
                raise ArtifactTransactionRecoveryError(
                    transaction_errors,
                    _retained_backup_paths(states),
                ) from primary_error
            raise

        for state in states:
            _discard_backup_best_effort(state)
    finally:
        for staged_path in staged_paths:
            _unlink_best_effort(staged_path)
        for state in states:
            _cleanup_unowned_backup(state)

    print(
        "reconciliation complete; "
        f"merged={reconciliation.merged_count} "
        f"additions={len(reconciliation.additions)} "
        f"review={len(reconciliation.review_items)}"
    )
    return sorted_releases


def _review_output_text(result: ReconciliationResult) -> str:
    payload = {
        "merged_count": result.merged_count,
        "addition_count": len(result.additions),
        "review_count": len(result.review_items),
        "items": [
            {
                "reason": item.reason,
                "candidate": item.release.to_dict(),
                "candidate_titles": list(item.candidate_titles),
            }
            for item in result.review_items
        ],
    }
    return json.dumps(payload, indent=2, ensure_ascii=False) + "\n"


def _stage_text(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    file_descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(file_descriptor, "w", encoding="utf-8", newline="") as handle:
            os.fchmod(handle.fileno(), PRIVATE_STAGE_MODE)
            handle.write(text)
    except BaseException:  # pragma: no cover - filesystem write failures vary
        _unlink_best_effort(temporary_path)
        raise
    return temporary_path


def _output_mode(path: Path) -> int:
    target_stat = _existing_target_stat(path)
    if target_stat is None or not stat.S_ISREG(target_stat.st_mode):
        return PUBLIC_OUTPUT_MODE
    return stat.S_IMODE(target_stat.st_mode)


def _entry_identity(path: Path) -> tuple[int, int] | None:
    try:
        entry_stat = path.lstat()
    except FileNotFoundError:
        return None
    return entry_stat.st_dev, entry_stat.st_ino


def _identity_outcome(
    path: Path,
    identity: tuple[int, int] | None,
) -> _IdentityOutcome:
    try:
        current_identity = _entry_identity(path)
    except OSError:
        return _IdentityOutcome.UNKNOWN
    if current_identity == identity:
        return _IdentityOutcome.MATCH
    return _IdentityOutcome.MISMATCH


def _record_identity_unknown(
    state: _AtomicOutputState,
) -> _IdentityReconciliationError:
    error = _IdentityReconciliationError(IDENTITY_RECONCILIATION_MESSAGE)
    state.identity_errors.append(error)
    return error


def _identity_errors(
    states: list[_AtomicOutputState],
) -> tuple[BaseException, ...]:
    return tuple(error for state in states for error in state.identity_errors)


def _reserve_backup_path(path: Path) -> Path:
    file_descriptor, reserved_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".bak",
        dir=path.parent,
    )
    reserved_path = Path(reserved_name)
    try:
        os.close(file_descriptor)
        reserved_path.unlink()
    except BaseException:
        _unlink_best_effort(reserved_path)
        raise
    return reserved_path


def _commit_output(state: _AtomicOutputState) -> None:
    state.original_identity = _entry_identity(state.target)
    state.staged_identity = _entry_identity(state.staged_path)
    if state.original_identity is not None:
        state.backup_path = _reserve_backup_path(state.target)
        backup_identity_error = None
        try:
            os.replace(state.target, state.backup_path)
        finally:
            backup_outcome = _identity_outcome(
                state.backup_path,
                state.original_identity,
            )
            if backup_outcome is _IdentityOutcome.MATCH:
                state.backup_holds_original = True
            elif backup_outcome is _IdentityOutcome.UNKNOWN:
                state.backup_holds_original = True
                backup_identity_error = _record_identity_unknown(state)
        if backup_identity_error is not None:
            raise backup_identity_error

    # _stage_text forces mode 0600. Keep it through the rename, then make the
    # target public only after it owns the new contents.
    stage_identity_error = None
    try:
        os.replace(state.staged_path, state.target)
    finally:
        stage_outcome = _identity_outcome(state.target, state.staged_identity)
        if stage_outcome is _IdentityOutcome.MATCH:
            state.committed = True
        elif stage_outcome is _IdentityOutcome.UNKNOWN:
            state.committed = True
            stage_identity_error = _record_identity_unknown(state)
    if stage_identity_error is not None:
        raise stage_identity_error
    state.target.chmod(state.output_mode)


def _recover_outputs(
    states: list[_AtomicOutputState],
) -> tuple[BaseException, ...]:
    recovery_errors: list[BaseException] = []
    for state in reversed(states):
        recovery_error = _recover_output(state)
        if recovery_error is not None:
            recovery_errors.append(recovery_error)
    return tuple(recovery_errors)


def _recover_output(state: _AtomicOutputState) -> BaseException | None:
    try:
        if state.backup_holds_original and state.backup_path is not None:
            try:
                os.replace(state.backup_path, state.target)
            finally:
                restore_outcome = _identity_outcome(
                    state.target,
                    state.original_identity,
                )
                if restore_outcome is _IdentityOutcome.MATCH:
                    state.backup_holds_original = False
                    state.backup_path = None
                    state.committed = False
                elif restore_outcome is _IdentityOutcome.UNKNOWN:
                    _record_identity_unknown(state)
                    backup_outcome = _identity_outcome(
                        state.backup_path,
                        state.original_identity,
                    )
                    if backup_outcome is _IdentityOutcome.MISMATCH:
                        state.backup_holds_original = False
                        state.backup_path = None
        elif state.committed:
            try:
                state.target.unlink(missing_ok=True)
            finally:
                removal_outcome = _identity_outcome(state.target, None)
                if removal_outcome is _IdentityOutcome.MATCH:
                    state.committed = False
                elif removal_outcome is _IdentityOutcome.UNKNOWN:
                    _record_identity_unknown(state)
    except BaseException as error:
        if not state.backup_holds_original and not state.committed:
            return None
        return error
    return None


def _retained_backup_paths(states: list[_AtomicOutputState]) -> tuple[Path, ...]:
    return tuple(
        state.backup_path
        for state in states
        if state.backup_holds_original
        and state.backup_path is not None
        and _identity_outcome(state.backup_path, state.original_identity)
        is not _IdentityOutcome.MISMATCH
    )


def _discard_backup_best_effort(state: _AtomicOutputState) -> None:
    if not state.backup_holds_original or state.backup_path is None:
        return
    try:
        state.backup_path.unlink(missing_ok=True)
    except BaseException:
        return
    state.backup_holds_original = False
    state.backup_path = None


def _cleanup_unowned_backup(state: _AtomicOutputState) -> None:
    if not state.backup_holds_original and state.backup_path is not None:
        _unlink_best_effort(state.backup_path)
        state.backup_path = None


def _unlink_best_effort(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except BaseException:
        pass


def _atomic_write_text(path: Path, text: str) -> None:
    output_mode = _output_mode(path)
    temporary_path = _stage_text(path, text)
    state = _AtomicOutputState(
        target=path,
        staged_path=temporary_path,
        output_mode=output_mode,
    )
    try:
        try:
            _commit_output(state)
        except BaseException as primary_error:
            recovery_error = _recover_output(state)
            transaction_errors = (
                () if recovery_error is None else (recovery_error,)
            ) + _identity_errors([state])
            if transaction_errors:
                raise ArtifactTransactionRecoveryError(
                    transaction_errors,
                    _retained_backup_paths([state]),
                ) from primary_error
            raise

        _discard_backup_best_effort(state)
    finally:
        _unlink_best_effort(temporary_path)
        _cleanup_unowned_backup(state)


def prune_unreferenced_posters(
    poster_dir: Path,
    releases: list[FelRelease],
    candidate_names: Iterable[str],
) -> list[Path]:
    if not poster_dir.exists():
        return []
    referenced = {
        Path(release.poster_path).name for release in releases if release.poster_path
    }
    removed: list[Path] = []
    for candidate_name in dict.fromkeys(Path(name).name for name in candidate_names):
        poster_path = poster_dir / candidate_name
        if poster_path.is_file() and poster_path.name not in referenced:
            poster_path.unlink()
            removed.append(poster_path)
    return removed


def _enforce_ai_source_label(releases: list[FelRelease]) -> None:
    # AGENTS.md contract: AI-discovered evidence must publish with source_label
    # "codex-ai". A prior merge that promoted ai-extracted evidence over weak
    # list evidence could leave a stale label (e.g. "FEL.txt"), so reassert the
    # invariant on every publish over the fully merged set.
    for release in releases:
        if release.fel_evidence.evidence_type == "ai-extracted":
            release.source_label = "codex-ai"


def _normalize_release_titles(releases: list[FelRelease]) -> list[FelRelease]:
    normalized: list[FelRelease] = []
    for release in releases:
        title = normalize_fel_title(release.movie_title) or release.movie_title
        normalized.append(replace(release, movie_title=title))
    return normalized


def _is_stale_google_sheet_release(release: FelRelease) -> bool:
    if release.fel_evidence.evidence_type != "google-sheet-row":
        return False
    if STALE_SHEET_COLLECTION_RE.search(release.movie_title):
        return True
    return bool(STALE_DOTTED_YEAR_TITLE_RE.search(release.movie_title))


_BARE_YEAR_RE = re.compile(r"^(?:19|20)\d{2}$")
_BARE_YEAR_MONTH_RE = re.compile(r"^(?:19|20)\d{2}-\d{2}$")


def _normalize_sort_date(value: str) -> str:
    # Pad bare year / year-month so they sort AFTER full dates of the same year
    # under newest-first ordering ('0' inverts higher than any real digit, so a
    # padded "2023-00-00" reads as older than every "2023-MM-DD").
    if _BARE_YEAR_RE.match(value):
        return f"{value}-00-00"
    if _BARE_YEAR_MONTH_RE.match(value):
        return f"{value}-00"
    return value


def _sort_key(release: FelRelease) -> tuple[int, str]:
    if release.release_date == UNKNOWN:
        return (1, "")
    return (0, _invert_date_text(_normalize_sort_date(release.release_date)))


def _invert_date_text(value: str) -> str:
    return "".join(chr(255 - ord(character)) for character in value)
