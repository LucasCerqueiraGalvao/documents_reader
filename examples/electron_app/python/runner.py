"""Standalone pipeline runner entrypoint for packaging.

This script is intended to be frozen with PyInstaller into a single binary,
and then embedded into the Electron app via electron-builder extraResources.

It runs the same pipeline as `src/pipeline.py` but is easier to bundle.
"""

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Optional


def _find_repo_root(start: Path) -> Optional[Path]:
    p = start
    for _ in range(6):
        if (p / "src" / "pipeline.py").exists():
            return p
        p = p.parent
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description="Documents Reader Pipeline (bundled)")
    ap.add_argument("--input", required=True, help="Input base dir (contains importation/raw)")
    ap.add_argument("--output", required=True, help="Output base dir")
    ap.add_argument("--flow", default="importation", choices=["importation", "exportation"])
    ap.add_argument("--ocr-lang", default="eng+por")
    ap.add_argument("--ocr-dpi", type=int, default=300)
    ap.add_argument("--min-chars", type=int, default=80)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    here = Path(__file__).resolve()
    repo = _find_repo_root(here)

    # When frozen, PyInstaller unpacks to a temp dir; in that case, repo root won't exist.
    # We rely on PyInstaller bundling the modules, but we also support dev runs.
    if repo is not None:
        sys.path.insert(0, str(repo / "src"))

    try:
        from pipeline import PipelineConfig, run_pipeline  # type: ignore

        config = PipelineConfig(
            input_dir=Path(args.input),
            output_dir=Path(args.output),
            flow=args.flow,
            ocr_lang=args.ocr_lang,
            ocr_dpi=args.ocr_dpi,
            min_chars=args.min_chars,
        )

        result = run_pipeline(config)

        if args.json:
            print(json.dumps(asdict(result), ensure_ascii=False))
        else:
            print("OK" if result.success else "FAIL")

        return 0 if result.success else 2

    except Exception as e:
        err = {"success": False, "error": str(e)}
        print(json.dumps(err, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
