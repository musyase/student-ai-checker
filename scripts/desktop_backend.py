"""PyInstaller entry point for the Tauri backend sidecar."""

from __future__ import annotations

import argparse
import runpy
import sys
from pathlib import Path

from student_ai_checker.local_app import serve


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "check":
        from student_ai_checker.cli import main as cli_main

        raise SystemExit(cli_main())
    if len(sys.argv) > 1 and sys.argv[1] == "batch":
        bundle_root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
        batch_script = bundle_root / "scripts" / "run_checked_batch.py"
        sys.argv = [str(batch_script), *sys.argv[2:]]
        runpy.run_path(str(batch_script), run_name="__main__")
        return
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    serve(host=args.host, port=args.port, open_browser=not args.no_browser)


if __name__ == "__main__":
    main()
