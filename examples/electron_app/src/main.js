const { app, BrowserWindow, ipcMain, dialog, shell } = require('electron');
const path = require('path');
const fs = require('fs');
const fsp = require('fs/promises');
const { spawn, spawnSync } = require('child_process');
const { createCodexAuthManager } = require('./codexAuth');
const CODEX_CLI_PREFLIGHT_TIMEOUT_MS = 15000;

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

function createRunLogger(logPath) {
  function safeJson(value) {
    if (value === null || typeof value === 'undefined') return 'null';
    if (typeof value === 'string') return value;
    try {
      return JSON.stringify(value);
    } catch {
      return String(value);
    }
  }

  function appendLine(level, message, details) {
    const parts = [`[${new Date().toISOString()}] [${String(level || 'INFO').toUpperCase()}] ${String(message || '')}`];
    if (typeof details !== 'undefined') {
      parts.push(`:: ${safeJson(details)}`);
    }
    const line = parts.join(' ') + '\n';
    try {
      fs.appendFileSync(logPath, line, 'utf8');
    } catch {
      // Keep pipeline running even if log file write fails.
    }
  }

  return {
    path: logPath,
    info: (message, details) => appendLine('INFO', message, details),
    warn: (message, details) => appendLine('WARN', message, details),
    error: (message, details) => appendLine('ERROR', message, details),
    stream: (stream, text) => {
      const payload = String(text || '');
      if (!payload) return;
      const lines = payload.split(/\r?\n/);
      for (const line of lines) {
        if (!line) continue;
        appendLine(stream === 'stderr' ? 'STDERR' : 'STDOUT', line);
      }
    },
  };
}

function probeCodexCli(command, cwd, timeoutMs = CODEX_CLI_PREFLIGHT_TIMEOUT_MS) {
  const result = spawnSync(command, ['--version'], {
    cwd,
    encoding: 'utf8',
    windowsHide: true,
    shell: process.platform === 'win32',
    timeout: Math.max(1, Number(timeoutMs) || CODEX_CLI_PREFLIGHT_TIMEOUT_MS),
  });

  const stdout = String(result.stdout || '').trim();
  const stderr = String(result.stderr || '').trim();
  const details = stderr || stdout || '';

  if (result.error) {
    const errorText = String(result.error.message || result.error);
    const timedOut =
      result.error.code === 'ETIMEDOUT' || /timed out|timeout/i.test(errorText);
    return {
      ok: false,
      error: errorText,
      errorCode: result.error.code || null,
      stdout,
      stderr,
      timedOut,
    };
  }

  if (result.status !== 0) {
    return {
      ok: false,
      error: details || `exit ${result.status}`,
      errorCode: null,
      stdout,
      stderr,
      timedOut: false,
    };
  }

  return {
    ok: true,
    version: details || 'unknown',
    stdout,
    stderr,
    timedOut: false,
  };
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

function mapDocTypeToPrefix(docType, flow) {
  const selectedFlow = normalizeFlow(flow);
  const up = String(docType || '').trim().toUpperCase();

  if (selectedFlow === 'exportation') {
    switch (up) {
      case 'COMMERCIAL INVOICE':
      case 'COMMERCIAL_INVOICE':
      case 'COMMERCIALINVOICE':
      case 'INVOICE':
        return 'COMMERCIAL INVOICE';
      case 'PACKING LIST':
      case 'PACKING_LIST':
      case 'PACKINGLIST':
        return 'PACKING LIST';
      case 'DRAFT BL':
      case 'DRAFT_BL':
      case 'DRAFTBL':
      case 'BL':
      case 'BILL OF LADING':
      case 'BILL_OF_LADING':
        return 'DRAFT BL';
      case 'CERTIFICATE OF ORIGIN':
      case 'CERTIFICATE_OF_ORIGIN':
      case 'CERTIFICATEOFORIGIN':
      case 'CO':
        return 'CERTIFICATE OF ORIGIN';
      case 'CONTAINER DATA':
      case 'CONTAINER_DATA':
      case 'CONTAINERDATA':
      case 'CNTR':
        return 'CONTAINER DATA';
      default:
        return 'DOC';
    }
  }

  switch (up) {
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


function resolveDocTypeFromUi(item, flow) {
  const provided = String(item?.docType || '').trim();
  return mapDocTypeToPrefix(provided, flow);
}

function normalizeFlow(value) {
  return String(value || '').trim().toLowerCase() === 'exportation' ? 'exportation' : 'importation';
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

function toStageDocKind(prefix, flow) {
  const selectedFlow = normalizeFlow(flow);
  if (selectedFlow === 'exportation') {
    switch (prefix) {
      case 'COMMERCIAL INVOICE':
        return 'commercial_invoice';
      case 'PACKING LIST':
        return 'packing_list';
      case 'DRAFT BL':
        return 'draft_bl';
      case 'CERTIFICATE OF ORIGIN':
        return 'certificate_of_origin';
      case 'CONTAINER DATA':
        return 'container_data';
      default:
        return 'unknown';
    }
  }

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

function createDocCounters(flow) {
  const selectedFlow = normalizeFlow(flow);
  if (selectedFlow === 'exportation') {
    return {
      'COMMERCIAL INVOICE': 0,
      'PACKING LIST': 0,
      'DRAFT BL': 0,
      'CERTIFICATE OF ORIGIN': 0,
      'CONTAINER DATA': 0,
    };
  }
  return { BL: 0, HBL: 0, INVOICE: 0, 'PACKING LIST': 0, DI: 0, LI: 0 };
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

async function runPipeline({ files, stage2Engine, flow }) {
  const requestedFlow = normalizeFlow(flow);
  const requestedStage2Engine = String(stage2Engine || 'regex').trim().toLowerCase() === 'llm' ? 'llm' : 'regex';
  const effectiveStage2Engine = requestedStage2Engine;
  broadcastCodexAuthLog({
    ts: new Date().toISOString(),
    level: 'info',
    step: 'pipeline.run.begin',
    details: {
      fileCount: Array.isArray(files) ? files.length : 0,
      stage2Engine: requestedStage2Engine,
      requestedFlow,
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
  const rawDir = path.join(inputBase, requestedFlow, 'raw');

  await ensureDir(rawDir);
  await ensureDir(outputBase);

  const runLogPath = path.join(runBase, 'pipeline_debug.log');
  const runLog = createRunLogger(runLogPath);
  runLog.info('pipeline.run.context', {
    runBase,
    inputBase,
    outputBase,
    isBundled,
    requestedFlow,
    requestedStage2Engine,
    effectiveStage2Engine,
    fileCount: Array.isArray(files) ? files.length : 0,
  });
  BrowserWindow.getAllWindows().forEach((w) => {
    w.webContents.send('pipeline:log', {
      stream: 'stdout',
      text: `RUN LOG: ${runLogPath}\n`,
    });
  });
  const selectedFlowLine = `FLOW SELECTED (UI): ${requestedFlow.toUpperCase()}\n`;
  runLog.stream('stdout', selectedFlowLine);
  BrowserWindow.getAllWindows().forEach((w) => {
    w.webContents.send('pipeline:log', { stream: 'stdout', text: selectedFlowLine });
  });
  // Copy files into run input folder with names that help doc detection.
  const counters = createDocCounters(requestedFlow);
  const copied = [];
  const docTypeHints = {};
  const originalFileNames = {};

  for (const item of files || []) {
    const srcPath = item.path;
    const typePrefix = resolveDocTypeFromUi(item, requestedFlow);
    if (typePrefix === 'DOC') {
      runLog.error('pipeline.input.invalid_doc_type', {
        sourcePath: srcPath || null,
        uiDocType: item && item.docType ? item.docType : null,
      });
      return {
        ok: false,
        error: `Invalid document type from UI for file: ${path.basename(srcPath || '')}`,
        runLogPath,
      };
    }
    counters[typePrefix] = (counters[typePrefix] || 0) + 1;

    const suffix = counters[typePrefix] === 1 ? '' : ` ${counters[typePrefix]}`;
    const destName = `${typePrefix}${suffix}.pdf`;
    const destPath = path.join(rawDir, destName);

    await fsp.copyFile(srcPath, destPath);
    const originalName = path.basename(srcPath || destName);
    runLog.info('pipeline.input.copy', {
      sourcePath: srcPath,
      destPath,
      resolvedType: typePrefix,
      providedDocType: item.docType || null,
      originalName,
    });
    copied.push({
      from: srcPath,
      to: destPath,
      docType: item.docType || null,
      resolvedType: typePrefix,
      originalName,
    });

    docTypeHints[destName] = toStageDocKind(typePrefix, requestedFlow);
    originalFileNames[destName] = originalName;
  }

  const hintFile = path.join(rawDir, '_doc_type_hints.json');
  await fsp.writeFile(hintFile, JSON.stringify(docTypeHints, null, 2), 'utf-8');
  runLog.info('pipeline.input.hints_written', { hintFile, hints: docTypeHints });

  const originalNameFile = path.join(rawDir, '_original_file_names.json');
  await fsp.writeFile(originalNameFile, JSON.stringify(originalFileNames, null, 2), 'utf-8');
  runLog.info('pipeline.input.original_names_written', {
    originalNameFile,
    names: originalFileNames,
  });

  if (copied.length) {
    const lines = copied
      .map((c) => `INPUT MAP: [${c.resolvedType}] ${path.basename(c.from)} -> ${path.basename(c.to)}`)
      .join('\n') + '\n';
    runLog.stream('stdout', lines);
    BrowserWindow.getAllWindows().forEach((w) => {
      w.webContents.send('pipeline:log', { stream: 'stdout', text: lines });
    });
  }

  const tessEnv = getBundledTesseractEnv();
  const runnerEnv = tessEnv ? { ...process.env, ...tessEnv } : { ...process.env };
  runnerEnv.PYTHONUNBUFFERED = '1';
  runnerEnv.DOCREADER_RUN_DEBUG_LOG_FILE = runLogPath;
  runnerEnv.DOCREADER_STAGE2_LLM_DETAILED_LOG =
    process.env.DOCREADER_STAGE2_LLM_DETAILED_LOG || '1';
  runnerEnv.DOCREADER_STAGE2_ENGINE = effectiveStage2Engine;
  // Keep strict behavior by default: LLM engine should stay LLM unless explicitly overridden.
  runnerEnv.DOCREADER_STAGE2_LLM_FALLBACK_REGEX =
    process.env.DOCREADER_STAGE2_LLM_FALLBACK_REGEX || '0';
  if (codexAuthStatus && codexAuthStatus.cliCommand) {
    runnerEnv.DOCREADER_CODEX_CLI_PATH = String(codexAuthStatus.cliCommand);
  }
  runLog.info('pipeline.env.stage2', {
    requestedStage2Engine,
    effectiveStage2Engine,
    fallbackRegex: runnerEnv.DOCREADER_STAGE2_LLM_FALLBACK_REGEX,
    llmDetailedLog: runnerEnv.DOCREADER_STAGE2_LLM_DETAILED_LOG,
    codexCliPath: runnerEnv.DOCREADER_CODEX_CLI_PATH || null,
    bundledTesseract: Boolean(tessEnv),
  });

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
  runLog.info('pipeline.codex.context', {
    codexContextPath,
    connected: codexContext.connected,
    provider: codexAuthStatus && codexAuthStatus.provider ? codexAuthStatus.provider : null,
    cliCommand: codexAuthStatus && codexAuthStatus.cliCommand ? codexAuthStatus.cliCommand : null,
    cliCommandExists:
      codexAuthStatus && Object.prototype.hasOwnProperty.call(codexAuthStatus, 'cliCommandExists')
        ? codexAuthStatus.cliCommandExists
        : null,
  });

  const codexStatusLine = codexContext.connected
    ? 'CODEX AUTH: connected (token available to stage_02)\n'
    : 'CODEX AUTH: not connected\n';
  const stage2EngineRequestedLine = `STAGE2 ENGINE REQUESTED: ${requestedStage2Engine.toUpperCase()}\n`;
  const stage2EngineEffectiveLine = `STAGE2 ENGINE EFFECTIVE: ${effectiveStage2Engine.toUpperCase()}\n`;
  runLog.stream('stdout', codexStatusLine);
  runLog.stream('stdout', stage2EngineRequestedLine);
  runLog.stream('stdout', stage2EngineEffectiveLine);
  BrowserWindow.getAllWindows().forEach((w) => {
    w.webContents.send('pipeline:log', { stream: 'stdout', text: codexStatusLine });
    w.webContents.send('pipeline:log', { stream: 'stdout', text: stage2EngineRequestedLine });
    w.webContents.send('pipeline:log', { stream: 'stdout', text: stage2EngineEffectiveLine });
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

  if (requestedStage2Engine === 'llm' && !codexContext.connected) {
    const guidance =
      'Codex auth obrigatoria para Stage 02 LLM. Conecte o Codex na UI e tente novamente.';
    const details = 'Token/sessao Codex indisponivel para este run.';
    runLog.error('pipeline.codex.auth.preflight_failed', {
      requestedStage2Engine,
      connected: codexContext.connected,
      details,
    });
    BrowserWindow.getAllWindows().forEach((w) => {
      w.webContents.send('pipeline:log', {
        stream: 'stderr',
        text: `ERROR: ${guidance}\nDETAILS: ${details}\n`,
      });
    });
    return {
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
      stderr: details,
      error: guidance,
      requestedStage2Engine,
      effectiveStage2Engine,
      runLogPath,
    };
  }

  if (effectiveStage2Engine === 'llm') {
    const cliCommand = String(runnerEnv.DOCREADER_CODEX_CLI_PATH || codexAuthStatus?.cliCommand || 'codex');
    const cliProbe = probeCodexCli(cliCommand, app.getPath('userData'), CODEX_CLI_PREFLIGHT_TIMEOUT_MS);
    runLog.info('pipeline.codex.cli.preflight', {
      command: cliCommand,
      ok: cliProbe.ok,
      version: cliProbe.version || null,
      error: cliProbe.error || null,
      errorCode: cliProbe.errorCode || null,
      timedOut: Boolean(cliProbe.timedOut),
      timeoutMs: CODEX_CLI_PREFLIGHT_TIMEOUT_MS,
    });

    if (!cliProbe.ok) {
      const details = cliProbe.error || 'unknown error';
      const guidance = cliProbe.timedOut
        ? `Codex CLI preflight excedeu ${Math.floor(CODEX_CLI_PREFLIGHT_TIMEOUT_MS / 1000)}s.`
        : "Codex CLI indisponivel para Stage 02 LLM. Instale/garanta o comando 'codex' no sistema ou configure DOCREADER_CODEX_CLI_PATH.";
      const text = `ERROR: ${guidance}\nDETAILS: ${details}\n`;
      runLog.error('pipeline.codex.cli.preflight_failed', {
        command: cliCommand,
        error: details,
        timedOut: Boolean(cliProbe.timedOut),
        stderr: cliProbe.stderr || null,
        stdout: cliProbe.stdout || null,
      });
      BrowserWindow.getAllWindows().forEach((w) => {
        w.webContents.send('pipeline:log', { stream: 'stderr', text });
      });
      return {
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
        stderr: details,
        error: guidance,
        requestedStage2Engine,
        effectiveStage2Engine,
        runLogPath,
      };
    }
  } else {
    runLog.info('pipeline.codex.cli.preflight', {
      skipped: true,
      reason: 'stage2_engine_not_llm',
      effectiveStage2Engine,
    });
  }

  let command;
  let args;
  let cwd;

  if (isBundled) {
    command = bundledRunner;
    args = ['--input', inputBase, '--output', outputBase, '--flow', requestedFlow, '--json'];
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
      requestedFlow,
      '--json',
    ];
    cwd = projectRoot;
  }
  runLog.info('pipeline.process.command', { command, args, cwd });

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
    runLog.info('pipeline.process.spawned', { pid: child && child.pid ? child.pid : null });

    let stdout = '';
    let stderr = '';

    child.stdout.on('data', (chunk) => {
      const text = chunk.toString();
      stdout += text;
      runLog.stream('stdout', text);
      BrowserWindow.getAllWindows().forEach((w) => {
        w.webContents.send('pipeline:log', { stream: 'stdout', text });
      });
    });

    child.stderr.on('data', (chunk) => {
      const text = chunk.toString();
      stderr += text;
      runLog.stream('stderr', text);
      BrowserWindow.getAllWindows().forEach((w) => {
        w.webContents.send('pipeline:log', { stream: 'stderr', text });
      });
    });

    child.on('error', (error) => {
      const message = String((error && error.message) || error || 'unknown spawn error');
      runLog.error('pipeline.process.error', { message });
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
        requestedStage2Engine,
        effectiveStage2Engine,
        runLogPath,
      });
    });

    child.on('close', async (code) => {
      const reportPath = path.join(
        outputBase,
        'stage_04_report',
        requestedFlow,
        '_stage04_report.html'
      );
      const debugReportPath = path.join(
        outputBase,
        'stage_05_debug_report',
        requestedFlow,
        '_stage05_debug_report.html'
      );

      const stage04JsonPath = path.join(
        outputBase,
        'stage_04_report',
        requestedFlow,
        '_stage04_report.json'
      );

      const reportExists = fs.existsSync(reportPath);
      const debugReportExists = fs.existsSync(debugReportPath);
      // Success is determined by the pipeline exiting OK *and* producing the Stage 4 HTML.
      // Parsing stdout is unreliable because it contains logs + pretty JSON.
      const ok = code === 0 && reportExists;
      runLog.info('pipeline.process.closed', {
        exitCode: code,
        ok,
        reportExists,
        debugReportExists,
        stdoutChars: stdout.length,
        stderrChars: stderr.length,
      });

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
        requestedStage2Engine,
        effectiveStage2Engine,
        runLogPath,
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

ipcMain.handle('app:getMeta', async () => {
  let releaseTag = null;
  try {
    const appPackagePath = path.join(app.getAppPath(), 'package.json');
    const raw = await fsp.readFile(appPackagePath, 'utf-8');
    const parsed = JSON.parse(raw);
    if (parsed && typeof parsed.docReaderReleaseTag === 'string') {
      const normalized = parsed.docReaderReleaseTag.trim();
      if (normalized) releaseTag = normalized;
    }
  } catch {
    // Keep metadata endpoint resilient even if package.json can't be read.
  }

  return {
    name: app.getName(),
    isPackaged: app.isPackaged,
    releaseTag,
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

