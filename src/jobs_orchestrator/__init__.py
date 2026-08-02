def main() -> None:
    """Package entry point for the same CLI exposed by orchestrator.py."""

    try:
        from orchestrator import app
    except ModuleNotFoundError:
        # The repository intentionally keeps the directly runnable CLI at its
        # root (`uv run python orchestrator.py`). The editable project install
        # used by `uv run jobs-orchestrator` does not put that root on sys.path.
        import importlib.util
        from pathlib import Path
        import sys

        root_module = Path(__file__).resolve().parents[2] / "orchestrator.py"
        sys.path.insert(0, str(root_module.parent))
        spec = importlib.util.spec_from_file_location("jobs_orchestrator_root_cli", root_module)
        if spec is None or spec.loader is None:
            raise
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        app = module.app

    app()
