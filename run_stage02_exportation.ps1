# run_stage02_exportation.ps1
# Stage 02 (EXPORTATION) - Extract fields from Stage 01 extracted JSONs

$ErrorActionPreference = "Stop"

# Console UTF-8
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$OutputEncoding = [System.Text.UTF8Encoding]::new()

$ROOT = Split-Path -Parent $MyInvocation.MyCommand.Path

$IN  = Join-Path $ROOT "data\output\stage_01_text\exportation"
$OUT = Join-Path $ROOT "data\output\stage_02_fields\exportation"
$PY  = Join-Path $ROOT ".venv\Scripts\python.exe"

# <<< AQUI corrigido para a sua pasta real:
$SRC = Join-Path $ROOT "src\stage_02_field_extract\extract_fields_exportation.py"

Write-Host "IN : $IN"
Write-Host "OUT: $OUT"
Write-Host "PY : $PY"
Write-Host "SRC: $SRC"

if (!(Test-Path $IN)) {
  throw "Pasta de entrada nao existe (rode o Stage 01 exportation antes): $IN"
}

if (!(Test-Path $SRC)) {
  throw "Arquivo do Stage 02 nao encontrado: $SRC"
}

if (!(Test-Path $OUT)) {
  New-Item -ItemType Directory -Force -Path $OUT | Out-Null
}

& $PY $SRC --input "$IN" --output "$OUT"

if ($LASTEXITCODE -ne 0) {
  throw "Stage 02 exportation falhou (exit code $LASTEXITCODE)."
}

Write-Host "`nConcluido."
