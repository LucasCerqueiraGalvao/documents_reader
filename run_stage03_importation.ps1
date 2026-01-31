# run_stage03_importation.ps1
# Stage 03 (IMPORTATION) - Compare fields between documents

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$OutputEncoding = [System.Text.UTF8Encoding]::new()

$ROOT = Split-Path -Parent $MyInvocation.MyCommand.Path

$IN  = Join-Path $ROOT "data\output\stage_02_fields\importation"
$OUT = Join-Path $ROOT "data\output\stage_03_compare\importation"
$PY  = Join-Path $ROOT ".venv\Scripts\python.exe"
$SRC = Join-Path $ROOT "src\stage_03_compare_docs\compare_importation.py"

Write-Host "IN : $IN"
Write-Host "OUT: $OUT"
Write-Host "PY : $PY"
Write-Host "SRC: $SRC"

if (!(Test-Path $IN))  { throw "Entrada não existe (rode Stage 02): $IN" }
if (!(Test-Path $SRC)) { throw "Arquivo do Stage 03 não encontrado: $SRC" }
if (!(Test-Path $OUT)) { New-Item -ItemType Directory -Force -Path $OUT | Out-Null }

& $PY $SRC --input "$IN" --output "$OUT"

if ($LASTEXITCODE -ne 0) {
  throw "Stage 03 falhou (exit code $LASTEXITCODE)."
}

Write-Host "`nConcluido."
