# Continuo configuration (schema 2)

Continuo resolves and persists a complete schema-2 configuration before it
creates target ownership state or invokes a provider. The initial catalog is
compatibility-only: it contains the four existing routes and the externally
managed Codex and Claude CLI-session account profiles. Every compatibility
route records effort as `provider_default`; display names containing `High` are
not effort evidence.

## Fixed trusted locations

Production reads only:

```text
~/.config/continuo/user-defaults.yaml
~/.config/continuo/projects/<target-key>/project-configuration.yaml
```

There is deliberately no environment variable or CLI option for changing this
root. Every existing directory in that tree must be owned by the effective user
with exact mode `0700`. Each source must be an owner-owned, single-linked
regular file with exact mode `0600`. Continuo reads these sources without
creating, repairing, chmodding, or rewriting them.

If the root or exact per-target directory is absent, Continuo uses a
deterministic target-bound `continuo.jobs-compat.v1` project configuration. An
existing exact target directory without `project-configuration.yaml` is an
incomplete installation and fails closed. Gate 4.5 will add controller-owned
setup commands and the Rust TUI; manual installation is intentionally an
advanced interim path.

## User defaults

The optional file can choose only a complete registered route/account pair:

```yaml
user_defaults_schema_version: 2
role_bindings:
  implementation:
    route_id: builtin.implementation.v1
    provider_account_profile_id: builtin.codex-cli.local-session.v1
```

Omitted roles use the project default. A present but invalid or unpermitted
selection fails; Continuo never falls back from an invalid higher-precedence
value.

## Project configuration

The target key is derived from the resolved Git root's device and inode. To
inspect the exact identity while preparing an interim source:

```sh
uv run python -c 'import sys; from pathlib import Path; from orchestrator import target_identity; print(target_identity(Path(sys.argv[1]).resolve()).target_key)' /absolute/path/to/target
```

The physical project file has this complete shape (replace both occurrences of
`<target-key>` and the canonical repository path exactly):

```yaml
project_configuration_schema_version: 2
project_configuration_id: project-config-v2:<target-key>
target_binding:
  target_key: <target-key>
  canonical_repo: /absolute/resolved/git/root
profile_id: continuo.jobs-compat.v1
role_bindings:
  implementation:
    permitted_bindings:
      - route_id: builtin.implementation.v1
        provider_account_profile_id: builtin.codex-cli.local-session.v1
    default_binding:
      route_id: builtin.implementation.v1
      provider_account_profile_id: builtin.codex-cli.local-session.v1
  adversarial_review:
    permitted_bindings:
      - route_id: builtin.adversarial_review.v1
        provider_account_profile_id: builtin.claude-cli.local-session.v1
    default_binding:
      route_id: builtin.adversarial_review.v1
      provider_account_profile_id: builtin.claude-cli.local-session.v1
  escalation_executive:
    permitted_bindings:
      - route_id: builtin.escalation_executive.v1
        provider_account_profile_id: builtin.codex-cli.local-session.v1
    default_binding:
      route_id: builtin.escalation_executive.v1
      provider_account_profile_id: builtin.codex-cli.local-session.v1
  policy_authority:
    permitted_bindings:
      - route_id: builtin.policy_authority.v1
        provider_account_profile_id: builtin.codex-cli.local-session.v1
    default_binding:
      route_id: builtin.policy_authority.v1
      provider_account_profile_id: builtin.codex-cli.local-session.v1
policy:
  correction_policy_id: builtin.correction_escalation.v1
```

JSON is also accepted in these `.yaml` files. Both decoders reject duplicate
keys, YAML aliases/anchors/custom tags, nulls, unknown fields, type coercion,
non-finite numbers, excessive nesting, and sources larger than 262,144 bytes.
Semantically equivalent JSON and YAML produce the same canonical SHA-256.

## Resolution and run behavior

Selection precedence is typed run override, user default, then project default.
Gate 4.2 exposes run overrides only to the internal typed API; there is no
public free-form override flag yet. All four resolved bindings, route and
account authority payloads, effort policies, source hashes, correction policy,
and the configuration hash are frozen in an ordinary schema-13 run.

`run --dry-run` emits the redacted `continuo.run-plan.v2` configuration summary.
`doctor` emits `continuo.doctor.v2` configuration readiness. Neither command
creates or repairs configuration. Resume revalidates the saved project-source
condition and built-in catalogs, but does not reread user defaults or typed run
overrides; those remain frozen provenance.
