# Electron Integration Guide

Complete guide for integrating the document processor into an Electron desktop application.

## Architecture

```text
Electron App (Node.js) <-HTTP-> Python API Server (Flask)
   - File upload UI              - Document processing
   - Progress tracking            - OCR, extraction
   - Report viewer                - Validation, reports
```

## Quick Start

1. Start Python API: `python src/api.py`
2. Use Node.js client: `const { DocumentProcessorClient } = require('./nodejs_client')`
3. Call API from Electron renderer or main process

## Example Integration

See README.md for full Node.js client usage and API endpoints.

For production Electron apps:

- Bundle Python with pyinstaller
- Use IPC for file handling
- Implement progress callbacks
- Add drag-and-drop support


## Working Example (this repo)

See the full Electron UI in [examples/electron_app](examples/electron_app).

Run it:

```bash
cd examples/electron_app
npm install
npm start
```
