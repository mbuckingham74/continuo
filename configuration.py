"""Trusted, deterministic configuration acquisition and resolution."""

from __future__ import annotations

import hashlib
import json
import math
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

import yaml
from pydantic import BaseModel, ValidationError
from yaml.tokens import AliasToken, AnchorToken

from models import (
    OPERATION_ROLES,
    ROUTE_IDENTITIES,
    ConfigurationSourceMetadata,
    EffortPolicy,
    OrchestrationRole,
    ProjectConfiguration,
    ProviderAccountProfile,
    ProviderRouteProfile,
    ResolvedConfiguration,
    ResolvedRoleBinding,
    RoleBindingPolicy,
    RouteAccountSelection,
    RunOverrides,
    UserDefaults,
    resolve_correction_policy,
)


MAX_SOURCE_BYTES = 262_144
MAX_NESTING_DEPTH = 32
CONFIGURATION_ROOT = Path.home() / ".config" / "continuo"
_DIRECTORY_MODE = 0o700
_FILE_MODE = 0o600
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)


class ConfigurationError(RuntimeError):
    """A stable, bounded configuration failure safe for CLI output."""

    def __init__(self, code: str, field_path: str | None = None) -> None:
        self.code = code
        self.field_path = field_path
        super().__init__(code if field_path is None else f"{code}:{field_path}")


def _json_value(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, Mapping):
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


def canonical_json_bytes(value: BaseModel | Mapping[str, Any] | list[Any]) -> bytes:
    payload = _json_value(value)
    try:
        return json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8") + b"\n"
    except (TypeError, ValueError) as exc:
        raise ConfigurationError("configuration_invalid_syntax") from exc


def canonical_sha256(value: BaseModel | Mapping[str, Any] | list[Any]) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _hashed_model(model: type[BaseModel], payload: dict[str, Any], field: str) -> Any:
    complete = dict(payload)
    complete[field] = canonical_sha256(payload)
    return model.model_validate(complete, strict=True)


def _route_profile(role: OrchestrationRole) -> ProviderRouteProfile:
    identity = ROUTE_IDENTITIES[role]
    operations = tuple(
        operation for operation, operation_role in OPERATION_ROLES.items()
        if operation_role == role
    )
    adapter = identity.provider_adapter_id.replace("_", "-")
    payload: dict[str, Any] = {
        "provider_route_profile_schema_version": 2,
        "route_id": identity.route_id,
        "role_id": role,
        "provider_adapter_id": identity.provider_adapter_id,
        "model_id": identity.model_id,
        "operation_ids": operations,
        "command_builder_policy_id": f"{adapter}.compatibility-builder.v1",
        "output_contract_id": f"{adapter}.compatibility-output.v1",
        "capability_profile_id": (
            "continuo.workspace-write.v1"
            if role == "implementation"
            else "continuo.read-only.v1"
        ),
        "prompt_preamble_id": f"continuo.{role}.preamble.v1",
        "supervision_policy_id": "continuo.supervision.v1",
        "retry_policy_id": "continuo.transport-retry.v1",
        "content_retry_policy_id": "continuo.content-retry.v1",
        "effort": {
            "mode": "provider_default",
            "effort_id": None,
            "enforcement_policy_id": f"{adapter}.effort-omission.v1",
        },
    }
    return _hashed_model(ProviderRouteProfile, payload, "route_profile_sha256")


def _account_profile(adapter_id: str) -> ProviderAccountProfile:
    adapter = adapter_id.replace("_", "-")
    profile_id = f"builtin.{adapter}.local-session.v1"
    payload: dict[str, Any] = {
        "provider_account_profile_schema_version": 1,
        "provider_account_profile_contract_id": "continuo.provider-account-profile.v1",
        "provider_account_profile_id": profile_id,
        "provider_adapter_id": adapter_id,
        "authentication_method_id": f"{adapter}.external-cli-session.v1",
        "transport_scope_profile_id": f"{adapter}.official-service.v1",
        "identity_assurance": "controller_profile",
        "remote_principal_id": None,
    }
    return _hashed_model(
        ProviderAccountProfile,
        payload,
        "provider_account_profile_sha256",
    )


ROUTE_PROFILE_CATALOG: Mapping[str, ProviderRouteProfile] = MappingProxyType(
    {
        profile.route_id: profile
        for profile in (_route_profile(role) for role in ROUTE_IDENTITIES)
    }
)
ACCOUNT_PROFILE_CATALOG: Mapping[str, ProviderAccountProfile] = MappingProxyType(
    {
        profile.provider_account_profile_id: profile
        for profile in (
            _account_profile("codex_cli"),
            _account_profile("claude_cli"),
        )
    }
)


def compatibility_selection(role: OrchestrationRole) -> RouteAccountSelection:
    route = ROUTE_PROFILE_CATALOG[ROUTE_IDENTITIES[role].route_id]
    account_id = (
        "builtin.codex-cli.local-session.v1"
        if route.provider_adapter_id == "codex_cli"
        else "builtin.claude-cli.local-session.v1"
    )
    return RouteAccountSelection(
        route_id=route.route_id,
        provider_account_profile_id=account_id,
    )


def _validate_catalogs() -> None:
    if len(ROUTE_PROFILE_CATALOG) != len(ROUTE_IDENTITIES):
        raise ConfigurationError("configuration_catalog_incoherent")
    for role, identity in ROUTE_IDENTITIES.items():
        route = ROUTE_PROFILE_CATALOG.get(identity.route_id)
        if (
            route is None
            or route.role_id != role
            or route.provider_adapter_id != identity.provider_adapter_id
            or route.model_id != identity.model_id
            or route.effort != EffortPolicy(
                mode="provider_default",
                enforcement_policy_id=(
                    f"{identity.provider_adapter_id.replace('_', '-')}.effort-omission.v1"
                ),
            )
        ):
            raise ConfigurationError("configuration_catalog_incoherent", role)
        selection = compatibility_selection(role)
        account = ACCOUNT_PROFILE_CATALOG.get(
            selection.provider_account_profile_id
        )
        if account is None or account.provider_adapter_id != route.provider_adapter_id:
            raise ConfigurationError("configuration_catalog_incoherent", role)


class _UniqueSafeLoader(yaml.SafeLoader):
    pass


def _construct_unique_mapping(
    loader: _UniqueSafeLoader,
    node: yaml.MappingNode,
    deep: bool = False,
) -> dict[Any, Any]:
    result: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in result
        except TypeError as exc:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                "unhashable mapping key",
                key_node.start_mark,
            ) from exc
        if duplicate:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                "duplicate mapping key",
                key_node.start_mark,
            )
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


_UniqueSafeLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


def _json_unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ConfigurationError("configuration_invalid_syntax")
        result[key] = value
    return result


def _reject_json_constant(_: str) -> None:
    raise ConfigurationError("configuration_invalid_syntax")


def _inspect_structure(value: Any, depth: int = 0) -> None:
    if depth > MAX_NESTING_DEPTH:
        raise ConfigurationError("configuration_invalid_syntax")
    if value is None:
        raise ConfigurationError("configuration_invalid_syntax")
    if isinstance(value, float) and not math.isfinite(value):
        raise ConfigurationError("configuration_invalid_syntax")
    if isinstance(value, dict):
        if not all(isinstance(key, str) for key in value):
            raise ConfigurationError("configuration_invalid_syntax")
        for item in value.values():
            _inspect_structure(item, depth + 1)
    elif isinstance(value, list):
        for item in value:
            _inspect_structure(item, depth + 1)


def decode_configuration(data: bytes) -> dict[str, Any]:
    if len(data) > MAX_SOURCE_BYTES:
        raise ConfigurationError("configuration_too_large")
    try:
        text = data.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ConfigurationError("configuration_invalid_utf8") from exc
    try:
        if text.lstrip().startswith(("{", "[")):
            value = json.loads(
                text,
                object_pairs_hook=_json_unique,
                parse_constant=_reject_json_constant,
            )
        else:
            tokens = yaml.scan(text)
            if any(isinstance(token, (AliasToken, AnchorToken)) for token in tokens):
                raise ConfigurationError("configuration_invalid_syntax")
            value = yaml.load(text, Loader=_UniqueSafeLoader)
    except ConfigurationError:
        raise
    except (json.JSONDecodeError, yaml.YAMLError, UnicodeError) as exc:
        raise ConfigurationError("configuration_invalid_syntax") from exc
    if not isinstance(value, dict):
        raise ConfigurationError("configuration_invalid_syntax")
    _inspect_structure(value)
    return value


@dataclass(frozen=True)
class SourceSnapshot:
    controller_root: Path
    components: tuple[str, ...]
    path: Path
    device: int
    inode: int
    size: int
    mtime_ns: int
    canonical_sha256: str
    model: type[BaseModel]


@dataclass(frozen=True)
class ResolvedConfigurationResult:
    configuration: ResolvedConfiguration
    project: ProjectConfiguration
    controller_root: Path
    physical_snapshots: tuple[SourceSnapshot, ...]


def _validate_directory_state(state: os.stat_result) -> None:
    if (
        not stat.S_ISDIR(state.st_mode)
        or state.st_uid != os.geteuid()
        or stat.S_IMODE(state.st_mode) != _DIRECTORY_MODE
    ):
        raise ConfigurationError("configuration_storage_unsafe")


def _open_root(path: Path, *, required: bool) -> int | None:
    try:
        before = path.lstat()
    except FileNotFoundError:
        if required:
            raise ConfigurationError("configuration_missing")
        return None
    except OSError as exc:
        raise ConfigurationError("configuration_storage_unsafe") from exc
    _validate_directory_state(before)
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | _NOFOLLOW)
        opened = os.fstat(descriptor)
        final = path.lstat()
        _validate_directory_state(opened)
        if (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino) or (
            opened.st_dev,
            opened.st_ino,
        ) != (final.st_dev, final.st_ino):
            raise ConfigurationError("configuration_source_changed")
        return descriptor
    except ConfigurationError:
        if "descriptor" in locals():
            os.close(descriptor)
        raise
    except OSError as exc:
        if "descriptor" in locals():
            os.close(descriptor)
        raise ConfigurationError("configuration_storage_unsafe") from exc


def _open_child_directory(
    parent: int,
    name: str,
    *,
    required: bool,
) -> int | None:
    if not name or name in {".", ".."} or "/" in name:
        raise ConfigurationError("configuration_storage_unsafe")
    try:
        before = os.stat(name, dir_fd=parent, follow_symlinks=False)
    except FileNotFoundError:
        if required:
            raise ConfigurationError("configuration_missing")
        return None
    except OSError as exc:
        raise ConfigurationError("configuration_storage_unsafe") from exc
    _validate_directory_state(before)
    descriptor = -1
    try:
        descriptor = os.open(
            name,
            os.O_RDONLY | os.O_DIRECTORY | _NOFOLLOW,
            dir_fd=parent,
        )
        opened = os.fstat(descriptor)
        final = os.stat(name, dir_fd=parent, follow_symlinks=False)
        _validate_directory_state(opened)
        if (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino) or (
            opened.st_dev,
            opened.st_ino,
        ) != (final.st_dev, final.st_ino):
            raise ConfigurationError("configuration_source_changed")
        return descriptor
    except ConfigurationError:
        if descriptor >= 0:
            os.close(descriptor)
        raise
    except OSError as exc:
        if descriptor >= 0:
            os.close(descriptor)
        raise ConfigurationError("configuration_storage_unsafe") from exc


def _entry_exists(parent: int, name: str) -> bool:
    try:
        os.stat(name, dir_fd=parent, follow_symlinks=False)
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise ConfigurationError("configuration_storage_unsafe") from exc
    return True


def _read_source_descriptor(
    *,
    controller_root: Path,
    components: tuple[str, ...],
    parent: int,
    model: type[BaseModel],
) -> tuple[BaseModel, SourceSnapshot]:
    name = components[-1]
    path = controller_root.joinpath(*components)
    try:
        before = os.stat(name, dir_fd=parent, follow_symlinks=False)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.geteuid()
            or before.st_nlink != 1
            or stat.S_IMODE(before.st_mode) != _FILE_MODE
        ):
            raise ConfigurationError("configuration_storage_unsafe")
        descriptor = os.open(name, os.O_RDONLY | _NOFOLLOW, dir_fd=parent)
    except FileNotFoundError as exc:
        raise ConfigurationError("configuration_missing") from exc
    except ConfigurationError:
        raise
    except OSError as exc:
        raise ConfigurationError("configuration_storage_unsafe") from exc
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_uid != os.geteuid()
            or opened.st_nlink != 1
            or stat.S_IMODE(opened.st_mode) != _FILE_MODE
            or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
            or opened.st_size > MAX_SOURCE_BYTES
        ):
            raise ConfigurationError("configuration_storage_unsafe")
        data = bytearray()
        while len(data) <= MAX_SOURCE_BYTES:
            chunk = os.read(descriptor, min(65_536, MAX_SOURCE_BYTES + 1 - len(data)))
            if not chunk:
                break
            data.extend(chunk)
        if len(data) > MAX_SOURCE_BYTES:
            raise ConfigurationError("configuration_too_large")
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    try:
        final = os.stat(name, dir_fd=parent, follow_symlinks=False)
    except OSError as exc:
        raise ConfigurationError("configuration_source_changed") from exc
    stable = (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns)
    if stable != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) or (
        opened.st_dev,
        opened.st_ino,
    ) != (final.st_dev, final.st_ino):
        raise ConfigurationError("configuration_source_changed")
    decoded = decode_configuration(bytes(data))
    try:
        # The models are globally strict. The one intentional collection
        # exception is JSON/YAML arrays materialized as immutable tuples.
        parsed = model.model_validate(decoded)
    except ValidationError as exc:
        location = ".".join(str(item) for item in exc.errors()[0].get("loc", ()))
        code = (
            "configuration_schema_unsupported"
            if location.endswith("schema_version")
            else "configuration_binding_mismatch"
        )
        raise ConfigurationError(code, location or None) from exc
    payload_hash = canonical_sha256(parsed)
    return parsed, SourceSnapshot(
        controller_root=controller_root,
        components=components,
        path=path,
        device=opened.st_dev,
        inode=opened.st_ino,
        size=opened.st_size,
        mtime_ns=opened.st_mtime_ns,
        canonical_sha256=payload_hash,
        model=model,
    )


def _read_source_at(
    controller_root: Path,
    components: tuple[str, ...],
    model: type[BaseModel],
) -> tuple[BaseModel, SourceSnapshot]:
    root = _open_root(controller_root, required=True)
    assert root is not None
    opened = [root]
    try:
        parent = root
        for component in components[:-1]:
            child = _open_child_directory(parent, component, required=True)
            assert child is not None
            opened.append(child)
            parent = child
        return _read_source_descriptor(
            controller_root=controller_root,
            components=components,
            parent=parent,
            model=model,
        )
    finally:
        for descriptor in reversed(opened):
            os.close(descriptor)


def builtin_project_configuration(
    target_key: str,
    canonical_repo: str,
) -> ProjectConfiguration:
    bindings = {
        role: RoleBindingPolicy(
            permitted_bindings=(compatibility_selection(role),),
            default_binding=compatibility_selection(role),
        )
        for role in ROUTE_IDENTITIES
    }
    return ProjectConfiguration(
        project_configuration_id=f"project-config-v2:{target_key}",
        target_binding={
            "target_key": target_key,
            "canonical_repo": canonical_repo,
        },
        role_bindings=bindings,
        policy={"correction_policy_id": "builtin.correction_escalation.v1"},
    )


def _project_and_sources(
    root: Path,
    target_key: str,
    canonical_repo: str,
) -> tuple[
    ProjectConfiguration,
    ConfigurationSourceMetadata,
    UserDefaults | None,
    ConfigurationSourceMetadata,
    tuple[SourceSnapshot, ...],
]:
    snapshots: list[SourceSnapshot] = []
    if not root.is_absolute():
        raise ConfigurationError("configuration_storage_unsafe")
    root_fd = _open_root(root, required=False)
    user_defaults: UserDefaults | None = None
    user_metadata = ConfigurationSourceMetadata(source_kind="absent")
    project: ProjectConfiguration | None = None
    project_metadata: ConfigurationSourceMetadata | None = None
    if root_fd is not None:
        opened: list[int] = [root_fd]
        try:
            if _entry_exists(root_fd, "user-defaults.yaml"):
                parsed, snapshot = _read_source_descriptor(
                    controller_root=root,
                    components=("user-defaults.yaml",),
                    parent=root_fd,
                    model=UserDefaults,
                )
                user_defaults = parsed  # type: ignore[assignment]
                snapshots.append(snapshot)
                user_metadata = ConfigurationSourceMetadata(
                    source_kind="private_file",
                    schema_version=2,
                    source_id="continuo.user-defaults.v2",
                    payload_sha256=snapshot.canonical_sha256,
                )
            projects_fd = _open_child_directory(
                root_fd,
                "projects",
                required=False,
            )
            if projects_fd is not None:
                opened.append(projects_fd)
                target_fd = _open_child_directory(
                    projects_fd,
                    target_key,
                    required=False,
                )
                if target_fd is not None:
                    opened.append(target_fd)
                    parsed, snapshot = _read_source_descriptor(
                        controller_root=root,
                        components=(
                            "projects",
                            target_key,
                            "project-configuration.yaml",
                        ),
                        parent=target_fd,
                        model=ProjectConfiguration,
                    )
                    project = parsed  # type: ignore[assignment]
                    snapshots.append(snapshot)
                    project_metadata = ConfigurationSourceMetadata(
                        source_kind="private_file",
                        schema_version=2,
                        source_id=project.project_configuration_id,
                        target_key=target_key,
                        canonical_repo=canonical_repo,
                        payload_sha256=snapshot.canonical_sha256,
                    )
        finally:
            for descriptor in reversed(opened):
                os.close(descriptor)

    if project is None:
        project = builtin_project_configuration(target_key, canonical_repo)
        project_metadata = ConfigurationSourceMetadata(
            source_kind="builtin_compatibility",
            schema_version=2,
            source_id=project.project_configuration_id,
            target_key=target_key,
            canonical_repo=canonical_repo,
            payload_sha256=canonical_sha256(project),
        )
    assert project_metadata is not None
    return project, project_metadata, user_defaults, user_metadata, tuple(snapshots)


def _validate_project_target(
    project: ProjectConfiguration,
    target_key: str,
    canonical_repo: str,
) -> None:
    if (
        project.target_binding.target_key != target_key
        or project.target_binding.canonical_repo != canonical_repo
        or project.project_configuration_id != f"project-config-v2:{target_key}"
    ):
        raise ConfigurationError("configuration_binding_mismatch", "target_binding")


def _lookup_binding(
    role: OrchestrationRole,
    selection: RouteAccountSelection,
) -> tuple[ProviderRouteProfile, ProviderAccountProfile]:
    route = ROUTE_PROFILE_CATALOG.get(selection.route_id)
    if route is None:
        raise ConfigurationError("configuration_route_unavailable", role)
    account = ACCOUNT_PROFILE_CATALOG.get(selection.provider_account_profile_id)
    if account is None:
        raise ConfigurationError("configuration_account_unavailable", role)
    if route.role_id != role or route.provider_adapter_id != account.provider_adapter_id:
        raise ConfigurationError("configuration_binding_mismatch", role)
    return route, account


def resolve_configuration(
    *,
    target_key: str,
    canonical_repo: str,
    controller_root: Path = CONFIGURATION_ROOT,
    run_overrides: RunOverrides | None = None,
) -> ResolvedConfigurationResult:
    """Resolve all authority from fixed trusted sources without mutating them."""

    _validate_catalogs()
    project, project_source, defaults, defaults_source, snapshots = (
        _project_and_sources(controller_root, target_key, canonical_repo)
    )
    _validate_project_target(project, target_key, canonical_repo)
    overrides = run_overrides
    overrides_source = (
        ConfigurationSourceMetadata(source_kind="absent")
        if overrides is None
        else ConfigurationSourceMetadata(
            source_kind="typed",
            schema_version=2,
            source_id="continuo.run-overrides.v2",
            payload_sha256=canonical_sha256(overrides),
        )
    )
    resolved: dict[OrchestrationRole, ResolvedRoleBinding] = {}
    account_hashes: dict[OrchestrationRole, str] = {}
    for role in ROUTE_IDENTITIES:
        policy = project.role_bindings[role]
        if overrides is not None and role in overrides.role_bindings:
            selection = overrides.role_bindings[role]
            source = "run_override"
        elif defaults is not None and role in defaults.role_bindings:
            selection = defaults.role_bindings[role]
            source = "user_default"
        else:
            selection = policy.default_binding
            source = "project_default"
        if selection not in policy.permitted_bindings:
            raise ConfigurationError("configuration_binding_unpermitted", role)
        route, account = _lookup_binding(role, selection)
        resolved[role] = ResolvedRoleBinding(
            selection_source=source,
            route_profile=route,
            provider_account_profile=account,
        )
        account_hashes[role] = account.provider_account_profile_sha256
    payload: dict[str, Any] = {
        "resolved_configuration_schema_version": 2,
        "profile_id": project.profile_id,
        "role_bindings": resolved,
        "correction_policy": resolve_correction_policy(),
        "project_source": project_source,
        "user_defaults_source": defaults_source,
        "run_overrides_source": overrides_source,
        "selected_account_profile_hashes": account_hashes,
    }
    configuration = _hashed_model(
        ResolvedConfiguration,
        payload,
        "configuration_sha256",
    )
    return ResolvedConfigurationResult(
        configuration=configuration,
        project=project,
        controller_root=controller_root,
        physical_snapshots=snapshots,
    )


def revalidate_sources(result: ResolvedConfigurationResult) -> None:
    """Prove each physical source is unchanged immediately before persistence."""

    for snapshot in result.physical_snapshots:
        parsed, current = _read_source_at(
            snapshot.controller_root,
            snapshot.components,
            snapshot.model,
        )
        del parsed
        if (
            current.device,
            current.inode,
            current.size,
            current.mtime_ns,
            current.canonical_sha256,
        ) != (
            snapshot.device,
            snapshot.inode,
            snapshot.size,
            snapshot.mtime_ns,
            snapshot.canonical_sha256,
        ):
            raise ConfigurationError("configuration_source_changed")
    validate_saved_configuration(
        result.configuration,
        target_key=result.project.target_binding.target_key,
        canonical_repo=result.project.target_binding.canonical_repo,
        controller_root=result.controller_root,
    )


def validate_saved_configuration(
    configuration: ResolvedConfiguration,
    *,
    target_key: str,
    canonical_repo: str,
    controller_root: Path = CONFIGURATION_ROOT,
) -> None:
    """Revalidate only resume-invalidating project and catalog authority."""

    _validate_catalogs()
    if (
        configuration.project_source.target_key != target_key
        or configuration.project_source.canonical_repo != canonical_repo
    ):
        raise ConfigurationError("configuration_binding_mismatch", "project_source")
    for role, binding in configuration.role_bindings.items():
        route = ROUTE_PROFILE_CATALOG.get(binding.route_profile.route_id)
        account = ACCOUNT_PROFILE_CATALOG.get(
            binding.provider_account_profile.provider_account_profile_id
        )
        if route != binding.route_profile or account != binding.provider_account_profile:
            raise ConfigurationError("configuration_catalog_incoherent", role)
    root_fd = _open_root(controller_root, required=False)
    project_exists = False
    if root_fd is not None:
        opened = [root_fd]
        try:
            projects_fd = _open_child_directory(
                root_fd,
                "projects",
                required=False,
            )
            if projects_fd is not None:
                opened.append(projects_fd)
                target_fd = _open_child_directory(
                    projects_fd,
                    target_key,
                    required=False,
                )
                if target_fd is not None:
                    opened.append(target_fd)
                    project_exists = True
        finally:
            for descriptor in reversed(opened):
                os.close(descriptor)
    source = configuration.project_source
    if source.source_kind == "builtin_compatibility":
        if project_exists:
            raise ConfigurationError("configuration_source_changed")
        builtin = builtin_project_configuration(target_key, canonical_repo)
        if canonical_sha256(builtin) != source.payload_sha256:
            raise ConfigurationError("configuration_hash_incoherent")
        return
    if source.source_kind != "private_file" or not project_exists:
        raise ConfigurationError("configuration_source_changed")
    try:
        project, _ = _read_source_at(
            controller_root,
            ("projects", target_key, "project-configuration.yaml"),
            ProjectConfiguration,
        )
    except ConfigurationError as exc:
        raise ConfigurationError("configuration_source_changed") from exc
    _validate_project_target(project, target_key, canonical_repo)  # type: ignore[arg-type]
    if canonical_sha256(project) != source.payload_sha256:
        raise ConfigurationError("configuration_source_changed")


def dry_run_configuration_summary(
    configuration: ResolvedConfiguration,
) -> dict[str, Any]:
    return {
        "profile_id": configuration.profile_id,
        "role_bindings": {
            role: {
                "route_id": binding.route_profile.route_id,
                "provider_account_profile_id": (
                    binding.provider_account_profile.provider_account_profile_id
                ),
                "effort_mode": binding.route_profile.effort.mode,
            }
            for role, binding in configuration.role_bindings.items()
        },
        "sources": {
            "project": {
                "kind": configuration.project_source.source_kind,
                "sha256": configuration.project_source.payload_sha256,
            },
            "user_defaults": {
                "kind": configuration.user_defaults_source.source_kind,
                "sha256": configuration.user_defaults_source.payload_sha256,
            },
            "run_overrides": {
                "kind": configuration.run_overrides_source.source_kind,
                "sha256": configuration.run_overrides_source.payload_sha256,
            },
        },
        "correction_policy_id": configuration.correction_policy.policy_id,
        "configuration_sha256": configuration.configuration_sha256,
    }
