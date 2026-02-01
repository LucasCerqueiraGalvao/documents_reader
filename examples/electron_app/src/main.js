const { app, BrowserWindow, ipcMain, dialog, shell } = require('electron');
const path = require('path');
const fs = require('fs');
const fsp = require('fs/promises');
const { spawn } = require('child_process');

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

  if (!fs.existsSync(exePath)) return null;
  const env = { TESSERACT_EXE: exePath };
  if (fs.existsSync(tessdataDir)) {
    env.TESSDATA_PREFIX = tessdataDir;
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

function detectVenvPython(projectRoot) {
  const candidates = [
    path.join(projectRoot, '.venv', 'bin', 'python'),
    path.join(projectRoot, '.venv', 'bin', 'python3'),
    path.join(projectRoot, '.venv', 'Scripts', 'python.exe')
  ];
  for (const p of candidates) {
    if (fs.existsSync(p)) return p;
  }
  return null;
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
    case 'INVOICE':
      return 'INVOICE';
    case 'PACKING LIST':
    case 'PACKING_LIST':
    case 'PACKINGLIST':
      return 'PACKING LIST';
    default:
      return 'DOC';
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

async function runPipeline({ files }) {
  const bundledRunner = getBundledRunnerPath();
  const projectRoot = await resolveProjectRoot();
  const isBundled = app.isPackaged;

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
    pythonPath = detectVenvPython(projectRoot);
    if (!pythonPath) {
      return {
        ok: false,
        error:
          'Python venv not found in the selected project. Create it at .venv and install requirements (see README).',
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
  const counters = { BL: 0, INVOICE: 0, 'PACKING LIST': 0, DOC: 0 };
  const copied = [];

  for (const item of files || []) {
    const srcPath = item.path;
    const typePrefix = mapDocTypeToPrefix(item.docType);
    counters[typePrefix] = (counters[typePrefix] || 0) + 1;

    const suffix = counters[typePrefix] === 1 ? '' : ` ${counters[typePrefix]}`;
    const destName = `${typePrefix}${suffix}.pdf`;
    const destPath = path.join(rawDir, destName);

    await fsp.copyFile(srcPath, destPath);
    copied.push({ from: srcPath, to: destPath, docType: item.docType || null });
  }

  const tessEnv = getBundledTesseractEnv();
  const runnerEnv = tessEnv ? { ...process.env, ...tessEnv } : { ...process.env };

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

    child.on('close', async (code) => {
      const reportPath = path.join(
        outputBase,
        'stage_04_report',
        'importation',
        '_stage04_report.html'
      );

      const stage04JsonPath = path.join(
        outputBase,
        'stage_04_report',
        'importation',
        '_stage04_report.json'
      );

      const reportExists = fs.existsSync(reportPath);
      // Success is determined by the pipeline exiting OK *and* producing the Stage 4 HTML.
      // Parsing stdout is unreliable because it contains logs + pretty JSON.
      const ok = code === 0 && reportExists;

      const parsed = (await tryReadJsonFile(stage04JsonPath)) || extractLastJson(stdout);

      if (ok && fs.existsSync(reportPath)) {
        await shell.openPath(reportPath);
      }

      resolve({
        ok,
        code,
        parsed,
        copied,
        runBase,
        inputBase,
        outputBase,
        reportPath: reportExists ? reportPath : null,
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

app.whenReady().then(() => {
  createWindow();

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
      error: 'A pasta selecionada não parece um project root válido (src/pipeline.py não encontrado).',
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
