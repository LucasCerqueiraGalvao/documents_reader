#!/usr/bin/env node

/**
 * Headless smoke test for the pipeline runner.
 *
 * It does NOT launch Electron.
 * It verifies:
 * - `.venv/bin/python` exists in repo root
 * - sample PDFs exist in `data/input/importation/raw`
 * - pipeline runs successfully and generates Stage 4 HTML
 */

const fs = require('node:fs');
const fsp = require('node:fs/promises');
const path = require('node:path');
const { spawn } = require('node:child_process');

function repoRoot() {
  // examples/electron_app/scripts -> repo root
  return path.resolve(__dirname, '..', '..', '..');
}

function detectVenvPython(root) {
  const candidates = [
    path.join(root, '.venv', 'bin', 'python'),
    path.join(root, '.venv', 'bin', 'python3'),
    path.join(root, '.venv', 'Scripts', 'python.exe'),
  ];
  return candidates.find((p) => fs.existsSync(p)) || null;
}

async function ensureDir(p) {
  await fsp.mkdir(p, { recursive: true });
}

async function copyFile(src, dst) {
  await ensureDir(path.dirname(dst));
  await fsp.copyFile(src, dst);
}

function runId() {
  return new Date().toISOString().replaceAll(':', '-').replaceAll('.', '-');
}

async function main() {
  const root = repoRoot();
  const python = detectVenvPython(root);
  if (!python) {
    console.error('ERROR: venv python not found at .venv. Create venv and install requirements.');
    process.exit(2);
  }

  const sampleRaw = path.join(root, 'data', 'input', 'importation', 'raw');
  const required = ['BL.pdf', 'INVOICE.pdf', 'PACKING LIST.pdf'];
  for (const f of required) {
    const p = path.join(sampleRaw, f);
    if (!fs.existsSync(p)) {
      console.error(`ERROR: missing sample input: ${p}`);
      process.exit(3);
    }
  }

  const runBase = path.join(root, '.electron_runs_smoke', runId());
  const inputBase = path.join(runBase, 'input');
  const outputBase = path.join(runBase, 'output');
  const rawDir = path.join(inputBase, 'importation', 'raw');

  await ensureDir(rawDir);
  await ensureDir(outputBase);

  // Copy samples
  await copyFile(path.join(sampleRaw, 'BL.pdf'), path.join(rawDir, 'BL.pdf'));
  await copyFile(path.join(sampleRaw, 'INVOICE.pdf'), path.join(rawDir, 'INVOICE.pdf'));
  await copyFile(path.join(sampleRaw, 'PACKING LIST.pdf'), path.join(rawDir, 'PACKING LIST.pdf'));

  const args = [
    path.join(root, 'src', 'pipeline.py'),
    '--input',
    inputBase,
    '--output',
    outputBase,
    '--flow',
    'importation',
    '--json',
  ];

  console.log('Running:', python, args.join(' '));

  const child = spawn(python, args, { cwd: root, env: { ...process.env } });

  let stdout = '';
  let stderr = '';

  child.stdout.on('data', (c) => {
    const t = c.toString();
    stdout += t;
    process.stdout.write(t);
  });
  child.stderr.on('data', (c) => {
    const t = c.toString();
    stderr += t;
    process.stderr.write(t);
  });

  const code = await new Promise((resolve) => child.on('close', resolve));

  const reportPath = path.join(
    outputBase,
    'stage_04_report',
    'importation',
    '_stage04_report.html'
  );

  if (code !== 0) {
    console.error(`ERROR: pipeline exited with code ${code}`);
    process.exit(10);
  }

  if (!fs.existsSync(reportPath)) {
    console.error('ERROR: Stage 4 HTML not found:', reportPath);
    console.error('STDERR:', stderr || '(empty)');
    console.error('STDOUT tail:', stdout.slice(-1500));
    process.exit(11);
  }

  console.log('\nOK: Stage 4 HTML generated at:', reportPath);
}

main().catch((e) => {
  console.error('FATAL:', e);
  process.exit(1);
});
