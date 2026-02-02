#!/usr/bin/env node

const fs = require('node:fs');
const path = require('node:path');
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

function langsToCheck() {
  const langs = (process.env.TESS_LANGS || 'eng+por')
    .split(/[+,]/)
    .map((s) => s.trim())
    .filter(Boolean);
  return langs.length ? langs : ['eng'];
}

function buildEnv(tesseractRoot) {
  const env = { ...process.env };

  const tessdataDir = path.join(tesseractRoot, 'tessdata');
  if (fs.existsSync(tessdataDir)) {
    env.TESSDATA_PREFIX = tesseractRoot;
  }

  const libDir = path.join(tesseractRoot, 'lib');
  if (fs.existsSync(libDir)) {
    if (process.platform === 'darwin') env.DYLD_LIBRARY_PATH = libDir;
    if (process.platform === 'linux') env.LD_LIBRARY_PATH = libDir;
  }

  return env;
}

function main() {
  const folder = platformFolder();
  const tesseractRoot = path.resolve(__dirname, '..', 'resources', 'tesseract', folder);
  const exeName = process.platform === 'win32' ? 'tesseract.exe' : 'tesseract';
  const exePath = path.join(tesseractRoot, exeName);

  if (!fs.existsSync(exePath)) {
    console.error(`ERROR: Tesseract not found: ${exePath}`);
    console.error('Run: npm run fetch:tesseract');
    process.exit(1);
  }

  const tessdataDir = path.join(tesseractRoot, 'tessdata');
  for (const lang of langsToCheck()) {
    const trained = path.join(tessdataDir, `${lang}.traineddata`);
    if (!fs.existsSync(trained)) {
      console.error(`ERROR: Missing tessdata: ${trained}`);
      process.exit(1);
    }
  }

  const res = spawnSync(exePath, ['--version'], {
    encoding: 'utf-8',
    env: buildEnv(tesseractRoot),
  });

  if (res.error) {
    console.error(`ERROR: Failed to run tesseract: ${res.error.message}`);
    process.exit(1);
  }

  if (res.status !== 0) {
    console.error('ERROR: tesseract --version failed');
    if (res.stdout) process.stderr.write(res.stdout);
    if (res.stderr) process.stderr.write(res.stderr);
    process.exit(res.status || 1);
  }

  process.stdout.write(res.stdout || '');
  console.log('OK: Tesseract smoke test passed');
}

main();
