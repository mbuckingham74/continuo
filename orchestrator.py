"""Deterministic jobs-repository orchestration controller."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import shutil
import sqlite3
import stat as stat_module
import subprocess
import tempfile
import uuid
from collections import Counter
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterator, NamedTuple

import typer
from rich.console import Console
from rich.table import Table

from models import (
    ADVERSARIAL_REVIEW_ROUTE,
    ApprovalDecision,
    ApprovalGate,
    ApprovalRequest,
    CorrectionPolicyAction,
    CURRENT_RUN_SCHEMA_VERSION,
    ESCALATION_EXECUTIVE_ROUTE,
    GitRecord,
    IMPLEMENTATION_ROUTE,
    OPERATION_ROLES,
    POLICY_AUTHORITY_ROUTE,
    PolicyDecision,
    ProviderCapability,
    ProviderOperation,
    ProviderRecord,
    ProviderRouteIdentity,
    ROUTE_IDENTITIES,
    RepoState,
    ReviewRecord,
    ReviewResult,
    ResolvedCorrectionPolicy,
    TargetOwnership,
    UnreadableReviewRecord,
    WorkflowRun,
    WriterAttemptPurpose,
    WriterAttemptStage,
    WriterAttemptState,
    WriterRecoveryAction,
    WriterRecoveryDecision,
    resolve_correction_policy,
)
from providers import (
    DEFAULT_REPO,
    ProviderExecution,
    execute_luna_implementation,
    execute_sonnet_review,
    execute_sol_escalation,
    execute_terra_resolution,
    classify_provider_failure,
    execution_failed,
    normalize_provider_execution,
    normalize_sonnet_execution,
    parse_sonnet_review,
)
from run_migrations import (
    MigrationError,
    MigrationResult,
    RecordClassification,
    classify_run_bytes,
    migrate_classification,
    migration_steps,
    _unreadable_review_reason,
)

app = typer.Typer(
    no_args_is_help=True,
    help="Continuo — deterministic notation for probabilistic work.",
)
console = Console()
RUNS = Path(__file__).parent / "runs"

_PRIVATE_DIRECTORY_MODE = 0o700
_PRIVATE_FILE_MODE = 0o600
_RUN_TEMP_PREFIX = ".continuo-run-"
_RUN_TEMP_SUFFIX = ".tmp"
_SQLITE_SUFFIXES = (
    ".sqlite3",
    ".sqlite3-journal",
    ".sqlite3-wal",
    ".sqlite3-shm",
)


class ControllerError(RuntimeError):
    pass


def configured_repo(explicit: Path | None = None) -> Path:
    return (explicit or Path(os.environ.get("JOBS_REPO", str(DEFAULT_REPO)))).expanduser().resolve()


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if check and result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise ControllerError(f"git {' '.join(args)} failed: {detail}")
    return result


def _git_bytes(
    repo: Path,
    *args: str,
    check: bool = True,
) -> subprocess.CompletedProcess[bytes]:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        check=False,
    )
    if check and result.returncode != 0:
        detail = (
            result.stderr.decode(errors="replace").strip()
            or result.stdout.decode(errors="replace").strip()
        )
        raise ControllerError(f"git {' '.join(args)} failed: {detail}")
    return result


def git_text(repo: Path, *args: str) -> str:
    return _git(repo, *args).stdout.strip()


def repo_state(repo: Path) -> RepoState:
    if not repo.exists() or not repo.is_dir():
        raise ControllerError(f"jobs repo does not exist: {repo}")
    root = Path(git_text(repo, "rev-parse", "--show-toplevel")).resolve()
    if root != repo.resolve():
        raise ControllerError(f"configured jobs repo is not the Git root: {repo}")
    branch = git_text(repo, "branch", "--show-current")
    if not branch:
        raise ControllerError("jobs repo must be on a named branch; detached HEAD is unsafe")
    origin = git_text(repo, "remote", "get-url", "origin")
    return RepoState(
        repo=str(repo),
        branch=branch,
        head=git_text(repo, "rev-parse", "HEAD"),
        clean=not bool(git_text(repo, "status", "--porcelain=v1", "--untracked-files=all")),
        origin=origin,
    )


def _read_only_git_text(repo: Path, *args: str) -> str:
    """Run a Git query with optional index locks disabled for diagnostic paths."""

    environment = os.environ.copy()
    environment["GIT_OPTIONAL_LOCKS"] = "0"
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=False,
        env=environment,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise ControllerError(f"git {' '.join(args)} failed: {detail}")
    return result.stdout.strip()


def read_only_repo_state(repo: Path) -> RepoState:
    """Inspect repository state without allowing Git's optional index refresh."""

    if not repo.exists() or not repo.is_dir():
        raise ControllerError(f"jobs repo does not exist: {repo}")
    root = Path(_read_only_git_text(repo, "rev-parse", "--show-toplevel")).resolve()
    if root != repo.resolve():
        raise ControllerError(f"configured jobs repo is not the Git root: {repo}")
    branch = _read_only_git_text(repo, "branch", "--show-current")
    if not branch:
        raise ControllerError("jobs repo must be on a named branch; detached HEAD is unsafe")
    origin = _read_only_git_text(repo, "remote", "get-url", "origin")
    return RepoState(
        repo=str(repo),
        branch=branch,
        head=_read_only_git_text(repo, "rev-parse", "HEAD"),
        clean=not bool(
            _read_only_git_text(
                repo,
                "status",
                "--porcelain=v1",
                "--untracked-files=all",
            )
        ),
        origin=origin,
    )


class TargetIdentity(NamedTuple):
    target_key: str
    canonical_repo: str
    device: int
    inode: int


def target_identity(repo: Path) -> TargetIdentity:
    canonical = repo.resolve(strict=True)
    stat = canonical.stat()
    encoded = f"continuo-target-v1\0{stat.st_dev}\0{stat.st_ino}".encode()
    return TargetIdentity(
        target_key=hashlib.sha256(encoded).hexdigest(),
        canonical_repo=str(canonical),
        device=stat.st_dev,
        inode=stat.st_ino,
    )


def resolve_task(repo: Path, task_ref: str) -> tuple[str, Path, str]:
    """Resolve exactly one tasks/<ref>-*.md and return its relative name/content."""

    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", task_ref or ""):
        raise ControllerError("task-ref must be a single task identifier, such as 009")
    task_dir = repo / "tasks"
    matches = sorted(task_dir.glob(f"{task_ref}-*.md"))
    if len(matches) == 0:
        raise ControllerError(f"no task specification matches tasks/{task_ref}-*.md")
    if len(matches) > 1:
        names = ", ".join(str(p.relative_to(repo)) for p in matches)
        raise ControllerError(f"task-ref is ambiguous; matches: {names}")
    path = matches[0]
    content = path.read_text(encoding="utf-8")
    return str(path.relative_to(repo)), path, content


CLI_CONTRACT_VERSION = "continuo.cli.v1"
RUN_PLAN_VERSION = "continuo.run-plan.v1"
DOCTOR_VERSION = "continuo.doctor.v1"

_PLANNED_STAGES = (
    ("created", "controller"),
    ("spec_reviewing", "provider_read_only"),
    ("spec_review_passed", "controller"),
    ("implementing", "provider_workspace_write"),
    ("implementation_completed", "controller"),
    ("verifying", "controller"),
    ("reviewing", "provider_read_only"),
    ("correction_pending", "controller"),
    ("awaiting_commit_approval", "human_approval"),
    ("awaiting_push_approval", "human_approval"),
)


def _read_only_storage_status(runs_dir: Path) -> dict[str, str]:
    storage = _absolute_path(runs_dir)
    state = _lstat(storage)
    if state is None:
        return {"status": "warn", "code": "storage_missing", "summary": "Run storage does not exist and was not created."}
    if (
        stat_module.S_ISDIR(state.st_mode)
        and state.st_uid == os.geteuid()
        and stat_module.S_IMODE(state.st_mode) == _PRIVATE_DIRECTORY_MODE
    ):
        return {"status": "pass", "code": "storage_private", "summary": "Run storage is private."}
    return {"status": "fail", "code": "storage_unsafe", "summary": "Run storage is not a private owner-controlled directory."}


def build_dry_run_plan(
    repo: Path,
    task_ref: str,
    runs_dir: Path = RUNS,
) -> dict[str, object]:
    """Read deterministic pre-provider inputs without creating controller state."""

    state = read_only_repo_state(repo)
    if not state.clean:
        raise ControllerError("jobs repo must be clean before starting")
    storage = _read_only_storage_status(runs_dir)
    if storage["status"] == "fail":
        raise ControllerError("private storage is unsafe for a dry-run plan")
    relative, _, specification = resolve_task(repo, task_ref)
    return {
        "plan_version": RUN_PLAN_VERSION,
        "task": {
            "ref": task_ref,
            "path": relative,
            "sha256": hashlib.sha256(specification.encode()).hexdigest(),
        },
        "repository": {
            "path": state.repo,
            "branch": state.branch,
            "head": state.head,
            "origin": state.origin,
            "clean": state.clean,
        },
        "planned_stages": [
            {"id": stage, "authority": authority}
            for stage, authority in _PLANNED_STAGES
        ],
        "run_storage": storage,
    }


def _doctor_check(
    check_id: str,
    status: str,
    code: str,
    summary: str,
) -> dict[str, str]:
    return {"id": check_id, "status": status, "code": code, "summary": summary}


def doctor_report(repo: Path, runs_dir: Path = RUNS) -> dict[str, object]:
    """Return local readiness evidence without repairing or invoking providers."""

    checks: list[dict[str, str]] = []
    git_binary = shutil.which("git")
    checks.append(
        _doctor_check(
            "git_binary",
            "pass" if git_binary else "fail",
            "git_available" if git_binary else "git_missing",
            "Git executable is available." if git_binary else "Git executable is not on PATH.",
        )
    )
    state: RepoState | None = None
    if git_binary:
        try:
            state = read_only_repo_state(repo)
        except (ControllerError, OSError, subprocess.SubprocessError) as exc:
            checks.append(
                _doctor_check(
                    "target_repository",
                    "fail",
                    "target_invalid",
                    f"Configured target cannot be inspected: {type(exc).__name__}.",
                )
            )
            checks.append(
                _doctor_check("target_state", "unknown", "target_unavailable", "Target state is unavailable.")
            )
        else:
            checks.append(
                _doctor_check("target_repository", "pass", "target_valid", "Configured target is a Git root.")
            )
            checks.append(
                _doctor_check(
                    "target_state",
                    "pass" if state.clean else "fail",
                    "target_clean" if state.clean else "target_dirty",
                    "Target worktree is clean." if state.clean else "Target worktree has changes.",
                )
            )
    else:
        checks.extend(
            (
                _doctor_check("target_repository", "unknown", "git_unavailable", "Target repository was not inspected."),
                _doctor_check("target_state", "unknown", "git_unavailable", "Target state was not inspected."),
            )
        )

    storage = _read_only_storage_status(runs_dir)
    checks.append(_doctor_check("run_storage", storage["status"], storage["code"], storage["summary"]))

    required_binaries = {
        "claude": "claude",
        "codex": "codex",
    }
    missing = sorted(name for name, binary in required_binaries.items() if not shutil.which(binary))
    checks.append(
        _doctor_check(
            "provider_binaries",
            "pass" if not missing else "fail",
            "provider_binaries_available" if not missing else "provider_binary_missing",
            "Configured provider binaries are discoverable." if not missing else "Configured provider binary is not on PATH.",
        )
    )
    checks.append(
        _doctor_check(
            "provider_auth",
            "unknown",
            "auth_probe_unavailable",
            "No side-effect-free local provider authentication probe is configured.",
        )
    )
    adapter_binaries = {"claude_cli": "claude", "codex_cli": "codex"}
    route_valid = all(
        route.provider_adapter_id in adapter_binaries
        and route.role_id in OPERATION_ROLES.values()
        and shutil.which(adapter_binaries[route.provider_adapter_id]) is not None
        for route in ROUTE_IDENTITIES.values()
    )
    checks.append(
        _doctor_check(
            "route_capabilities",
            "pass" if route_valid else "fail",
            "route_capabilities_valid" if route_valid else "route_capabilities_invalid",
            "Compiled route capabilities are coherent." if route_valid else "Compiled route capabilities are incoherent.",
        )
    )
    return {
        "doctor_version": DOCTOR_VERSION,
        "repository": {"path": state.repo} if state is not None else None,
        "checks": checks,
    }


_PORCELAIN_V1_STATUS_BYTES = frozenset(b" MADRCUT?!X")


def _decode_porcelain_path(raw: bytes) -> str:
    try:
        path = os.fsdecode(raw)
    except UnicodeError as exc:
        raise ControllerError(
            "Git path cannot be decoded with the platform filesystem encoding"
        ) from exc
    if os.fsencode(path) != raw:
        raise ControllerError(
            "Git path does not round-trip through the platform filesystem encoding"
        )
    try:
        path.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ControllerError(
            "Git path cannot be represented by the current UTF-8 run-state format"
        ) from exc
    return path


def _parse_porcelain_v1_z(output: bytes) -> list[str]:
    files: list[str] = []
    cursor = 0

    while cursor < len(output):
        if len(output) - cursor < 4:
            raise ControllerError("Git porcelain status contains a truncated record")

        status = output[cursor : cursor + 2]
        if any(value not in _PORCELAIN_V1_STATUS_BYTES for value in status):
            raise ControllerError(
                "Git porcelain status contains an invalid status code"
            )
        if output[cursor + 2] != 0x20:
            raise ControllerError(
                "Git porcelain status record is missing its path separator"
            )

        path_start = cursor + 3
        path_end = output.find(b"\0", path_start)
        if path_end < 0:
            raise ControllerError("Git porcelain status contains an unterminated path")
        if path_end == path_start:
            raise ControllerError("Git porcelain status contains an empty path")

        files.append(_decode_porcelain_path(output[path_start:path_end]))
        cursor = path_end + 1

        if status[0] in b"RC" or status[1] in b"RC":
            source_end = output.find(b"\0", cursor)
            if source_end < 0:
                raise ControllerError(
                    "Git porcelain rename/copy record is missing its source path"
                )
            if source_end == cursor:
                raise ControllerError(
                    "Git porcelain rename/copy record contains an empty source path"
                )
            files.append(_decode_porcelain_path(output[cursor:source_end]))
            cursor = source_end + 1

    return sorted(set(files))


def changed_files(repo: Path) -> list[str]:
    result = _git_bytes(
        repo,
        "status",
        "--porcelain=v1",
        "-z",
        "--untracked-files=all",
    )
    return _parse_porcelain_v1_z(result.stdout)


def working_tree_fingerprint(repo: Path, files: list[str] | None = None) -> str:
    digest = hashlib.sha256()
    status = _git(repo, "status", "--porcelain=v1", "--untracked-files=all").stdout
    digest.update(status.encode())
    for command in (("diff", "--no-ext-diff", "--binary"), ("diff", "--cached", "--no-ext-diff", "--binary")):
        digest.update(_git(repo, *command).stdout.encode())
    for relative in files if files is not None else changed_files(repo):
        path = repo / relative
        digest.update(relative.encode())
        if path.is_file():
            digest.update(path.read_bytes())
    return digest.hexdigest()


def approval_fingerprint(repo: Path) -> str:
    """Hash the full repository snapshot guarded by one human approval."""

    state = repo_state(repo)
    parts = (
        "continuo-approval-fingerprint-v1",
        state.repo,
        state.branch,
        state.head,
        state.origin,
        "1" if state.clean else "0",
        working_tree_fingerprint(repo),
    )
    return hashlib.sha256("\0".join(parts).encode()).hexdigest()


def diff_check(repo: Path) -> tuple[bool, str]:
    result = _git(repo, "diff", "--check", check=False)
    return result.returncode == 0, (result.stdout + result.stderr).strip()


class StoragePreflight(NamedTuple):
    runs_dir: Path
    hardened_directories: int
    hardened_files: int


class RunRecordSnapshot(NamedTuple):
    content: bytes
    device: int
    inode: int
    mtime_ns: int


def _absolute_path(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path.expanduser())))


def _storage_error(path: Path, problem: str) -> ControllerError:
    return ControllerError(f"private storage rejected {path}: {problem}")


def _lstat(path: Path) -> os.stat_result | None:
    try:
        return os.lstat(path)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise _storage_error(path, "metadata is unavailable") from exc


def _secure_directory(path: Path) -> bool:
    before = _lstat(path)
    if before is None:
        raise _storage_error(path, "directory is missing")
    if stat_module.S_ISLNK(before.st_mode):
        raise _storage_error(path, "path is a symlink")
    if not stat_module.S_ISDIR(before.st_mode):
        raise _storage_error(path, "path is not a directory")
    if before.st_uid != os.geteuid():
        raise _storage_error(path, "directory has a foreign owner")

    changed = stat_module.S_IMODE(before.st_mode) != _PRIVATE_DIRECTORY_MODE
    try:
        os.chmod(path, _PRIVATE_DIRECTORY_MODE, follow_symlinks=False)
        flags = (
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise _storage_error(path, "directory permission check failed") from exc
    try:
        after = os.fstat(descriptor)
        if (
            not stat_module.S_ISDIR(after.st_mode)
            or after.st_uid != os.geteuid()
            or (after.st_dev, after.st_ino) != (before.st_dev, before.st_ino)
        ):
            raise _storage_error(path, "directory identity changed during validation")
        os.fchmod(descriptor, _PRIVATE_DIRECTORY_MODE)
    except OSError as exc:
        raise _storage_error(path, "directory permission check failed") from exc
    finally:
        os.close(descriptor)
    return changed


def _create_private_directory(path: Path) -> int:
    path = _absolute_path(path)
    if _lstat(path) is not None:
        return int(_secure_directory(path))

    missing: list[Path] = []
    candidate = path
    while _lstat(candidate) is None:
        missing.append(candidate)
        parent = candidate.parent
        if parent == candidate:
            raise _storage_error(path, "no existing parent directory")
        candidate = parent

    hardened = 0
    for directory in reversed(missing):
        try:
            os.mkdir(directory, _PRIVATE_DIRECTORY_MODE)
        except FileExistsError:
            pass
        except OSError as exc:
            raise _storage_error(directory, "private directory creation failed") from exc
        hardened += int(_secure_directory(directory))
    return hardened


def _secure_regular_file(path: Path) -> bool:
    before = _lstat(path)
    if before is None:
        raise _storage_error(path, "file is missing")
    if stat_module.S_ISLNK(before.st_mode):
        raise _storage_error(path, "path is a symlink")
    if not stat_module.S_ISREG(before.st_mode):
        raise _storage_error(path, "path is not a regular file")
    if before.st_uid != os.geteuid():
        raise _storage_error(path, "file has a foreign owner")
    if before.st_nlink != 1:
        raise _storage_error(path, "file has multiple hard links")

    changed = stat_module.S_IMODE(before.st_mode) != _PRIVATE_FILE_MODE
    try:
        os.chmod(path, _PRIVATE_FILE_MODE, follow_symlinks=False)
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise _storage_error(path, "file permission check failed") from exc
    try:
        after = os.fstat(descriptor)
        if (
            not stat_module.S_ISREG(after.st_mode)
            or after.st_uid != os.geteuid()
            or after.st_nlink != 1
            or (after.st_dev, after.st_ino) != (before.st_dev, before.st_ino)
        ):
            raise _storage_error(path, "file identity changed during validation")
        os.fchmod(descriptor, _PRIVATE_FILE_MODE)
    except OSError as exc:
        raise _storage_error(path, "file permission check failed") from exc
    finally:
        os.close(descriptor)
    return changed


def _is_run_artifact(name: str) -> bool:
    return name.endswith(".json") or name.endswith(".json.tmp") or (
        name.startswith(_RUN_TEMP_PREFIX) and name.endswith(_RUN_TEMP_SUFFIX)
    )


def _is_sqlite_artifact(name: str) -> bool:
    return any(name.endswith(suffix) for suffix in _SQLITE_SUFFIXES)


def _scan_private_files(directory: Path, predicate: Callable[[str], bool]) -> int:
    hardened = 0
    try:
        entries = list(os.scandir(directory))
    except OSError as exc:
        raise _storage_error(directory, "directory scan failed") from exc
    for entry in entries:
        if not predicate(entry.name):
            continue
        try:
            hardened += int(_secure_regular_file(directory / entry.name))
        except ControllerError:
            if _lstat(directory / entry.name) is None:
                continue
            raise
    return hardened


def _prepare_private_storage(
    runs_dir: Path,
    *,
    create_locks: bool = False,
) -> StoragePreflight:
    root = _absolute_path(runs_dir)
    hardened_directories = _create_private_directory(root)
    hardened_files = _scan_private_files(root, _is_run_artifact)

    locks = root / ".target-locks"
    lock_state = _lstat(locks)
    if lock_state is not None or create_locks:
        hardened_directories += _create_private_directory(locks)
        hardened_files += _scan_private_files(locks, _is_sqlite_artifact)

    return StoragePreflight(root, hardened_directories, hardened_files)


def _print_storage_hardening(result: StoragePreflight) -> None:
    if not (result.hardened_directories or result.hardened_files):
        return
    console.print(
        "Hardened legacy storage permissions: "
        f"{result.hardened_directories} director"
        f"{'y' if result.hardened_directories == 1 else 'ies'}, "
        f"{result.hardened_files} file"
        f"{'s' if result.hardened_files != 1 else ''}."
    )


def _run_record_path(run_id: str, runs_dir: Path) -> Path:
    if (
        not run_id
        or run_id in {".", ".."}
        or "\0" in run_id
        or "/" in run_id
        or "\\" in run_id
    ):
        raise ControllerError("run id is not a single safe identifier")
    path = runs_dir / f"{run_id}.json"
    if path.parent != runs_dir:
        raise ControllerError("run id escapes private storage")
    return path


def _read_private_bytes(path: Path) -> RunRecordSnapshot:
    _secure_regular_file(path)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise _storage_error(path, "private file could not be opened") from exc
    try:
        current = os.fstat(descriptor)
        if (
            not stat_module.S_ISREG(current.st_mode)
            or current.st_uid != os.geteuid()
            or current.st_nlink != 1
            or stat_module.S_IMODE(current.st_mode) != _PRIVATE_FILE_MODE
        ):
            raise _storage_error(path, "private file validation failed")
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = -1
            return RunRecordSnapshot(
                content=stream.read(),
                device=current.st_dev,
                inode=current.st_ino,
                mtime_ns=current.st_mtime_ns,
            )
    except OSError as exc:
        raise _storage_error(path, "private file could not be read") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _read_private_text(path: Path) -> str:
    try:
        return _read_private_bytes(path).content.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise _storage_error(path, "private file is not valid UTF-8") from exc


def _safe_unlink_created(path: Path, identity: tuple[int, int]) -> None:
    state = _lstat(path)
    if state is None:
        return
    if (
        stat_module.S_ISREG(state.st_mode)
        and state.st_uid == os.geteuid()
        and state.st_nlink == 1
        and (state.st_dev, state.st_ino) == identity
    ):
        try:
            os.unlink(path)
        except OSError:
            pass


def persist(run: WorkflowRun, runs_dir: Path = RUNS) -> Path:
    if (
        type(run.schema_version) is not int
        or run.schema_version != CURRENT_RUN_SCHEMA_VERSION
    ):
        raise ControllerError("run schema is not current")
    root = _absolute_path(runs_dir)
    path = _run_record_path(run.run_id, root)
    root = _prepare_private_storage(root).runs_dir
    temporary: Path | None = None
    temporary_identity: tuple[int, int] | None = None
    descriptor = -1
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f"{_RUN_TEMP_PREFIX}{run.run_id}-",
            suffix=_RUN_TEMP_SUFFIX,
            dir=root,
        )
        temporary = Path(temporary_name)
        state = os.fstat(descriptor)
        temporary_identity = (state.st_dev, state.st_ino)
        if (
            not stat_module.S_ISREG(state.st_mode)
            or state.st_uid != os.geteuid()
            or state.st_nlink != 1
        ):
            raise _storage_error(temporary, "temporary file validation failed")
        os.fchmod(descriptor, _PRIVATE_FILE_MODE)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            descriptor = -1
            stream.write(run.model_dump_json(indent=2) + "\n")
            stream.flush()
        os.replace(temporary, path)
        temporary = None
        return path
    except ControllerError:
        raise
    except OSError as exc:
        raise _storage_error(path, "atomic persistence failed") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary is not None and temporary_identity is not None:
            _safe_unlink_created(temporary, temporary_identity)


def load_run(run_id: str, runs_dir: Path = RUNS) -> WorkflowRun:
    root = _absolute_path(runs_dir)
    path = _run_record_path(run_id, root)
    root = _prepare_private_storage(root).runs_dir
    if _lstat(path) is None:
        raise ControllerError(f"unknown run {run_id}")
    classification = classify_run_bytes(_read_private_bytes(path).content)
    if classification.treatment != "current" or classification.current_run is None:
        raise _classification_error(classification, path)
    if classification.current_run.run_id != run_id:
        raise ControllerError(f"run record identity_mismatch: {path}")
    return classification.current_run


def _classification_error(
    classification: RecordClassification,
    path: Path,
) -> ControllerError:
    detail = classification.reason_code
    if classification.field_path:
        detail = f"{detail}:{classification.field_path}"
    return ControllerError(f"run state is invalid ({detail}): {path}")


def inspect_run_record(
    run_id: str,
    runs_dir: Path = RUNS,
) -> tuple[Path, RunRecordSnapshot, RecordClassification]:
    root = _absolute_path(runs_dir)
    path = _run_record_path(run_id, root)
    root = _prepare_private_storage(root).runs_dir
    if _lstat(path) is None:
        raise ControllerError(f"unknown run {run_id}")
    snapshot = _read_private_bytes(path)
    classification = classify_run_bytes(snapshot.content)
    if classification.run_id is not None and classification.run_id != run_id:
        classification = RecordClassification(
            treatment="archive",
            record_state="ARCHIVE_ONLY",
            source_sha256=classification.source_sha256,
            schema_version=classification.schema_version,
            structural_class=classification.structural_class,
            disposition="inspection_only",
            reason_code="identity_mismatch",
            field_path="run_id",
            run_id=classification.run_id,
            task_ref=classification.task_ref,
            stage=classification.stage,
            payload=classification.payload,
            current_run=None,
        )
    return path, snapshot, classification


@contextmanager
def _migration_write_lock(root: Path) -> Iterator[None]:
    flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor = -1
    try:
        descriptor = os.open(root, flags)
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    except OSError as exc:
        raise _storage_error(root, "migration lock failed") from exc
    finally:
        if descriptor >= 0:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)


def _write_migrated_run(
    path: Path,
    expected: RunRecordSnapshot,
    result: MigrationResult,
) -> None:
    with _migration_write_lock(path.parent):
        _write_migrated_run_locked(path, expected, result)


def _write_migrated_run_locked(
    path: Path,
    expected: RunRecordSnapshot,
    result: MigrationResult,
) -> None:
    root = path.parent
    temporary: Path | None = None
    temporary_identity: tuple[int, int] | None = None
    descriptor = -1
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f"{_RUN_TEMP_PREFIX}{result.run.run_id}-",
            suffix=_RUN_TEMP_SUFFIX,
            dir=root,
        )
        temporary = Path(temporary_name)
        state = os.fstat(descriptor)
        temporary_identity = (state.st_dev, state.st_ino)
        if (
            not stat_module.S_ISREG(state.st_mode)
            or state.st_uid != os.geteuid()
            or state.st_nlink != 1
        ):
            raise _storage_error(temporary, "temporary file validation failed")
        os.fchmod(descriptor, _PRIVATE_FILE_MODE)
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = -1
            stream.write(result.run.model_dump_json(indent=2).encode("utf-8") + b"\n")
            stream.flush()

        current = _read_private_bytes(path)
        if (
            (current.device, current.inode) != (expected.device, expected.inode)
            or current.content != expected.content
            or hashlib.sha256(current.content).hexdigest() != result.source_sha256
        ):
            raise ControllerError("run record source_changed")
        os.replace(temporary, path)
        temporary = None
    except (ControllerError, MigrationError):
        raise
    except OSError as exc:
        raise _storage_error(path, "atomic migration persistence failed") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary is not None and temporary_identity is not None:
            _safe_unlink_created(temporary, temporary_identity)


def migrate_run_record(
    run_id: str,
    runs_dir: Path = RUNS,
    *,
    approval: Callable[[str], bool] | None = None,
    now: Callable[[], str] | None = None,
    migration_id: Callable[[], str] | None = None,
) -> WorkflowRun | None:
    path, first_snapshot, first = inspect_run_record(run_id, runs_dir)
    console.print(f"Run: {run_id}")
    console.print(f"Source schema: {first.schema_version or 'unsupported'}")
    console.print(f"Structural class: {first.structural_class or 'unknown'}")
    console.print(f"Source SHA-256: {first.source_sha256}")
    console.print(f"Treatment: {first.treatment}")
    console.print(f"Record state: {first.record_state}")
    if first.schema_version is not None:
        steps = migration_steps(first.schema_version)
        console.print(f"Steps: {', '.join(steps) if steps else '(none)'}")
    if first.disposition is not None:
        console.print(f"Final disposition: {first.disposition}")

    if first.treatment == "current":
        console.print("Run record is already current; no rewrite was performed.")
        return first.current_run
    if first.treatment != "migrate":
        raise _classification_error(first, path)

    confirm = approval or (
        lambda prompt: typer.confirm(prompt, default=False)
    )
    if not confirm("Migrate this run record to the current schema?"):
        console.print("Migration not approved; source record was not changed.")
        return None

    path, second_snapshot, second = inspect_run_record(run_id, runs_dir)
    if (
        second.source_sha256 != first.source_sha256
        or (second_snapshot.device, second_snapshot.inode)
        != (first_snapshot.device, first_snapshot.inode)
    ):
        raise ControllerError("run record source_changed")
    try:
        result = migrate_classification(
            second,
            migration_id=(migration_id or (lambda: uuid.uuid4().hex))(),
            migrated_at=(
                now
                or (lambda: datetime.now(timezone.utc).isoformat())
            )(),
        )
        _write_migrated_run(path, second_snapshot, result)
    except MigrationError as exc:
        detail = exc.reason_code
        if exc.field_path:
            detail = f"{detail}:{exc.field_path}"
        raise ControllerError(f"run migration {detail}") from exc
    audit = (
        result.run.provider_invocation_migration_audit
        or result.run.approval_migration_audit
        or result.run.policy_migration_audit
        or result.run.review_migration_audit
        or result.run.identity_migration_audit
    )
    if audit is None:
        raise ControllerError("run migration migration_audit_invalid")
    console.print(
        f"Migrated run {run_id} to schema {CURRENT_RUN_SCHEMA_VERSION}; "
        f"disposition is {audit.disposition}."
    )
    return result.run


class TargetCoordinator:
    """Per-target durable ownership plus a crash-releasing SQLite mutex."""

    SCHEMA_VERSION = 1

    def __init__(self, repo: Path, runs_dir: Path) -> None:
        self.repo = repo
        self.runs_dir = _absolute_path(runs_dir)
        self.identity = target_identity(repo)
        self.database = (
            self.runs_dir
            / ".target-locks"
            / f"{self.identity.target_key}.sqlite3"
        )

    def _connect(self) -> sqlite3.Connection:
        _prepare_private_storage(self.runs_dir, create_locks=True)
        if _lstat(self.database) is None:
            try:
                descriptor = os.open(
                    self.database,
                    os.O_WRONLY
                    | os.O_CREAT
                    | os.O_EXCL
                    | getattr(os, "O_NOFOLLOW", 0),
                    _PRIVATE_FILE_MODE,
                )
            except FileExistsError:
                _secure_regular_file(self.database)
            except OSError as exc:
                raise _storage_error(
                    self.database, "private database creation failed"
                ) from exc
            else:
                try:
                    state = os.fstat(descriptor)
                    if (
                        not stat_module.S_ISREG(state.st_mode)
                        or state.st_uid != os.geteuid()
                        or state.st_nlink != 1
                    ):
                        raise _storage_error(
                            self.database, "database validation failed"
                        )
                    os.fchmod(descriptor, _PRIVATE_FILE_MODE)
                finally:
                    os.close(descriptor)
        else:
            _secure_regular_file(self.database)
        try:
            connection = sqlite3.connect(
                self.database,
                timeout=0,
                isolation_level=None,
            )
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA busy_timeout = 0")
            _secure_regular_file(self.database)
            return connection
        except sqlite3.Error as exc:
            raise ControllerError(
                "target ownership database could not be opened"
            ) from exc

    def _ensure_schema(self, connection: sqlite3.Connection) -> None:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS coordination_meta (
                singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                schema_version INTEGER NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS target_owner (
                singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                target_key TEXT NOT NULL,
                canonical_repo TEXT NOT NULL,
                device INTEGER NOT NULL,
                inode INTEGER NOT NULL,
                run_id TEXT NOT NULL,
                acquired_at TEXT NOT NULL
            )
            """
        )
        metadata = connection.execute(
            "SELECT schema_version FROM coordination_meta WHERE singleton = 1"
        ).fetchone()
        if metadata is None:
            connection.execute(
                "INSERT INTO coordination_meta(singleton, schema_version) "
                "VALUES (1, ?)",
                (self.SCHEMA_VERSION,),
            )
        elif metadata["schema_version"] != self.SCHEMA_VERSION:
            raise ControllerError("target ownership database schema is unsupported")

    def _owner_hint(self) -> str | None:
        try:
            connection = self._connect()
            try:
                row = connection.execute(
                    "SELECT run_id FROM target_owner WHERE singleton = 1"
                ).fetchone()
                return row["run_id"] if row is not None else None
            finally:
                connection.close()
        except (ControllerError, sqlite3.Error):
            return None

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            try:
                connection.execute("BEGIN IMMEDIATE")
            except sqlite3.OperationalError as exc:
                if "locked" in str(exc).lower() or "busy" in str(exc).lower():
                    owner = self._owner_hint()
                    suffix = f" by run {owner}" if owner else ""
                    raise ControllerError(
                        "target is currently executing in another controller"
                        + suffix
                    ) from exc
                raise
            self._ensure_schema(connection)
            _prepare_private_storage(self.runs_dir, create_locks=True)
            yield connection
            connection.commit()
        except ControllerError:
            connection.rollback()
            raise
        except sqlite3.Error as exc:
            connection.rollback()
            raise ControllerError(
                "target ownership database is invalid or unavailable"
            ) from exc
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _owner(self, connection: sqlite3.Connection) -> sqlite3.Row | None:
        return connection.execute(
            "SELECT * FROM target_owner WHERE singleton = 1"
        ).fetchone()

    def _validate_identity_fields(
        self,
        target_key: str,
        canonical_repo: str,
        device: int,
        inode: int,
    ) -> None:
        identity = self.identity
        if (
            target_key != identity.target_key
            or canonical_repo != identity.canonical_repo
            or device != identity.device
            or inode != identity.inode
        ):
            raise ControllerError("target ownership identity does not match checkout")

    def _validate_run_identity(self, run: WorkflowRun) -> None:
        try:
            saved_repo = str(Path(run.repo.repo).resolve(strict=True))
        except OSError as exc:
            raise ControllerError(
                "saved target repository identity cannot be resolved"
            ) from exc
        if saved_repo != self.identity.canonical_repo:
            raise ControllerError("saved run targets a different checkout")
        ownership = run.target_ownership
        if ownership is not None:
            self._validate_identity_fields(
                ownership.target_key,
                ownership.canonical_repo,
                ownership.device,
                ownership.inode,
            )

    def _validate_owner_row(self, owner: sqlite3.Row) -> None:
        self._validate_identity_fields(
            owner["target_key"],
            owner["canonical_repo"],
            owner["device"],
            owner["inode"],
        )

    def _validate_owner_audit(
        self,
        owner: sqlite3.Row,
        ownership: TargetOwnership,
    ) -> None:
        if ownership.acquired_at != owner["acquired_at"]:
            raise ControllerError("target ownership state is unknown")

    def _release_audit(
        self,
        run: WorkflowRun,
        reason: str,
        note: str | None,
    ) -> None:
        ownership = run.target_ownership
        if ownership is None:
            raise ControllerError("target ownership audit is missing")
        run.target_ownership = TargetOwnership.model_validate(
            {
                **ownership.model_dump(),
                "released_at": datetime.now(timezone.utc).isoformat(),
                "release_reason": reason,
                "release_note": note,
            }
        )
        run.updated_at = datetime.now(timezone.utc).isoformat()
        persist(run, self.runs_dir)

    def _reconcile_other_owner(
        self,
        connection: sqlite3.Connection,
        owner: sqlite3.Row,
    ) -> None:
        self._validate_owner_row(owner)
        try:
            owner_run = load_run(owner["run_id"], self.runs_dir)
        except ControllerError as exc:
            raise ControllerError("target ownership state is unknown") from exc
        self._validate_run_identity(owner_run)
        ownership = owner_run.target_ownership
        if ownership is None:
            raise ControllerError("target ownership state is unknown")
        self._validate_owner_audit(owner, ownership)

        if ownership.released_at is not None:
            current = repo_state(self.repo)
            if not current.clean:
                raise ControllerError("released target is not clean")
            connection.execute("DELETE FROM target_owner WHERE singleton = 1")
            return

        if owner_run.stage == "pushed_awaiting_merge":
            current = repo_state(self.repo)
            if (
                owner_run.commit_hash is None
                or current.head != owner_run.commit_hash
                or not current.clean
                or current.branch != owner_run.repo.branch
                or current.origin != owner_run.repo.origin
            ):
                raise ControllerError("published target release cannot be proven")
            self._release_audit(owner_run, "published", None)
            connection.execute("DELETE FROM target_owner WHERE singleton = 1")
            return

        raise ControllerError(
            f"target is owned by unresolved run {owner['run_id']}"
        )

    def _clear_releasable_other_owner(
        self,
        connection: sqlite3.Connection,
    ) -> None:
        owner = self._owner(connection)
        if owner is not None:
            self._reconcile_other_owner(connection, owner)

    def claim_new(self, run: WorkflowRun) -> None:
        self._validate_run_identity(run)
        with self.transaction() as connection:
            self._clear_releasable_other_owner(connection)
            acquired_at = datetime.now(timezone.utc).isoformat()
            run.target_ownership = TargetOwnership(
                target_key=self.identity.target_key,
                canonical_repo=self.identity.canonical_repo,
                device=self.identity.device,
                inode=self.identity.inode,
                acquired_at=acquired_at,
            )
            run.updated_at = acquired_at
            persist(run, self.runs_dir)
            connection.execute(
                """
                INSERT INTO target_owner(
                    singleton, target_key, canonical_repo, device, inode,
                    run_id, acquired_at
                ) VALUES (1, ?, ?, ?, ?, ?, ?)
                """,
                (
                    self.identity.target_key,
                    self.identity.canonical_repo,
                    self.identity.device,
                    self.identity.inode,
                    run.run_id,
                    acquired_at,
                ),
            )

    def _claim_legacy(
        self,
        connection: sqlite3.Connection,
        run: WorkflowRun,
    ) -> None:
        acquired_at = datetime.now(timezone.utc).isoformat()
        run.target_ownership = TargetOwnership(
            target_key=self.identity.target_key,
            canonical_repo=self.identity.canonical_repo,
            device=self.identity.device,
            inode=self.identity.inode,
            acquired_at=acquired_at,
        )
        run.updated_at = acquired_at
        persist(run, self.runs_dir)
        connection.execute(
            """
            INSERT INTO target_owner(
                singleton, target_key, canonical_repo, device, inode,
                run_id, acquired_at
            ) VALUES (1, ?, ?, ?, ?, ?, ?)
            """,
            (
                self.identity.target_key,
                self.identity.canonical_repo,
                self.identity.device,
                self.identity.inode,
                run.run_id,
                acquired_at,
            ),
        )

    def claim_legacy(self, run: WorkflowRun) -> None:
        self._validate_run_identity(run)
        if run.target_ownership is not None:
            raise ControllerError("run already has a target ownership audit")
        with self.transaction() as connection:
            self._clear_releasable_other_owner(connection)
            self._claim_legacy(connection, run)

    @contextmanager
    def execute(
        self,
        run: WorkflowRun,
    ) -> Iterator[sqlite3.Connection]:
        self._validate_run_identity(run)
        ownership = run.target_ownership
        if ownership is not None and ownership.released_at is not None:
            raise ControllerError("released run cannot reacquire target ownership")

        with self.transaction() as connection:
            owner = self._owner(connection)
            if owner is None:
                raise ControllerError("run is not the durable target owner")
            else:
                self._validate_owner_row(owner)
                if owner["run_id"] != run.run_id:
                    self._reconcile_other_owner(connection, owner)
                    raise ControllerError("run is not the durable target owner")
                if ownership is None:
                    raise ControllerError("target ownership audit is missing")
                self._validate_owner_audit(owner, ownership)
            yield connection

    def release(
        self,
        connection: sqlite3.Connection,
        run: WorkflowRun,
        *,
        reason: str,
        note: str | None,
    ) -> None:
        owner = self._owner(connection)
        if owner is None or owner["run_id"] != run.run_id:
            raise ControllerError("run is not the durable target owner")
        self._validate_owner_row(owner)
        current = repo_state(self.repo)
        if not current.clean:
            raise ControllerError("target release requires a clean checkout")
        if reason == "published" and (
            run.stage != "pushed_awaiting_merge"
            or run.commit_hash is None
            or current.head != run.commit_hash
            or current.branch != run.repo.branch
            or current.origin != run.repo.origin
        ):
            raise ControllerError("published target release cannot be proven")
        self._release_audit(run, reason, note)
        connection.execute("DELETE FROM target_owner WHERE singleton = 1")


def _identity_control_key(
    identity: ProviderRouteIdentity,
) -> tuple[str, str, str, str]:
    """Return control-relevant route identity, excluding display metadata."""

    return (
        identity.role_id,
        identity.provider_adapter_id,
        identity.route_id,
        identity.model_id,
    )


def _validate_route_operation(
    identity: ProviderRouteIdentity,
    operation_id: ProviderOperation,
    capability: ProviderCapability,
) -> None:
    if OPERATION_ROLES[operation_id] != identity.role_id:
        raise ControllerError("provider operation does not match role")
    catalog = ROUTE_IDENTITIES[identity.role_id]
    if _identity_control_key(identity) != _identity_control_key(catalog):
        raise ControllerError("provider route identity is not recognized")
    expected_capability: ProviderCapability = (
        "workspace_write"
        if identity.role_id == "implementation"
        else "read_only"
    )
    if capability != expected_capability:
        raise ControllerError("provider capability does not match role")


def _record_provider(
    run: WorkflowRun,
    operation_id: ProviderOperation,
    identity: ProviderRouteIdentity,
    execution: ProviderExecution,
    *,
    capability: ProviderCapability,
    repository_fingerprint_before: str | None = None,
    repository_fingerprint_after: str | None = None,
    logical_invocation_id: str | None = None,
) -> ProviderExecution:
    _validate_route_operation(identity, operation_id, capability)
    if execution.capability not in {None, capability}:
        raise ControllerError(
            f"provider execution capability mismatch: expected {capability}, "
            f"received {execution.capability}"
        )
    execution = replace(
        execution,
        capability=capability,
        repository_fingerprint_before=repository_fingerprint_before,
        repository_fingerprint_after=repository_fingerprint_after,
        attempts=tuple(
            replace(
                attempt,
                capability=capability,
                repository_fingerprint_before=repository_fingerprint_before,
                repository_fingerprint_after=repository_fingerprint_after,
            )
            for attempt in execution.attempts
        ),
    )
    execution = (
        normalize_sonnet_execution(execution)
        if identity.provider_adapter_id == "claude_cli"
        else normalize_provider_execution(execution)
    )
    invocation_id = logical_invocation_id or (
        f"provider-invocation-{uuid.uuid4().hex[:20]}"
    )
    if execution.attempts:
        for index, attempt in enumerate(execution.attempts):
            is_final = index == len(execution.attempts) - 1
            if attempt.retry_scheduled == is_final:
                raise ControllerError(
                    "provider execution attempt retry sequence is incoherent"
                )
            if attempt.retry_scheduled and (
                attempt.capability != "read_only"
                or attempt.failure_kind != "unavailable"
            ):
                raise ControllerError(
                    "provider execution retry authority is incoherent"
                )
            run.provider_runs.append(
                ProviderRecord(
                    identity=identity,
                    operation_id=operation_id,
                    logical_invocation_id=invocation_id,
                    physical_attempt_ordinal=index + 1,
                    command=attempt.command,
                    returncode=attempt.returncode,
                    stdout=attempt.stdout,
                    stderr=attempt.stderr,
                    duration_seconds=attempt.duration_seconds,
                    failure_kind=attempt.failure_kind,
                    failure_source=attempt.failure_source,
                    failure_code=attempt.failure_code,
                    capability=attempt.capability,
                    repository_fingerprint_before=(
                        attempt.repository_fingerprint_before
                    ),
                    repository_fingerprint_after=(
                        attempt.repository_fingerprint_after
                    ),
                    retry_scheduled=attempt.retry_scheduled,
                )
            )
        return execution

    run.provider_runs.append(
        ProviderRecord(
            identity=identity,
            operation_id=operation_id,
            logical_invocation_id=invocation_id,
            physical_attempt_ordinal=1,
            command=execution.command,
            returncode=execution.returncode,
            stdout=execution.stdout,
            stderr=execution.stderr,
            duration_seconds=execution.duration_seconds,
            failure_kind=execution.failure_kind,
            failure_source=execution.failure_source,
            failure_code=execution.failure_code,
            capability=execution.capability,
            repository_fingerprint_before=(
                execution.repository_fingerprint_before
            ),
            repository_fingerprint_after=(
                execution.repository_fingerprint_after
            ),
        )
    )
    return execution


def _record_git(run: WorkflowRun, operation: str, result: subprocess.CompletedProcess[str]) -> None:
    run.git_operations.append(
        GitRecord(
            operation=operation,
            command=["git", *result.args[3:]] if isinstance(result.args, list) else ["git"],
            returncode=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
        )
    )


def _format_duration(seconds: float | None) -> str:
    if seconds is None:
        return "unavailable"
    total = max(0, int(round(seconds)))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes:02d}m {secs:02d}s"
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"


def _report_review_history(run: WorkflowRun) -> list[ReviewResult]:
    return [
        record.result
        for record in run.review_records
        if record.operation_id == "implementation_review"
    ]


def _provider_invocation_groups(
    run: WorkflowRun,
) -> list[list[ProviderRecord]]:
    groups: list[list[ProviderRecord]] = []
    for record in run.provider_runs:
        invocation_id = record.logical_invocation_id
        if invocation_id is None or record.physical_attempt_ordinal is None:
            raise ControllerError(
                "provider metrics unavailable: invocation identity is absent"
            )
        if not groups or groups[-1][0].logical_invocation_id != invocation_id:
            groups.append([record])
        else:
            groups[-1].append(record)
    return groups


def _run_report(run: WorkflowRun) -> dict[str, object]:
    invocation_groups = _provider_invocation_groups(run)
    logical_role_counts = Counter(
        records[0].identity.role_id for records in invocation_groups
    )
    physical_role_counts = Counter(
        record.identity.role_id for record in run.provider_runs
    )
    physical_role_seconds: Counter[str] = Counter()
    untimed_physical_counts: Counter[str] = Counter()
    failure_counts: Counter[str] = Counter()
    retry_transitions = 0

    for record in run.provider_runs:
        if record.duration_seconds is None:
            untimed_physical_counts[record.identity.role_id] += 1
        else:
            physical_role_seconds[record.identity.role_id] += (
                record.duration_seconds
            )
        if record.failure_kind:
            failure_counts[record.failure_kind] += 1
        if record.retry_scheduled:
            retry_transitions += 1

    reviews = _report_review_history(run)
    defect_keys = {
        _finding_key(review)
        for review in reviews
        if review.category not in {"PASS", "POLICY_AMBIGUITY"}
    }
    unreadable_review_records = [
        {
            "operation_id": record.operation_id,
            "provider_record_index": record.provider_record_index,
            "reason_code": record.reason_code,
        }
        for record in run.unreadable_review_records
    ]

    successful_writer_invocations = sum(
        1
        for records in invocation_groups
        if records[0].identity.role_id == "implementation"
        and records[0].operation_id
        in {"implementation_write", "correction_write"}
        and records[-1].returncode == 0
        and records[-1].failure_kind is None
    )

    wall_seconds: float | None = None
    if run.updated_at:
        try:
            created = datetime.fromisoformat(run.created_at)
            updated = datetime.fromisoformat(run.updated_at)
            wall_seconds = max(0.0, (updated - created).total_seconds())
        except ValueError:
            pass

    pushed = any(
        record.operation == "push" and record.returncode == 0
        for record in run.git_operations
    )

    return {
        "provider_logical_invocations_total": len(invocation_groups),
        "provider_physical_attempts_total": len(run.provider_runs),
        "role_logical_invocation_counts": dict(logical_role_counts),
        "role_physical_attempt_counts": dict(physical_role_counts),
        "role_physical_attempt_seconds": dict(physical_role_seconds),
        "untimed_physical_attempt_counts": dict(untimed_physical_counts),
        "provider_physical_attempt_seconds_total": sum(
            physical_role_seconds.values()
        ),
        "provider_physical_attempt_failure_counts": dict(failure_counts),
        "provider_retry_transitions_total": retry_transitions,
        "wall_seconds": wall_seconds,
        "corrections": run.correction_cycles,
        "resolved_correction_policy": (
            run.resolved_correction_policy.model_dump(mode="json")
            if run.resolved_correction_policy is not None
            else None
        ),
        "distinct_defects": len(defect_keys),
        "unreadable_review_count": len(unreadable_review_records),
        "unreadable_review_records": unreadable_review_records,
        "sol_escalations": logical_role_counts.get(
            "escalation_executive",
            0,
        ),
        "policy_decisions": len(run.policy_decisions),
        "approval_requests": len(run.approval_requests),
        "approval_approved": sum(
            decision.decision == "approved" for decision in run.approval_decisions
        ),
        "approval_declined": sum(
            decision.decision == "declined" for decision in run.approval_decisions
        ),
        "writer_recovery_decisions": len(run.writer_recovery_decisions),
        "target_ownership_state": (
            "legacy"
            if run.target_ownership is None
            else (
                "released"
                if run.target_ownership.released_at is not None
                else "active"
            )
        ),
        "target_key": (
            run.target_ownership.target_key
            if run.target_ownership is not None
            else None
        ),
        "pending_writer_state": (
            run.stage if run.stage.startswith("blocked_writer_") else None
        ),
        "successful_writer_invocations": successful_writer_invocations,
        "final_review": (
            run.implementation_review.status
            if run.implementation_review is not None
            else None
        ),
        "committed": run.commit_hash is not None,
        "commit_hash": run.commit_hash,
        "pushed": pushed,
    }


def _print_run_report(run: WorkflowRun) -> None:
    report = _run_report(run)

    console.print(f"\n[bold]Run {run.run_id} — {run.stage}[/bold]")
    console.print(
        "Wall-clock span: "
        + _format_duration(report["wall_seconds"])
    )

    logical_total = int(report["provider_logical_invocations_total"])
    physical_total = int(report["provider_physical_attempts_total"])
    console.print(f"Provider logical invocations: {logical_total}")
    console.print(f"Provider physical attempts: {physical_total}")

    logical_counts = report["role_logical_invocation_counts"]
    physical_counts = report["role_physical_attempt_counts"]
    seconds = report["role_physical_attempt_seconds"]
    untimed = report["untimed_physical_attempt_counts"]
    preferred = (
        "implementation",
        "adversarial_review",
        "escalation_executive",
        "policy_authority",
    )
    roles = list(preferred)
    roles.extend(role for role in logical_counts if role not in roles)

    for role in roles:
        logical_count = logical_counts.get(role, 0)
        physical_count = physical_counts.get(role, 0)
        if not logical_count:
            continue
        timed = seconds.get(role, 0.0)
        legacy = untimed.get(role, 0)
        timing = _format_duration(timed) if timed else "no recorded timing"
        suffix = (
            f"; {legacy} untimed physical attempt(s)"
            if legacy
            else ""
        )
        route = ROUTE_IDENTITIES[role]
        console.print(
            f"  {role} ({route.display_name}; {route.model_id}): "
            f"{logical_count} logical invocation(s), "
            f"{physical_count} physical attempt(s), {timing}{suffix}"
        )

    total_timed = float(report["provider_physical_attempt_seconds_total"])
    total_untimed = sum(untimed.values())
    provider_time = _format_duration(total_timed) if total_timed else "no recorded timing"
    legacy_suffix = (
        f" ({total_untimed} untimed physical attempt(s))"
        if total_untimed
        else ""
    )
    console.print(
        f"Provider physical-attempt time: {provider_time}{legacy_suffix}"
    )

    failures = report["provider_physical_attempt_failure_counts"]
    console.print(
        "Physical-attempt infrastructure/provider failures: "
        f"{sum(failures.values())}"
    )
    for kind, count in sorted(failures.items()):
        console.print(f"  {kind}: {count}")
    console.print(
        "Same-provider retry transitions: "
        f"{report['provider_retry_transitions_total']}"
    )

    console.print(f"Corrections: {report['corrections']}")
    policy = report["resolved_correction_policy"]
    if policy is not None:
        console.print(
            "Correction policy: "
            f"{policy['policy_id']} "
            f"({report['corrections']}/{policy['maximum_total_corrections']}; "
            f"{policy['maximum_sol_escalations_per_persistent_finding']} "
            "Sol escalation(s) per persistent finding; "
            + " -> ".join(policy["persistent_finding_actions"])
            + ")"
        )
    console.print(f"Distinct defects: {report['distinct_defects']}")
    unreadable = report["unreadable_review_records"]
    if unreadable:
        console.print(
            f"{len(unreadable)} unreadable review record(s)"
        )
        for item in unreadable:
            console.print(
                f"  #{item['provider_record_index']} {item['operation_id']}: "
                f"{item['reason_code']}"
            )
    console.print(f"Sol escalations: {report['sol_escalations']}")
    console.print(
        "Successful writer invocations (not verification executions): "
        f"{report['successful_writer_invocations']}"
    )

    console.print(f"Policy decisions: {report['policy_decisions']}")
    for decision in run.policy_decisions:
        text = " ".join(decision.approved_text.split())
        if len(text) > 110:
            text = text[:107] + "..."
        console.print(f"  {decision.decision_id}: {text}")

    console.print(
        "Approval audit: "
        f"{report['approval_requests']} request(s), "
        f"{report['approval_approved']} approved, "
        f"{report['approval_declined']} declined"
    )
    if run.approval_decisions:
        latest = run.approval_decisions[-1]
        console.print(
            f"  latest {latest.gate}: {latest.decision} by "
            f"{latest.decided_by} at {latest.decided_at} "
            f"({latest.decided_fingerprint[:12]})"
        )

    console.print(
        "Writer recovery decisions: "
        f"{report['writer_recovery_decisions']}"
    )
    ownership_text = str(report["target_ownership_state"])
    if report["target_key"]:
        ownership_text += f" ({str(report['target_key'])[:12]})"
    console.print(f"Target ownership: {ownership_text}")
    if report["pending_writer_state"]:
        console.print(
            f"Pending writer state: {report['pending_writer_state']}"
        )

    final_review = report["final_review"] or "not available"
    console.print(f"Final review: {final_review}")

    if report["committed"]:
        console.print(f"Git commit: {report['commit_hash']}")
    else:
        console.print("Git commit: not made")

    console.print(f"Git push: {'completed' if report['pushed'] else 'not completed'}")


def _task_prompt(run: WorkflowRun) -> str:
    approved = ""
    if run.policy_decisions:
        decisions = "\n".join(
            f"- {decision.decision_id}: {decision.approved_text}"
            for decision in run.policy_decisions
        )
        approved = (
            "\n\nHuman-approved policy decisions (authoritative for this run):\n"
            + decisions
        )

    return (
        f"Task specification ({run.task_file}):\n\n{run.specification}"
        + approved
        + "\n\nImplement only this task in the jobs repository. Preserve useful existing code. "
        "Use deterministic Python for workflow/control decisions; do not invent policy."
    )


def _spec_review_prompt(run: WorkflowRun) -> str:
    return (
        "You are performing a fresh-context, read-only specification review. Read the task "
        "specification below. Return PASS only when it is sufficiently precise and bounded "
        "for implementation without inventing policy. Categorize a failure as exactly one "
        "of IMPLEMENTATION_DEFECT, POLICY_AMBIGUITY, or SCOPE_VIOLATION. Always return a "
        "finding_key: use PASS when passing; otherwise use a concise stable kebab-case defect "
        "identity. Do not edit files.\n\n"
        + _task_prompt(run)
    )


def _implementation_review_prompt(run: WorkflowRun, diff: str) -> str:
    prior = _implementation_review_history(run)[-8:]
    prior_text = "\n".join(
        f"- {_finding_key(item)}: {item.summary[:500]}"
        for item in prior
    )
    unreadable = [
        record
        for record in run.unreadable_review_records
        if record.operation_id == "implementation_review"
    ]
    if unreadable:
        prior_text += "\n" + "\n".join(
            f"- unreadable review record: {record.reason_code}"
            for record in unreadable
        )
    prior_text = prior_text or "(none)"

    return (
        "You are performing a fresh-context, read-only adversarial implementation review. "
        "Compare the actual changed files and diff to the task specification. Return PASS "
        "only if the implementation is correct. Otherwise categorize exactly one failure "
        "as IMPLEMENTATION_DEFECT, POLICY_AMBIGUITY, or SCOPE_VIOLATION. Always return a "
        "finding_key. Use PASS when passing. For a failure, use a concise stable kebab-case "
        "defect identity. If the current failure is materially the same defect as a prior "
        "finding below, REUSE THAT EXACT finding_key. If it is genuinely a different defect, "
        "create a new key. Do not edit files.\n\n"
        + _task_prompt(run)
        + "\n\nPrior implementation QA findings:\n"
        + prior_text
        + "\n\nChanged files:\n"
        + "\n".join(run.changed_files)
        + "\n\nDiff:\n"
        + diff
    )


def _terra_prompt(run: WorkflowRun, reason: str) -> str:
    return (
        "Read-only policy/architecture clarification. Do not edit files, run Git mutations, "
        "or decide whether to proceed. Identify the precise missing policy decision and "
        "propose a narrowly worded resolution for a human to approve.\n\n"
        f"Reason: {reason}\n\n{_task_prompt(run)}"
    )


def _implementation_diff(repo: Path) -> str:
    tracked = _git(repo, "diff", "HEAD", "--no-ext-diff").stdout
    untracked = [
        relative
        for relative in changed_files(repo)
        if not _git(
            repo,
            "ls-files",
            "--error-unmatch",
            "--",
            relative,
            check=False,
        ).stdout.strip()
    ]
    extra = []
    for relative in untracked:
        path = repo / relative
        if path.is_file():
            extra.append(f"\n--- untracked: {relative}\n{path.read_text(encoding='utf-8', errors='replace')}")
    return tracked + "".join(extra)


def _stageable_changed_files(repo: Path, files: list[str]) -> list[str]:
    """Exclude absent paths whose deletion is already represented in the index."""

    return [
        relative
        for relative in files
        if os.path.lexists(repo / relative)
        or bool(
            _git(
                repo,
                "ls-files",
                "--error-unmatch",
                "--",
                relative,
                check=False,
            ).stdout.strip()
        )
    ]


def _finding_key(result: ReviewResult) -> str:
    if result.finding_key:
        return result.finding_key
    normalized = " ".join(result.summary.lower().split())
    digest = hashlib.sha256(normalized.encode()).hexdigest()[:16]
    return f"legacy-{digest}"


def _implementation_review_history(run: WorkflowRun) -> list[ReviewResult]:
    return [
        record.result
        for record in run.review_records
        if record.operation_id == "implementation_review"
        and record.result.category != "PASS"
    ]


def _current_finding_streak(run: WorkflowRun, result: ReviewResult) -> int:
    target = _finding_key(result)
    streak = 0
    for prior in reversed(_implementation_review_history(run)):
        if _finding_key(prior) != target:
            break
        streak += 1
    return max(1, streak)


def _saved_correction_policy(run: WorkflowRun) -> ResolvedCorrectionPolicy:
    policy = run.resolved_correction_policy
    if policy is None:
        raise ControllerError("run lacks a resolved correction policy")
    return policy


def _correction_action(
    run: WorkflowRun,
    finding_streak: int,
) -> CorrectionPolicyAction:
    policy = _saved_correction_policy(run)
    if run.correction_cycles >= policy.maximum_total_corrections:
        return "block"
    index = min(finding_streak - 1, len(policy.persistent_finding_actions) - 1)
    return policy.persistent_finding_actions[index]


def _block_for_correction_policy(
    controller: "Controller",
    run: WorkflowRun,
    finding_key: str,
) -> WorkflowRun:
    policy = _saved_correction_policy(run)
    if run.correction_cycles >= policy.maximum_total_corrections:
        return controller._block(
            run,
            "blocked_correction_budget",
            "implementation still failing after "
            f"{policy.maximum_total_corrections} total corrections",
        )
    return controller._block(
        run,
        "blocked_repeated_finding",
        f"finding {finding_key} still fails after "
        f"{policy.maximum_sol_escalations_per_persistent_finding} "
        "Sol-guided corrections",
    )


def _sol_prompt(
    run: WorkflowRun,
    repo: Path,
    finding_key: str,
    escalation_round: int,
) -> str:
    findings = _implementation_review_history(run)[-8:]
    history = "\n\n".join(
        f"Finding {index} [{_finding_key(finding)}]:\n{finding.summary}"
        for index, finding in enumerate(findings, start=1)
    )
    unreadable = [
        record
        for record in run.unreadable_review_records
        if record.operation_id == "implementation_review"
    ]
    if unreadable:
        history += "\n\n" + "\n".join(
            f"Finding {index} (unreadable, {record.reason_code}): "
            "review output is not parseable"
            for index, record in enumerate(unreadable, start=1)
        )
    history = history or "(no parseable prior implementation findings)"

    prior_guidance = run.sol_guidance or "(none)"

    return f"""You are Sol High, the read-only escalation executive.

The SAME implementation defect has survived a Luna correction.
This is escalation round {escalation_round} for finding_key: {finding_key}.

Do not edit files and do not perform Git operations.

Diagnose why this specific defect persists and give the smallest useful guidance
for another bounded Luna correction. Focus on the current finding; do not expand
scope merely because other unrelated defects may exist.

If the remaining problem is genuinely a policy or architecture ambiguity that
requires authority rather than implementation debugging, report that instead.

Return exactly one of these forms:
GUIDANCE: <concise root-cause analysis and bounded correction guidance>
POLICY_AMBIGUITY: <concise reason authority is required>

Previous Sol guidance for this finding/run:
{prior_guidance}

Task specification:
{run.specification}

Recent Sonnet implementation findings:
{history}

Current implementation diff:
{_implementation_diff(repo)}
"""


def _parse_sol_response(execution: ProviderExecution) -> tuple[str, str]:
    if execution.returncode != 0:
        raise RuntimeError(execution.stderr or "Sol escalation failed")

    text = execution.stdout.strip()
    for kind in ("GUIDANCE", "POLICY_AMBIGUITY"):
        prefix = kind + ":"
        if text.startswith(prefix):
            body = text[len(prefix):].strip()
            if not body:
                raise ValueError("Sol returned an empty escalation result")
            return kind, body

    raise ValueError("Sol returned an invalid escalation result")


def _terra_proposed_approval_text(resolution: str) -> str | None:
    marker = "Proposed approval text:"
    if marker not in resolution:
        return None

    tail = resolution.split(marker, 1)[1].strip()
    quoted: list[str] = []
    collecting = False

    for raw in tail.splitlines():
        line = raw.strip()
        if line.startswith(">"):
            collecting = True
            body = line[1:].strip()
            if body:
                quoted.append(body)
            continue
        if collecting and line:
            break

    text = " ".join(quoted).strip()
    return text or None


_PROVIDER_RESUMABLE_BLOCKS = {
    "blocked_provider_quota",
    "blocked_provider_billing",
    "blocked_provider_auth",
    "blocked_provider_rate_limit",
    "blocked_provider_unavailable",
    "blocked_provider_configuration",
    "blocked_provider_failure",
    "blocked_provider_output",
}


class Controller:
    def __init__(
        self,
        repo: Path,
        runs_dir: Path = RUNS,
        sonnet: Callable[[str, Path], ProviderExecution] = execute_sonnet_review,
        terra: Callable[[str, Path], ProviderExecution] = execute_terra_resolution,
        sol: Callable[[str, Path], ProviderExecution] = execute_sol_escalation,
        luna: Callable[[str, Path], ProviderExecution] = execute_luna_implementation,
        approval: Callable[[str], bool] | None = None,
        approval_actor: Callable[[], str] | None = None,
    ) -> None:
        self.repo = repo
        self.runs_dir = runs_dir
        self.sonnet = sonnet
        self.terra = terra
        self.sol = sol
        self.luna = luna
        self._providers_by_role: dict[
            str,
            Callable[[str, Path], ProviderExecution],
        ] = {
            "adversarial_review": self.sonnet,
            "policy_authority": self.terra,
            "escalation_executive": self.sol,
            "implementation": self.luna,
        }
        self.approval = approval or (lambda prompt: typer.confirm(prompt, default=False))
        self.approval_actor = approval_actor or (
            lambda: f"local-os-uid:{os.geteuid()}"
        )

    def _approval_request(
        self,
        run: WorkflowRun,
        gate: ApprovalGate,
    ) -> ApprovalRequest:
        fingerprint = approval_fingerprint(self.repo)
        unanswered = [
            request
            for request in run.approval_requests
            if request.gate == gate
            and not any(
                decision.request_id == request.request_id
                for decision in run.approval_decisions
            )
        ]
        if unanswered:
            request = unanswered[-1]
            if (
                request.requested_stage != run.stage
                or request.requested_fingerprint != fingerprint
            ):
                raise ControllerError("approval request no longer matches repository state")
            return request
        request = ApprovalRequest(
            request_id=f"approval-request-{uuid.uuid4().hex[:20]}",
            gate=gate,
            requested_at=datetime.now(timezone.utc).isoformat(),
            requested_stage=run.stage,
            requested_fingerprint=fingerprint,
        )
        run.approval_requests.append(request)
        return request

    @staticmethod
    def _approval_decision_for(
        run: WorkflowRun,
        request: ApprovalRequest,
    ) -> ApprovalDecision | None:
        return next(
            (
                decision
                for decision in run.approval_decisions
                if decision.request_id == request.request_id
            ),
            None,
        )

    def _record_approval_decision(
        self,
        run: WorkflowRun,
        request: ApprovalRequest,
        decision: bool,
        text: str,
        *,
        policy_decision_id: str | None = None,
    ) -> ApprovalDecision:
        if self._approval_decision_for(run, request) is not None:
            raise ControllerError("approval request already has a decision")
        if approval_fingerprint(self.repo) != request.requested_fingerprint:
            raise ControllerError("approval decision refused: repository state changed")
        actor = self.approval_actor()
        if not re.fullmatch(r"local-os-uid:[0-9]+", actor):
            raise ControllerError("approval actor must be a local effective-UID principal")
        record = ApprovalDecision(
            decision_id=f"approval-decision-{uuid.uuid4().hex[:20]}",
            request_id=request.request_id,
            gate=request.gate,
            decided_at=datetime.now(timezone.utc).isoformat(),
            decided_by=actor,
            decision="approved" if decision else "declined",
            decision_text=text.strip(),
            decided_fingerprint=request.requested_fingerprint,
            policy_decision_id=policy_decision_id,
        )
        run.approval_decisions.append(record)
        return record

    def _provider_for(
        self,
        identity: ProviderRouteIdentity,
    ) -> Callable[[str, Path], ProviderExecution]:
        catalog = ROUTE_IDENTITIES[identity.role_id]
        if _identity_control_key(identity) != _identity_control_key(catalog):
            raise ControllerError("saved provider route is not recognized")
        return self._providers_by_role[identity.role_id]

    @staticmethod
    def _require_executable(run: WorkflowRun) -> None:
        if run.provider_invocation_migration_audit is not None:
            raise ControllerError(
                "run execution refused: migrated record disposition is "
                f"{run.provider_invocation_migration_audit.disposition}"
            )
        if run.approval_migration_audit is not None:
            raise ControllerError(
                "run execution refused: migrated record disposition is "
                f"{run.approval_migration_audit.disposition}"
            )
        if run.policy_migration_audit is not None:
            raise ControllerError(
                "run execution refused: migrated record disposition is "
                f"{run.policy_migration_audit.disposition}"
            )
        if run.identity_migration_audit is not None:
            raise ControllerError(
                "run execution refused: migrated record disposition is "
                f"{run.identity_migration_audit.disposition}"
            )
        if run.migration_audit is not None:
            raise ControllerError(
                "run execution refused: migrated record disposition is "
                f"{run.migration_audit.disposition}"
            )
        if run.review_migration_audit is not None:
            raise ControllerError(
                "run execution refused: migrated record disposition is "
                f"{run.review_migration_audit.disposition}"
            )

    def _save(self, run: WorkflowRun) -> None:
        self._require_executable(run)
        if (
            type(run.schema_version) is not int
            or run.schema_version != CURRENT_RUN_SCHEMA_VERSION
        ):
            raise ControllerError("run schema is not current")
        run.updated_at = datetime.now(timezone.utc).isoformat()
        persist(run, self.runs_dir)

    def approve_policy(self, run_id: str, approved_text: str) -> WorkflowRun:
        run = load_run(run_id, self.runs_dir)
        self._require_executable(run)
        coordinator = TargetCoordinator(self.repo, self.runs_dir)
        if run.target_ownership is None:
            self._resume_guard(run)
            coordinator.claim_legacy(run)
        with coordinator.execute(run) as connection:
            result = self._approve_policy_owned(run, approved_text)
            return self._finish_owned_action(
                coordinator,
                connection,
                result,
            )

    def _approve_policy_owned(
        self,
        run: WorkflowRun,
        approved_text: str,
    ) -> WorkflowRun:
        self._resume_guard(run)

        if run.stage != "blocked_policy_ambiguity":
            raise ControllerError("policy approval requires blocked_policy_ambiguity stage")

        approved_text = approved_text.strip()
        if not approved_text:
            raise ControllerError("approved policy text cannot be empty")

        request = self._approval_request(run, "policy")
        if self._approval_decision_for(run, request) is not None:
            raise ControllerError("policy approval request already has a decision")

        trigger_summary = (
            run.sol_guidance
            or (run.implementation_review.summary if run.implementation_review else None)
            or (run.spec_review.summary if run.spec_review else None)
            or run.last_error
            or "policy ambiguity"
        )
        trigger_key = (
            _finding_key(run.implementation_review)
            if run.implementation_review is not None
            else None
        )

        source_index = next(
            (
                index
                for index in range(len(run.provider_runs) - 1, -1, -1)
                if run.provider_runs[index].identity.role_id == "policy_authority"
                and run.provider_runs[index].operation_id == "policy_clarification"
                and run.provider_runs[index].returncode == 0
                and run.provider_runs[index].failure_kind is None
            ),
            None,
        )
        if source_index is None:
            raise ControllerError(
                "policy approval lacks a successful policy-authority audit record"
            )

        policy_decision = PolicyDecision(
            decision_id=f"policy-{len(run.policy_decisions) + 1:02d}",
                approved_at=datetime.now(timezone.utc).isoformat(),
                source_provider_record_index=source_index,
                trigger_finding_key=trigger_key,
                trigger_summary=trigger_summary,
                recommendation=run.terra_resolution or "",
            approved_text=approved_text,
        )
        self._record_approval_decision(
            run,
            request,
            True,
            approved_text,
            policy_decision_id=policy_decision.decision_id,
        )
        run.policy_decisions.append(policy_decision)

        run.sol_guidance = None
        run.last_error = None

        # Specification ambiguity: re-review the specification with the
        # approved decision now included in the authoritative task prompt.
        if run.implementation_review is None:
            run.stage = "created"
        else:
            policy = _saved_correction_policy(run)
            if run.correction_cycles >= policy.maximum_total_corrections:
                return _block_for_correction_policy(
                    self,
                    run,
                    _finding_key(run.implementation_review),
                )
            run.correction_cycles += 1
            run.stage = "correction_pending"

        self._save(run)
        return run

    def decline_policy(self, run_id: str, text: str = "Policy decision declined.") -> WorkflowRun:
        run = load_run(run_id, self.runs_dir)
        self._require_executable(run)
        coordinator = TargetCoordinator(self.repo, self.runs_dir)
        if run.target_ownership is None:
            self._resume_guard(run)
            coordinator.claim_legacy(run)
        with coordinator.execute(run) as connection:
            self._resume_guard(run)
            if run.stage != "blocked_policy_ambiguity":
                raise ControllerError("policy decline requires blocked_policy_ambiguity stage")
            request = self._approval_request(run, "policy")
            self._record_approval_decision(run, request, False, text)
            self._save(run)
            return self._finish_owned_action(coordinator, connection, run)

    def recover_writer(
        self,
        run_id: str,
        action: WriterRecoveryAction,
        note: str,
    ) -> WorkflowRun:
        run = load_run(run_id, self.runs_dir)
        self._require_executable(run)
        coordinator = TargetCoordinator(self.repo, self.runs_dir)
        if run.target_ownership is None:
            self._resume_guard(run)
            coordinator.claim_legacy(run)
        with coordinator.execute(run) as connection:
            result = self._recover_writer_owned(run, action, note)
            return self._finish_owned_action(
                coordinator,
                connection,
                result,
            )

    def _recover_writer_owned(
        self,
        run: WorkflowRun,
        action: WriterRecoveryAction,
        note: str,
    ) -> WorkflowRun:
        if run.stage not in {
            "blocked_writer_retry_required",
            "blocked_writer_partial_changes",
            "blocked_writer_state_unknown",
        }:
            raise ControllerError(
                "writer recovery requires a blocked writer-recovery stage"
            )
        active = run.active_writer_attempt
        if active is None:
            raise ControllerError(
                "writer recovery requires durable pre-attempt evidence"
            )
        prompt = run.provider_resume_prompt
        if not prompt:
            raise ControllerError("writer recovery requires the saved writer prompt")
        note = note.strip()
        if not note:
            raise ControllerError("writer recovery note cannot be empty")
        if len(note) > 1000:
            raise ControllerError("writer recovery note exceeds 1000 characters")
        if action not in {"retry_restored", "adopt_current"}:
            raise ControllerError("unsupported writer recovery action")

        files, fingerprint = self._writer_snapshot(run)
        if action == "retry_restored":
            if fingerprint != active.pre_fingerprint:
                raise ControllerError(
                    "retry-restored requires the exact saved pre-attempt state"
                )
        elif fingerprint == active.pre_fingerprint or not files:
            raise ControllerError(
                "adopt-current requires trustworthy changes beyond the "
                "saved pre-attempt state"
            )

        run.writer_recovery_decisions.append(
            WriterRecoveryDecision(
                decision_id=(
                    f"writer-recovery-{len(run.writer_recovery_decisions) + 1:02d}"
                ),
                decided_at=datetime.now(timezone.utc).isoformat(),
                action=action,
                note=note,
                writer_attempt_id=active.attempt_id,
                stage=active.stage,
                purpose=active.purpose,
                pre_fingerprint=active.pre_fingerprint,
                saved_post_fingerprint=active.post_fingerprint,
                observed_fingerprint=fingerprint,
                observed_changed_files=files,
            )
        )

        if action == "retry_restored":
            self._arm_writer_attempt(
                run,
                active.stage,
                active.purpose,
                prompt,
                files,
                fingerprint,
            )
            if not self._execute_armed_writer(run):
                return run
            return self._run_from(run)

        completed_stage = (
            "correction_completed"
            if active.stage == "correcting"
            else "implementation_completed"
        )
        self._clear_provider(run)
        run.active_writer_attempt = None
        run.stage = completed_stage
        run.last_error = None
        self._save(run)
        if not self._verify(run):
            return run
        return self._review_and_correct(run)

    def _finish_owned_action(
        self,
        coordinator: TargetCoordinator,
        connection: sqlite3.Connection,
        run: WorkflowRun,
    ) -> WorkflowRun:
        ownership = run.target_ownership
        if (
            run.stage == "pushed_awaiting_merge"
            and ownership is not None
            and ownership.released_at is None
        ):
            coordinator.release(
                connection,
                run,
                reason="published",
                note=None,
            )
        return run

    def release_target(self, run_id: str, note: str) -> WorkflowRun:
        run = load_run(run_id, self.runs_dir)
        self._require_executable(run)
        note = note.strip()
        if not note:
            raise ControllerError("target release note cannot be empty")
        if len(note) > 1000:
            raise ControllerError("target release note exceeds 1000 characters")
        if not (
            run.stage.startswith("blocked")
            or run.stage in {"commit_declined", "push_declined"}
        ):
            raise ControllerError(
                "target release requires a blocked or declined run"
            )
        coordinator = TargetCoordinator(self.repo, self.runs_dir)
        with coordinator.execute(run) as connection:
            coordinator.release(
                connection,
                run,
                reason="operator_released",
                note=note,
            )
        return run

    def _block(self, run: WorkflowRun, stage: str, message: str) -> WorkflowRun:
        run.stage = stage
        run.last_error = message
        self._save(run)
        console.print(f"[red]BLOCKED:[/red] {message}")
        _print_run_report(run)
        return run

    def _arm_provider(
        self,
        run: WorkflowRun,
        stage: str,
        prompt: str,
        identity: ProviderRouteIdentity,
        operation_id: ProviderOperation,
    ) -> None:
        capability: ProviderCapability = (
            "workspace_write"
            if identity.role_id == "implementation"
            else "read_only"
        )
        _validate_route_operation(identity, operation_id, capability)
        run.provider_resume_stage = stage
        run.provider_resume_prompt = prompt
        run.provider_resume_identity = identity
        run.provider_resume_operation_id = operation_id

    def _clear_provider(self, run: WorkflowRun) -> None:
        run.provider_resume_stage = None
        run.provider_resume_prompt = None
        run.provider_resume_identity = None
        run.provider_resume_operation_id = None

    def _block_provider(
        self,
        run: WorkflowRun,
        identity: ProviderRouteIdentity,
        operation_id: ProviderOperation,
        execution: ProviderExecution,
    ) -> WorkflowRun:
        kind = (
            execution.failure_kind
            or classify_provider_failure(
                execution.returncode,
                execution.stdout,
                execution.stderr,
            )
            or "provider_error"
        )

        stage = {
            "quota": "blocked_provider_quota",
            "billing": "blocked_provider_billing",
            "auth": "blocked_provider_auth",
            "rate_limit": "blocked_provider_rate_limit",
            "unavailable": "blocked_provider_unavailable",
            "timeout": "blocked_provider_timeout",
            "interrupted": "blocked_provider_interrupted",
            "configuration": "blocked_provider_configuration",
            "provider_error": "blocked_provider_failure",
        }[kind]

        description = {
            "quota": "quota or usage cap exhausted",
            "billing": "billing failure",
            "auth": "authentication failure",
            "rate_limit": "rate limit reached",
            "unavailable": "provider unavailable after bounded retries",
            "timeout": "provider deadline exceeded and process group stopped",
            "interrupted": "provider interrupted and process group stopped",
            "configuration": "provider CLI/model configuration failure",
            "provider_error": "unclassified provider failure",
        }[kind]

        return self._block(
            run,
            stage,
            f"{identity.display_name}: {description} during {operation_id}; "
            "no alternate provider was invoked",
        )

    def _block_provider_output(
        self,
        run: WorkflowRun,
        identity: ProviderRouteIdentity,
        operation_id: ProviderOperation,
        detail: str,
    ) -> WorkflowRun:
        return self._block(
            run,
            "blocked_provider_output",
            f"{identity.display_name}: invalid structured output during "
            f"{operation_id} "
            f"after one same-provider retry: {detail}",
        )

    def _retry_structured_once(
        self,
        run: WorkflowRun,
        identity: ProviderRouteIdentity,
        operation_id: ProviderOperation,
        parser: Callable[[ProviderExecution], object],
    ) -> object | None:
        prompt = run.provider_resume_prompt
        if not prompt:
            self._block(
                run,
                "blocked_interrupted_provider",
                "cannot safely retry malformed provider output without saved prompt",
            )
            return None

        console.print(
            f"[yellow]Invalid {identity.display_name} structured output; "
            "retrying the same provider once.[/yellow]"
        )
        provider = self._provider_for(identity)
        execution = provider(prompt, self.repo)
        execution = _record_provider(
            run,
            operation_id,
            identity,
            execution,
            capability="read_only",
        )
        self._save(run)

        if execution_failed(execution):
            self._block_provider(
                run,
                identity,
                operation_id,
                execution,
            )
            return None

        try:
            return parser(execution)
        except Exception as exc:
            if identity.role_id == "adversarial_review":
                run.unreadable_review_records.append(
                    UnreadableReviewRecord(
                        recorded_at=datetime.now(timezone.utc).isoformat(),
                        operation_id=operation_id,
                        provider_record_index=len(run.provider_runs) - 1,
                        reason_code=_unreadable_review_reason(exc),
                    )
                )
                self._save(run)
            self._block_provider_output(
                run,
                identity,
                operation_id,
                str(exc),
            )
            return None

    def _resume_blocked_provider(self, run: WorkflowRun) -> WorkflowRun:
        stage = run.provider_resume_stage
        prompt = run.provider_resume_prompt
        saved_identity = run.provider_resume_identity
        saved_operation = run.provider_resume_operation_id

        if not stage or not prompt or saved_identity is None or saved_operation is None:
            return self._block(
                run,
                "blocked_interrupted_provider",
                "provider failure lacks enough saved context for safe resumption",
            )

        if stage in {"implementing", "correcting"}:
            return self._observe_unrecorded_writer(run)

        provider_map: dict[
            str,
            tuple[ProviderOperation, ProviderRouteIdentity],
        ] = {
            "terra_resolving": (
                "policy_clarification",
                POLICY_AUTHORITY_ROUTE,
            ),
            "sol_escalating": (
                "escalation_guidance",
                ESCALATION_EXECUTIVE_ROUTE,
            ),
            "spec_reviewing": (
                "specification_review",
                ADVERSARIAL_REVIEW_ROUTE,
            ),
            "reviewing": (
                "implementation_review",
                ADVERSARIAL_REVIEW_ROUTE,
            ),
        }

        if stage not in provider_map:
            return self._block(
                run,
                "blocked_interrupted_provider",
                f"unsupported provider resume stage: {stage}",
            )

        operation_id, identity = provider_map[stage]
        if (
            saved_operation != operation_id
            or _identity_control_key(saved_identity)
            != _identity_control_key(identity)
        ):
            return self._block(
                run,
                "blocked_interrupted_provider",
                "saved provider identity does not match the resume stage",
            )
        provider = self._provider_for(saved_identity)
        run.stage = stage
        run.last_error = None
        self._save(run)

        console.print(
            f"[cyan]Stage:[/cyan] Resuming {stage} — "
            f"{saved_identity.display_name}"
        )
        execution = provider(prompt, self.repo)
        execution = _record_provider(
            run,
            operation_id,
            saved_identity,
            execution,
            capability="read_only",
        )
        self._save(run)

        if execution_failed(execution):
            return self._block_provider(
                run,
                saved_identity,
                operation_id,
                execution,
            )

        result = self._recover_provider_stage(run)

        # Do not erase context for a NEW provider failure reached while
        # continuing the workflow.
        if (
            not result.stage.startswith("blocked")
            and result.provider_resume_stage == stage
            and result.provider_resume_prompt == prompt
        ):
            self._clear_provider(result)
            self._save(result)

        return result

    def _policy_stop(self, run: WorkflowRun, reason: str) -> WorkflowRun:
        run.stage = "terra_resolving"
        prompt = _terra_prompt(run, reason)
        self._arm_provider(
            run,
            run.stage,
            prompt,
            POLICY_AUTHORITY_ROUTE,
            "policy_clarification",
        )
        self._save(run)

        console.print("[cyan]Stage:[/cyan] Policy clarification — Terra High")
        execution = self.terra(prompt, self.repo)
        execution = _record_provider(
            run,
            "policy_clarification",
            POLICY_AUTHORITY_ROUTE,
            execution,
            capability="read_only",
        )
        self._save(run)

        if execution_failed(execution):
            return self._block_provider(
                run,
                POLICY_AUTHORITY_ROUTE,
                "policy_clarification",
                execution,
            )

        self._clear_provider(run)
        run.terra_resolution = execution.stdout or execution.stderr
        run.stage = "blocked_policy_ambiguity"
        run.last_error = "policy ambiguity requires human approval"
        self._approval_request(run, "policy")
        self._save(run)
        console.print("[red]BLOCKED:[/red] policy ambiguity requires human approval")
        _print_run_report(run)
        return run

    def _review(self, run: WorkflowRun, purpose: str) -> ReviewResult | None:
        run.stage = (
            "spec_reviewing"
            if purpose == "specification"
            else "reviewing"
        )
        label = (
            "Specification review"
            if purpose == "specification"
            else "Implementation review"
        )
        prompt = (
            _spec_review_prompt(run)
            if purpose == "specification"
            else _implementation_review_prompt(
                run,
                _implementation_diff(self.repo),
            )
        )
        operation_id: ProviderOperation = (
            "specification_review"
            if purpose == "specification"
            else "implementation_review"
        )
        self._arm_provider(
            run,
            run.stage,
            prompt,
            ADVERSARIAL_REVIEW_ROUTE,
            operation_id,
        )
        self._save(run)

        console.print(
            f"[cyan]Stage:[/cyan] {label} — Sonnet 5 High"
        )
        execution = self.sonnet(prompt, self.repo)
        execution = _record_provider(
            run,
            operation_id,
            ADVERSARIAL_REVIEW_ROUTE,
            execution,
            capability="read_only",
        )
        self._save(run)

        if execution_failed(execution):
            self._block_provider(
                run,
                ADVERSARIAL_REVIEW_ROUTE,
                operation_id,
                execution,
            )
            return None

        try:
            result = parse_sonnet_review(execution)
        except Exception:
            retried = self._retry_structured_once(
                run,
                ADVERSARIAL_REVIEW_ROUTE,
                operation_id,
                parse_sonnet_review,
            )
            if retried is None:
                return None
            result = retried

        self._clear_provider(run)
        run.review_records.append(
            ReviewRecord(
                recorded_at=datetime.now(timezone.utc).isoformat(),
                operation_id=operation_id,
                result=result,
                provider_record_index=len(run.provider_runs) - 1,
            )
        )

        if purpose == "specification":
            run.spec_review = result
            run.stage = (
                "spec_review_passed"
                if result.category == "PASS"
                else "spec_review_failed"
            )
        else:
            run.implementation_review = result
            run.stage = "implementation_reviewed"

        self._save(run)
        return result

    def _verify(self, run: WorkflowRun) -> bool:
        run.stage = "verifying"
        self._save(run)
        console.print("[cyan]Stage:[/cyan] Deterministic verification")
        current = repo_state(self.repo)
        checks: dict[str, object] = {
            "branch_matches": current.branch == run.repo.branch,
            "head_matches": current.head == run.repo.head,
            "origin_matches": current.origin == run.repo.origin,
        }
        try:
            files = changed_files(self.repo)
        except ControllerError as exc:
            checks.update(
                {
                    "change_enumeration": False,
                    "change_enumeration_error": str(exc),
                }
            )
            run.verification = checks
            self._save(run)
            self._block(
                run,
                "blocked_unexpected_repo_state",
                f"could not enumerate repository changes: {exc}",
            )
            return False
        checks["change_enumeration"] = True
        clean_diff, diff_message = diff_check(self.repo)
        run.changed_files = files
        run.working_tree_fingerprint = working_tree_fingerprint(self.repo, files)
        checks.update({"diff_check": clean_diff, "diff_check_output": diff_message, "changed_files": files})
        run.verification = checks
        self._save(run)
        if not all(value for key, value in checks.items() if key != "diff_check_output" and key != "changed_files"):
            self._block(run, "blocked_unexpected_repo_state", "repository changed unexpectedly during orchestration")
            return False
        if not files:
            self._block(run, "blocked_no_changes", "implementation produced no changed files")
            return False
        run.stage = "implementation_verified"
        self._save(run)
        return True

    def _writer_snapshot(self, run: WorkflowRun) -> tuple[list[str], str]:
        current = repo_state(self.repo)
        if current.repo != run.repo.repo:
            raise ControllerError(
                "writer recovery refused: configured repository differs from saved run"
            )
        if (
            current.branch != run.repo.branch
            or current.head != run.repo.head
            or current.origin != run.repo.origin
        ):
            raise ControllerError(
                "writer recovery refused: branch, HEAD, or origin changed"
            )
        files = changed_files(self.repo)
        return files, working_tree_fingerprint(self.repo, files)

    def _arm_writer_attempt(
        self,
        run: WorkflowRun,
        stage: WriterAttemptStage,
        purpose: WriterAttemptPurpose,
        prompt: str,
        files: list[str],
        fingerprint: str,
    ) -> None:
        run.stage = stage
        run.last_error = None
        operation_id: ProviderOperation = (
            "correction_write"
            if purpose == "correction"
            else "implementation_write"
        )
        self._arm_provider(
            run,
            stage,
            prompt,
            IMPLEMENTATION_ROUTE,
            operation_id,
        )
        run.active_writer_attempt = WriterAttemptState(
            attempt_id=f"writer-{uuid.uuid4().hex[:12]}",
            stage=stage,
            purpose=purpose,
            pre_fingerprint=fingerprint,
            pre_changed_files=files,
        )
        self._save(run)

    def _block_writer_state(
        self,
        run: WorkflowRun,
        detail: str,
    ) -> WorkflowRun:
        active = run.active_writer_attempt
        if active is None or active.inspection_error:
            stage = "blocked_writer_state_unknown"
            state_detail = "repository state could not be determined"
        elif active.post_fingerprint == active.pre_fingerprint:
            stage = "blocked_writer_retry_required"
            state_detail = "repository matches the saved pre-attempt state"
        else:
            stage = "blocked_writer_partial_changes"
            state_detail = "repository differs from the saved pre-attempt state"
        return self._block(
            run,
            stage,
            f"{detail}; {state_detail}; ordinary resume will not invoke Luna",
        )

    def _observe_unrecorded_writer(self, run: WorkflowRun) -> WorkflowRun:
        active = run.active_writer_attempt
        if active is None:
            return self._block(
                run,
                "blocked_writer_state_unknown",
                "legacy or interrupted writer stage lacks pre-attempt evidence; "
                "ordinary resume will not invoke Luna",
            )
        try:
            files, fingerprint = self._writer_snapshot(run)
            active.post_changed_files = files
            active.post_fingerprint = fingerprint
            active.inspection_error = None
        except ControllerError as exc:
            active.inspection_error = str(exc)[:1000]
        self._save(run)
        return self._block_writer_state(
            run,
            "writer outcome was not durably recorded",
        )

    def _execute_armed_writer(self, run: WorkflowRun) -> bool:
        active = run.active_writer_attempt
        prompt = run.provider_resume_prompt
        if active is None or not prompt:
            self._block(
                run,
                "blocked_writer_state_unknown",
                "writer attempt lacks durable state or prompt",
            )
            return False

        label = "Correction" if active.stage == "correcting" else "Implementation"
        console.print(f"[cyan]Stage:[/cyan] {label} — Luna High")
        execution = self.luna(prompt, self.repo)

        post_files: list[str] | None = None
        post_fingerprint: str | None = None
        try:
            post_files, post_fingerprint = self._writer_snapshot(run)
            active.post_changed_files = post_files
            active.post_fingerprint = post_fingerprint
            active.inspection_error = None
        except ControllerError as exc:
            active.inspection_error = str(exc)[:1000]

        execution = _record_provider(
            run,
            (
                "correction_write"
                if active.purpose == "correction"
                else "implementation_write"
            ),
            IMPLEMENTATION_ROUTE,
            execution,
            capability="workspace_write",
            repository_fingerprint_before=active.pre_fingerprint,
            repository_fingerprint_after=post_fingerprint,
        )
        active.provider_record_index = len(run.provider_runs) - 1
        self._save(run)

        if active.inspection_error:
            self._block_writer_state(
                run,
                "writer returned but repository inspection failed: "
                + active.inspection_error,
            )
            return False
        if execution_failed(execution):
            self._block_writer_state(
                run,
                f"Luna High failed during {active.purpose}",
            )
            return False

        completed_stage = (
            "correction_completed"
            if active.stage == "correcting"
            else "implementation_completed"
        )
        self._clear_provider(run)
        run.active_writer_attempt = None
        run.stage = completed_stage
        self._save(run)
        return self._verify(run)

    def _implement(self, run: WorkflowRun, correction: bool = False) -> bool:
        stage: WriterAttemptStage = (
            "correcting" if correction else "implementing"
        )
        purpose: WriterAttemptPurpose = (
            "correction" if correction else "implementation"
        )
        prompt = _task_prompt(run)
        if correction:
            prompt += (
                "\n\nCorrect the implementation for this review finding. "
                "Make the smallest bounded change that addresses it; do not "
                "change policy or perform Git operations.\n"
                + (
                    run.implementation_review.summary
                    if run.implementation_review
                    else ""
                )
            )
            if run.sol_guidance:
                prompt += (
                    "\n\nSol High escalation guidance for this bounded "
                    "correction:\n"
                    + run.sol_guidance
                )
        try:
            files, fingerprint = self._writer_snapshot(run)
        except ControllerError as exc:
            self._block(
                run,
                "blocked_writer_state_unknown",
                f"writer pre-attempt repository inspection failed: {exc}",
            )
            return False
        self._arm_writer_attempt(
            run,
            stage,
            purpose,
            prompt,
            files,
            fingerprint,
        )
        return self._execute_armed_writer(run)

    def _escalate_to_sol(
        self,
        run: WorkflowRun,
        finding_key: str,
        escalation_round: int,
    ) -> bool:
        run.stage = "sol_escalating"
        prompt = _sol_prompt(
            run,
            self.repo,
            finding_key,
            escalation_round,
        )
        self._arm_provider(
            run,
            run.stage,
            prompt,
            ESCALATION_EXECUTIVE_ROUTE,
            "escalation_guidance",
        )
        self._save(run)

        console.print(
            "[cyan]Stage:[/cyan] Escalation diagnosis — Sol High"
        )
        execution = self.sol(prompt, self.repo)
        execution = _record_provider(
            run,
            "escalation_guidance",
            ESCALATION_EXECUTIVE_ROUTE,
            execution,
            capability="read_only",
        )
        self._save(run)

        if execution_failed(execution):
            self._block_provider(
                run,
                ESCALATION_EXECUTIVE_ROUTE,
                "escalation_guidance",
                execution,
            )
            return False

        try:
            parsed = _parse_sol_response(execution)
        except Exception:
            retried = self._retry_structured_once(
                run,
                ESCALATION_EXECUTIVE_ROUTE,
                "escalation_guidance",
                _parse_sol_response,
            )
            if retried is None:
                return False
            parsed = retried

        kind, guidance = parsed
        self._clear_provider(run)
        run.sol_guidance = guidance
        self._save(run)

        if kind == "POLICY_AMBIGUITY":
            self._policy_stop(
                run,
                "Sol High escalation identified policy ambiguity: "
                + guidance,
            )
            return False

        run.stage = "sol_guidance_ready"
        self._save(run)
        return True

    def _review_and_correct(self, run: WorkflowRun) -> WorkflowRun:
        result = run.implementation_review if run.stage == "implementation_reviewed" else self._review(run, "implementation")
        if result is None:
            return run
        if result.category == "PASS":
            run.stage = "awaiting_commit_approval"
            self._approval_request(run, "commit")
            self._save(run)
            return self._approval_gates(run)
        if result.category == "POLICY_AMBIGUITY":
            return self._policy_stop(run, result.summary)

        finding_key = _finding_key(result)
        streak = _current_finding_streak(run, result)
        action = _correction_action(run, streak)
        if action == "block":
            return _block_for_correction_policy(self, run, finding_key)

        # New defect: one ordinary bounded Luna correction.
        if action == "ordinary_correction":
            run.sol_guidance = None
            run.correction_cycles += 1
            run.stage = "correction_pending"
            self._save(run)
            if not self._implement(run, correction=True):
                return run
            return self._review_and_correct(run)

        if action == "sol_guided_correction":
            escalation_round = streak - 1
            if not self._escalate_to_sol(run, finding_key, escalation_round):
                return run
            return self._run_from(run)

        raise ControllerError("resolved correction policy action is unsupported")


    def _approval_gates(self, run: WorkflowRun) -> WorkflowRun:
        self._resume_guard(run)
        if run.stage in {"awaiting_commit_approval", "commit_declined"}:
            request = self._approval_request(run, "commit")
            prior = self._approval_decision_for(run, request)
            if prior is None:
                self._save(run)
            _print_run_report(run)
            console.print("\n[cyan]Commit approval gate[/cyan]")
            console.print("Changed files: " + ", ".join(run.changed_files))
            if run.implementation_review:
                console.print("Review: " + run.implementation_review.summary)
            approved = prior.decision == "approved" if prior is not None else self.approval("Commit these changes?")
            if prior is None:
                self._record_approval_decision(
                    run,
                    request,
                    approved,
                    "Commit approved." if approved else "Commit declined.",
                )
                self._save(run)
            if not approved:
                run.stage = "commit_declined"
                self._save(run)
                console.print("Commit not approved; no Git commit was made.")
                return run
            self._resume_guard(run)
            stageable = _stageable_changed_files(self.repo, run.changed_files)
            if stageable:
                add = _git(self.repo, "add", "-A", "--", *stageable, check=False)
                _record_git(run, "stage changed files", add)
                if add.returncode != 0:
                    return self._block(run, "blocked_git_failure", add.stderr.strip() or "git add failed")
            message = f"Implement task {run.task_ref}"
            commit = _git(self.repo, "commit", "-m", message, check=False)
            _record_git(run, "commit", commit)
            if commit.returncode != 0:
                return self._block(run, "blocked_git_failure", commit.stderr.strip() or "git commit failed")
            run.commit_hash = git_text(self.repo, "rev-parse", "HEAD")
            run.commit_message = message
            run.stage = "awaiting_push_approval"
            self._approval_request(run, "push")
            self._save(run)
        if run.stage in {"awaiting_push_approval", "push_declined"}:
            request = self._approval_request(run, "push")
            prior = self._approval_decision_for(run, request)
            if prior is None:
                self._save(run)
            console.print("\n[cyan]Push approval gate[/cyan]")
            approved = prior.decision == "approved" if prior is not None else self.approval("Push this commit to origin?")
            if prior is None:
                self._record_approval_decision(
                    run,
                    request,
                    approved,
                    "Push approved." if approved else "Push declined.",
                )
                self._save(run)
            if not approved:
                run.stage = "push_declined"
                self._save(run)
                console.print("Push not approved; no Git push was made.")
                return run
            self._resume_guard(run)
            push = _git(self.repo, "push", "origin", run.repo.branch, check=False)
            _record_git(run, "push", push)
            if push.returncode != 0:
                return self._block(run, "blocked_git_failure", push.stderr.strip() or "git push failed")
            run.stage = "pushed_awaiting_merge"
            self._save(run)
            console.print("Pushed. Merge remains a separate manual/future gate.")
            _print_run_report(run)
        return run

    def new_run(self, task_ref: str) -> WorkflowRun:
        state = repo_state(self.repo)
        relative, _, specification = resolve_task(self.repo, task_ref)
        run = WorkflowRun(
            run_id=uuid.uuid4().hex[:12],
            created_at=datetime.now(timezone.utc).isoformat(),
            task_ref=task_ref,
            task_file=relative,
            task_sha256=hashlib.sha256(specification.encode()).hexdigest(),
            specification=specification,
            repo=state,
            resolved_correction_policy=resolve_correction_policy(),
        )
        if not state.clean:
            self._save(run)
            return self._block(run, "blocked_dirty_repo", "jobs repo must be clean before starting")
        coordinator = TargetCoordinator(self.repo, self.runs_dir)
        coordinator.claim_new(run)
        with coordinator.execute(run) as connection:
            result = self._run_from(run)
            return self._finish_owned_action(
                coordinator,
                connection,
                result,
            )

    def _run_from(self, run: WorkflowRun) -> WorkflowRun:
        if run.stage == "created":
            result = self._review(run, "specification")
            if result is None:
                return run
            if result.category == "POLICY_AMBIGUITY":
                return self._policy_stop(run, result.summary)
            if result.category != "PASS":
                return self._block(run, "blocked_spec_review", f"specification review failed: {result.summary}")
        if run.stage == "spec_review_passed":
            if not self._implement(run):
                return run
        if run.stage == "implementation_completed" or run.stage == "correction_completed":
            if not self._verify(run):
                return run
        if run.stage == "sol_guidance_ready":
            if run.implementation_review is None:
                raise ControllerError("Sol guidance lacks an implementation review")
            action = _correction_action(
                run,
                _current_finding_streak(run, run.implementation_review),
            )
            if action == "block":
                return _block_for_correction_policy(
                    self,
                    run,
                    _finding_key(run.implementation_review),
                )
            if action != "sol_guided_correction":
                raise ControllerError("Sol guidance does not match the saved policy")
            run.correction_cycles += 1
            run.stage = "correction_pending"
            self._save(run)
        if run.stage == "correction_pending":
            if not self._implement(run, correction=True):
                return run
        if run.stage == "implementation_verified":
            return self._review_and_correct(run)
        if run.stage == "implementation_reviewed":
            return self._review_and_correct(run)
        if run.stage in {"awaiting_commit_approval", "commit_declined", "awaiting_push_approval", "push_declined"}:
            return self._approval_gates(run)
        return run

    def _resume_guard(self, run: WorkflowRun) -> None:
        current = repo_state(self.repo)
        if current.repo != run.repo.repo:
            raise ControllerError("resume refused: configured repository differs from saved run")
        if current.branch != run.repo.branch or current.origin != run.repo.origin:
            raise ControllerError("resume refused: branch or origin no longer matches saved snapshot")
        if run.commit_hash:
            if current.head != run.commit_hash or not current.clean:
                raise ControllerError("resume refused: committed repository state no longer matches saved run")
        elif current.head != run.repo.head:
            raise ControllerError("resume refused: HEAD no longer matches saved snapshot")
        elif run.working_tree_fingerprint and working_tree_fingerprint(self.repo) != run.working_tree_fingerprint:
            raise ControllerError("resume refused: working tree no longer matches saved run")
        elif run.stage in {"created", "spec_reviewing", "spec_review_passed"} and not current.clean:
            raise ControllerError("resume refused: repository is dirty before implementation")

    def resume(self, run_id: str) -> WorkflowRun:
        run = load_run(run_id, self.runs_dir)
        self._require_executable(run)
        coordinator = TargetCoordinator(self.repo, self.runs_dir)
        if run.target_ownership is None:
            self._resume_guard(run)
            coordinator.claim_legacy(run)
        with coordinator.execute(run) as connection:
            result = self._resume_owned(run)
            return self._finish_owned_action(
                coordinator,
                connection,
                result,
            )

    def _resume_owned(self, run: WorkflowRun) -> WorkflowRun:

        if run.stage in {
            "blocked_writer_retry_required",
            "blocked_writer_partial_changes",
            "blocked_writer_state_unknown",
        }:
            console.print(f"Stage: {run.stage}")
            console.print(
                "Writer recovery requires the explicit recover-writer command; "
                "ordinary resume will not invoke Luna."
            )
            return run

        if run.stage in {"implementing", "correcting"}:
            return self._recover_provider_stage(run)

        if (
            run.stage in _PROVIDER_RESUMABLE_BLOCKS
            and run.provider_resume_stage in {"implementing", "correcting"}
        ):
            return self._resume_blocked_provider(run)

        self._resume_guard(run)

        if (
            run.stage in _PROVIDER_RESUMABLE_BLOCKS
            and run.provider_resume_stage
            and run.provider_resume_prompt
        ):
            return self._resume_blocked_provider(run)

        if (
            run.stage in {"blocked_after_correction", "blocked_after_escalation"}
            and run.implementation_review is not None
            and run.implementation_review.category not in {"PASS", "POLICY_AMBIGUITY"}
        ):
            run.stage = "implementation_reviewed"
            run.last_error = None
            self._save(run)
            return self._review_and_correct(run)
        if run.stage.startswith("blocked") or run.stage == "pushed_awaiting_merge":
            console.print(f"Stage: {run.stage}")
            return run
        if run.stage in {
            "spec_reviewing",
            "reviewing",
            "terra_resolving",
            "sol_escalating",
        }:
            return self._recover_provider_stage(run)
        return self._run_from(run)

    def _recover_saved_writer(self, run: WorkflowRun) -> WorkflowRun:
        active = run.active_writer_attempt
        if active is None:
            return self._block(
                run,
                "blocked_writer_state_unknown",
                "legacy or interrupted writer stage lacks pre-attempt evidence; "
                "ordinary resume will not invoke Luna",
            )
        index = active.provider_record_index
        if index is None:
            return self._observe_unrecorded_writer(run)
        if index >= len(run.provider_runs):
            active.inspection_error = "saved writer provider record is missing"
            self._save(run)
            return self._block_writer_state(
                run,
                "writer audit linkage is invalid",
            )
        record = run.provider_runs[index]
        expected_operation: ProviderOperation = (
            "correction_write"
            if active.purpose == "correction"
            else "implementation_write"
        )
        if (
            _identity_control_key(record.identity)
            != _identity_control_key(IMPLEMENTATION_ROUTE)
            or record.operation_id != expected_operation
            or record.capability != "workspace_write"
        ):
            active.inspection_error = "saved writer provider record does not match"
            self._save(run)
            return self._block_writer_state(
                run,
                "writer audit linkage is invalid",
            )
        if active.post_fingerprint is None and active.inspection_error is None:
            try:
                files, fingerprint = self._writer_snapshot(run)
                active.post_changed_files = files
                active.post_fingerprint = fingerprint
            except ControllerError as exc:
                active.inspection_error = str(exc)[:1000]
            self._save(run)

        execution = normalize_provider_execution(
            ProviderExecution(
                command=record.command,
                returncode=record.returncode,
                stdout=record.stdout,
                stderr=record.stderr,
                duration_seconds=record.duration_seconds,
                failure_kind=record.failure_kind,
                failure_source=record.failure_source,
                failure_code=record.failure_code,
                capability=record.capability,
                repository_fingerprint_before=(
                    record.repository_fingerprint_before
                ),
                repository_fingerprint_after=(
                    record.repository_fingerprint_after
                ),
            )
        )
        if active.inspection_error:
            return self._block_writer_state(
                run,
                "saved writer result has unknown repository state",
            )
        if execution_failed(execution):
            return self._block_writer_state(
                run,
                f"Luna High failed during {active.purpose}",
            )

        completed_stage = (
            "correction_completed"
            if active.stage == "correcting"
            else "implementation_completed"
        )
        self._clear_provider(run)
        run.active_writer_attempt = None
        run.stage = completed_stage
        self._save(run)
        if not self._verify(run):
            return run
        return self._review_and_correct(run)

    def _recover_provider_stage(self, run: WorkflowRun) -> WorkflowRun:
        """Consume a provider result already persisted before a process crash."""

        if run.stage in {"implementing", "correcting"}:
            return self._recover_saved_writer(run)

        if not run.provider_runs:
            return self._block(run, "blocked_interrupted_provider", "provider stage was interrupted before output was recorded")
        record = run.provider_runs[-1]
        expected_provider_run: dict[
            str,
            tuple[ProviderOperation, ProviderRouteIdentity],
        ] = {
            "terra_resolving": (
                "policy_clarification",
                POLICY_AUTHORITY_ROUTE,
            ),
            "sol_escalating": (
                "escalation_guidance",
                ESCALATION_EXECUTIVE_ROUTE,
            ),
            "spec_reviewing": (
                "specification_review",
                ADVERSARIAL_REVIEW_ROUTE,
            ),
            "reviewing": (
                "implementation_review",
                ADVERSARIAL_REVIEW_ROUTE,
            ),
        }[run.stage]
        expected_operation, expected_identity = expected_provider_run
        pending_identity = run.provider_resume_identity
        if (
            record.operation_id != expected_operation
            or _identity_control_key(record.identity)
            != _identity_control_key(expected_identity)
            or pending_identity is None
            or run.provider_resume_operation_id != expected_operation
            or _identity_control_key(pending_identity)
            != _identity_control_key(expected_identity)
        ):
            return self._block(
                run,
                "blocked_interrupted_provider",
                "provider stage was interrupted before matching output was recorded",
            )
        execution = ProviderExecution(
            command=record.command,
            returncode=record.returncode,
            stdout=record.stdout,
            stderr=record.stderr,
            duration_seconds=record.duration_seconds,
            failure_kind=record.failure_kind,
            failure_source=record.failure_source,
            failure_code=record.failure_code,
            capability=record.capability,
            repository_fingerprint_before=(
                record.repository_fingerprint_before
            ),
            repository_fingerprint_after=(
                record.repository_fingerprint_after
            ),
        )
        execution = (
            normalize_sonnet_execution(execution)
            if record.identity.provider_adapter_id == "claude_cli"
            else normalize_provider_execution(execution)
        )
        if execution_failed(execution):
            return self._block_provider(
                run,
                record.identity,
                record.operation_id,
                execution,
            )
        if run.stage in {"spec_reviewing", "reviewing"}:
            last_index = len(run.provider_runs) - 1
            persisted = next(
                (
                    review
                    for review in run.review_records
                    if review.provider_record_index == last_index
                ),
                None,
            )
            if persisted is not None:
                result = persisted.result
            else:
                if any(
                    review.provider_record_index == last_index
                    for review in run.unreadable_review_records
                ):
                    return self._block_provider_output(
                        run,
                        ADVERSARIAL_REVIEW_ROUTE,
                        expected_operation,
                        "review output is unreadable",
                    )
                try:
                    result = parse_sonnet_review(execution)
                except Exception as exc:
                    run.unreadable_review_records.append(
                        UnreadableReviewRecord(
                            recorded_at=datetime.now(timezone.utc).isoformat(),
                            operation_id=expected_operation,
                            provider_record_index=last_index,
                            reason_code=_unreadable_review_reason(exc),
                        )
                    )
                    self._save(run)
                    return self._block_provider_output(
                        run,
                        ADVERSARIAL_REVIEW_ROUTE,
                        expected_operation,
                        str(exc),
                    )
                run.review_records.append(
                    ReviewRecord(
                        recorded_at=datetime.now(timezone.utc).isoformat(),
                        operation_id=expected_operation,
                        result=result,
                        provider_record_index=last_index,
                    )
                )
            if run.stage == "spec_reviewing":
                run.spec_review = result
                run.stage = (
                    "spec_review_passed"
                    if result.category == "PASS"
                    else "spec_review_failed"
                )
                self._clear_provider(run)
                self._save(run)
                if result.category == "POLICY_AMBIGUITY":
                    return self._policy_stop(run, result.summary)
                if result.category != "PASS":
                    return self._block(
                        run,
                        "blocked_spec_review",
                        f"specification review failed: {result.summary}",
                    )
                return self._run_from(run)
            run.implementation_review = result
            run.stage = "implementation_reviewed"
            self._clear_provider(run)
            self._save(run)
            return self._review_and_correct(run)
        if run.stage == "terra_resolving":
            run.terra_resolution = execution.stdout or execution.stderr
            return self._block(run, "blocked_policy_ambiguity", "policy ambiguity requires human approval")
        if run.stage == "sol_escalating":
            try:
                kind, guidance = _parse_sol_response(execution)
            except Exception as exc:
                return self._block_provider_output(
                    run,
                    ESCALATION_EXECUTIVE_ROUTE,
                    "escalation_guidance",
                    str(exc),
                )
            run.sol_guidance = guidance
            self._save(run)
            if kind == "POLICY_AMBIGUITY":
                return self._policy_stop(
                    run,
                    "Sol High escalation identified policy ambiguity: " + guidance,
                )
            run.stage = "sol_guidance_ready"
            self._save(run)
            return self._run_from(run)

        raise ControllerError(f"unsupported recoverable provider stage: {run.stage}")


def _run_read_result(run: WorkflowRun) -> dict[str, object]:
    return {
        "run_id": run.run_id,
        "stage": run.stage,
        "task_ref": run.task_ref,
        "task_file": run.task_file,
        "schema_version": run.schema_version,
    }


def _classification_read_result(classification: RecordClassification) -> dict[str, object]:
    return {
        "run_id": classification.run_id,
        "schema_version": classification.schema_version,
        "record_state": classification.record_state,
        "reason_code": classification.reason_code,
        "task_ref": classification.task_ref,
        "stage": classification.stage,
    }


def _emit_json(
    command: str,
    *,
    result: dict[str, object] | None = None,
    error: dict[str, str] | None = None,
) -> None:
    typer.echo(
        json.dumps(
            {
                "contract_version": CLI_CONTRACT_VERSION,
                "command": command,
                "ok": error is None,
                "result": result,
                "error": error,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )


def _require_json_version(
    command: str,
    json_output: bool,
    json_version: str | None,
) -> None:
    if json_version is None:
        return
    if json_output and json_version == CLI_CONTRACT_VERSION:
        return
    if json_output:
        _emit_json(
            command,
            error={
                "code": "invalid_input",
                "message": "--json-version must be continuo.cli.v1",
            },
        )
    else:
        console.print("[red]BLOCKED:[/red] --json-version requires --json and continuo.cli.v1")
    raise typer.Exit(1)


def _error_code(exc: Exception) -> str:
    message = str(exc).lower()
    if "task-ref" in message or "single safe identifier" in message:
        return "invalid_input"
    if "no task specification matches" in message:
        return "not_found"
    if "unknown run" in message:
        return "not_found"
    if "storage" in message or "private" in message:
        return "storage_unsafe"
    if "migration" in message or "run state is invalid" in message:
        return "record_not_current"
    if "git" in message:
        return "git_unavailable"
    if "repo" in message or "repository" in message or "task" in message:
        return "target_invalid"
    return "precondition_failed"


def _handle_error(
    action: Callable[[], object],
    *,
    command: str | None = None,
    json_output: bool = False,
) -> object | None:
    try:
        if json_output:
            with console.capture():
                return action()
        return action()
    except (ControllerError, OSError, subprocess.SubprocessError) as exc:
        if json_output and command is not None:
            _emit_json(
                command,
                error={"code": _error_code(exc), "message": str(exc)},
            )
            raise typer.Exit(1) from exc
        console.print(f"[red]BLOCKED:[/red] {exc}")
        raise typer.Exit(1) from exc


@app.command("run")
def run_task(
    task_ref: str = typer.Argument(..., help="Task identifier resolved as tasks/<task-ref>-*.md."),
    repo: Path | None = typer.Option(None, "--repo", help="Override the jobs repository path."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Validate and print a read-only execution plan."),
    json_output: bool = typer.Option(False, "--json", help="Emit the versioned machine-readable response."),
    json_version: str | None = typer.Option(None, "--json-version", help="Require a supported machine-output contract version."),
) -> None:
    """Run orchestration through review, correction, and human approval gates."""

    _require_json_version("run", json_output, json_version)
    target = configured_repo(repo)
    if dry_run:
        result = _handle_error(
            lambda: build_dry_run_plan(target, task_ref, RUNS),
            command="run",
            json_output=json_output,
        )
        if result is not None:
            if json_output:
                _emit_json("run", result=result)
            else:
                console.print(json.dumps(result, indent=2, ensure_ascii=False))
        return

    result = _handle_error(
        lambda: Controller(target).new_run(task_ref),
        command="run",
        json_output=json_output,
    )
    if isinstance(result, WorkflowRun):
        if json_output:
            _emit_json("run", result=_run_read_result(result))
        else:
            console.print(f"Run {result.run_id}: {result.stage}")


@app.command()
def resume(
    run_id: str = typer.Argument(..., help="Saved run id."),
    repo: Path | None = typer.Option(None, "--repo", help="Override the jobs repository path."),
    json_output: bool = typer.Option(False, "--json", help="Emit the versioned machine-readable response."),
    json_version: str | None = typer.Option(None, "--json-version", help="Require a supported machine-output contract version."),
) -> None:
    """Safely continue a saved run without repeating completed provider stages."""

    _require_json_version("resume", json_output, json_version)

    def action() -> WorkflowRun:
        saved = load_run(run_id)
        return Controller(configured_repo(repo) if repo else Path(saved.repo.repo)).resume(run_id)

    result = _handle_error(action, command="resume", json_output=json_output)
    if isinstance(result, WorkflowRun):
        if json_output:
            _emit_json("resume", result=_run_read_result(result))
        else:
            console.print(f"Run {result.run_id}: {result.stage}")


@app.command("recover-writer")
def recover_writer_command(
    run_id: str = typer.Argument(..., help="Saved run awaiting writer recovery."),
    action: str = typer.Option(
        ...,
        "--action",
        help="Recovery action: retry-restored or adopt-current.",
    ),
    note: str = typer.Option(
        ...,
        "--note",
        help="Required operator rationale recorded in the run audit.",
    ),
    repo: Path | None = typer.Option(
        None,
        "--repo",
        help="Override the jobs repository path.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit the versioned machine-readable response."),
    json_version: str | None = typer.Option(None, "--json-version", help="Require a supported machine-output contract version."),
) -> None:
    """Explicitly recover a blocked workspace-writing provider attempt."""

    _require_json_version("recover-writer", json_output, json_version)

    def recover() -> WorkflowRun:
        actions: dict[str, WriterRecoveryAction] = {
            "retry-restored": "retry_restored",
            "adopt-current": "adopt_current",
        }
        if action not in actions:
            raise ControllerError(
                "--action must be retry-restored or adopt-current"
            )
        saved = load_run(run_id)
        controller = Controller(
            configured_repo(repo) if repo else Path(saved.repo.repo)
        )
        return controller.recover_writer(run_id, actions[action], note)

    result = _handle_error(recover, command="recover-writer", json_output=json_output)
    if isinstance(result, WorkflowRun):
        if json_output:
            _emit_json("recover-writer", result=_run_read_result(result))
        else:
            console.print(f"Run {result.run_id}: {result.stage}")


@app.command("release-target")
def release_target_command(
    run_id: str = typer.Argument(..., help="Saved blocked or declined run."),
    note: str = typer.Option(
        ...,
        "--note",
        help="Required operator rationale recorded in the ownership audit.",
    ),
    repo: Path | None = typer.Option(
        None,
        "--repo",
        help="Override the jobs repository path.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit the versioned machine-readable response."),
    json_version: str | None = typer.Option(None, "--json-version", help="Require a supported machine-output contract version."),
) -> None:
    """Release a clean target from a deliberately abandoned run."""

    _require_json_version("release-target", json_output, json_version)

    def release() -> WorkflowRun:
        saved = load_run(run_id)
        controller = Controller(
            configured_repo(repo) if repo else Path(saved.repo.repo)
        )
        return controller.release_target(run_id, note)

    result = _handle_error(release, command="release-target", json_output=json_output)
    if isinstance(result, WorkflowRun):
        if json_output:
            _emit_json("release-target", result=_run_read_result(result))
        else:
            console.print(
                f"Run {result.run_id}: target released; workflow remains "
                f"{result.stage}."
            )


@app.command("approve-policy")
def approve_policy_command(
    run_id: str = typer.Argument(..., help="Saved run awaiting human policy approval."),
    decision: str | None = typer.Option(
        None,
        "--decision",
        help="Exact approved policy text. If omitted, use Terra's quoted proposal.",
    ),
    repo: Path | None = typer.Option(None, "--repo", help="Override the jobs repository path."),
    json_output: bool = typer.Option(False, "--json", help="Emit the versioned machine-readable response."),
    json_version: str | None = typer.Option(None, "--json-version", help="Require a supported machine-output contract version."),
) -> None:
    """Record a human-approved Terra clarification without invoking providers."""

    _require_json_version("approve-policy", json_output, json_version)
    if json_output:
        _emit_json(
            "approve-policy",
            error={
                "code": "interactive_confirmation_required",
                "message": "approve-policy requires an interactive human confirmation",
            },
        )
        raise typer.Exit(1)

    def action() -> None:
        saved = load_run(run_id)
        Controller._require_executable(saved)
        if saved.stage != "blocked_policy_ambiguity":
            raise ControllerError("run is not awaiting policy approval")

        approved = decision or _terra_proposed_approval_text(saved.terra_resolution or "")
        if not approved:
            raise ControllerError(
                "could not extract Terra's proposed approval text; use --decision"
            )

        console.print("\n[cyan]Terra recommendation[/cyan]")
        console.print(saved.terra_resolution or "(none)")
        console.print("\n[cyan]Policy text to approve[/cyan]")
        console.print(approved)

        if not typer.confirm(
            "Approve this policy decision and resume orchestration?",
            default=False,
        ):
            controller = Controller(
                configured_repo(repo) if repo else Path(saved.repo.repo)
            )
            controller.decline_policy(run_id)
            console.print("Policy decision not approved; run remains blocked.")
            return

        controller = Controller(
            configured_repo(repo) if repo else Path(saved.repo.repo)
        )

        # Persist the human decision before any provider work resumes.
        result = controller.approve_policy(run_id, approved)
        console.print(
            f"Recorded {result.policy_decisions[-1].decision_id}. "
            "Resuming orchestration."
        )
        console.print(
            "Policy approval does not approve any future commit, push, or merge."
        )

        result = controller.resume(run_id)
        console.print(f"Run {result.run_id}: {result.stage}")

    _handle_error(action)


@app.command()
def report(
    run_id: str = typer.Argument(..., help="Saved run id."),
    json_output: bool = typer.Option(False, "--json", help="Emit the versioned machine-readable response."),
    json_version: str | None = typer.Option(None, "--json-version", help="Require a supported machine-output contract version."),
) -> None:
    """Show a concise orchestration audit and timing report."""

    _require_json_version("report", json_output, json_version)

    def action() -> WorkflowRun:
        preflight = _prepare_private_storage(RUNS)
        if not json_output:
            _print_storage_hardening(preflight)
        path, _, classification = inspect_run_record(run_id, RUNS)
        if (
            classification.record_state != "CURRENT"
            or classification.current_run is None
        ):
            raise _classification_error(classification, path)
        return classification.current_run

    result = _handle_error(action, command="report", json_output=json_output)
    if isinstance(result, WorkflowRun):
        if json_output:
            _emit_json("report", result=_run_report(result))
        else:
            _print_run_report(result)


@app.command("migrate-run")
def migrate_run_command(
    run_id: str = typer.Argument(..., help="Historical saved run id."),
    json_output: bool = typer.Option(False, "--json", help="Emit the versioned machine-readable response."),
    json_version: str | None = typer.Option(None, "--json-version", help="Require a supported machine-output contract version."),
) -> None:
    """Explicitly migrate one historical record; defaults to no rewrite."""

    _require_json_version("migrate-run", json_output, json_version)
    if json_output:
        _emit_json(
            "migrate-run",
            error={
                "code": "interactive_confirmation_required",
                "message": "migrate-run requires an interactive human confirmation",
            },
        )
        raise typer.Exit(1)
    _handle_error(lambda: migrate_run_record(run_id, RUNS))


@app.command()
def status(
    run_id: str | None = typer.Argument(None, help="Run id; omit to list recent runs."),
    json_output: bool = typer.Option(False, "--json", help="Emit the versioned machine-readable response."),
    json_version: str | None = typer.Option(None, "--json-version", help="Require a supported machine-output contract version."),
) -> None:
    """Inspect one saved run or list recent run stages."""

    _require_json_version("status", json_output, json_version)

    def action() -> dict[str, object] | None:
        preflight = _prepare_private_storage(RUNS)
        if not json_output:
            _print_storage_hardening(preflight)
        if run_id:
            _, _, classification = inspect_run_record(run_id, RUNS)
            if (
                classification.record_state == "CURRENT"
                and classification.current_run is not None
            ):
                if json_output:
                    return {"run": _run_read_result(classification.current_run)}
                console.print(classification.current_run.model_dump_json(indent=2))
                return None
            if json_output:
                return {"record": _classification_read_result(classification)}
            console.print(f"Run: {classification.run_id or run_id}")
            console.print(
                f"Schema: {classification.schema_version or 'unsupported'}"
            )
            console.print(
                "Structural class: "
                f"{classification.structural_class or 'unknown'}"
            )
            console.print(f"Record state: {classification.record_state}")
            console.print(f"Reason: {classification.reason_code}")
            if classification.field_path:
                console.print(f"Field: {classification.field_path}")
            console.print(f"Source SHA-256: {classification.source_sha256}")
            return None
        root = preflight.runs_dir
        paths = []
        for entry in os.scandir(root):
            if not entry.name.endswith(".json"):
                continue
            path = root / entry.name
            _secure_regular_file(path)
            paths.append(path)
        paths = sorted(paths, key=lambda p: os.lstat(p).st_mtime, reverse=True)[:10]
        if json_output:
            records: list[dict[str, object]] = []
            for path in paths:
                classification = classify_run_bytes(_read_private_bytes(path).content)
                if classification.record_state == "CURRENT" and classification.current_run is not None:
                    records.append(_run_read_result(classification.current_run))
                else:
                    records.append(_classification_read_result(classification))
            return {"records": records}
        console.print(
            "Record states: CURRENT | MIGRATION_REQUIRED | RESUME_BLOCKED | "
            "ARCHIVE_ONLY | UNSUPPORTED"
        )
        table = Table(
            "Run",
            "Sch",
            "State",
            "Reason",
            "Task",
            "Stage",
            "Own",
            "Corr",
            "Policy",
            box=None,
            padding=(0, 1),
            collapse_padding=True,
        )
        for path in paths:
            classification = classify_run_bytes(_read_private_bytes(path).content)
            run = classification.current_run
            if classification.record_state == "CURRENT" and run is not None:
                ownership = (
                    "legacy"
                    if run.target_ownership is None
                    else (
                        "released"
                        if run.target_ownership.released_at is not None
                        else "active"
                    )
                )
                table.add_row(
                    run.run_id,
                    str(run.schema_version),
                    classification.record_state,
                    classification.reason_code,
                    run.task_ref,
                    run.stage,
                    ownership,
                    str(run.correction_cycles),
                    str(len(run.policy_decisions)),
                )
                continue
            table.add_row(
                classification.run_id or path.stem,
                str(classification.schema_version or "—"),
                classification.record_state,
                classification.reason_code,
                classification.task_ref or "—",
                classification.stage or "—",
                "—",
                "—",
                "—",
            )
        console.print(table)
        return None

    result = _handle_error(action, command="status", json_output=json_output)
    if json_output and isinstance(result, dict):
        _emit_json("status", result=result)


@app.command()
def doctor(
    repo: Path | None = typer.Option(None, "--repo", help="Override the jobs repository path."),
    json_output: bool = typer.Option(False, "--json", help="Emit the versioned machine-readable response."),
    json_version: str | None = typer.Option(None, "--json-version", help="Require a supported machine-output contract version."),
) -> None:
    """Inspect local readiness without repairing state or invoking providers."""

    _require_json_version("doctor", json_output, json_version)
    result = _handle_error(
        lambda: doctor_report(configured_repo(repo), RUNS),
        command="doctor",
        json_output=json_output,
    )
    if not isinstance(result, dict):
        return
    failed = any(check["status"] == "fail" for check in result["checks"])
    if json_output:
        _emit_json("doctor", result=result)
    else:
        for check in result["checks"]:
            console.print(
                f"{check['id']}: {check['status'].upper()} "
                f"({check['code']}) — {check['summary']}"
            )
    if failed:
        raise typer.Exit(1)


if __name__ == "__main__":
    app()
