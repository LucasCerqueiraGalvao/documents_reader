# run_stage04_importation.ps1
$ErrorActionPreference = "Stop"

$ROOT = Split-Path -Parent $MyInvocation.MyCommand.Path

$STAGE01 = Join-Path $ROOT "data\output\stage_01_text\importation"
$STAGE02 = Join-Path $ROOT "data\output\stage_02_fields\importation"
$STAGE03 = Join-Path $ROOT "data\output\stage_03_compare\importation\_stage03_comparison.json"
$OUT     = Join-Path $ROOT "data\output\stage_04_report\importation"
$PY      = Join-Path $ROOT ".venv\Scripts\python.exe"
$SRC     = Join-Path $ROOT "src\stage_04_report\generate_report_importation.py"

Write-Host "STAGE01: $STAGE01"
Write-Host "STAGE02: $STAGE02"
Write-Host "STAGE03: $STAGE03"
Write-Host "OUT    : $OUT"
Write-Host "PY     : $PY"
Write-Host "SRC    : $SRC"

if (!(Test-Path $PY))      { throw "Python da venv não encontrado: $PY" }
if (!(Test-Path $SRC))     { throw "Arquivo do Stage 04 não encontrado: $SRC" }
if (!(Test-Path $STAGE01)) { throw "Stage 01 folder não encontrado: $STAGE01" }
if (!(Test-Path $STAGE02)) { throw "Stage 02 folder não encontrado: $STAGE02" }
if (!(Test-Path $STAGE03)) { throw "Stage 03 json não encontrado: $STAGE03" }

New-Item -ItemType Directory -Force -Path $OUT | Out-Null

& $PY $SRC --stage01 $STAGE01 --stage02 $STAGE02 --stage03 $STAGE03 --out $OUT

Write-Host "Concluido."
