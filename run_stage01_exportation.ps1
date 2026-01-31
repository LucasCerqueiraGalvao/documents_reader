# run_stage01_exportation.ps1
# Stage 01 (EXPORTATION) - Extract text from PDFs (direct text; OCR should be 0)

$ErrorActionPreference = "Stop"

# Console UTF-8 (evita "ConcluÃ­do")
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$OutputEncoding = [System.Text.UTF8Encoding]::new()

# Root folder = folder where this .ps1 is located
$ROOT = Split-Path -Parent $MyInvocation.MyCommand.Path

$IN  = Join-Path $ROOT "data\input\exportation\raw"
$OUT = Join-Path $ROOT "data\output\stage_01_text\exportation"
$PY  = Join-Path $ROOT ".venv\Scripts\python.exe"
$SRC = Join-Path $ROOT "src\stage_01_text_extract\extract_text_exportation.py"

Write-Host "IN : $IN"
Write-Host "OUT: $OUT"
Write-Host "PY : $PY"
Write-Host "SRC: $SRC"
Write-Host "OBS: Exportation PDFs -> texto direto (OCR deve ficar 0)"

if (!(Test-Path $IN)) {
  throw "Pasta de entrada nao existe: $IN"
}

if (!(Test-Path $OUT)) {
  New-Item -ItemType Directory -Force -Path $OUT | Out-Null
}

# Exportation: PDFs ja tem texto -> nao passa --enable-ocr
& $PY $SRC `
  --input "$IN" `""
  --output "$OUT" `
  --mode "blocks" `
  --min-chars 80

Write-Host "`nConcluido."
