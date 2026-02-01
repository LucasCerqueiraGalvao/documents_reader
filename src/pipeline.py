"""
Pipeline orchestrator for document processing
Provides both programmatic API and CLI interface
"""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any, Optional

from stage_01_text_extract.extract_text_importation import run_stage_01_extraction
from stage_02_field_extract.importation.extract_fields_importation import (
    run_stage_02_extraction,
)
from stage_03_compare_docs.compare_importation import run_stage_03_comparison
from stage_04_report.generate_report_importation import run_stage_04_report


@dataclass
class PipelineConfig:
    input_dir: Path
    output_dir: Path
    flow: str = "importation"
    ocr_lang: str = "eng+por"
    ocr_dpi: int = 300
    min_chars: int = 80


@dataclass
class PipelineResult:
    success: bool
    flow: str
    stages_completed: List[str]
    output_files: Dict[str, str]
    errors: List[str]
    warnings: List[str]
    metadata: Dict[str, Any]
    completed_at: str


def run_pipeline(config: PipelineConfig) -> PipelineResult:
    """
    Execute full document processing pipeline

    Args:
        config: Pipeline configuration

    Returns:
        PipelineResult with status and output paths
    """
    errors: List[str] = []
    warnings: List[str] = []
    stages_completed: List[str] = []
    output_files: Dict[str, str] = {}

    try:
        # Stage 01: Text extraction
        stage01_out = config.output_dir / "stage_01_text" / config.flow
        result_01 = run_stage_01_extraction(
            in_dir=config.input_dir / config.flow / "raw",
            out_dir=stage01_out,
            ocr_lang=config.ocr_lang,
            ocr_dpi=config.ocr_dpi,
            min_chars=config.min_chars,
        )
        stages_completed.append("stage_01_text_extract")
        output_files["stage_01"] = str(stage01_out)
        warnings.extend(result_01.get("warnings", []))

        # Stage 02: Field extraction
        stage02_out = config.output_dir / "stage_02_fields" / config.flow
        result_02 = run_stage_02_extraction(in_dir=stage01_out, out_dir=stage02_out)
        stages_completed.append("stage_02_field_extract")
        output_files["stage_02"] = str(stage02_out)
        warnings.extend(result_02.get("warnings", []))

        # Stage 03: Document comparison
        stage03_out = config.output_dir / "stage_03_compare" / config.flow
        result_03 = run_stage_03_comparison(in_dir=stage02_out, out_dir=stage03_out)
        stages_completed.append("stage_03_compare")
        output_files["stage_03"] = str(stage03_out / "_stage03_comparison.json")
        warnings.extend(result_03.get("warnings", []))

        # Stage 04: Report generation
        stage04_out = config.output_dir / "stage_04_report" / config.flow
        result_04 = run_stage_04_report(
            stage01_dir=stage01_out,
            stage02_dir=stage02_out,
            stage03_file=stage03_out / "_stage03_comparison.json",
            out_dir=stage04_out,
        )
        stages_completed.append("stage_04_report")
        output_files["stage_04_json"] = str(stage04_out / "_stage04_report.json")
        output_files["stage_04_html"] = str(stage04_out / "_stage04_report.html")
        output_files["stage_04_md"] = str(stage04_out / "_stage04_report.md")

        return PipelineResult(
            success=True,
            flow=config.flow,
            stages_completed=stages_completed,
            output_files=output_files,
            errors=errors,
            warnings=warnings,
            metadata={
                "documents_processed": result_01.get("processed_count", 0),
                "ocr_lang": config.ocr_lang,
                "ocr_dpi": config.ocr_dpi,
            },
            completed_at=datetime.now().isoformat(),
        )

    except Exception as e:
        errors.append(str(e))
        return PipelineResult(
            success=False,
            flow=config.flow,
            stages_completed=stages_completed,
            output_files=output_files,
            errors=errors,
            warnings=warnings,
            metadata={},
            completed_at=datetime.now().isoformat(),
        )


def run_pipeline_from_dict(params: Dict[str, Any]) -> Dict[str, Any]:
    """
    Run pipeline from dictionary parameters (for JSON/HTTP API)

    Args:
        params: {
            "input_dir": "/path/to/input",
            "output_dir": "/path/to/output",
            "flow": "importation",
            "ocr_lang": "eng+por",
            "ocr_dpi": 300,
            "min_chars": 80
        }

    Returns:
        Dictionary with pipeline result
    """
    config = PipelineConfig(
        input_dir=Path(params["input_dir"]),
        output_dir=Path(params["output_dir"]),
        flow=params.get("flow", "importation"),
        ocr_lang=params.get("ocr_lang", "eng+por"),
        ocr_dpi=params.get("ocr_dpi", 300),
        min_chars=params.get("min_chars", 80),
    )

    result = run_pipeline(config)
    return asdict(result)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Document Processing Pipeline")
    parser.add_argument("--input", required=True, help="Input directory with raw PDFs")
    parser.add_argument(
        "--output", required=True, help="Output directory for all stages"
    )
    parser.add_argument(
        "--flow", default="importation", choices=["importation", "exportation"]
    )
    parser.add_argument("--ocr-lang", default="eng+por", help="OCR language(s)")
    parser.add_argument("--ocr-dpi", type=int, default=300, help="OCR DPI")
    parser.add_argument(
        "--min-chars", type=int, default=80, help="Minimum chars for direct text"
    )
    parser.add_argument("--json", action="store_true", help="Output JSON result")

    args = parser.parse_args()

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
        print(json.dumps(asdict(result), indent=2))
    else:
        if result.success:
            print(f"✓ Pipeline completed successfully")
            print(f"  Flow: {result.flow}")
            print(f"  Stages: {', '.join(result.stages_completed)}")
            print(f"  Report: {result.output_files.get('stage_04_html', 'N/A')}")
            if result.warnings:
                print(f"  Warnings: {len(result.warnings)}")
        else:
            print(f"✗ Pipeline failed")
            for err in result.errors:
                print(f"  Error: {err}")
