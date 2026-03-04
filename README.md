# Documents Reader

Pipeline para leitura de documentos de comercio exterior, com OCR, extracao de campos, comparacao entre documentos e geracao de relatorios (Stage 04 e Stage 05).

Este README e a fonte principal da documentacao do projeto.

## Sumario

1. [Objetivo e escopo](#objetivo-e-escopo)
2. [Capacidades atuais](#capacidades-atuais)
3. [Arquitetura](#arquitetura)
4. [Estrutura do repositorio](#estrutura-do-repositorio)
5. [Requisitos e setup](#requisitos-e-setup)
6. [Modelo de entrada](#modelo-de-entrada)
7. [Como executar](#como-executar)
8. [Desktop app Electron](#desktop-app-electron)
9. [Stage 02 - Extracao de campos](#stage-02---extracao-de-campos)
10. [Stage 03 - Comparacoes](#stage-03---comparacoes)
11. [Stage 04 e Stage 05](#stage-04-e-stage-05)
12. [Mapa de saidas](#mapa-de-saidas)
13. [Testes](#testes)
14. [Troubleshooting](#troubleshooting)
15. [Manutencao e evolucao](#manutencao-e-evolucao)
16. [Riscos operacionais](#riscos-operacionais)
17. [Versionamento e release](#versionamento-e-release)
18. [Documentacao extra](#documentacao-extra)
19. [License](#license)

## Objetivo e escopo

O projeto automatiza validacoes que normalmente seriam manuais em processos de importacao/exportacao:

1. Leitura de PDFs (digitais ou escaneados).
2. Extracao de texto por pagina (Stage 01).
3. Extracao de campos estruturados por tipo documental (Stage 02, com `regex` ou `llm`).
4. Comparacao de coerencia entre documentos (Stage 03).
5. Gera relatorio final para operacao (Stage 04) e relatorio tecnico detalhado para depuracao (Stage 05).

## Capacidades atuais

- Dois fluxos suportados: `importation` e `exportation`.
- Execucao via:
  - CLI (`src/pipeline.py`)
  - API HTTP Flask (`src/api.py`)
  - Interface desktop Electron (`examples/electron_app`)
- Stage 05 ativo para os dois fluxos no pipeline atual.
- Stage 02 com motor selecionavel:
  - `regex` (padrao)
  - `llm` (Codex CLI)
- Politica padrao de falha da LLM: fail-fast (sem fallback automatico para regex, salvo configuracao explicita por env var).

## Arquitetura

### Entradas possiveis

- CLI/Python chama diretamente `run_pipeline` em `src/pipeline.py`.
- API HTTP exposta por `src/api.py`.
- Electron chama pipeline por processo local (desenvolvimento) ou runner empacotado (build instalada).

### Stages

1. `stage_01_text_extract`
2. `stage_02_field_extract`
3. `stage_03_compare`
4. `stage_04_report`
5. `stage_05_debug_report`

### Fluxo de dados

```text
PDF -> Stage 01 (_extracted.json/.txt)
    -> Stage 02 (_fields.json + _stage02_summary.json)
    -> Stage 03 (_stage03_comparison.json)
    -> Stage 04 (_stage04_report.{json,md,html})
    -> Stage 05 (_stage05_debug_report.{json,md,html})
```

Cada stage persiste artefatos em disco para facilitar auditoria, reprocessamento parcial e debug.

## Estrutura do repositorio

```text
documents_reader/
|- src/
|  |- pipeline.py
|  |- api.py
|  |- stage_01_text_extract/
|  |- stage_02_field_extract/
|  |  |- importation/
|  |  |- exportation/
|  |- stage_03_compare_docs/
|  |- stage_04_report/
|  |- stage_05_debug_report/
|- data/
|  |- input/
|  |  |- importation/raw/
|  |  |- exportation/raw/
|  |- output/
|- examples/
|  |- electron_app/
|- scripts/
|- README.md
```

## Requisitos e setup

### Python

Dependencias em `requirements.txt`:

- `pymupdf==1.26.5`
- `pillow==11.3.0`
- `pytesseract==0.3.13`
- `packaging==25.0`
- `flask==3.0.0`
- `flask-cors==4.0.0`

Setup recomendado:

```bash
python -m venv .venv
# Linux/macOS
source .venv/bin/activate
# Windows PowerShell
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### Tesseract OCR

Necessario para paginas escaneadas.

- Windows: instalador UB Mannheim
- macOS: `brew install tesseract tesseract-lang`
- Ubuntu/Debian: `sudo apt-get install tesseract-ocr tesseract-ocr-por tesseract-ocr-eng`

Se necessario, force caminho do executavel:

- `TESSERACT_EXE=/caminho/para/tesseract`

### Node/Electron

Para UI desktop:

```bash
cd examples/electron_app
npm install
```

## Modelo de entrada

### Layouts aceitos no `--input`

`src/pipeline.py` resolve automaticamente estes formatos:

1. `<input>/<flow>/raw`
2. `<input>/raw`
3. `<input>` contendo PDFs diretamente

### Tipos de documento por fluxo

`importation`:

- `INVOICE`
- `PACKING LIST`
- `BL`
- `HBL`
- `DI`
- `LI`

`exportation`:

- `COMMERCIAL INVOICE`
- `PACKING LIST`
- `DRAFT BL`
- `CERTIFICATE OF ORIGIN`
- `CONTAINER DATA`

### Contrato de hints por arquivo (`_doc_type_hints.json`)

O Electron gera hints no input raw para evitar classificacao por heuristica.

Exemplo:

```json
{
  "COMMERCIAL INVOICE 1.pdf": "commercial_invoice",
  "PACKING LIST 1.pdf": "packing_list",
  "DRAFT BL 1.pdf": "draft_bl"
}
```

## Como executar

### Pipeline completo via CLI

Importation:

```bash
python src/pipeline.py --input data/input/importation --output data/output --flow importation --json
```

Exportation:

```bash
python src/pipeline.py --input data/input/exportation --output data/output --flow exportation --json
```

### Execucao stage a stage (CLI)

Importation:

```bash
python src/stage_01_text_extract/extract_text_importation.py --in data/input/importation/raw --out data/output/stage_01_text/importation
python src/stage_02_field_extract/importation/extract_fields_importation.py --in data/output/stage_01_text/importation --out data/output/stage_02_fields/importation --engine regex
python src/stage_03_compare_docs/compare_importation.py --input data/output/stage_02_fields/importation --output data/output/stage_03_compare/importation
python src/stage_04_report/generate_report_importation.py --stage01 data/output/stage_01_text/importation --stage02 data/output/stage_02_fields/importation --stage03 data/output/stage_03_compare/importation/_stage03_comparison.json --out data/output/stage_04_report/importation
python src/stage_05_debug_report/generate_debug_report_importation.py --stage02 data/output/stage_02_fields/importation --stage03 data/output/stage_03_compare/importation/_stage03_comparison.json --out data/output/stage_05_debug_report/importation
```

Exportation:

```bash
python src/stage_01_text_extract/extract_text_exportation.py --in data/input/exportation/raw --out data/output/stage_01_text/exportation
python src/stage_02_field_extract/exportation/extract_fields_exportation.py --in data/output/stage_01_text/exportation --out data/output/stage_02_fields/exportation --engine regex
python src/stage_03_compare_docs/compare_exportation.py --input data/output/stage_02_fields/exportation --output data/output/stage_03_compare/exportation
python src/stage_04_report/generate_report_exportation.py --stage01 data/output/stage_01_text/exportation --stage02 data/output/stage_02_fields/exportation --stage03 data/output/stage_03_compare/exportation/_stage03_comparison.json --out data/output/stage_04_report/exportation
python src/stage_05_debug_report/generate_debug_report_exportation.py --stage02 data/output/stage_02_fields/exportation --stage03 data/output/stage_03_compare/exportation/_stage03_comparison.json --out data/output/stage_05_debug_report/exportation
```

### Uso programatico (Python)

```python
from pathlib import Path
from pipeline import PipelineConfig, run_pipeline

cfg = PipelineConfig(
    input_dir=Path("data/input/exportation"),
    output_dir=Path("data/output"),
    flow="exportation",
    ocr_lang="eng+por",
    ocr_dpi=300,
    min_chars=80,
)

result = run_pipeline(cfg)
print(result.success)
print(result.output_files.get("stage_04_html"))
```

### API HTTP

Subir servidor:

```bash
python src/api.py --host 127.0.0.1 --port 5000
```

Endpoints:

- `GET /health`
- `POST /api/v1/process`
- `POST /api/v1/process/stage/1`
- `POST /api/v1/process/stage/2`
- `POST /api/v1/process/stage/3`
- `POST /api/v1/process/stage/4`
- `POST /api/v1/process/stage/5`

Payload minimo para pipeline completo:

```json
{
  "input_dir": "C:/path/input",
  "output_dir": "C:/path/output",
  "flow": "importation",
  "ocr_lang": "eng+por",
  "ocr_dpi": 300,
  "min_chars": 80
}
```

Para `stage/<n>`, inclua `flow` no body quando necessario (default e `importation`).

## Desktop app Electron

### Execucao em desenvolvimento

```bash
cd examples/electron_app
npm install
npm start
```

Atalho no repo raiz:

- PowerShell: `scripts/run-electron.ps1`
- Bash: `scripts/run-electron.sh`

### Operacao na UI

1. Escolher `Modo` (`Importation` ou `Exportation`).
2. Adicionar PDFs (botao ou drag-and-drop).
3. Confirmar tipo documental por arquivo.
4. Escolher engine do Stage 02 (`Regex` ou `LLM (Codex)`).
5. Clicar `Run`.

Comportamentos relevantes:

- UI envia `flow` e `docType` para o main process.
- Main gera `_doc_type_hints.json` e executa pipeline no flow correto.
- Stage 04 abre automaticamente ao final quando sucesso.
- Stage 05 pode ser aberto pelo botao de report detalhado.

### Stage 02 LLM na UI

Quando `LLM (Codex)` e selecionado:

- exige auth Codex conectada
- faz preflight do comando Codex CLI
- por padrao nao faz fallback automatico para regex (`DOCREADER_STAGE2_LLM_FALLBACK_REGEX=0`)

Logs de progresso relevantes:

- `[Stage02-LLM]` (importation)
- `[Stage02-LLM-EXPORT]` (exportation)

### Scripts de smoke/test da UI

```bash
npm --prefix examples/electron_app run smoke
npm --prefix examples/electron_app run smoke:exportation
npm --prefix examples/electron_app run test:renderer-flow
```

### Build local e release

Build local:

```bash
npm --prefix examples/electron_app run dist:win:full
```

Pipeline de release no GitHub:

- Workflow: `.github/workflows/windows-installer.yml`
- Trigger de release: push em tag `v*`
- Passos principais:
  1. build runner Python embutido (`docreader-runner`)
  2. fetch/bundle Tesseract
  3. smoke local de artefatos
  4. build NSIS
  5. rename do instalador para `Documents.Reader.Setup.<tag>.exe`
  6. create/update GitHub Release com artefatos

Versionamento visivel no app:

- release tag e injetada em `package.json` (`docReaderReleaseTag`)
- renderer mostra no rodape: `versao: <tag>`

## Stage 02 - Extracao de campos

### Contrato de saida por documento

Cada `*_fields.json` tem estrutura base:

```json
{
  "source": {
    "stage01_file": "..._extracted.json",
    "original_file": "...pdf",
    "doc_kind": "...",
    "doc_kind_hint": "..."
  },
  "generated_at": "YYYY-MM-DDTHH:MM:SS",
  "fields": {
    "campo": {
      "present": true,
      "required": true,
      "value": "...",
      "evidence": ["..."],
      "method": "regex|llm_manual|..."
    }
  },
  "missing_required_fields": [],
  "warnings": []
}
```

### Importation - campos por doc_kind (template LLM)

`invoice`

- Required:
  - `invoice_number`, `invoice_date`, `payment_terms`, `importer_name`, `importer_cnpj`, `consignee_cnpj`, `shipper_name`, `currency`, `incoterm`, `net_weight_kg`, `gross_weight_kg`
- Optional:
  - `country_of_origin`, `country_of_acquisition`, `country_of_provenance`, `total_quantity`, `freight_and_expenses`, `line_items`

`packing_list`

- Required:
  - `invoice_number`, `importer_name`, `importer_cnpj`, `packages_total`, `net_weight_kg`, `gross_weight_kg`, `measurement_total_m3`, `items`
- Optional:
  - `shipper_name`

`bl` e `hbl`

- Required:
  - `shipper_name`, `importer_name`, `consignee_name`, `importer_cnpj`, `consignee_cnpj`, `ncm`, `ncm_or_hs`, `gross_weight_kg`
- Optional:
  - `freight_terms`, `freight_term`, `measurement_m3`, `notify_party`, `port_of_loading`, `port_of_discharge`

`di`

- Required:
  - `importer_name`, `importer_cnpj`, `invoice_numbers`
- Optional:
  - `invoice_number`, `net_weight_kg`, `gross_weight_kg`, `ncm`, `ncm_or_hs`, `di_number`, `reference_internal`, `reference_client`, `bl_number`, `transport_mode`, `port_of_loading`, `shipment_date`, `arrival_date`, `declaration_type`, `operational_unit`, `dispatch_urf`, `dispatch_modality`, `transport_carrier`, `entry_urf`, `country_of_provenance`, `importer_address`, `importer_number`, `importer_complement`, `importer_neighborhood`, `importer_cep`, `importer_city_uf`, `importer_country`

`li`

- Required:
  - `importer_name`, `importer_cnpj`
- Optional:
  - `li_number`, `li_reference`, `invoice_number`, `net_weight_kg`, `gross_weight_kg`, `ncm`, `ncm_or_hs`, `country_of_origin`, `country_of_provenance`, `country_of_acquisition`, `country_proc`, `exporter_name`, `quantity`, `unit_measure`, `incoterm`, `importer_address`, `importer_number`, `importer_complement`, `importer_city`, `importer_country`, `exporter_address`, `exporter_city`, `exporter_country`, `dispatch_urf`, `entry_urf`, `currency`, `purchase_condition`, `unit_commercial`

### Exportation - campos por doc_kind (template LLM)

`commercial_invoice`

- Required:
  - `invoice_number`, `invoice_date`, `country_of_origin`, `transport_mode`, `port_of_loading`, `port_of_destination`, `gross_weight_kg`, `net_weight_kg`, `incoterm`, `currency`, `ncm`, `container_count`, `exporter_cnpj`, `exporter_name`, `importer_name`
- Optional:
  - `payment_terms`

`packing_list`

- Required:
  - `packing_list_number`, `packing_date`, `gross_weight_kg`, `net_weight_kg`, `ncm`, `incoterm`, `container_count`, `containers`

`draft_bl`

- Required:
  - `freight_mode`, `incoterm`, `ncm`, `due`, `ruc`, `wooden_packing`, `containers`, `total_cartons`, `net_weight_kg_total`, `gross_weight_kg_total`, `cubic_meters_total`, `exporter_cnpj`, `exporter_name`, `importer_name`, `notify_party_name`
- Optional:
  - `booking_number`, `phones_found`

`certificate_of_origin`

- Required:
  - `invoice_number`, `certificate_date`, `transport_mode`, `exporter_name`, `importer_name`, `net_weight_kg`, `gross_weight_kg`, `total_m2`

`container_data`

- Required:
  - `invoice_number`, `booking_number`, `containers`

### Engine Stage 02 e variaveis de ambiente

Selecao:

- `DOCREADER_STAGE2_ENGINE=regex|llm` (default: `regex`)

Execucao LLM:

- `DOCREADER_CODEX_CLI_PATH` (default: `codex`)
- `DOCREADER_STAGE2_LLM_MODEL` (opcional)
- `DOCREADER_STAGE2_LLM_TIMEOUT_SEC` (default: `240`)
- `DOCREADER_STAGE2_LLM_DETAILED_LOG=1` para log detalhado
- `DOCREADER_STAGE2_LLM_FALLBACK_REGEX=0|1` (default recomendado: `0`)

Contexto de auth passado ao Stage 02 (normalmente pelo Electron):

- `DOCREADER_CODEX_AUTH_CONTEXT_FILE`
- `DOCREADER_CODEX_ACCESS_TOKEN`
- `DOCREADER_CODEX_TOKEN_TYPE`
- `DOCREADER_CODEX_EXPIRES_AT`
- `DOCREADER_CODEX_SUB`

Variaveis de OAuth/CLI no Electron (avancado):

- `DOCREADER_CODEX_CLIENT_ID`
- `DOCREADER_CODEX_CLI_CMD`
- `DOCREADER_CODEX_AUTH_FILE`
- `DOCREADER_CODEX_AUTH_URL`
- `DOCREADER_CODEX_TOKEN_URL`
- `DOCREADER_CODEX_SCOPE`
- `DOCREADER_CODEX_AUDIENCE`
- `DOCREADER_CODEX_RESOURCE`

## Stage 03 - Comparacoes

### Status possiveis

- `match`
- `divergent`
- `skipped`
- `missing` (group checks)

### Importation - regras atuais

Pair checks principais:

- `invoice_vs_packing`
  - reference number (docref)
  - consignee/importer name
  - consignee/importer CNPJ
  - gross/net weight
- `invoice_vs_bl`
  - consignee/importer name
  - consignee/importer CNPJ
  - gross weight
- `packing_vs_bl`
  - consignee/importer name
  - consignee/importer CNPJ
  - gross weight
- `di/li` compares com invoice/packing/base e NCM-HS com BL

Group checks:

- `shipper_exporter_equal_across_invoice_packing_bl`
- `consignee_cnpj_equal_across_invoice_packing_bl`

Rule check:

- `incoterm_vs_freight_mode`

Comparadores usados:

- numero com tolerancia (`abs_tol` e `rel_tol`)
- string por overlap de tokens (Jaccard + fallback)
- CNPJ por digitos
- docref com tolerancia para sufixo `P`
- NCM/HS com prefix match (4/6 x 8 digitos)

### Exportation - regras atuais

Pair checks:

- `invoice_vs_packing`
  - `invoice_number` x `packing_list_number`
  - `gross_weight_kg`
  - `net_weight_kg`
  - `ncm`
  - `incoterm`
  - `container_count`
- `invoice_vs_draft_bl`
  - `incoterm`, `ncm`, `exporter_cnpj`, `exporter_name`, `importer_name`, `gross_weight_kg`, `net_weight_kg`
- `packing_vs_draft_bl`
  - `ncm`, `incoterm`, `gross_weight_kg`, `net_weight_kg`
- `coo_vs_invoice`
  - invoice ref, exporter/importer name, gross/net weight
- `container_data_vs_draft_bl`
  - booking number + set de container numbers

Group checks:

- `exporter_name_equal_across_invoice_bl_coo`
- `importer_name_equal_across_invoice_bl_coo`
- `exporter_cnpj_equal_across_invoice_bl`

Rule checks:

- `incoterm_vs_freight_mode`

Semantica dos comparadores:

- `number`: `abs_tol=1.0`, `rel_tol=0.01`
- `string`: overlap de tokens
- `cnpj`: igualdade de digitos
- `docref`: normalizacao + tolerancia para sufixo `P`
- `code_prefix`: prefixo numerico (4 ou 6)

## Stage 04 e Stage 05

### Stage 04 (relatorio final)

Gera:

- `_stage04_report.json`
- `_stage04_report.md`
- `_stage04_report.html`

Conteudo:

- qualidade da extracao do Stage 01
- status de campos por documento (Stage 02)
- resumo e listas de divergencias/skipped do Stage 03
- status geral (`OK`, `ALERT`, `FAIL`)

### Stage 05 (debug report)

Gera:

- `_stage05_debug_report.json`
- `_stage05_debug_report.md`
- `_stage05_debug_report.html`

Conteudo:

- dump detalhado de campos Stage 02 (incluindo value/evidence/method)
- pair/group/rule checks Stage 03 com mais detalhe
- payload bruto para auditoria tecnica

## Mapa de saidas

```text
<output>/
|- stage_01_text/
|  |- <flow>/
|     |- *_extracted.json
|     |- *_extracted.txt
|- stage_02_fields/
|  |- <flow>/
|     |- *_fields.json
|     |- _stage02_summary.json
|- stage_03_compare/
|  |- <flow>/
|     |- _stage03_comparison.json
|- stage_04_report/
|  |- <flow>/
|     |- _stage04_report.json
|     |- _stage04_report.md
|     |- _stage04_report.html
|- stage_05_debug_report/
   |- <flow>/
      |- _stage05_debug_report.json
      |- _stage05_debug_report.md
      |- _stage05_debug_report.html
```

## Testes

### Python

```bash
python -m unittest src/stage_02_field_extract/importation/test_stage_02_llm.py -v
python -m unittest src/stage_02_field_extract/exportation/test_stage_02_llm.py -v
python -m unittest src/test_pipeline_exportation_smoke.py -v
```

Smoke via pipeline completo:

```bash
python src/pipeline.py --input data/input/importation --output .tmp_tests/importation --flow importation --json
python src/pipeline.py --input data/input/exportation --output .tmp_tests/exportation --flow exportation --json
```

### Electron

```bash
npm --prefix examples/electron_app run smoke
npm --prefix examples/electron_app run smoke:exportation
npm --prefix examples/electron_app run test:renderer-flow
npm --prefix examples/electron_app run smoke:python
npm --prefix examples/electron_app run smoke:tesseract
```

## Troubleshooting

### 1) "Codex auth obrigatoria para Stage 02 LLM"

Causa:

- engine em `llm`, sem sessao/auth valida.

Acao:

1. Conectar Codex na UI.
2. Reexecutar.

### 2) "Codex CLI indisponivel"

Causa:

- comando `codex` nao encontrado ou inacessivel no processo.

Acao:

1. Validar `codex --version`.
2. Configurar `DOCREADER_CODEX_CLI_PATH` se necessario.

### 3) Timeout da LLM

Sintoma:

- mensagens com `timeout` ou `Codex CLI timeout after ...s`.

Acao:

1. Aumentar `DOCREADER_STAGE2_LLM_TIMEOUT_SEC`.
2. Verificar log detalhado do run (`RUN LOG: ...pipeline_debug.log`).

### 4) LLM falhou e nao houve fallback para regex

Comportamento esperado quando `DOCREADER_STAGE2_LLM_FALLBACK_REGEX=0`.

Acao:

- corrigir auth/CLI/model/prompt e reexecutar em LLM.
- so habilitar fallback automatico se esta for uma decisao operacional explicita (`DOCREADER_STAGE2_LLM_FALLBACK_REGEX=1`).

### 5) Tipo documental errado em execucao CLI crua

Causa:

- sem `_doc_type_hints.json`, Stage 02 usa heuristica por nome/conteudo.

Acao:

- preferir fluxo Electron (gera hints).
- em CLI, padronizar nomes de arquivo e validar `*_fields.json` antes de seguir.

### 6) Stage 04/05 nao gerado

Checklist:

1. Confirmar existencia de saida do Stage 01 e Stage 02.
2. Confirmar `_stage03_comparison.json`.
3. Reexecutar com `--json` e revisar `errors/warnings` do resultado.

### 7) OCR nao funciona para PDF escaneado

Causa comum:

- Tesseract nao instalado ou fora do PATH.

Acao:

1. Instalar Tesseract no sistema.
2. Opcional: configurar `TESSERACT_EXE`.

## Manutencao e evolucao

### Onde editar regras de negocio

- Extracao de campos:
  - `src/stage_02_field_extract/importation/`
  - `src/stage_02_field_extract/exportation/`
- Comparacoes entre docs:
  - `src/stage_03_compare_docs/compare_importation.py`
  - `src/stage_03_compare_docs/compare_exportation.py`
- Relatorios:
  - `src/stage_04_report/`
  - `src/stage_05_debug_report/`

### Como adicionar novo tipo de documento

1. Criar extracao no Stage 02 do fluxo alvo.
2. Registrar mapeamento de doc kind/hints (`normalize_doc_kind_hint`, aliases e UI mapping).
3. Incluir comparacoes no Stage 03, se aplicavel.
4. Garantir renderizacao Stage 04/05.
5. Adicionar testes de contrato e smoke.

### Como adicionar nova regra de comparacao

1. Criar `CheckSpec` novo no Stage 03 alvo.
2. Inserir no bloco de pair/group/rule checks correto.
3. Garantir contagem em `summary`.
4. Validar exibicao no Stage 04/05.

## Riscos operacionais

- OCR ruim em scans de baixa qualidade reduz acuracia de Stage 02.
- Layouts novos de fornecedores podem quebrar regex existentes.
- Mudanca de contrato JSON entre stages pode quebrar Stage 03/04/05.
- Dependencia de Codex CLI/auth para engine LLM.
- Entrada sem hints pode aumentar erro de classificacao documental.

## Versionamento e release

Dois valores convivem no produto:

1. Versao do app (`examples/electron_app/package.json`, hoje `0.1.0`).
2. Tag de release (`vYYYY.MM.DD` ou outra convencao de tag), usada na distribuicao.

No fluxo atual de release Windows:

- arquivo final publicado: `Documents.Reader.Setup.<tag>.exe`
- rodape do app mostra a tag publicada (`versao: <tag>`)

Isso facilita rastrear exatamente qual instalador foi baixado/instalado.

## Documentacao extra

Conteudo complementar/historico foi consolidado em `EXTRA_DOCUMENTATION.md`.

## License

MIT
