# Continuo contracts

[`ENGINE_ROADMAP.md`](../ENGINE_ROADMAP.md) is authoritative for priorities,
scope, and sequencing. [`EXECUTION_PLAN.md`](../EXECUTION_PLAN.md) is the
concise tracker for gate status and links.

This directory holds the detailed, bounded contracts for individual approved
work items: invariants, persistence and migration decisions, adversarial test
matrices, validation evidence, and publication evidence. New contracts belong
under `gate-<number>/` and must be linked from the corresponding tracker item.

- [`gate-1/`](gate-1/) contains Milestone 0 engine-stabilization contracts.
- [`gate-2/`](gate-2/) contains Milestone 1 persisted-contract work.
  - [`gate-2.6-q3-approval-records.md`](gate-2/gate-2.6-q3-approval-records.md) is the published approval-record contract.
  - [`gate-2.7-q7-q8-metric-semantics.md`](gate-2/gate-2.7-q7-q8-metric-semantics.md) is the published provider-metric semantics contract.
  - [`gate-2.8-machine-cli-doctor-dry-run.md`](gate-2/gate-2.8-machine-cli-doctor-dry-run.md) is the published versioned machine-CLI, diagnostic, and read-only planning contract.
  - [`gate-2.9-event-state-architecture-adr.md`](gate-2/gate-2.9-event-state-architecture-adr.md) is the accepted event/state architecture decision record.
- [`gate-3/`](gate-3/) contains proposed Generic Engine Core contracts.
  - [`gate-3.1-versioned-resolved-configuration.md`](gate-3/gate-3.1-versioned-resolved-configuration.md) defines the proposed versioned configuration-resolution contract.
  - [`gate-3.2-trusted-project-configuration.md`](gate-3/gate-3.2-trusted-project-configuration.md) defines the proposed trusted project-configuration boundary.
  - [`gate-3.3-normalized-task-envelope.md`](gate-3/gate-3.3-normalized-task-envelope.md) defines the proposed immutable normalized task contract.

Do not put a new full contract, matrix, or implementation transcript back in
the execution tracker. Keep the tracker to status, a concise result, and a
link to this directory.
