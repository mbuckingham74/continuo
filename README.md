## Jobs orchestrator

Run one task from the jobs repository (default: `~/Documents/my-apps/jobs`):

```sh
uv run python orchestrator.py run 009
```

The controller resolves exactly one `tasks/009-*.md`, snapshots the clean Git
state, runs the read-only Sonnet reviews and bounded Luna implementation, and
persists state in `runs/`. Commit and push are separate prompts and both
default to no. Set `JOBS_REPO` or pass `--repo` to use another jobs checkout.

Useful commands:

```sh
uv run python orchestrator.py status
uv run python orchestrator.py resume <run-id>
```
