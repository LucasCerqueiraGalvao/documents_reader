# Documents Reader

Document processing pipeline for import/export trade documents with OCR, field extraction, validation and reporting.

## Architecture

**Refactored for reusability** - All stages are now callable Python functions that can be:
- Used programmatically in Python
- Called via HTTP API from Node.js/Electron
- Executed from CLI
- Orchestrated through pipeline

### Pipeline Stages

1. **Stage 01** - Text Extraction (PyMuPDF + Tesseract OCR)
2. **Stage 02** - Field Extraction (Invoice, Packing List, Bill of Lading)
3. **Stage 03** - Document Comparison & Validation
4. **Stage 04** - Report Generation (JSON/Markdown/HTML)

## Structure

```
data/
  input/importation/raw/     # Input PDFs
  output/                    # All pipeline outputs
src/
  stage_01_text_extract/
  stage_02_field_extract/
  stage_03_compare_docs/
  stage_04_report/
  pipeline.py                # Main orchestrator
  api.py                     # HTTP API for Node.js
examples/
  nodejs_client.js           # Node.js/Electron integration
```

## Installation

```bash
python -m venv .venv
source .venv/bin/activate  # Linux/Mac
# .venv\Scripts\activate   # Windows

pip install -r requirements.txt
```

**Tesseract OCR** (required for scanned PDFs):
- macOS: `brew install tesseract tesseract-lang`
- Ubuntu: `apt-get install tesseract-ocr tesseract-ocr-por`
- Windows: Download from https://github.com/UB-Mannheim/tesseract/wiki

## Usage

### 1. Python CLI - Full Pipeline

```bash
python src/pipeline.py \
  --input data/input/importation \
  --output data/output \
  --flow importation \
  --json  # Optional: JSON output
```

### 2. Python Programmatic

```python
from pathlib import Path
from src.pipeline import run_pipeline, PipelineConfig

config = PipelineConfig(
    input_dir=Path("data/input/importation"),
    output_dir=Path("data/output"),
    flow="importation",
    ocr_lang="eng+por",
    ocr_dpi=300
)

result = run_pipeline(config)
print(f"Report: {result.output_files['stage_04_html']}")
```

### 3. HTTP API (for Node.js/Electron)

**Start API server:**
```bash
python src/api.py --host 127.0.0.1 --port 5000
```

**Node.js client:**
```javascript
const { DocumentProcessorClient } = require('./examples/nodejs_client');

const client = new DocumentProcessorClient();

const result = await client.processDocuments({
  inputDir: '/path/to/pdfs',
  outputDir: '/path/to/output',
  flow: 'importation'
});

console.log('Report:', result.output_files.stage_04_html);
```

### 4. Individual Stages

```bash
# Stage 1: Text extraction
python src/stage_01_text_extract/extract_text_importation.py \
  --in data/input/importation/raw \
  --out data/output/stage_01_text/importation

# Stage 2: Field extraction
python src/stage_02_field_extract/importation/extract_fields_importation.py \
  --in data/output/stage_01_text/importation \
  --out data/output/stage_02_fields/importation

# Stage 3: Comparison
python src/stage_03_compare_docs/compare_importation.py \
  --input data/output/stage_02_fields/importation \
  --output data/output/stage_03_compare/importation

# Stage 4: Report
python src/stage_04_report/generate_report_importation.py \
  --stage01 data/output/stage_01_text/importation \
  --stage02 data/output/stage_02_fields/importation \
  --stage03 data/output/stage_03_compare/importation/_stage03_comparison.json \
  --out data/output/stage_04_report/importation
```

## API Endpoints

### Full Pipeline
```
POST /api/v1/process
Body: {
  "input_dir": "/path/to/input",
  "output_dir": "/path/to/output",
  "flow": "importation",
  "ocr_lang": "eng+por",
  "ocr_dpi": 300
}
```

### Single Stage
```
POST /api/v1/process/stage/1  # Text extraction
POST /api/v1/process/stage/2  # Field extraction
POST /api/v1/process/stage/3  # Comparison
POST /api/v1/process/stage/4  # Report
```

### Health Check
```
GET /health
```

## Electron Integration Example

```javascript
// main.js (Electron main process)
const { spawn } = require('child_process');
const path = require('path');

// Start Python API server
const apiServer = spawn('python', [
  path.join(__dirname, 'src/api.py'),
  '--host', '127.0.0.1',
  '--port', '5000'
]);

// renderer.js (Electron renderer)
const { DocumentProcessorClient } = require('./examples/nodejs_client');
const client = new DocumentProcessorClient('http://127.0.0.1:5000');

ipcRenderer.on('process-documents', async (event, files) => {
  const result = await client.processDocuments({
    inputDir: files.inputPath,
    outputDir: files.outputPath
  });
  
  // Open report in browser
  shell.openExternal(`file://${result.output_files.stage_04_html}`);
});
```

## Output Files

- **Stage 01**: `*_extracted.txt`, `*_extracted.json`
- **Stage 02**: `*_fields.json`, `_stage02_summary.json`
- **Stage 03**: `_stage03_comparison.json`
- **Stage 04**: `_stage04_report.json`, `_stage04_report.html`, `_stage04_report.md`

## Document Types Supported

- **Invoice** (Commercial Invoice)
- **Packing List** (Romaneio)
- **Bill of Lading** (BL/HBL)
- **DI** (Declaração de Importação)
- **LI** (Licença de Importação)

## Validation Rules

- Cross-document field matching (Invoice ↔ Packing List ↔ BL)
- CNPJ validation (Brazilian tax ID)
- Shipper/Exporter consistency
- Weight validation (Net/Gross)
- Incoterm vs Freight mode compatibility
- Country of origin/acquisition/provenance

## Requirements

- Python 3.8+
- Tesseract OCR (optional, for scanned PDFs)
- Node.js 16+ (for Electron integration)

## License

MIT
