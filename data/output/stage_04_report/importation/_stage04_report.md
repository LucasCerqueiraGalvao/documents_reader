# Stage 04 Report — Importação
- Gerado em: **2026-02-01T10:22:51**
- Status final: **FAIL**
- Motivos: missing_required_fields_em_algum_documento, divergencias_stage03=5

## Stage 01 — Qualidade da extração
- Docs: 3 | all_direct: 2 | com OCR: 1
- **BL.pdf** | pages=2 | has_ocr=True | methods=['ocr', 'ocr']
- **INVOICE.pdf** | pages=2 | has_ocr=False | methods=['direct', 'direct']
- **PACKING LIST.pdf** | pages=3 | has_ocr=False | methods=['direct', 'direct', 'direct']

## Stage 02 — Campos (por documento)
### bl | BL.pdf
- Severidade: **FAIL** (missing_required_fields=1)
- Missing: shipper_name
- Warnings: NCM/HS encontrado com 4 dígitos (8407). Pode ser HS (4/6) e não NCM completo (8).
- Fields encontrados: 8 / 9
### invoice | INVOICE.pdf
- Severidade: **OK** (no_missing_no_warnings)
- Fields encontrados: 14 / 16
### packing_list | PACKING LIST.pdf
- Severidade: **OK** (no_missing_no_warnings)
- Fields encontrados: 9 / 9

## Stage 03 — Comparações
- Total checks: 11 | matches: 6 | divergences: 5 | skipped: 0

### Divergências
- [pair] invoice_vs_packing | INVOICE.pdf <> PACKING LIST.pdf | ? | A=9825.0 | B=4584.857
- [pair] invoice_vs_packing | INVOICE.pdf <> PACKING LIST.pdf | ? | A=7980.0 | B=5429.23
- [pair] invoice_vs_bl | INVOICE.pdf <> BL.pdf | ? | A=GHANDI SECAF VEICULOS LTDA. | B=FESS) E
- [pair] packing_vs_bl | PACKING LIST.pdf <> BL.pdf | ? | A=GHANDI SECAF VEICULOS LTDA. AUG. 28,2025 | B=FESS) E
- [pair] packing_vs_bl | PACKING LIST.pdf <> BL.pdf | ? | A=4584.857 | B=9825.0

### Skipped (não comparados)
- (nenhum)

