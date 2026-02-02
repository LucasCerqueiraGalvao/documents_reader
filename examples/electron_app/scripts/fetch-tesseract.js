#!/usr/bin/env node

/**
 * Fetch/pack Tesseract into examples/electron_app/resources/tesseract/<platform>/
 *
 * Windows-first:
 * - downloads the UB Mannheim installer
 * - runs a silent install into resources/tesseract/win
 * - ensures tessdata contains at least eng+por traineddata
 *
 * macOS:
 * - packages a locally installed `tesseract` (Homebrew recommended)
 * - copies dependent Homebrew dylibs into resources/tesseract/mac/lib
 * - attempts to relink the binary/libs to be relocatable
 * - ensures tessdata contains at least eng+por traineddata
 *
 * Linux:
 * - currently prints guidance (packaging a relocatable tesseract is distro-specific)
 */

const fs = require('node:fs');
const fsp = require('node:fs/promises');
const path = require('node:path');
const os = require('node:os');
const https = require('node:https');
const { spawnSync } = require('node:child_process');

function appRoot() {
  return path.resolve(__dirname, '..');
}

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

function run(cmd, args, opts = {}) {
  const res = spawnSync(cmd, args, { stdio: 'inherit', ...opts });
  if (res.error) throw res.error;
  if (res.status !== 0) {
    throw new Error(`Command failed (${res.status}): ${cmd} ${args.join(' ')}`);
  }
}

function runCapture(cmd, args, opts = {}) {
  const res = spawnSync(cmd, args, { encoding: 'utf-8', ...opts });
  if (res.error) throw res.error;
  if (res.status !== 0) {
    const stderr = (res.stderr || '').toString();
    throw new Error(`Command failed (${res.status}): ${cmd} ${args.join(' ')}\n${stderr}`);
  }
  return (res.stdout || '').toString();
}

function commandExists(cmd) {
  // Cross-platform enough for our purposes: this script only uses it for macOS.
  const res = spawnSync('which', [cmd], { stdio: 'ignore' });
  return !res.error && res.status === 0;
}

async function ensureDir(p) {
  await fsp.mkdir(p, { recursive: true });
}

async function rmrf(p) {
  await fsp.rm(p, { recursive: true, force: true });
}

function parseArgs() {
  const args = process.argv.slice(2);
  const out = { clean: false };
  for (let i = 0; i < args.length; i++) {
    const a = args[i];
    if (a === '--clean') out.clean = true;
  }
  return out;
}

function downloadToFile(url, destPath) {
  return new Promise((resolve, reject) => {
    const doReq = (u, redirectsLeft) => {
      https
        .get(u, (res) => {
          if (res.statusCode && res.statusCode >= 300 && res.statusCode < 400 && res.headers.location) {
            if (redirectsLeft <= 0) {
              reject(new Error('Too many redirects while downloading'));
              return;
            }
            doReq(res.headers.location, redirectsLeft - 1);
            return;
          }

          if (res.statusCode !== 200) {
            reject(new Error(`Download failed: ${res.statusCode} ${res.statusMessage}`));
            return;
          }

          const file = fs.createWriteStream(destPath);
          res.pipe(file);
          file.on('finish', () => file.close(() => resolve()));
          file.on('error', reject);
        })
        .on('error', reject);
    };

    doReq(url, 5);
  });
}

async function fetchTraineddata(destTessdataDir) {
  // Keep it small: only what we need by default.
  const langs = (process.env.TESS_LANGS || 'eng+por')
    .split(/[+,]/)
    .map((s) => s.trim())
    .filter(Boolean);

  const variant = (process.env.TESSDATA_VARIANT || 'fast').toLowerCase();
  const repo = variant === 'best' ? 'tessdata_best' : 'tessdata_fast';

  await ensureDir(destTessdataDir);

  for (const lang of langs) {
    const file = `${lang}.traineddata`;
    const url = `https://github.com/tesseract-ocr/${repo}/raw/main/${file}`;
    const dest = path.join(destTessdataDir, file);
    if (fs.existsSync(dest)) continue;

    console.log(`Downloading tessdata: ${file} (${repo})`);
    const tmp = path.join(os.tmpdir(), `docreader-${file}-${Date.now()}`);
    await downloadToFile(url, tmp);
    await fsp.rename(tmp, dest);
  }
}

async function findFileRecursive(root, fileName, maxDepth = 4) {
  async function walk(dir, depth) {
    if (depth > maxDepth) return null;
    const entries = await fsp.readdir(dir, { withFileTypes: true });
    for (const e of entries) {
      const p = path.join(dir, e.name);
      if (e.isFile() && e.name.toLowerCase() === fileName.toLowerCase()) return p;
      if (e.isDirectory()) {
        const found = await walk(p, depth + 1);
        if (found) return found;
      }
    }
    return null;
  }

  return await walk(root, 0);
}

function macBrewPrefixes() {
  return ['/opt/homebrew', '/usr/local'];
}

function parseOtoolDeps(otoolOutput) {
  // Lines look like:
  // \t/opt/homebrew/opt/leptonica/lib/liblept.5.dylib (compatibility version..., current version...)
  const deps = [];
  for (const line of String(otoolOutput).split(/\r?\n/)) {
    const m = line.match(/^\s+([^\s]+)\s+\(/);
    if (m) deps.push(m[1]);
  }
  return deps;
}

function tryResolveMacRpathLib(dep, allowPrefixes) {
  // Resolve @rpath/<name> to an absolute file path by searching common Homebrew locations.
  const base = path.basename(dep);
  if (!base || base === dep) return null;

  for (const prefix of allowPrefixes) {
    const direct = path.join(prefix, 'lib', base);
    if (fs.existsSync(direct)) return direct;

    // Search under opt/*/lib (bounded depth to keep it reasonable).
    try {
      const out = runCapture('find', [path.join(prefix, 'opt'), '-maxdepth', '4', '-type', 'f', '-name', base]);
      const first = out
        .split(/\r?\n/)
        .map((s) => s.trim())
        .find(Boolean);
      if (first && fs.existsSync(first)) return first;
    } catch {
      // ignore
    }
  }
  return null;
}

async function copyMacDylibTree(tesseractPath, destRoot) {
  const libDir = path.join(destRoot, 'lib');
  await ensureDir(libDir);

  const allowPrefixes = macBrewPrefixes();

  const toCopy = new Map(); // absPath -> basename
  const seen = new Set();

  const enqueueFrom = (filePath) => {
    const out = runCapture('otool', ['-L', filePath]);
    for (const dep of parseOtoolDeps(out)) {
      let resolved = null;

      if (dep.startsWith('/')) {
        if (allowPrefixes.some((p) => dep.startsWith(p)) && fs.existsSync(dep)) {
          resolved = dep;
        }
      } else if (dep.startsWith('@rpath/')) {
        const r = tryResolveMacRpathLib(dep, allowPrefixes);
        if (r) resolved = r;
      }

      if (!resolved) continue;

      const base = path.basename(resolved);
      if (!toCopy.has(resolved)) toCopy.set(resolved, base);
    }
  };

  enqueueFrom(tesseractPath);

  // BFS a few rounds to capture nested dylib deps.
  for (let round = 0; round < 8; round++) {
    let added = 0;
    for (const dep of Array.from(toCopy.keys())) {
      if (seen.has(dep)) continue;
      seen.add(dep);
      enqueueFrom(dep);
      added++;
    }
    if (added === 0) break;
  }

  // Copy dylibs
  for (const [src, base] of toCopy.entries()) {
    const dst = path.join(libDir, base);
    if (!fs.existsSync(dst)) {
      await fsp.copyFile(src, dst);
      // Make sure we can patch/sign it.
      await fsp.chmod(dst, 0o644);
    }
  }

  // Build mapping old->new for install_name_tool.
  const mapping = new Map();
  for (const [src, base] of toCopy.entries()) {
    mapping.set(src, `@executable_path/lib/${base}`);
  }

  // Relink ids + deps.
  const filesToPatch = [path.join(destRoot, 'tesseract'), ...Array.from(toCopy.values()).map((b) => path.join(libDir, b))];

  for (const file of filesToPatch) {
    // Add rpath as a fallback
    try {
      run('install_name_tool', ['-add_rpath', '@executable_path/lib', file], { stdio: 'ignore' });
    } catch {
      // ignore if already present
    }
  }

  for (const libBase of toCopy.values()) {
    const libFile = path.join(libDir, libBase);
    try {
      run('install_name_tool', ['-id', `@executable_path/lib/${libBase}`, libFile]);
    } catch {
      // Some libs might not support id change; continue.
    }
  }

  for (const file of filesToPatch) {
    for (const [oldPath, newPath] of mapping.entries()) {
      try {
        run('install_name_tool', ['-change', oldPath, newPath, file]);
      } catch {
        // Not all deps appear in all files; ignore failures.
      }
    }
  }

  // After modifying Mach-O files, macOS may kill the process if signatures are invalid.
  // Ad-hoc signing is enough for local execution and for unsigned builds.
  if (commandExists('codesign')) {
    for (const file of filesToPatch) {
      try {
        // Ensure writable; some Homebrew artifacts can be read-only.
        if (path.basename(file) === 'tesseract') {
          await fsp.chmod(file, 0o755);
        } else {
          await fsp.chmod(file, 0o644);
        }

        run('codesign', ['--force', '--sign', '-', file]);
      } catch {
        // best-effort
      }
    }
  }
}

async function fetchWindows(destRoot, opts) {
  // You can override the URL if the default becomes unavailable.
  const url =
    process.env.TESSERACT_WIN_URL ||
    'https://digi.bib.uni-mannheim.de/tesseract/tesseract-ocr-w64-setup-v5.3.4.20240501.exe';

  if (opts.clean) await rmrf(destRoot);
  await ensureDir(destRoot);

  console.log('Downloading Windows Tesseract installer...');
  const tmpInstaller = path.join(os.tmpdir(), `docreader-tesseract-${Date.now()}.exe`);
  await downloadToFile(url, tmpInstaller);

  console.log('Running silent install into:', destRoot);

  // NSIS: /S for silent, /D=... must be last and typically unquoted.
  const destWin = path.resolve(destRoot);
  const args = ['/S', `/D=${destWin}`];
  run(tmpInstaller, args, { windowsHide: true });

  // Locate tesseract.exe (some installers create a subfolder).
  let exePath = path.join(destRoot, 'tesseract.exe');
  if (!fs.existsSync(exePath)) {
    const found = await findFileRecursive(destRoot, 'tesseract.exe', 5);
    if (found) exePath = found;
  }

  if (!fs.existsSync(exePath)) {
    throw new Error('tesseract.exe not found after install; check installer URL or permissions.');
  }

  if (path.dirname(exePath) !== destRoot) {
    await fsp.copyFile(exePath, path.join(destRoot, 'tesseract.exe'));
  }

  // Copy tessdata if present
  const installedTessdata = path.join(path.dirname(exePath), 'tessdata');
  const destTessdata = path.join(destRoot, 'tessdata');
  if (fs.existsSync(installedTessdata)) {
    await ensureDir(destTessdata);
    const entries = await fsp.readdir(installedTessdata);
    for (const name of entries) {
      const src = path.join(installedTessdata, name);
      const dst = path.join(destTessdata, name);
      if (!fs.existsSync(dst)) {
        await fsp.copyFile(src, dst);
      }
    }
  }

  await fetchTraineddata(destTessdata);

  console.log('OK: Packed Windows Tesseract into:', destRoot);
}

async function fetchMac(destRoot, opts) {
  if (opts.clean) await rmrf(destRoot);
  await ensureDir(destRoot);

  let tesseractPath;
  try {
    tesseractPath = runCapture('which', ['tesseract']).trim();
  } catch {
    tesseractPath = null;
  }

  if (!tesseractPath || !fs.existsSync(tesseractPath)) {
    throw new Error(
      'tesseract not found on PATH. Install it first (recommended: `brew install tesseract`) then rerun `npm run fetch:tesseract`.'
    );
  }

  const destExe = path.join(destRoot, 'tesseract');
  await fsp.copyFile(tesseractPath, destExe);
  await fsp.chmod(destExe, 0o755);

  // Copy required traineddata
  const destTessdata = path.join(destRoot, 'tessdata');
  await fetchTraineddata(destTessdata);

  // Attempt to make it relocatable by copying Homebrew dylibs and relinking.
  // This requires Xcode Command Line Tools (otool/install_name_tool).
  try {
    runCapture('xcrun', ['--find', 'otool']);
    runCapture('xcrun', ['--find', 'install_name_tool']);
  } catch {
    // xcrun may not exist; fall back to direct tools.
  }

  console.log('Packaging macOS dylibs (best-effort)...');
  await copyMacDylibTree(destExe, destRoot);

  console.log('OK: Packed macOS Tesseract into:', destRoot);
}

async function main() {
  const opts = parseArgs();
  const root = appRoot();
  const folder = platformFolder();
  const destRoot = path.join(root, 'resources', 'tesseract', folder);

  if (process.platform === 'win32') {
    await fetchWindows(destRoot, opts);
    return;
  }

  if (process.platform === 'darwin') {
    await fetchMac(destRoot, opts);
    return;
  }

  console.error('Linux: automated bundling is not implemented yet.');
  console.error('Option A (recommended): bundle a statically-linked tesseract build for your distro.');
  console.error('Option B: rely on system tesseract and document it (not single-installer).');
  process.exit(2);
}

main().catch((e) => {
  console.error(`ERROR: ${e?.stack || e}`);
  process.exit(1);
});
