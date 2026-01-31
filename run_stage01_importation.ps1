$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$OutputEncoding = [System.Text.UTF8Encoding]::new()

$ROOT = Split-Path -Parent $MyInvocation.MyCommand.Path

$IN  = Join-Path $ROOT "data\input\importation\raw"
$OUT = Join-Path $ROOT "data\output\stage_01_text\importation"
$PY  = Join-Path $ROOT ".venv\Scripts\python.exe"
$SRC = Join-Path $ROOT "src\stage_01_text_extract\extract_text_importation.py"

Write-Host "IN : $IN"
Write-Host "OUT: $OUT"
Write-Host "PY : $PY"
Write-Host "SRC: $SRC"

if (!(Test-Path $OUT)) { New-Item -ItemType Directory -Force -Path $OUT | Out-Null }

& $PY $SRC --input "$IN" --output "$OUT" --ocr-lang "eng+por" --dpi 300 --min-chars 80
Write-Host "`nConcluido."
