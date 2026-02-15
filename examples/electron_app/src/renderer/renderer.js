const DOC_TYPES = ['BL', 'HBL', 'INVOICE', 'PACKING LIST', 'DI', 'LI'];

const state = {
  files: /** @type {Array<{ path: string, name: string, docType: string }>} */ ([]),
  reportPath: null,
  detailedReportPath: null,
  running: false,
  stage2Engine: 'regex',
  progress: {
    percent: 0,
    label: 'Progresso: aguardando execucao',
    stage: 0,
    totalDocs: 0,
    stage1Done: 0,
    stage2Done: 0,
    stage2Engine: 'regex',
  },
  projectRoot: null,
  isPackaged: false,
  codexAuth: {
    connected: false,
    configured: true,
    missingConfig: [],
    identity: null,
    expiresAt: null,
    provider: null,
    busy: false,
  },
};

function baseName(p) {
  return String(p).split(/[/\\]/).pop();
}

function setStatus(text) {
  document.getElementById('status').textContent = text;
}

function setReportPath(pathValue) {
  const el = document.getElementById('reportPath');
  if (!pathValue) {
    el.style.display = 'none';
    el.textContent = '';
    return;
  }
  el.style.display = '';
  el.textContent = 'Report: ' + pathValue;
}

function appendLog(text) {
  const el = document.getElementById('log');
  el.textContent += text;
  el.scrollTop = el.scrollHeight;
}

function clearLog() {
  document.getElementById('log').textContent = '';
}

function clampPercent(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return 0;
  return Math.max(0, Math.min(100, Math.round(n)));
}

function setProgress(percent, label) {
  const pct = clampPercent(percent);
  state.progress.percent = pct;
  if (label) state.progress.label = String(label);

  const pctEl = document.getElementById('progressPct');
  const labelEl = document.getElementById('progressLabel');
  const fillEl = document.getElementById('progressFill');

  if (pctEl) pctEl.textContent = pct + '%';
  if (labelEl) labelEl.textContent = state.progress.label;
  if (fillEl) fillEl.style.width = pct + '%';
}

function computeProgressPercent() {
  const p = state.progress;
  const total = Math.max(1, Number(p.totalDocs) || 1);

  if (p.stage <= 0) return p.percent;
  if (p.stage === 1) return 10 + (Math.min(p.stage1Done, total) / total) * 30; // 10-40
  if (p.stage === 2) return 40 + (Math.min(p.stage2Done, total) / total) * 40; // 40-80
  if (p.stage === 3) return 90; // compare
  if (p.stage >= 5) return 99; // debug report generation
  if (p.stage >= 4) return 97; // report generation
  return p.percent;
}

function syncProgressLabel() {
  const p = state.progress;
  const engineText = normalizeStage2Engine(p.stage2Engine) === 'llm' ? 'LLM' : 'Regex';

  if (p.stage <= 0) {
    setProgress(p.percent, p.label || 'Progresso: aguardando execucao');
    return;
  }
  if (p.stage === 1) {
    setProgress(computeProgressPercent(), `Stage 01 (OCR): ${p.stage1Done}/${Math.max(1, p.totalDocs)} arquivos`);
    return;
  }
  if (p.stage === 2) {
    setProgress(
      computeProgressPercent(),
      `Stage 02 (${engineText}): ${p.stage2Done}/${Math.max(1, p.totalDocs)} arquivos`
    );
    return;
  }
  if (p.stage === 3) {
    setProgress(90, 'Stage 03: comparando documentos');
    return;
  }
  if (p.stage >= 5) {
    setProgress(Math.max(p.percent, 99), 'Stage 05: gerando report detalhado');
    return;
  }
  if (p.stage >= 4) {
    setProgress(Math.max(p.percent, 97), 'Stage 04: gerando report');
    return;
  }
}

function resetProgress(totalDocs, stage2Engine) {
  state.progress = {
    percent: 0,
    label: 'Progresso: aguardando execucao',
    stage: 0,
    totalDocs: Math.max(0, Number(totalDocs) || 0),
    stage1Done: 0,
    stage2Done: 0,
    stage2Engine: normalizeStage2Engine(stage2Engine),
  };
  setProgress(0, 'Progresso: aguardando execucao');
}

function handlePipelineProgressFromLog(text) {
  const lines = String(text || '')
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);

  for (const line of lines) {
    if (/^OCR:\s*/i.test(line) || /^Processando:\s*/i.test(line)) {
      state.progress.stage = Math.max(state.progress.stage, 1);
      syncProgressLabel();
      continue;
    }

    if (/OK -> .*_extracted\.txt\/\.json/i.test(line)) {
      state.progress.stage = Math.max(state.progress.stage, 1);
      state.progress.stage1Done = Math.min(
        Math.max(1, state.progress.totalDocs),
        state.progress.stage1Done + 1
      );
      syncProgressLabel();
      continue;
    }

    const stage2Selected = line.match(/^Stage 02 engine selected:\s*(\w+)/i);
    if (stage2Selected) {
      state.progress.stage = Math.max(state.progress.stage, 2);
      state.progress.stage2Engine = normalizeStage2Engine(stage2Selected[1]);
      syncProgressLabel();
      continue;
    }

    const llmProcessing = line.match(/^\[Stage02-LLM\]\s+(\d+)\/(\d+)\s+processing/i);
    if (llmProcessing) {
      const idx = Number(llmProcessing[1]) || 1;
      const total = Number(llmProcessing[2]) || state.progress.totalDocs || 1;
      state.progress.stage = Math.max(state.progress.stage, 2);
      state.progress.totalDocs = Math.max(state.progress.totalDocs, total);
      state.progress.stage2Done = Math.max(state.progress.stage2Done, Math.max(0, idx - 1));
      syncProgressLabel();
      continue;
    }

    if (/^\[Stage02-LLM\]\s+OK\s+->\s+.*_fields\.json/i.test(line) || /^OK -> .*_fields\.json/i.test(line)) {
      state.progress.stage = Math.max(state.progress.stage, 2);
      state.progress.stage2Done = Math.min(
        Math.max(1, state.progress.totalDocs),
        state.progress.stage2Done + 1
      );
      syncProgressLabel();
      continue;
    }

    if (/Sa[ií]da:\s+.*_stage03_comparison\.json/i.test(line)) {
      state.progress.stage = Math.max(state.progress.stage, 3);
      syncProgressLabel();
      continue;
    }

    if (/^JSON\s*:\s*.*_stage04_report\.json/i.test(line)) {
      state.progress.stage = Math.max(state.progress.stage, 4);
      syncProgressLabel();
      continue;
    }

    if (/^HTML\s*:\s*.*_stage04_report\.html/i.test(line)) {
      state.progress.stage = Math.max(state.progress.stage, 4);
      syncProgressLabel();
      continue;
    }

    if (/^Stage 05 completed\./i.test(line) || /^HTML\s*:\s*.*_stage05_debug_report\.html/i.test(line)) {
      state.progress.stage = Math.max(state.progress.stage, 5);
      syncProgressLabel();
      continue;
    }

    if (/"success"\s*:\s*true/i.test(line)) {
      state.progress.stage = Math.max(state.progress.stage, 5);
      setProgress(100, 'Pipeline concluido');
      continue;
    }
  }
}

function formatExpiry(ts) {
  if (!ts) return 'sem expiracao informada';
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return 'expiracao invalida';
  return d.toLocaleString('pt-BR');
}

function describeCodexAuthProvider(provider) {
  if (provider === 'codex-cli-local') return 'via Codex CLI local';
  if (provider === 'codex-cli') return 'via OAuth PKCE';
  if (!provider) return 'provedor desconhecido';
  return String(provider);
}

function formatCodexAuthLogEntry(entry) {
  if (!entry || typeof entry !== 'object') return null;

  const rawTs = entry.ts || new Date().toISOString();
  const ts = new Date(rawTs);
  const timePart = Number.isNaN(ts.getTime()) ? String(rawTs) : ts.toLocaleTimeString('pt-BR');

  const level = String(entry.level || 'info').toUpperCase();
  const step = String(entry.step || 'unknown_step');
  const details = entry.details ? String(entry.details) : '';

  if (details) {
    return '[' + timePart + '] [AUTH] [' + level + '] ' + step + ' :: ' + details + '\n';
  }
  return '[' + timePart + '] [AUTH] [' + level + '] ' + step + '\n';
}

function setCodexAuthText() {
  const el = document.getElementById('codexAuthStatus');
  const auth = state.codexAuth;

  if (!auth.configured) {
    const missing = (auth.missingConfig || []).join(', ');
    el.textContent = 'Codex: configuracao OAuth incompleta (' + missing + ')';
    return;
  }

  if (!auth.connected) {
    el.textContent = 'Codex: nao conectado';
    return;
  }

  const identity = auth.identity || {};
  const who = identity.email || identity.name || identity.sub || 'usuario autenticado';
  const providerText = describeCodexAuthProvider(auth.provider);
  el.textContent =
    'Codex: conectado ' + providerText + ' como ' + who + ' (expira: ' + formatExpiry(auth.expiresAt) + ')';
}

function setCodexAuthBusy(isBusy) {
  state.codexAuth.busy = Boolean(isBusy);
  updateButtons();
}

function normalizeStage2Engine(value) {
  return String(value || '').trim().toLowerCase() === 'llm' ? 'llm' : 'regex';
}

function stage2EngineLabel(engine) {
  return normalizeStage2Engine(engine) === 'llm' ? 'LLM (Codex)' : 'Regex';
}

function syncStage2EngineControl() {
  const select = document.getElementById('stage2Engine');
  if (!select) return;

  const llmOption = select.querySelector('option[value="llm"]');
  if (llmOption) {
    llmOption.disabled = !state.codexAuth.connected;
  }

  if (!state.codexAuth.connected && state.stage2Engine === 'llm') {
    state.stage2Engine = 'regex';
  }

  select.value = normalizeStage2Engine(state.stage2Engine);
  select.disabled = state.running || state.codexAuth.busy;
}

function setStage2Engine(nextEngine, options) {
  const opts = options || {};
  const normalized = normalizeStage2Engine(nextEngine);

  if (normalized === 'llm' && !state.codexAuth.connected) {
    state.stage2Engine = 'regex';
    syncStage2EngineControl();
    if (opts.logBlocked) {
      appendLog('INFO: para usar LLM, conecte o Codex primeiro.\n');
    }
    return false;
  }

  state.stage2Engine = normalized;
  syncStage2EngineControl();
  return true;
}

function updateButtons() {
  const btnRun = document.getElementById('btnRun');
  btnRun.disabled = state.running || state.files.length === 0;

  const btnOpenReport = document.getElementById('btnOpenReport');
  btnOpenReport.disabled = state.running || !state.reportPath;

  const btnOpenDetailedReport = document.getElementById('btnOpenDetailedReport');
  if (btnOpenDetailedReport) {
    btnOpenDetailedReport.disabled = state.running || !state.detailedReportPath;
  }

  const auth = state.codexAuth;
  const authBusy = state.running || auth.busy;

  const btnCodexConnect = document.getElementById('btnCodexConnect');
  btnCodexConnect.disabled = authBusy || auth.connected;

  const btnCodexLogout = document.getElementById('btnCodexLogout');
  btnCodexLogout.disabled = authBusy || !auth.connected;

  syncStage2EngineControl();
}

function setProjectRootText() {
  const el = document.getElementById('projectRoot');
  if (state.isPackaged) {
    el.textContent = 'Projeto: (embutido no app)';
    return;
  }
  if (!state.projectRoot) {
    el.textContent = 'Projeto: (nao configurado)';
    return;
  }
  el.textContent = 'Projeto: ' + state.projectRoot;
}

async function refreshProjectRoot() {
  try {
    const info = await globalThis.docReader.getProjectRoot();
    state.projectRoot = info && info.projectRoot ? info.projectRoot : null;
    state.isPackaged = Boolean(info && info.isPackaged);

    setProjectRootText();
    updateButtons();
  } catch (error) {
    appendLog('\nERROR: falha ao ler configuracao do projeto: ' + String(error) + '\n');
  }
}

function applyCodexAuthStatus(status) {
  if (!status || typeof status !== 'object') return;
  const wasConnected = state.codexAuth.connected;
  const previousEngine = state.stage2Engine;

  state.codexAuth.connected = Boolean(status.connected);
  state.codexAuth.configured = status.configured !== false;
  state.codexAuth.missingConfig = Array.isArray(status.missingConfig) ? status.missingConfig : [];
  state.codexAuth.identity = status.identity || null;
  state.codexAuth.expiresAt = status.expiresAt || null;
  state.codexAuth.provider = status.provider || null;

  if (wasConnected && !state.codexAuth.connected && previousEngine === 'llm') {
    state.stage2Engine = 'regex';
    appendLog('INFO: Codex desconectado. Stage 02 voltou automaticamente para Regex.\n');
  }

  setCodexAuthText();
  updateButtons();
}

async function refreshCodexAuthStatus(options) {
  const opts = options || {};
  const autoRefresh = Boolean(opts.autoRefresh);
  const logErrors = Boolean(opts.logErrors);
  try {
    const status = await globalThis.docReader.codexAuthGetStatus({ autoRefresh });
    applyCodexAuthStatus(status);
    if (status && status.refreshError && logErrors) {
      appendLog('\nWARN: falha ao atualizar token automaticamente: ' + status.refreshError + '\n');
    }
  } catch (error) {
    if (logErrors) {
      appendLog('\nWARN: falha ao consultar status de auth Codex: ' + String(error) + '\n');
    }
  }
}

async function connectCodex() {
  setCodexAuthBusy(true);
  appendLog('\nIniciando autenticacao Codex...\n');

  try {
    const res = await globalThis.docReader.codexAuthStart({
      allowLocalImport: true,
      forceDeviceAuth: false,
    });
    if (res && res.ok) {
      applyCodexAuthStatus(res.status || {});
      appendLog('Codex autenticado com sucesso.\n');
      if (res.source === 'codex-cli-status') {
        appendLog('INFO: Codex CLI ja estava autenticado; login web nao foi necessario.\n');
      } else if (res.source === 'codex-cli-device-auth-forced') {
        appendLog('INFO: novo login realizado via Codex CLI device auth (forcado).\n');
      } else if (res.source === 'codex-cli-device-auth') {
        appendLog('INFO: login realizado via Codex CLI device auth.\n');
      } else if (res.source === 'codex-cli-local') {
        appendLog('INFO: sessao importada de ~/.codex/auth.json (Codex CLI local).\n');
      }
    } else {
      appendLog('ERROR: ' + ((res && res.error) || 'Falha desconhecida na autenticacao Codex.') + '\n');
      if (res && res.details) {
        appendLog('DETAILS: ' + String(res.details) + '\n');
      }
      if (res && Array.isArray(res.missingConfig) && res.missingConfig.length) {
        appendLog('INFO: variaveis obrigatorias faltando: ' + res.missingConfig.join(', ') + '\n');
      }
      await refreshCodexAuthStatus({ autoRefresh: false });
    }
  } catch (error) {
    appendLog('ERROR: ' + String(error) + '\n');
  } finally {
    setCodexAuthBusy(false);
  }
}

async function logoutCodex() {
  setCodexAuthBusy(true);
  try {
    const res = await globalThis.docReader.codexAuthLogout();
    if (res && res.ok) {
      applyCodexAuthStatus(res.status || {});
      appendLog('Codex desconectado.\n');
    } else {
      appendLog('ERROR: ' + ((res && res.error) || 'Falha ao desconectar Codex.') + '\n');
    }
  } catch (error) {
    appendLog('ERROR: ' + String(error) + '\n');
  } finally {
    setCodexAuthBusy(false);
  }
}

function normalizeDocType(v) {
  const up = String(v || '').toUpperCase();
  if (up === 'PACKING_LIST' || up === 'PACKINGLIST') return 'PACKING LIST';
  if (up === 'HBL') return 'HBL';
  if (up === 'DI') return 'DI';
  if (up === 'LI') return 'LI';
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

    const groupId = 'docType:' + f.path;

    for (const t of DOC_TYPES) {
      const label = document.createElement('label');
      label.className = 'choice';

      const input = document.createElement('input');
      input.type = 'checkbox';
      input.checked = normalizeDocType(f.docType) === t;

      input.addEventListener('change', () => {
        if (!input.checked) {
          input.checked = true;
          return;
        }

        const selector = 'input[data-group="' + CSS.escape(groupId) + '"]';
        const siblings = document.querySelectorAll(selector);
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
  if (n.includes('HBL')) return 'HBL';
  if (n.includes('CONFERENCIA DI') || n.includes('RASCUNHO DI') || n.match(/\bDI\b/) || n.match(/\bDI[\s\-_]*\d+/)) return 'DI';
  if (n.includes('CONFERENCIA LI') || n.includes('RASCUNHO LI') || n.match(/\bLI\b/) || n.match(/\bLI[\s\-_]*\d+/)) return 'LI';
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
  resetProgress(state.files.length, state.stage2Engine);
  setProgress(2, 'Preparando execucao');
  state.reportPath = null;
  state.detailedReportPath = null;
  setReportPath(null);
  updateButtons();
  clearLog();
  setStatus('Executando pipeline...');

  await refreshCodexAuthStatus({ autoRefresh: true, logErrors: true });

  const payload = {
    files: state.files.map((f) => ({ path: f.path, docType: f.docType })),
    stage2Engine: normalizeStage2Engine(state.stage2Engine),
  };
  appendLog('Stage 02 engine selecionada: ' + stage2EngineLabel(state.stage2Engine) + '.\n');

  try {
    const res = await globalThis.docReader.runPipeline(payload);
    if (res && res.runLogPath) {
      appendLog('Log detalhado do run: ' + res.runLogPath + '\n');
    }

    if (res && res.ok) {
      state.reportPath = res.reportPath;
      state.detailedReportPath = res.debugReportPath || null;
      setStatus('Concluido. Report aberto (Stage 4).');
      setProgress(100, 'Pipeline concluido');
      setReportPath(state.reportPath);
      if (state.detailedReportPath) {
        appendLog('Report detalhado Stage 5 disponivel.\n');
      }
      if (res.codexAuth && res.codexAuth.connected) {
        appendLog('Codex auth disponivel para stage_02.\n');
      }
    } else {
      setStatus('Falhou. Veja os logs.');
      setProgress(100, 'Pipeline falhou');
      if (res && res.error) appendLog('\nERROR: ' + res.error + '\n');
      if (res && res.stderr) appendLog('\nSTDERR:\n' + res.stderr + '\n');
      setReportPath(null);
      state.detailedReportPath = null;
    }
  } catch (e) {
    setStatus('Erro ao executar.');
    setProgress(100, 'Erro ao executar pipeline');
    appendLog('\nERROR: ' + String(e) + '\n');
    setReportPath(null);
    state.detailedReportPath = null;
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
    if (!msg || !msg.text) return;
    appendLog(msg.text);
    handlePipelineProgressFromLog(msg.text);
  });
}

function setupAuthEvents() {
  globalThis.docReader.onCodexAuthChanged((status) => {
    applyCodexAuthStatus(status || {});
  });

  if (typeof globalThis.docReader.onCodexAuthLog === 'function') {
    globalThis.docReader.onCodexAuthLog((entry) => {
      const line = formatCodexAuthLogEntry(entry);
      if (!line) return;
      appendLog(line);
    });
  }
}

function setup() {
  if (!globalThis.docReader) {
    setStatus('Erro de inicializacao.');
    appendLog('ERROR: bridge do Electron (preload) indisponivel. Reinicie o app.\n');
    return;
  }

  document.getElementById('btnPick').addEventListener('click', pickFiles);
  document.getElementById('btnRun').addEventListener('click', run);
  document.getElementById('btnCodexConnect').addEventListener('click', connectCodex);
  document.getElementById('btnCodexLogout').addEventListener('click', logoutCodex);
  document.getElementById('stage2Engine').addEventListener('change', (event) => {
    const requested = normalizeStage2Engine(event && event.target ? event.target.value : 'regex');
    const changed = setStage2Engine(requested, { logBlocked: true });
    if (changed) {
      appendLog('Stage 02 engine alterada para: ' + stage2EngineLabel(state.stage2Engine) + '.\n');
    }
  });
  document.getElementById('btnOpenReport').addEventListener('click', async () => {
    if (!state.reportPath) return;
    const res = await globalThis.docReader.openReport(state.reportPath);
    if (!res || !res.ok) {
      appendLog('\nERROR: Nao foi possivel abrir o report: ' + ((res && res.error) || 'unknown') + '\n');
    }
  });
  document.getElementById('btnOpenDetailedReport').addEventListener('click', async () => {
    if (!state.detailedReportPath) return;
    const res = await globalThis.docReader.openReport(state.detailedReportPath);
    if (!res || !res.ok) {
      appendLog(
        '\nERROR: Nao foi possivel abrir o report detalhado: ' + ((res && res.error) || 'unknown') + '\n'
      );
    }
  });

  setupDragAndDrop();
  setupLogs();
  setupAuthEvents();
  renderFileList();
  setStage2Engine('regex');
  setCodexAuthText();
  setStatus('Pronto.');
  refreshProjectRoot();
  refreshCodexAuthStatus({ autoRefresh: false, logErrors: false });
}

try {
  setup();
} catch (error) {
  setStatus('Erro de inicializacao.');
  appendLog('ERROR: falha no setup da interface: ' + String(error) + '\n');
}
