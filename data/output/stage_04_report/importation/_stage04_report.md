# Stage 04 Report — Importação
- Gerado em: **2026-02-01T14:14:27**
- Status final: **FAIL**
- Motivos: divergencias_stage03=2

## Stage 01 — Qualidade da extração
- Docs: 3 | all_direct: 2 | com OCR: 1
- **BL.pdf** | pages=2 | has_ocr=True | methods=['ocr', 'ocr']
- **INVOICE.pdf** | pages=2 | has_ocr=False | methods=['direct', 'direct']
- **PACKING LIST.pdf** | pages=3 | has_ocr=False | methods=['direct', 'direct', 'direct']

## Stage 02 — Campos (por documento)
### bl | BL.pdf
- Severidade: **OK** (no_missing_no_warnings)
- Fields encontrados: 5 / 5
### invoice | INVOICE.pdf
- Severidade: **OK** (no_missing_no_warnings)
- Fields encontrados: 14 / 16
### packing_list | PACKING LIST.pdf
- Severidade: **OK** (no_missing_no_warnings)
- Fields encontrados: 8 / 8

## Stage 03 — Comparações
- Total checks: 11 | matches: 9 | divergences: 2 | skipped: 0

### Divergências
- [pair] invoice_vs_bl | INVOICE.pdf <> BL.pdf | ? | A=GHANDI SECAF VEICULOS LTDA. | B=GHANDI SECAF VEICU LOS LTDA. Received by the Carrier from the Shipper in apparent good order and condition unless otherwise indicated herein, the
- [pair] packing_vs_bl | PACKING LIST.pdf <> BL.pdf | ? | A=GHANDI SECAF VEICULOS LTDA. AUG. 28,2025 | B=GHANDI SECAF VEICU LOS LTDA. Received by the Carrier from the Shipper in apparent good order and condition unless otherwise indicated herein, the

### Skipped (não comparados)
- (nenhum)

