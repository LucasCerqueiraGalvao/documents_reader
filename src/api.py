"""
HTTP API for Node.js/Electron integration
Simple Flask-based REST API for document processing pipeline
"""
from __future__ import annotations

import json
import os
import traceback
from pathlib import Path
from typing import Dict, Any

try:
    from flask import Flask, request, jsonify
    from flask_cors import CORS
except ImportError:
    Flask = None
    CORS = None
    print("Warning: flask and flask-cors not installed. Install with: pip install flask flask-cors")

from pipeline import run_pipeline_from_dict, PipelineConfig, run_pipeline
from dataclasses import asdict


app = Flask(__name__) if Flask else None
if app and CORS:
    CORS(app)


@app.route("/health", methods=["GET"])
def health_check():
    """Health check endpoint"""
    return jsonify({"status": "ok", "service": "document-processor"})


@app.route("/api/v1/process", methods=["POST"])
def process_documents():
    """
    Process documents through the full pipeline
    
    Request body:
    {
        "input_dir": "/path/to/input",
        "output_dir": "/path/to/output",
        "flow": "importation",  // optional, default: importation
        "ocr_lang": "eng+por",  // optional, default: eng+por
        "ocr_dpi": 300,         // optional, default: 300
        "min_chars": 80         // optional, default: 80
    }
    
    Response:
    {
        "success": true,
        "flow": "importation",
        "stages_completed": ["stage_01_text_extract", ...],
        "output_files": {
            "stage_01": "/path/to/output/stage_01_text/importation",
            ...
        },
        "errors": [],
        "warnings": [],
        "metadata": {...}
    }
    """
    try:
        data = request.get_json()
        
        if not data:
            return jsonify({"error": "No JSON data provided"}), 400
        
        required_fields = ["input_dir", "output_dir"]
        for field in required_fields:
            if field not in data:
                return jsonify({"error": f"Missing required field: {field}"}), 400
        
        # Validate paths
        input_dir = Path(data["input_dir"])
        if not input_dir.exists():
            return jsonify({"error": f"Input directory does not exist: {input_dir}"}), 400
        
        result = run_pipeline_from_dict(data)
        
        status_code = 200 if result["success"] else 500
        return jsonify(result), status_code
        
    except Exception as e:
        error_trace = traceback.format_exc()
        return jsonify({
            "error": str(e),
            "traceback": error_trace
        }), 500


@app.route("/api/v1/process/stage/<stage_num>", methods=["POST"])
def process_single_stage(stage_num: str):
    """
    Process a single stage
    
    Stage 1: /api/v1/process/stage/1
    Request: {"in_dir": "...", "out_dir": "...", "ocr_lang": "eng+por", "ocr_dpi": 300}
    
    Stage 2: /api/v1/process/stage/2
    Request: {"in_dir": "...", "out_dir": "..."}
    
    Stage 3: /api/v1/process/stage/3
    Request: {"in_dir": "...", "out_dir": "..."}
    
    Stage 4: /api/v1/process/stage/4
    Request: {"stage01_dir": "...", "stage02_dir": "...", "stage03_file": "...", "out_dir": "..."}
    """
    try:
        data = request.get_json()
        
        if stage_num == "1":
            from stage_01_text_extract.extract_text_importation import run_stage_01_extraction
            result = run_stage_01_extraction(
                in_dir=Path(data["in_dir"]),
                out_dir=Path(data["out_dir"]),
                ocr_lang=data.get("ocr_lang", "eng+por"),
                ocr_dpi=data.get("ocr_dpi", 300),
                min_chars=data.get("min_chars", 80),
                verbose=False
            )
        elif stage_num == "2":
            from stage_02_field_extract.importation.extract_fields_importation import run_stage_02_extraction
            result = run_stage_02_extraction(
                in_dir=Path(data["in_dir"]),
                out_dir=Path(data["out_dir"]),
                verbose=False
            )
        elif stage_num == "3":
            from stage_03_compare_docs.compare_importation import run_stage_03_comparison
            result = run_stage_03_comparison(
                in_dir=Path(data["in_dir"]),
                out_dir=Path(data["out_dir"]),
                verbose=False
            )
        elif stage_num == "4":
            from stage_04_report.generate_report_importation import run_stage_04_report
            result = run_stage_04_report(
                stage01_dir=Path(data["stage01_dir"]),
                stage02_dir=Path(data["stage02_dir"]),
                stage03_file=Path(data["stage03_file"]),
                out_dir=Path(data["out_dir"]),
                verbose=False
            )
        else:
            return jsonify({"error": f"Invalid stage number: {stage_num}"}), 400
        
        return jsonify(result), 200
        
    except Exception as e:
        error_trace = traceback.format_exc()
        return jsonify({
            "error": str(e),
            "traceback": error_trace
        }), 500


def run_server(host: str = "127.0.0.1", port: int = 5000, debug: bool = False):
    """Run Flask development server"""
    if not app:
        raise RuntimeError("Flask not installed. Install with: pip install flask flask-cors")
    
    print(f"Starting document processor API server on {host}:{port}")
    print(f"Health check: http://{host}:{port}/health")
    print(f"Process endpoint: http://{host}:{port}/api/v1/process")
    
    app.run(host=host, port=port, debug=debug)


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Document Processor HTTP API")
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind to")
    parser.add_argument("--port", type=int, default=5000, help="Port to bind to")
    parser.add_argument("--debug", action="store_true", help="Enable debug mode")
    
    args = parser.parse_args()
    run_server(host=args.host, port=args.port, debug=args.debug)
