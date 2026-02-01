const DOC_TYPES = ['BL', 'INVOICE', 'PACKING LIST'];

const state = {
  files: /** @type {Array<{ path: string, name: string, docType: string }>} */ ([]),
  reportPath: null,
  running: false,
  projectRoot: null,
  isPackaged: false,
};

function baseName(p) {
  return String(p).split(/[/\\]/).pop();
}

function setStatus(text) {
  document.getElementById('status').textContent = text;
}

function setReportPath(path) {
  const el = document.getElementById('reportPath');
  if (!path) {
    el.style.display = 'none';
    el.textContent = '';
    return;
  }
  el.style.display = '';
  el.textContent = `Report: ${path}`;
}

function appendLog(text) {
  const el = document.getElementById('log');
  el.textContent += text;
  el.scrollTop = el.scrollHeight;
}

function clearLog() {
  document.getElementById('log').textContent = '';
}

function updateButtons() {
  const btnRun = document.getElementById('btnRun');
  btnRun.disabled = state.running || state.files.length === 0 || (!state.projectRoot && !state.isPackaged);

  const btnOpenReport = document.getElementById('btnOpenReport');
  btnOpenReport.disabled = state.running || !state.reportPath;
}

function setProjectRootText() {
  const el = document.getElementById('projectRoot');
  if (state.isPackaged) {
    el.textContent = 'Projeto: (embutido no app)';
    return;
  }
  if (!state.projectRoot) {
    el.textContent = 'Projeto: (não configurado)';
    return;
  }
  el.textContent = `Projeto: ${state.projectRoot}`;
}

async function refreshProjectRoot() {
  const info = await globalThis.docReader.getProjectRoot();
  state.projectRoot = info?.projectRoot || null;
  state.isPackaged = Boolean(info?.isPackaged);

  const btnProject = document.getElementById('btnProject');
  btnProject.disabled = state.isPackaged;

  setProjectRootText();
  updateButtons();
}

async function configureProjectRoot() {
  const res = await globalThis.docReader.selectProjectRoot();
  if (res?.ok && res.projectRoot) {
    state.projectRoot = res.projectRoot;
    appendLog(`\nProjeto configurado: ${res.projectRoot}\n`);
  } else if (res?.error) {
    appendLog(`\nERROR: ${res.error}\n`);
  }
  setProjectRootText();
  updateButtons();
}

function normalizeDocType(v) {
  const up = String(v || '').toUpperCase();
  if (up === 'PACKING_LIST' || up === 'PACKINGLIST') return 'PACKING LIST';
  if (DOC_TYPES.includes(up)) return up;
  return 'INVOICE';
}

function renderFileList() {
  const list = document.getElementById('fileList');
  list.innerHTML = '';

  for (const f of state.files) {
    const row = document.createElement('div');
    row.className = 'file-row';

    const left = document.createElement('div');
    left.className = 'file-name';
    left.textContent = f.name;

    const right = document.createElement('div');
    right.className = 'type-choices';

    const groupId = `docType:${f.path}`;

    for (const t of DOC_TYPES) {
      const label = document.createElement('label');
      label.className = 'choice';

      // Using checkboxes as requested, but enforcing single selection (radio-like)
      const input = document.createElement('input');
      input.type = 'checkbox';
      input.checked = normalizeDocType(f.docType) === t;

      input.addEventListener('change', () => {
        if (!input.checked) {
          // never allow "none"
          input.checked = true;
          return;
        }

        // uncheck siblings
        const siblings = document.querySelectorAll(`input[data-group="${CSS.escape(groupId)}"]`);
        siblings.forEach((sib) => {
          if (sib !== input) sib.checked = false;
        });

        f.docType = t;
      });

      input.dataset.group = groupId;

      const span = document.createElement('span');
      span.textContent = t;

      label.appendChild(input);
      label.appendChild(span);
      right.appendChild(label);
    }

    row.appendChild(left);
    row.appendChild(right);
    list.appendChild(row);
  }

  updateButtons();
}

function addFiles(filePaths) {
  const existing = new Set(state.files.map((f) => f.path));

  for (const p of filePaths || []) {
    if (!p) continue;
    if (existing.has(p)) continue;

    state.files.push({
      path: p,
      name: baseName(p),
      docType: guessDocTypeFromName(baseName(p)),
    });
  }

  renderFileList();
}

function guessDocTypeFromName(name) {
  const n = String(name || '').toUpperCase();
  if (n.includes('PACKING')) return 'PACKING LIST';
  if (n.includes('INVOICE')) return 'INVOICE';
  if (n.startsWith('BL') || n.includes('B/L') || n.includes('LADING')) return 'BL';
  return 'INVOICE';
}

async function pickFiles() {
  const filePaths = await globalThis.docReader.selectFiles();
  addFiles(filePaths);
}

async function run() {
  state.running = true;
  state.reportPath = null;
  setReportPath(null);
  updateButtons();
  clearLog();
  setStatus('Executando pipeline...');

  const payload = {
    files: state.files.map((f) => ({ path: f.path, docType: f.docType })),
  };

  try {
    const res = await globalThis.docReader.runPipeline(payload);

    if (res?.ok) {
      state.reportPath = res.reportPath;
      setStatus('Concluído. Report aberto (Stage 4).');
      setReportPath(state.reportPath);
    } else {
      setStatus('Falhou. Veja os logs.');
      if (res?.error) appendLog(`\nERROR: ${res.error}\n`);
      if (res?.stderr) appendLog(`\nSTDERR:\n${res.stderr}\n`);
      setReportPath(null);
    }
  } catch (e) {
    setStatus('Erro ao executar.');
    appendLog(`\nERROR: ${String(e)}\n`);
    setReportPath(null);
  } finally {
    state.running = false;
    updateButtons();
  }
}

function setupDragAndDrop() {
  const drop = document.getElementById('dropzone');

  const setOver = (on) => {
    drop.classList.toggle('dragover', on);
  };

  drop.addEventListener('dragover', (ev) => {
    ev.preventDefault();
    setOver(true);
  });

  drop.addEventListener('dragleave', () => setOver(false));

  drop.addEventListener('drop', (ev) => {
    ev.preventDefault();
    setOver(false);

    const files = Array.from(ev.dataTransfer.files || []);
    const pdfs = files
      .filter((f) => String(f.name || '').toLowerCase().endsWith('.pdf'))
      .map((f) => f.path);

    addFiles(pdfs);
  });

  drop.addEventListener('click', () => {
    pickFiles();
  });
}

function setupLogs() {
  globalThis.docReader.onPipelineLog((msg) => {
    if (!msg?.text) return;
    appendLog(msg.text);
  });
}

function setup() {
  document.getElementById('btnProject').addEventListener('click', configureProjectRoot);
  document.getElementById('btnPick').addEventListener('click', pickFiles);
  document.getElementById('btnRun').addEventListener('click', run);
  document.getElementById('btnOpenReport').addEventListener('click', async () => {
    if (!state.reportPath) return;
    const res = await globalThis.docReader.openReport(state.reportPath);
    if (!res?.ok) {
      appendLog(`\nERROR: Não foi possível abrir o report: ${res?.error || 'unknown'}\n`);
    }
  });

  setupDragAndDrop();
  setupLogs();
  renderFileList();
  setStatus('Pronto.');
  refreshProjectRoot();
}

setup();
