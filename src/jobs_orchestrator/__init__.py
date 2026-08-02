def main() -> None:
    """Package entry point for the same CLI exposed by orchestrator.py."""

    try:
        from orchestrator import app
    except ModuleNotFoundError:
        # The repository intentionally keeps the directly runnable CLI at its
        # root (`uv run python orchestrator.py`). Neither the src-layout
        # editable path nor a local non-editable install includes that root.
        import importlib.util
        from importlib import metadata
        import json
        from pathlib import Path
        import sys
        from urllib.parse import unquote, urlparse

        candidates = [Path(__file__).resolve().parents[2], Path.cwd()]
        try:
            direct_url = metadata.distribution("jobs-orchestrator").read_text(
                "direct_url.json"
            )
            if direct_url:
                parsed = urlparse(json.loads(direct_url)["url"])
                if parsed.scheme == "file":
                    candidates.append(Path(unquote(parsed.path)))
        except (KeyError, json.JSONDecodeError, metadata.PackageNotFoundError):
            pass

        root_module = next(
            (
                candidate / "orchestrator.py"
                for candidate in candidates
                if (candidate / "orchestrator.py").is_file()
            ),
            None,
        )
        if root_module is None:
            raise ModuleNotFoundError(
                "the jobs-orchestrator compatibility entry point cannot locate "
                "the Continuo source checkout"
            )
        sys.path.insert(0, str(root_module.parent))
        spec = importlib.util.spec_from_file_location("jobs_orchestrator_root_cli", root_module)
        if spec is None or spec.loader is None:
            raise
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        app = module.app

    app()
