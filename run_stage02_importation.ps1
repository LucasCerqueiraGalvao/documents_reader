# run_stage02_importation.ps1
$ErrorActionPreference = "Stop"

$ROOT = Split-Path -Parent $MyInvocation.MyCommand.Path

$IN  = Join-Path $ROOT "data\output\stage_01_text\importation"
$OUT = Join-Path $ROOT "data\output\stage_02_fields\importation"
$PY  = Join-Path $ROOT ".venv\Scripts\python.exe"

# SUA ESTRUTURA: extract_fields_importation.py está dentro de src\stage_02_field_extract\importation\
$SRC = Join-Path $ROOT "src\stage_02_field_extract\importation\extract_fields_importation.py"

Write-Host "IN : $IN"
Write-Host "OUT: $OUT"
Write-Host "PY : $PY"
Write-Host "SRC: $SRC"

if (!(Test-Path $PY))  { throw "Python da venv não encontrado: $PY" }
if (!(Test-Path $SRC)) { throw "Arquivo do Stage 02 não encontrado: $SRC" }

New-Item -ItemType Directory -Force -Path $OUT | Out-Null

& $PY $SRC --in $IN --out $OUT

Write-Host "Concluido."
