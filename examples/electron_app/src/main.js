const { app, BrowserWindow, ipcMain, dialog, shell } = require('electron');
const path = require('path');
const fs = require('fs');
const fsp = require('fs/promises');
const { spawn, spawnSync } = require('child_process');
const { createCodexAuthManager } = require('./codexAuth');

function platformResourceFolder() {
  switch (process.platform) {
    case 'darwin':
      return 'mac';
    case 'win32':
      return 'win';
    default:
      return 'linux';
  }
}

function getBundledRunnerPath() {
  if (!app.isPackaged) return null;
  const folder = platformResourceFolder();
  const exe = process.platform === 'win32' ? 'docreader-runner.exe' : 'docreader-runner';
  const p = path.join(process.resourcesPath, 'python', folder, exe);
  return fs.existsSync(p) ? p : null;
}

function getBundledTesseractEnv() {
  if (!app.isPackaged) return null;
  const folder = platformResourceFolder();
  const exe = process.platform === 'win32' ? 'tesseract.exe' : 'tesseract';
  const exePath = path.join(process.resourcesPath, 'tesseract', folder, exe);
  const tessdataDir = path.join(process.resourcesPath, 'tesseract', folder, 'tessdata');
  const libDir = path.join(process.resourcesPath, 'tesseract', folder, 'lib');

  if (!fs.existsSync(exePath)) return null;
  const env = { TESSERACT_EXE: exePath };
  if (fs.existsSync(tessdataDir)) {
    env.TESSDATA_PREFIX = tessdataDir;
  }

  if (fs.existsSync(libDir)) {
    if (process.platform === 'darwin') {
      env.DYLD_LIBRARY_PATH = [libDir, process.env.DYLD_LIBRARY_PATH].filter(Boolean).join(':');
    } else if (process.platform === 'linux') {
      env.LD_LIBRARY_PATH = [libDir, process.env.LD_LIBRARY_PATH].filter(Boolean).join(':');
    }
  }
  return env;
}

function getProjectRoot() {
  // examples/electron_app/src -> repo root
  return path.resolve(__dirname, '..', '..', '..');
}

function getSettingsPath() {
  return path.join(app.getPath('userData'), 'settings.json');
}

async function readSettings() {
  try {
    const raw = await fsp.readFile(getSettingsPath(), 'utf-8');
    return JSON.parse(raw);
  } catch {
    return {};
  }
}

async function writeSettings(settings) {
  await fsp.mkdir(app.getPath('userData'), { recursive: true });
  await fsp.writeFile(getSettingsPath(), JSON.stringify(settings || {}, null, 2), 'utf-8');
}

function looksLikeProjectRoot(dirPath) {
  if (!dirPath || typeof dirPath !== 'string') return false;
  const pipelinePath = path.join(dirPath, 'src', 'pipeline.py');
  return fs.existsSync(pipelinePath);
}

async function resolveProjectRoot() {
  if (process.env.DOCREADER_PROJECT_ROOT && looksLikeProjectRoot(process.env.DOCREADER_PROJECT_ROOT)) {
    return process.env.DOCREADER_PROJECT_ROOT;
  }

  const settings = await readSettings();
  if (settings.projectRoot && looksLikeProjectRoot(settings.projectRoot)) {
    return settings.projectRoot;
  }

  // In dev (running inside repo), fallback to computed root.
  const devRoot = getProjectRoot();
  if (!app.isPackaged && looksLikeProjectRoot(devRoot)) {
    return devRoot;
  }

  return null;
}

function detectPythonCandidates(projectRoot) {
  const candidates = [
    path.join(projectRoot, '.venv', 'bin', 'python'),
    path.join(projectRoot, '.venv', 'bin', 'python3'),
    path.join(projectRoot, '.venv', 'Scripts', 'python.exe'),
    process.env.DOCREADER_PYTHON || '',
    'python',
  ];

  return [...new Set(candidates.filter(Boolean))];
}

function probePython(command, cwd) {
  const result = spawnSync(command, ['--version'], {
    cwd,
    encoding: 'utf8',
    windowsHide: true,
  });

  if (result.error) {
    return { ok: false, error: String(result.error.message || result.error) };
  }

  if (result.status !== 0) {
    const detail = String(result.stderr || result.stdout || '').trim();
    return {
      ok: false,
      error: detail || `exit ${result.status}`,
    };
  }

  const version = String(result.stdout || result.stderr || '').trim();
  return { ok: true, version };
}

function safeRunId() {
  return new Date().toISOString().replaceAll(':', '-').replaceAll('.', '-');
}

function sanitizeBaseName(name) {
  // Keep it readable but filesystem-friendly
  return String(name)
    .replace(/\.[^.]+$/, '')
    .replaceAll(/[^A-Za-z0-9 _-]+/g, ' ')
    .replaceAll(/\s+/g, ' ')
    .trim();
}

function mapDocTypeToPrefix(docType) {
  switch ((docType || '').toUpperCase()) {
    case 'BL':
      return 'BL';
    case 'HBL':
      return 'HBL';
    case 'INVOICE':
      return 'INVOICE';
    case 'PACKING LIST':
    case 'PACKING_LIST':
    case 'PACKINGLIST':
      return 'PACKING LIST';
    case 'DI':
      return 'DI';
    case 'LI':
      return 'LI';
    default:
      return 'DOC';
  }
}


function resolveDocTypeFromUi(item) {
  const provided = String(item?.docType || '').trim();
  return mapDocTypeToPrefix(provided);
}

function broadcastToAllWindows(channel, payload) {
  BrowserWindow.getAllWindows().forEach((w) => {
    w.webContents.send(channel, payload);
  });
}

function normalizeCodexIdentity(identity) {
  if (!identity || typeof identity !== 'object') return null;
  return {
    sub: identity.sub || null,
    email: identity.email || null,
    name: identity.name || null,
    preferredUsername: identity.preferredUsername || null,
  };
}

function toRendererCodexStatus(status) {
  if (!status || typeof status !== 'object') {
    return {
      connected: false,
      configured: false,
      missingConfig: [],
      identity: null,
      expiresAt: null,
      provider: null,
      configuredAt: null,
      hasAccessToken: false,
      isExpired: false,
      refreshError: null,
    };
  }

  return {
    connected: Boolean(status.connected),
    configured: status.configured !== false,
    missingConfig: Array.isArray(status.missingConfig) ? status.missingConfig : [],
    identity: normalizeCodexIdentity(status.identity),
    expiresAt: status.expiresAt || null,
    provider: status.provider || null,
    configuredAt: status.configuredAt || null,
    hasAccessToken: Boolean(status.hasAccessToken),
    isExpired: Boolean(status.isExpired),
    refreshError: status.refreshError || null,
  };
}

function stringifyAuthLogDetails(details) {
  if (details === null || typeof details === 'undefined') return null;
  if (typeof details === 'string') return details;
  if (typeof details === 'number' || typeof details === 'boolean') return String(details);
  try {
    return JSON.stringify(details);
  } catch {
    return String(details);
  }
}

function toRendererCodexLog(entry) {
  return {
    ts: entry && entry.ts ? String(entry.ts) : new Date().toISOString(),
    level: entry && entry.level ? String(entry.level) : 'info',
    step: entry && entry.step ? String(entry.step) : 'unknown',
    details: stringifyAuthLogDetails(entry ? entry.details : null),
  };
}

function broadcastCodexAuthLog(entry) {
  broadcastToAllWindows('codexAuth:log', toRendererCodexLog(entry));
}

function toRendererCodexAuthResponse(result) {
  if (!result || typeof result !== 'object') return result;
  if (!Object.prototype.hasOwnProperty.call(result, 'status')) {
    return result;
  }
  return {
    ...result,
    status: toRendererCodexStatus(result.status),
  };
}

const codexAuth = createCodexAuthManager({
  readSettings,
  writeSettings,
  openExternal: (url) => shell.openExternal(url),
  notifyChanged: (status) => broadcastToAllWindows('codexAuth:changed', toRendererCodexStatus(status)),
  notifyLog: (entry) => broadcastCodexAuthLog(entry),
});

function toStageDocKind(prefix) {
  switch (prefix) {
    case 'BL':
      return 'bl';
    case 'HBL':
      return 'hbl';
    case 'INVOICE':
      return 'invoice';
    case 'PACKING LIST':
      return 'packing_list';
    case 'DI':
      return 'di';
    case 'LI':
      return 'li';
    default:
      return 'unknown';
  }
}

async function ensureDir(dirPath) {
  await fsp.mkdir(dirPath, { recursive: true });
}

function extractLastJson(stdoutText) {
  const idx = stdoutText.lastIndexOf('{');
  if (idx === -1) return null;
  const tail = stdoutText.slice(idx);
  try {
    return JSON.parse(tail);
  } catch {
    return null;
  }
}

async function tryReadJsonFile(filePath) {
  try {
    const raw = await fsp.readFile(filePath, 'utf-8');
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

async function runPipeline({ files, stage2Engine }) {
  broadcastCodexAuthLog({
    ts: new Date().toISOString(),
    level: 'info',
    step: 'pipeline.run.begin',
    details: {
      fileCount: Array.isArray(files) ? files.length : 0,
      stage2Engine: String(stage2Engine || 'regex'),
    },
  });

  const bundledRunner = getBundledRunnerPath();
  const projectRoot = await resolveProjectRoot();
  const isBundled = app.isPackaged;
  const codexAuthStatus = await codexAuth.getStatus({ autoRefresh: true });

  // In packaged builds we expect a bundled runner binary and do not require a project root.
  if (isBundled && !bundledRunner) {
    return {
      ok: false,
      error:
        'Bundled Python runner not found. The installer is missing the embedded pipeline binary (docreader-runner).',
    };
  }

  // In dev we run the repo pipeline via the repo venv.
  let pythonPath = null;
  if (!isBundled) {
    if (!projectRoot) {
      return {
        ok: false,
        error:
          'Project root not configured. Use "Configurar Projeto" to select the folder that contains src/pipeline.py and .venv.',
      };
    }
    const pythonCandidates = detectPythonCandidates(projectRoot);
    const probeFailures = [];
    for (const candidate of pythonCandidates) {
      const probe = probePython(candidate, projectRoot);
      if (probe.ok) {
        pythonPath = candidate;
        break;
      }
      probeFailures.push(candidate + ': ' + probe.error);
    }

    if (!pythonPath) {
      return {
        ok: false,
        error:
          'No working Python executable found. Recreate .venv or set DOCREADER_PYTHON to a valid interpreter.',
        details: probeFailures,
      };
    }
  }

  const runBase = path.join(app.getPath('userData'), 'runs', safeRunId());
  const inputBase = path.join(runBase, 'input');
  const outputBase = path.join(runBase, 'output');
  const rawDir = path.join(inputBase, 'importation', 'raw');

  await ensureDir(rawDir);
  await ensureDir(outputBase);

  // Copy files into run input folder with names that help doc detection.
  const counters = { BL: 0, HBL: 0, INVOICE: 0, 'PACKING LIST': 0, DI: 0, LI: 0 };
  const copied = [];
  const docTypeHints = {};

  for (const item of files || []) {
    const srcPath = item.path;
    const typePrefix = resolveDocTypeFromUi(item);
    if (typePrefix === 'DOC') {
      return {
        ok: false,
        error: `Invalid document type from UI for file: ${path.basename(srcPath || '')}`,
      };
    }
    counters[typePrefix] = (counters[typePrefix] || 0) + 1;

    const suffix = counters[typePrefix] === 1 ? '' : ` ${counters[typePrefix]}`;
    const destName = `${typePrefix}${suffix}.pdf`;
    const destPath = path.join(rawDir, destName);

    await fsp.copyFile(srcPath, destPath);
    copied.push({
      from: srcPath,
      to: destPath,
      docType: item.docType || null,
      resolvedType: typePrefix,
    });

    docTypeHints[destName] = toStageDocKind(typePrefix);
  }

  const hintFile = path.join(rawDir, '_doc_type_hints.json');
  await fsp.writeFile(hintFile, JSON.stringify(docTypeHints, null, 2), 'utf-8');

  if (copied.length) {
    const lines = copied
      .map((c) => `INPUT MAP: [${c.resolvedType}] ${path.basename(c.from)} -> ${path.basename(c.to)}`)
      .join('\n') + '\n';
    BrowserWindow.getAllWindows().forEach((w) => {
      w.webContents.send('pipeline:log', { stream: 'stdout', text: lines });
    });
  }

  const tessEnv = getBundledTesseractEnv();
  const runnerEnv = tessEnv ? { ...process.env, ...tessEnv } : { ...process.env };
  runnerEnv.PYTHONUNBUFFERED = '1';
  const requestedStage2Engine = String(stage2Engine || 'regex').trim().toLowerCase() === 'llm' ? 'llm' : 'regex';
  const effectiveStage2Engine =
    requestedStage2Engine === 'llm' && !codexAuthStatus?.connected ? 'regex' : requestedStage2Engine;
  runnerEnv.DOCREADER_STAGE2_ENGINE = effectiveStage2Engine;
  // Keep strict behavior by default: LLM engine should stay LLM unless explicitly overridden.
  runnerEnv.DOCREADER_STAGE2_LLM_FALLBACK_REGEX =
    process.env.DOCREADER_STAGE2_LLM_FALLBACK_REGEX || '0';
  if (codexAuthStatus && codexAuthStatus.cliCommand) {
    runnerEnv.DOCREADER_CODEX_CLI_PATH = String(codexAuthStatus.cliCommand);
  }

  const codexContext = {
    connected: Boolean(codexAuthStatus?.connected),
    expiresAt: codexAuthStatus?.expiresAt || null,
    identity: codexAuthStatus?.identity || null,
    provider: 'codex-cli',
  };

  if (codexAuthStatus?.connected && codexAuthStatus?.token?.accessToken) {
    runnerEnv.DOCREADER_CODEX_ACCESS_TOKEN = codexAuthStatus.token.accessToken;
    runnerEnv.DOCREADER_CODEX_TOKEN_TYPE = codexAuthStatus.token.tokenType || 'Bearer';
    if (codexAuthStatus.token.expiresAt) {
      runnerEnv.DOCREADER_CODEX_EXPIRES_AT = String(codexAuthStatus.token.expiresAt);
    }
    if (codexAuthStatus.identity?.sub) {
      runnerEnv.DOCREADER_CODEX_SUB = codexAuthStatus.identity.sub;
    }
  }

  const codexContextPath = path.join(runBase, 'codex_auth_context.json');
  await fsp.writeFile(codexContextPath, JSON.stringify(codexContext, null, 2), 'utf-8');
  runnerEnv.DOCREADER_CODEX_AUTH_CONTEXT_FILE = codexContextPath;

  const codexStatusLine = codexContext.connected
    ? 'CODEX AUTH: connected (token available to stage_02)\n'
    : 'CODEX AUTH: not connected\n';
  const stage2EngineLine = `STAGE2 ENGINE: ${effectiveStage2Engine.toUpperCase()}\n`;
  BrowserWindow.getAllWindows().forEach((w) => {
    w.webContents.send('pipeline:log', { stream: 'stdout', text: codexStatusLine });
    w.webContents.send('pipeline:log', { stream: 'stdout', text: stage2EngineLine });
  });
  broadcastCodexAuthLog({
    ts: new Date().toISOString(),
    level: 'info',
    step: 'pipeline.codex.status',
    details: {
      connected: codexContext.connected,
      provider: codexAuthStatus && codexAuthStatus.provider ? codexAuthStatus.provider : null,
      cliCommand: codexAuthStatus && codexAuthStatus.cliCommand ? codexAuthStatus.cliCommand : null,
      cliCommandExists:
        codexAuthStatus && Object.prototype.hasOwnProperty.call(codexAuthStatus, 'cliCommandExists')
          ? codexAuthStatus.cliCommandExists
          : null,
      expiresAt: codexContext.expiresAt,
      requestedStage2Engine,
      effectiveStage2Engine,
    },
  });

  let command;
  let args;
  let cwd;

  if (isBundled) {
    command = bundledRunner;
    args = ['--input', inputBase, '--output', outputBase, '--flow', 'importation', '--json'];
    cwd = app.getPath('userData');
    // Ensure it's executable on macOS/Linux.
    if (process.platform !== 'win32') {
      try {
        await fsp.chmod(command, 0o755);
      } catch {
        // ignore
      }
    }
  } else {
    command = pythonPath;
    args = [
      '-u',
      path.join(projectRoot, 'src', 'pipeline.py'),
      '--input',
      inputBase,
      '--output',
      outputBase,
      '--flow',
      'importation',
      '--json',
    ];
    cwd = projectRoot;
  }

  return await new Promise((resolve) => {
    let settled = false;
    const resolveOnce = (value) => {
      if (settled) return;
      settled = true;
      resolve(value);
    };

    const child = spawn(command, args, {
      cwd,
      env: runnerEnv,
    });

    let stdout = '';
    let stderr = '';

    child.stdout.on('data', (chunk) => {
      const text = chunk.toString();
      stdout += text;
      BrowserWindow.getAllWindows().forEach((w) => {
        w.webContents.send('pipeline:log', { stream: 'stdout', text });
      });
    });

    child.stderr.on('data', (chunk) => {
      const text = chunk.toString();
      stderr += text;
      BrowserWindow.getAllWindows().forEach((w) => {
        w.webContents.send('pipeline:log', { stream: 'stderr', text });
      });
    });

    child.on('error', (error) => {
      const message = String((error && error.message) || error || 'unknown spawn error');
      BrowserWindow.getAllWindows().forEach((w) => {
        w.webContents.send('pipeline:log', {
          stream: 'stderr',
          text: 'ERROR: failed to start pipeline process: ' + message + '\n',
        });
      });
      resolveOnce({
        ok: false,
        code: null,
        parsed: null,
        copied,
        runBase,
        inputBase,
        outputBase,
        codexAuth: codexContext,
        codexAuthContextPath: codexContextPath,
        reportPath: null,
        stderr: message,
        error: 'Failed to start pipeline process: ' + message,
      });
    });

    child.on('close', async (code) => {
      const reportPath = path.join(
        outputBase,
        'stage_04_report',
        'importation',
        '_stage04_report.html'
      );
      const debugReportPath = path.join(
        outputBase,
        'stage_05_debug_report',
        'importation',
        '_stage05_debug_report.html'
      );

      const stage04JsonPath = path.join(
        outputBase,
        'stage_04_report',
        'importation',
        '_stage04_report.json'
      );

      const reportExists = fs.existsSync(reportPath);
      const debugReportExists = fs.existsSync(debugReportPath);
      // Success is determined by the pipeline exiting OK *and* producing the Stage 4 HTML.
      // Parsing stdout is unreliable because it contains logs + pretty JSON.
      const ok = code === 0 && reportExists;

      const parsed = (await tryReadJsonFile(stage04JsonPath)) || extractLastJson(stdout);

      if (ok && fs.existsSync(reportPath)) {
        await shell.openPath(reportPath);
      }
      broadcastCodexAuthLog({
        ts: new Date().toISOString(),
        level: ok ? 'info' : 'warn',
        step: 'pipeline.run.finish',
        details: { ok, exitCode: code, reportExists },
      });
      resolveOnce({
        ok,
        code,
        parsed,
        copied,
        runBase,
        inputBase,
        outputBase,
        codexAuth: codexContext,
        codexAuthContextPath: codexContextPath,
        reportPath: reportExists ? reportPath : null,
        debugReportPath: debugReportExists ? debugReportPath : null,
        stderr: stderr || null,
      });
    });
  });
}

function createWindow() {
  const win = new BrowserWindow({
    width: 980,
    height: 720,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
    },
  });

  win.loadFile(path.join(__dirname, 'renderer', 'index.html'));
}

app.whenReady().then(async () => {
  createWindow();
  broadcastCodexAuthLog({
    ts: new Date().toISOString(),
    level: 'info',
    step: 'app.ready',
    details: { platform: process.platform },
  });
  const status = await codexAuth.getStatus({ autoRefresh: false });
  broadcastToAllWindows('codexAuth:changed', toRendererCodexStatus(status));

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});

ipcMain.handle('dialog:selectFiles', async () => {
  const result = await dialog.showOpenDialog({
    properties: ['openFile', 'multiSelections'],
    filters: [{ name: 'PDF', extensions: ['pdf'] }],
  });
  if (result.canceled) return [];
  return result.filePaths;
});

ipcMain.handle('pipeline:run', async (_event, payload) => {
  return await runPipeline(payload || {});
});

ipcMain.handle('codexAuth:getStatus', async (_event, payload) => {
  const options = payload || {};
  broadcastCodexAuthLog({
    ts: new Date().toISOString(),
    level: 'info',
    step: 'ipc.codexAuth.getStatus.request',
    details: { autoRefresh: Boolean(options.autoRefresh) },
  });
  const status = await codexAuth.getStatus(options);
  const safeStatus = toRendererCodexStatus(status);
  broadcastCodexAuthLog({
    ts: new Date().toISOString(),
    level: 'info',
    step: 'ipc.codexAuth.getStatus.response',
    details: {
      connected: safeStatus.connected,
      configured: safeStatus.configured,
      provider: safeStatus.provider,
    },
  });
  return safeStatus;
});

ipcMain.handle('codexAuth:start', async (_event, payload) => {
  broadcastCodexAuthLog({
    ts: new Date().toISOString(),
    level: 'info',
    step: 'ipc.codexAuth.start.request',
    details: null,
  });
  const result = await codexAuth.start(payload || {});
  const safeResult = toRendererCodexAuthResponse(result);
  broadcastCodexAuthLog({
    ts: new Date().toISOString(),
    level: safeResult && safeResult.ok ? 'info' : 'warn',
    step: 'ipc.codexAuth.start.response',
    details: {
      ok: Boolean(safeResult && safeResult.ok),
      source: safeResult && safeResult.source ? safeResult.source : null,
      error: safeResult && safeResult.error ? safeResult.error : null,
    },
  });
  return safeResult;
});

ipcMain.handle('codexAuth:refresh', async () => {
  broadcastCodexAuthLog({
    ts: new Date().toISOString(),
    level: 'info',
    step: 'ipc.codexAuth.refresh.request',
    details: null,
  });
  const result = await codexAuth.refresh();
  const safeResult = toRendererCodexAuthResponse(result);
  broadcastCodexAuthLog({
    ts: new Date().toISOString(),
    level: safeResult && safeResult.ok ? 'info' : 'warn',
    step: 'ipc.codexAuth.refresh.response',
    details: {
      ok: Boolean(safeResult && safeResult.ok),
      error: safeResult && safeResult.error ? safeResult.error : null,
    },
  });
  return safeResult;
});

ipcMain.handle('codexAuth:logout', async () => {
  broadcastCodexAuthLog({
    ts: new Date().toISOString(),
    level: 'info',
    step: 'ipc.codexAuth.logout.request',
    details: null,
  });
  const result = await codexAuth.logout();
  const safeResult = toRendererCodexAuthResponse(result);
  broadcastCodexAuthLog({
    ts: new Date().toISOString(),
    level: safeResult && safeResult.ok ? 'info' : 'warn',
    step: 'ipc.codexAuth.logout.response',
    details: {
      ok: Boolean(safeResult && safeResult.ok),
      error: safeResult && safeResult.error ? safeResult.error : null,
    },
  });
  return safeResult;
});

ipcMain.handle('projectRoot:get', async () => {
  const settings = await readSettings();
  const root = await resolveProjectRoot();
  return {
    projectRoot: root,
    configured: Boolean(settings.projectRoot),
    envOverride: Boolean(process.env.DOCREADER_PROJECT_ROOT),
    isPackaged: app.isPackaged,
    bundledRunner: Boolean(getBundledRunnerPath()),
  };
});

ipcMain.handle('projectRoot:select', async () => {
  const result = await dialog.showOpenDialog({
    properties: ['openDirectory'],
    title: 'Selecione a pasta do projeto (onde existe src/pipeline.py)',
  });
  if (result.canceled || !result.filePaths || result.filePaths.length === 0) {
    return { ok: false, canceled: true };
  }

  const selected = result.filePaths[0];
  if (!looksLikeProjectRoot(selected)) {
    return {
      ok: false,
      error: 'A pasta selecionada nao parece um project root valido (src/pipeline.py nao encontrado).',
    };
  }

  const settings = await readSettings();
  settings.projectRoot = selected;
  await writeSettings(settings);
  return { ok: true, projectRoot: selected };
});

ipcMain.handle('report:open', async (_event, reportPath) => {
  if (!reportPath || typeof reportPath !== 'string') {
    return { ok: false, error: 'Invalid report path' };
  }
  const result = await shell.openPath(reportPath);
  if (result) {
    return { ok: false, error: result };
  }
  return { ok: true };
});

