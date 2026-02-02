# Documents Reader – Electron App

UI desktop (Electron) por cima do pipeline Python.

## Pré-requisitos

- Node.js + npm
- Python com venv criado em `.venv` na raiz do repo
- Dependências Python instaladas: `pip install -r requirements.txt`
- (Opcional) Tesseract instalado para OCR em PDFs escaneados

## Configurar o projeto (onde está o Python)

Em modo dev, o app Electron roda o pipeline Python a partir do repo.

Para distribuição “um instalador só”, o app empacotado roda um binário do pipeline embutido (gerado com PyInstaller).

Ele precisa apontar para uma pasta que contenha:

- `src/pipeline.py`
- `.venv/` (com as deps instaladas via `pip install -r requirements.txt`)

No app, clique em **Configurar Projeto** e selecione a pasta raiz do repo.

Alternativa: defina a variável de ambiente `DOCREADER_PROJECT_ROOT`.

## Rodar

A partir da raiz do repo:

```bash
cd examples/electron_app
npm install
npm start
```

## Smoke test (sem abrir UI)

Valida se o pipeline roda e gera o Stage 4 HTML usando os PDFs de exemplo em `data/input/importation/raw`:

```bash
cd examples/electron_app
npm run smoke
```

## Gerar build (Windows / Linux / macOS)

O build é gerado via `electron-builder`.

### 1) Gerar o runner Python (PyInstaller)

Em cada plataforma, gere o binário embutido (isso exige Python apenas na máquina de build):

```bash
cd examples/electron_app
npm install
npm run build:python
npm run smoke:python
```

Isso gera o executável em:

- macOS: `examples/electron_app/resources/python/mac/docreader-runner`
- Windows: `examples/electron_app/resources/python/win/docreader-runner.exe`
- Linux: `examples/electron_app/resources/python/linux/docreader-runner`

### 2) Gerar o instalador Electron

O build é gerado via `electron-builder`.

### Windows

Em uma máquina Windows (recomendado):

```powershell
cd examples/electron_app
npm install

# Recomendado: gera tudo (runner Python + Tesseract + smoke tests + instalador)
npm run dist:win:full
```

Saída em `examples/electron_app/dist/` (instalador NSIS).

Notas:

- Esse comando precisa rodar no Windows porque o PyInstaller não gera `.exe` a partir do macOS.
- Se você preferir rodar passo-a-passo: `npm run build:python`, `npm run fetch:tesseract`, `npm run smoke:python`, `npm run smoke:tesseract`, `npm run dist:win`.

### Linux

Em uma máquina Linux (recomendado):

```bash
cd examples/electron_app
npm install
npm run dist:linux
```

Saída em `examples/electron_app/dist/` (AppImage).

### macOS

```bash
cd examples/electron_app
npm install
npm run dist:mac
```

### Observações importantes

- Em build empacotado, o app **não precisa** de repo/.venv e o botão **Configurar Projeto** fica desnecessário.
- OCR (Tesseract): para suportar PDFs escaneados sem depender de instalação externa, empacote o Tesseract em `examples/electron_app/resources/tesseract/<plataforma>/`.

Automatizado (Windows-first, macOS suportado):

```bash
cd examples/electron_app
npm install
npm run fetch:tesseract

# Opcional: valida se o Tesseract empacotado executa
npm run smoke:tesseract
```

Variáveis úteis:

- `TESS_LANGS` (default `eng+por`)
- `TESSDATA_VARIANT` (`fast` default, ou `best`)
- Windows: `TESSERACT_WIN_URL` para trocar o instalador (se o link padrão mudar)

O app empacotado tenta usar `tesseract/<plataforma>/tesseract(.exe)` e `tessdata/` via `TESSDATA_PREFIX`.

## Como funciona

- Você seleciona/arrasta PDFs
- Define o tipo do documento (BL / INVOICE / PACKING LIST)
- Clica em **Run**
- O app copia os PDFs para um diretório isolado em `.electron_runs/<run-id>/input/importation/raw`
- Executa o pipeline:
  - `src/pipeline.py --input <run>/input --output <run>/output --flow importation --json`
- Ao finalizar com sucesso, abre automaticamente o HTML do Stage 4.
