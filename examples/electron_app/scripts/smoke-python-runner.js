const path = require('node:path');
const fs = require('node:fs');
const fsp = require('node:fs/promises');
const os = require('node:os');
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

async function ensureDir(p) {
  await fsp.mkdir(p, { recursive: true });
}

async function copyFile(src, dest) {
  await ensureDir(path.dirname(dest));
  await fsp.copyFile(src, dest);
}

async function main() {
  const appRoot = path.resolve(__dirname, '..');
  const repoRoot = path.resolve(appRoot, '..', '..');

  const folder = platformFolder();
  const exe = process.platform === 'win32' ? 'docreader-runner.exe' : 'docreader-runner';
  const runner = path.join(appRoot, 'resources', 'python', folder, exe);

  if (!fs.existsSync(runner)) {
    console.error(`ERROR: bundled runner not found: ${runner}`);
    console.error('Run: npm run build:python');
    process.exit(1);
  }

  const tmpBase = await fsp.mkdtemp(path.join(os.tmpdir(), 'docreader-smoke-runner-'));
  const inputBase = path.join(tmpBase, 'input');
  const outputBase = path.join(tmpBase, 'output');

  const sampleRaw = path.join(repoRoot, 'data', 'input', 'importation', 'raw');
  const destRaw = path.join(inputBase, 'importation', 'raw');

  await ensureDir(destRaw);
  await ensureDir(outputBase);

  const sampleFiles = ['BL.pdf', 'INVOICE.pdf', 'PACKING LIST.pdf'];
  for (const name of sampleFiles) {
    const src = path.join(sampleRaw, name);
    if (!fs.existsSync(src)) {
      console.error(`ERROR: sample file not found: ${src}`);
      process.exit(1);
    }
    await copyFile(src, path.join(destRaw, name));
  }

  const res = spawnSync(
    runner,
    ['--input', inputBase, '--output', outputBase, '--flow', 'importation', '--json'],
    { stdio: 'inherit' }
  );

  if (res.error) throw res.error;
  if (res.status !== 0) {
    console.error(`ERROR: runner exited with code ${res.status}`);
    process.exit(res.status);
  }

  const report = path.join(
    outputBase,
    'stage_04_report',
    'importation',
    '_stage04_report.html'
  );

  if (!fs.existsSync(report)) {
    console.error(`ERROR: Stage 4 HTML not found: ${report}`);
    process.exit(1);
  }

  console.log(`\nOK: runner produced Stage 4 report: ${report}`);
}

main().catch((e) => {
  console.error(`ERROR: ${e?.stack || e}`);
  process.exit(1);
});
