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

async function copyDirRecursive(srcDir, destDir) {
  await ensureDir(destDir);
  const entries = await fsp.readdir(srcDir, { withFileTypes: true });
  for (const entry of entries) {
    const src = path.join(srcDir, entry.name);
    const dst = path.join(destDir, entry.name);

    if (entry.isDirectory()) {
      await copyDirRecursive(src, dst);
      continue;
    }

    // Treat files and symlinks as files.
    if (!fs.existsSync(dst)) {
      await fsp.copyFile(src, dst);
    }
  }
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

function fetchText(url) {
  return new Promise((resolve, reject) => {
    const handleResponse = (res, redirectsLeft) => {
      if (res.statusCode && res.statusCode >= 300 && res.statusCode < 400 && res.headers.location) {
        if (redirectsLeft <= 0) {
          reject(new Error('Too many redirects while fetching text'));
          return;
        }
        doReq(res.headers.location, redirectsLeft - 1);
        return;
      }

      if (res.statusCode !== 200) {
        reject(new Error(`Fetch failed: ${res.statusCode} ${res.statusMessage}`));
        return;
      }

      res.setEncoding('utf-8');
      let data = '';
      res.on('data', (chunk) => {
        data += chunk;
      });
      res.on('end', () => {
        resolve(data);
      });
      res.on('error', reject);
    };

    const doReq = (u, redirectsLeft) => {
      const req = https.get(u, (res) => handleResponse(res, redirectsLeft));
      req.on('error', reject);
    };

    doReq(url, 5);
  });
}

function discoverWindowsInstallerUrlsFromHtml(html, baseUrl) {
  const out = new Set();
  const text = String(html || '');

  // Common filenames:
  // - tesseract-ocr-w64-setup-5.4.0.20240606.exe
  // - tesseract-ocr-w64-setup-v5.3.4.20240501.exe
  const re = /href\s*=\s*"([^"]*tesseract-ocr-w64-setup[^"]*\.exe)"/gi;
  let m;
  while ((m = re.exec(text))) {
    const href = m[1];
    if (!href) continue;
    if (/\.exe\b/i.test(href)) {
      const abs = href.startsWith('http') ? href : new URL(href, baseUrl).toString();
      out.add(abs);
    }
  }

  // Some directory listings don't use hrefs exactly as above, so also scan raw filenames.
  const re2 = /(tesseract-ocr-w64-setup[^\s"']*\.exe)/gi;
  while ((m = re2.exec(text))) {
    const file = m[1];
    if (!file) continue;
    out.add(new URL(file, baseUrl).toString());
  }

  return Array.from(out);
}

async function resolveWindowsInstallerUrl() {
  const override = (process.env.TESSERACT_WIN_URL || '').trim();
  if (override) return override;

  const base = 'https://digi.bib.uni-mannheim.de/tesseract/';

  // Best effort: discover the newest installer from the directory listing.
  try {
    const html = await fetchText(base);
    const urls = discoverWindowsInstallerUrlsFromHtml(html, base)
      .filter((u) => /tesseract-ocr-w64-setup/i.test(u))
      .filter((u) => /\.exe$/i.test(u));

    // Prefer newer versions by sorting with numeric compare on the filename.
    const sorted = urls.toSorted((a, b) =>
      path.basename(a).localeCompare(path.basename(b), undefined, { numeric: true, sensitivity: 'base' })
    );

    const best = sorted.at(-1);
    if (best) return best;
  } catch {
    // ignore; fallback below
  }

  // Fallback list: these may become stale, but cover common versions.
  return [
    'https://digi.bib.uni-mannheim.de/tesseract/tesseract-ocr-w64-setup-v5.3.0.20221214.exe',
    'https://digi.bib.uni-mannheim.de/tesseract/tesseract-ocr-w64-setup-v5.2.0.20220712.exe',
    'https://digi.bib.uni-mannheim.de/tesseract/tesseract-ocr-w64-setup-v5.2.0.20220708.exe',
  ];
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
    try {
      await fsp.rename(tmp, dest);
    } catch (e) {
      // On Windows CI the temp dir can be on a different drive than the workspace (EXDEV).
      if (e && (e.code === 'EXDEV' || e.code === 'EPERM')) {
        await fsp.copyFile(tmp, dest);
        await fsp.rm(tmp, { force: true });
      } else {
        throw e;
      }
    }
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
  // If not provided, we try to discover the latest installer from the UB Mannheim directory listing.
  const resolved = await resolveWindowsInstallerUrl();
  const candidateUrls = Array.isArray(resolved) ? resolved : [resolved];

  if (opts.clean) await rmrf(destRoot);
  await ensureDir(destRoot);

  console.log('Downloading Windows Tesseract installer...');
  const tmpInstaller = path.join(os.tmpdir(), `docreader-tesseract-${Date.now()}.exe`);

  let lastErr = null;
  for (const url of candidateUrls) {
    try {
      console.log('Installer URL:', url);
      await downloadToFile(url, tmpInstaller);
      lastErr = null;
      break;
    } catch (e) {
      lastErr = e;
      console.warn(`WARN: download failed for ${url}: ${e?.message || e}`);
      try {
        await fsp.rm(tmpInstaller, { force: true });
      } catch {
        // ignore
      }
    }
  }

  if (lastErr) {
    throw new Error(
      `Failed to download Windows Tesseract installer. You can override with TESSERACT_WIN_URL.\n${lastErr?.message || lastErr}`
    );
  }

  console.log('Running silent install into:', destRoot);

  // NSIS: /S for silent, /D=... must be last and typically unquoted.
  const destWin = path.resolve(destRoot);
  const args = ['/S', `/D=${destWin}`];
  run(tmpInstaller, args, { windowsHide: true });

  // Locate tesseract.exe.
  // Some installers ignore /D=... and install to Program Files, so we search a few places.
  const candidateExePaths = [];
  candidateExePaths.push(path.join(destRoot, 'tesseract.exe'));

  const foundUnderDest = await findFileRecursive(destRoot, 'tesseract.exe', 6);
  if (foundUnderDest) candidateExePaths.push(foundUnderDest);

  // Common default install locations.
  candidateExePaths.push('C:\\Program Files\\Tesseract-OCR\\tesseract.exe');
  candidateExePaths.push('C:\\Program Files (x86)\\Tesseract-OCR\\tesseract.exe');

  // If installer added it to PATH, use `where`.
  try {
    const whereRes = spawnSync('where', ['tesseract.exe'], { encoding: 'utf-8' });
    if (!whereRes.error && whereRes.status === 0) {
      const lines = String(whereRes.stdout || '')
        .split(/\r?\n/)
        .map((s) => s.trim())
        .filter(Boolean);
      for (const p of lines) candidateExePaths.push(p);
    }
  } catch {
    // ignore
  }

  const exePath = candidateExePaths.find((p) => p && fs.existsSync(p));
  if (!exePath) {
    throw new Error(
      'tesseract.exe not found after install. The installer may have ignored /D=... or installed somewhere unexpected.\n' +
        'Tip: set TESSERACT_WIN_URL to a known-good UB Mannheim installer, or rerun with a different version.'
    );
  }

  // Copy required binaries (tesseract.exe + DLLs) into destRoot.
  // The UB Mannheim builds rely on nearby DLLs; copying only tesseract.exe is not enough.
  const installRoot = path.dirname(exePath);
  if (installRoot !== destRoot) {
    const entries = await fsp.readdir(installRoot, { withFileTypes: true });
    for (const e of entries) {
      if (!e.isFile()) continue;
      const name = e.name;
      const lower = name.toLowerCase();
      if (!(lower.endsWith('.exe') || lower.endsWith('.dll'))) continue;
      const src = path.join(installRoot, name);
      const dst = path.join(destRoot, name);
      if (!fs.existsSync(dst)) {
        await fsp.copyFile(src, dst);
      }
    }
  }

  // Ensure tesseract.exe is at the root.
  const rootExe = path.join(destRoot, 'tesseract.exe');
  if (!fs.existsSync(rootExe)) {
    await fsp.copyFile(exePath, rootExe);
  }

  // Copy tessdata if present
  const installedTessdata = path.join(installRoot, 'tessdata');
  const destTessdata = path.join(destRoot, 'tessdata');
  if (fs.existsSync(installedTessdata)) {
    await copyDirRecursive(installedTessdata, destTessdata);
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
