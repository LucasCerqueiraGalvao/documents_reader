const path = require('node:path');
const fs = require('node:fs');
const fsp = require('node:fs/promises');
const { spawnSync } = require('node:child_process');

function platformFolder() {
  switch (process.platform) {
    case 'darwin':
      return 'mac';
    case 'win32':
      return 'win';
    default:
      return 'linux';
  }
}

function findVenvPython(repoRoot) {
  const candidates = [
    path.join(repoRoot, '.venv', 'bin', 'python3'),
    path.join(repoRoot, '.venv', 'bin', 'python'),
    path.join(repoRoot, '.venv', 'Scripts', 'python.exe'),
  ];
  for (const p of candidates) {
    if (fs.existsSync(p)) return p;
  }
  return null;
}

function run(cmd, args, opts = {}) {
  const res = spawnSync(cmd, args, { stdio: 'inherit', ...opts });
  if (res.error) throw res.error;
  if (res.status !== 0) {
    throw new Error(`Command failed (${res.status}): ${cmd} ${args.join(' ')}`);
  }
}

async function main() {
  const appRoot = path.resolve(__dirname, '..');
  const repoRoot = path.resolve(appRoot, '..', '..');

  const py = findVenvPython(repoRoot);
  if (!py) {
    console.error('ERROR: repo .venv not found. Create it at repo root and install requirements first.');
    console.error(String.raw`Expected one of: .venv/bin/python3, .venv/bin/python, .venv\Scripts\python.exe`);
    process.exit(1);
  }

  const folder = platformFolder();
  const runnerScript = path.join(appRoot, 'python', 'runner.py');
  const distPath = path.join(appRoot, 'resources', 'python', folder);
  const workPath = path.join(appRoot, 'build', 'pyinstaller-work', folder);
  const specPath = path.join(appRoot, 'build', 'pyinstaller-spec', folder);

  await fsp.mkdir(distPath, { recursive: true });
  await fsp.mkdir(workPath, { recursive: true });
  await fsp.mkdir(specPath, { recursive: true });

  // Ensure build tooling exists in the venv.
  run(py, ['-m', 'pip', 'install', '--upgrade', 'pip']);
  run(py, ['-m', 'pip', 'install', '--upgrade', 'pyinstaller']);

  // Build one-file runner executable.
  run(py, [
    '-m',
    'PyInstaller',
    '--noconfirm',
    '--clean',
    '--onefile',
    '--name',
    'docreader-runner',
    '--distpath',
    distPath,
    '--workpath',
    workPath,
    '--specpath',
    specPath,
    '--paths',
    path.join(repoRoot, 'src'),
    runnerScript,
  ]);

  const exe = process.platform === 'win32' ? 'docreader-runner.exe' : 'docreader-runner';
  const out = path.join(distPath, exe);
  if (!fs.existsSync(out)) {
    console.error(`ERROR: build succeeded but output not found: ${out}`);
    process.exit(1);
  }

  console.log(`\nOK: Built bundled runner: ${out}`);
}

main().catch((e) => {
  console.error(`ERROR: ${e?.stack || e}`);
  process.exit(1);
});
