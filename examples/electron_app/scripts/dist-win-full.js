#!/usr/bin/env node

const { spawnSync } = require('node:child_process');

function run(cmd, args, opts = {}) {
  const res = spawnSync(cmd, args, { stdio: 'inherit', ...opts });
  if (res.error) throw res.error;
  if (res.status !== 0) {
    throw new Error(`Command failed (${res.status}): ${cmd} ${args.join(' ')}`);
  }
}

function main() {
  if (process.platform !== 'win32') {
    console.error('ERROR: dist:win:full must be run on Windows (win32).');
    console.error('Reason: PyInstaller cannot cross-compile Windows executables from macOS/Linux.');
    process.exit(2);
  }

  const npmCmd = 'npm.cmd';

  // Build embedded Python runner
  run(npmCmd, ['run', 'build:python']);

  // Fetch/pack Tesseract into resources/tesseract/win
  run(npmCmd, ['run', 'fetch:tesseract']);

  // Validate both artifacts before producing the installer
  run(npmCmd, ['run', 'smoke:python']);
  run(npmCmd, ['run', 'smoke:tesseract']);

  // Build NSIS installer
  run(npmCmd, ['run', 'dist:win']);
}

main();
