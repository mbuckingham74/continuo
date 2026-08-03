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
  - [`gate-2.6-q3-approval-records.md`](gate-2/gate-2.6-q3-approval-records.md) is the draft approval-record contract awaiting owner approval.

Do not put a new full contract, matrix, or implementation transcript back in
the execution tracker. Keep the tracker to status, a concise result, and a
link to this directory.
