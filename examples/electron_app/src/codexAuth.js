const crypto = require("crypto");
const fs = require("fs");
const http = require("http");
const os = require("os");
const path = require("path");
const { spawn } = require("child_process");

function base64UrlEncode(input) {
  let out = Buffer.from(input)
    .toString("base64")
    .replaceAll("+", "-")
    .replaceAll("/", "_");
  while (out.endsWith("=")) {
    out = out.slice(0, -1);
  }
  return out;
}
function randomBase64Url(byteLength) {
  return base64UrlEncode(crypto.randomBytes(byteLength || 64));
}

function normalizeCallbackPath(value) {
  const maybePath = String(value || "/oauth/callback").trim() || "/oauth/callback";
  return maybePath.startsWith("/") ? maybePath : "/" + maybePath;
}

function parseNumber(value) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function parseJwt(token) {
  if (!token || typeof token !== "string") return null;
  const parts = token.split(".");
  if (parts.length < 2) return null;
  const raw = parts[1].replaceAll("-", "+").replaceAll("_", "/");
  const padding = (4 - (raw.length % 4)) % 4;
  const padded = raw + "=".repeat(padding);
  try {
    return JSON.parse(Buffer.from(padded, "base64").toString("utf-8"));
  } catch {
    return null;
  }
}

function createPkcePair() {
  const verifier = randomBase64Url(64);
  const challenge = base64UrlEncode(crypto.createHash("sha256").update(verifier).digest());
  return { verifier, challenge };
}
function buildAuthConfig() {
  return {
    host: String(process.env.DOCREADER_CODEX_CALLBACK_HOST || "127.0.0.1").trim(),
    port: parseNumber(process.env.DOCREADER_CODEX_CALLBACK_PORT) || 0,
    callbackPath: normalizeCallbackPath(process.env.DOCREADER_CODEX_CALLBACK_PATH),
    authUrl: String(process.env.DOCREADER_CODEX_AUTH_URL || "https://auth.openai.com/oauth/authorize").trim(),
    tokenUrl: String(process.env.DOCREADER_CODEX_TOKEN_URL || "https://auth.openai.com/oauth/token").trim(),
    clientId: String(process.env.DOCREADER_CODEX_CLIENT_ID || "").trim(),
    scope: String(process.env.DOCREADER_CODEX_SCOPE || "openid profile email offline_access").trim(),
    audience: String(process.env.DOCREADER_CODEX_AUDIENCE || "").trim(),
    resource: String(process.env.DOCREADER_CODEX_RESOURCE || "").trim(),
    timeoutMs: parseNumber(process.env.DOCREADER_CODEX_OAUTH_TIMEOUT_MS) || 180000,
  };
}

function normalizeToken(tokenPayload, oldToken) {
  const now = Date.now();
  const expiresIn = parseNumber(tokenPayload && tokenPayload.expires_in);
  const expiresAt = expiresIn !== null ? now + Math.max(0, Math.floor(expiresIn - 30)) * 1000 : null;

  return {
    accessToken: (tokenPayload && tokenPayload.access_token) || null,
    refreshToken: (tokenPayload && tokenPayload.refresh_token) || (oldToken && oldToken.refreshToken) || null,
    idToken: (tokenPayload && tokenPayload.id_token) || (oldToken && oldToken.idToken) || null,
    tokenType: (tokenPayload && tokenPayload.token_type) || (oldToken && oldToken.tokenType) || "Bearer",
    scope: (tokenPayload && tokenPayload.scope) || (oldToken && oldToken.scope) || null,
    expiresAt,
    obtainedAt: now,
  };
}

function buildIdentity(token) {
  const decoded = parseJwt(token && token.idToken) || parseJwt(token && token.accessToken) || {};
  return {
    sub: decoded.sub || null,
    email: decoded.email || null,
    name: decoded.name || null,
    preferredUsername: decoded.preferred_username || null,
  };
}

function candidateCodexCliCommands() {
  const overridePath = String(process.env.DOCREADER_CODEX_CLI_CMD || "").trim();
  if (overridePath) {
    return [overridePath];
  }

  const commands = ["codex"];
  if (process.platform !== "win32") {
    return commands;
  }

  const home = os.homedir();
  const appData = process.env.APPDATA || path.join(home, "AppData", "Roaming");
  const localAppData = process.env.LOCALAPPDATA || path.join(home, "AppData", "Local");
  const npmPrefix = process.env.PREFIX ? String(process.env.PREFIX) : "";

  const windowsCandidates = [
    path.join(appData, "npm", "codex.cmd"),
    path.join(localAppData, "npm", "codex.cmd"),
    npmPrefix ? path.join(npmPrefix, "codex.cmd") : "",
    npmPrefix ? path.join(npmPrefix, "bin", "codex.cmd") : "",
    path.join(home, ".npm-global", "bin", "codex.cmd"),
  ].filter(Boolean);

  return [...windowsCandidates, ...commands];
}

function resolveCodexCliCommand() {
  const candidates = candidateCodexCliCommands();
  for (const candidate of candidates) {
    if (candidate.includes(path.sep) || candidate.includes("/")) {
      if (fs.existsSync(candidate)) {
        return candidate;
      }
      continue;
    }
    return candidate;
  }
  return "codex";
}

function detectCommandMissing(result) {
  const combined = [result && result.error, result && result.stderr, result && result.stdout]
    .filter(Boolean)
    .join("\n")
    .toLowerCase();
  const byCode = String(result && result.errorCode || "").toUpperCase() === "ENOENT";
  const byText =
    combined.includes("is not recognized as an internal or external command") ||
    combined.includes("reconhecido como um comando interno") ||
    combined.includes("nao e reconhecido como um comando interno") ||
    combined.includes("command not found") ||
    combined.includes("not recognized as an internal") ||
    combined.includes("not found");
  return byCode || byText;
}

function resolveCodexCliAuthFile() {
  const overridePath = String(process.env.DOCREADER_CODEX_AUTH_FILE || "").trim();
  if (overridePath) return overridePath;

  const home = os.homedir();
  const candidates = [path.join(home, ".codex", "auth.json")];

  if (process.platform === "win32") {
    const appData = process.env.APPDATA || path.join(home, "AppData", "Roaming");
    const localAppData = process.env.LOCALAPPDATA || path.join(home, "AppData", "Local");
    candidates.push(path.join(appData, "Codex", "auth.json"));
    candidates.push(path.join(appData, "codex", "auth.json"));
    candidates.push(path.join(localAppData, "Codex", "auth.json"));
    candidates.push(path.join(localAppData, "codex", "auth.json"));
  }

  for (const candidate of candidates) {
    if (fs.existsSync(candidate)) {
      return candidate;
    }
  }

  // Keep previous default if none exists yet.
  return candidates[0];
}

function readCodexCliAuthPayload() {
  const authFile = resolveCodexCliAuthFile();
  if (!fs.existsSync(authFile)) {
    return { ok: false, error: "not_found", authFile };
  }

  try {
    const raw = fs.readFileSync(authFile, "utf8");
    const parsed = JSON.parse(raw);
    return { ok: true, parsed, authFile };
  } catch (error) {
    return { ok: false, error: String((error && error.message) || error), authFile };
  }
}

function buildPayloadFromCodexCli(parsed) {
  const tokens = parsed && parsed.tokens ? parsed.tokens : null;
  const accessToken = tokens && tokens.access_token ? String(tokens.access_token) : "";
  if (!accessToken) return null;

  const token = {
    accessToken,
    refreshToken: tokens && tokens.refresh_token ? String(tokens.refresh_token) : null,
    idToken: tokens && tokens.id_token ? String(tokens.id_token) : null,
    tokenType: tokens && tokens.token_type ? String(tokens.token_type) : "Bearer",
    scope: parsed && parsed.scope ? String(parsed.scope) : null,
    expiresAt: null,
    obtainedAt: Date.now(),
  };

  const identity = buildIdentity(token);
  if (!identity.sub && tokens && tokens.account_id) {
    identity.sub = String(tokens.account_id);
  }

  return {
    provider: "codex-cli-local",
    configuredAt: new Date().toISOString(),
    token,
    identity,
  };
}

async function parseJsonResponse(resp) {
  const body = await resp.text();
  try {
    return { body, json: JSON.parse(body) };
  } catch {
    return { body, json: null };
  }
}

function runCommand(command, args, options) {
  const opts = options || {};
  const timeoutMs = Number.isFinite(opts.timeoutMs) ? opts.timeoutMs : 120000;

  return new Promise((resolve) => {
    let settled = false;
    const settle = (value) => {
      if (settled) return;
      settled = true;
      resolve(value);
    };

    const child = spawn(command, args || [], {
      cwd: opts.cwd || process.cwd(),
      env: opts.env || process.env,
      windowsHide: opts.windowsHide !== false,
      shell: Boolean(opts.shell),
    });

    let stdout = "";
    let stderr = "";

    child.stdout.on("data", (chunk) => {
      const text = String(chunk || "");
      stdout += text;
      if (typeof opts.onStdout === "function") {
        opts.onStdout(text);
      }
    });

    child.stderr.on("data", (chunk) => {
      const text = String(chunk || "");
      stderr += text;
      if (typeof opts.onStderr === "function") {
        opts.onStderr(text);
      }
    });

    const timeout = setTimeout(() => {
      try {
        child.kill();
      } catch {
        // ignore
      }
      settle({ ok: false, code: null, stdout, stderr, error: "timeout" });
    }, timeoutMs);

    child.on("error", (error) => {
      clearTimeout(timeout);
      settle({
        ok: false,
        code: null,
        stdout,
        stderr,
        error: String((error && error.message) || error),
        errorCode: error && error.code ? String(error.code) : null,
      });
    });

    child.on("close", (code) => {
      clearTimeout(timeout);
      settle({ ok: code === 0, code, stdout, stderr, error: code === 0 ? null : "exit_" + String(code) });
    });
  });
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function checkCodexLoggedIn(cliCommand) {
  const command = cliCommand || resolveCodexCliCommand();
  const result = await runCommand(command, ["login", "status"], {
    timeoutMs: 15000,
    windowsHide: true,
    shell: process.platform === "win32",
  });
  const text = (result.stdout || "") + "\n" + (result.stderr || "");
  const normalized = String(text || "").trim();
  const commandMissing = detectCommandMissing(result);
  const explicitlyNotLoggedIn = /\bnot logged in\b/i.test(normalized);
  const explicitlyLoggedIn = /\blogged in\b/i.test(normalized);
  const loggedIn = commandMissing ? false : explicitlyNotLoggedIn ? false : result.code === 0 || explicitlyLoggedIn;

  return {
    ok: result.ok,
    code: result.code,
    loggedIn,
    command,
    commandMissing,
    errorCode: result.errorCode || null,
    output: text,
  };
}

async function waitForCodexLoginCompletion(options) {
  const opts = options || {};
  const cliCommand = opts.cliCommand || resolveCodexCliCommand();
  const timeoutMs = Number.isFinite(opts.timeoutMs) ? opts.timeoutMs : 300000;
  const intervalMs = Number.isFinite(opts.intervalMs) ? opts.intervalMs : 1500;
  const onPoll = typeof opts.onPoll === "function" ? opts.onPoll : () => {};

  const startedAt = Date.now();
  let attempt = 0;

  while (Date.now() - startedAt < timeoutMs) {
    attempt += 1;
    const status = await checkCodexLoggedIn(cliCommand);
    onPoll({ attempt, ...status });
    if (status.loggedIn) {
      return { ok: true, attempt };
    }
    await sleep(intervalMs);
  }

  return { ok: false, error: "timeout_waiting_for_codex_login" };
}

async function launchCodexDeviceAuthInteractive(cliCommand) {
  const command = cliCommand || resolveCodexCliCommand();
  if (process.platform === "win32") {
    const safeCommand = String(command).replaceAll('"', '""');
    const commandLine = `"${safeCommand}" login --device-auth`;
    // Open an interactive terminal window and keep it open so the user can complete/inspect auth.
    return await runCommand("cmd.exe", ["/c", "start", "", "cmd.exe", "/k", commandLine], {
      timeoutMs: 10000,
      windowsHide: false,
      shell: false,
    });
  }

  return await runCommand(command, ["login", "--device-auth"], {
    timeoutMs: 10 * 60 * 1000,
    windowsHide: false,
  });
}

function buildStatus(payload) {
  const token = payload && payload.token ? payload.token : null;
  const identity = payload && payload.identity ? payload.identity : null;
  const hasAccessToken = Boolean(token && token.accessToken);
  const expiresAt = token ? token.expiresAt || null : null;
  const isExpired = expiresAt !== null ? Date.now() >= expiresAt : false;

  return {
    connected: hasAccessToken && !isExpired,
    hasAccessToken,
    isExpired,
    expiresAt,
    scope: token ? token.scope || null : null,
    tokenType: token ? token.tokenType || null : null,
    token: token
      ? {
          accessToken: token.accessToken || null,
          tokenType: token.tokenType || null,
          expiresAt: token.expiresAt || null,
        }
      : null,
    identity,
    configuredAt: payload && payload.configuredAt ? payload.configuredAt : null,
    provider: payload && payload.provider ? payload.provider : "codex-cli",
  };
}
async function startLoopbackServer(config, expectedState) {
  const resultState = {
    done: false,
    timeoutHandle: null,
  };

  let resolveResult;
  let rejectResult;

  const resultPromise = new Promise((resolve, reject) => {
    resolveResult = resolve;
    rejectResult = reject;
  });

  const resolveOnce = (value) => {
    if (resultState.done) return;
    resultState.done = true;
    resolveResult(value);
  };

  const rejectOnce = (error) => {
    if (resultState.done) return;
    resultState.done = true;
    rejectResult(error);
  };

  const server = http.createServer((req, res) => {
    const fallbackHost = config.host + ":" + String(config.port || 80);
    const baseUrl = "http://" + String(req.headers.host || fallbackHost);
    let requestUrl;
    try {
      requestUrl = new URL(req.url || "/", baseUrl);
    } catch {
      res.statusCode = 400;
      res.end("Invalid request");
      rejectOnce(new Error("Invalid callback request."));
      return;
    }

    if (requestUrl.pathname !== config.callbackPath) {
      res.statusCode = 404;
      res.end("Not found");
      return;
    }

    const authError = requestUrl.searchParams.get("error");
    const authErrorDescription = requestUrl.searchParams.get("error_description");
    const code = requestUrl.searchParams.get("code");
    const stateParam = requestUrl.searchParams.get("state");

    if (authError) {
      res.statusCode = 400;
      res.setHeader("content-type", "text/html; charset=utf-8");
      res.end("<html><body><h1>Authentication failed</h1><p>You can close this window.</p></body></html>");
      const details = authErrorDescription
        ? "Authorization failed: " + authError + " (" + authErrorDescription + ")"
        : "Authorization failed: " + authError;
      rejectOnce(new Error(details));
      return;
    }

    if (!code) {
      res.statusCode = 400;
      res.setHeader("content-type", "text/html; charset=utf-8");
      res.end("<html><body><h1>Missing code</h1><p>You can close this window.</p></body></html>");
      rejectOnce(new Error("Authorization callback did not include code."));
      return;
    }
    if (!stateParam || stateParam !== expectedState) {
      res.statusCode = 400;
      res.setHeader("content-type", "text/html; charset=utf-8");
      res.end("<html><body><h1>State mismatch</h1><p>You can close this window.</p></body></html>");
      rejectOnce(new Error("Authorization callback state mismatch."));
      return;
    }

    res.statusCode = 200;
    res.setHeader("content-type", "text/html; charset=utf-8");
    res.end(
      "<html><body><h1>Authenticated</h1><p>You can close this window and return to Documents Reader.</p></body></html>"
    );
    resolveOnce({ code });
  });

  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(config.port, config.host, () => resolve());
  });

  const address = server.address();
  const listeningPort =
    address && typeof address === "object" && Object.prototype.hasOwnProperty.call(address, "port")
      ? address.port
      : config.port;
  const redirectUri = "http://" + config.host + ":" + String(listeningPort) + config.callbackPath;

  resultState.timeoutHandle = setTimeout(() => {
    rejectOnce(new Error("Timed out waiting for OAuth callback."));
  }, config.timeoutMs);

  const waitForResult = async () => {
    try {
      return await resultPromise;
    } finally {
      if (resultState.timeoutHandle) {
        clearTimeout(resultState.timeoutHandle);
      }
      await new Promise((resolve) => server.close(() => resolve()));
    }
  };

  return { redirectUri, waitForResult };
}
function createCodexAuthManager(options) {
  const readSettings = options.readSettings;
  const writeSettings = options.writeSettings;
  const openExternal = options.openExternal;
  const notifyChanged = options.notifyChanged || (() => {});
  const notifyLog = options.notifyLog || (() => {});

  function emitLog(step, details, level) {
    try {
      notifyLog({
        ts: new Date().toISOString(),
        level: level || "info",
        step,
        details: details || null,
      });
    } catch {
      // keep auth flow working even if logging sink fails
    }
  }

  let currentAuthPromise = null;

  async function readStoredPayload() {
    const settings = await readSettings();
    const payload = settings.codexAuth || null;
    emitLog("storage.read", {
      found: Boolean(payload),
      provider: payload && payload.provider ? payload.provider : null,
    });
    return payload;
  }

  async function writeStoredPayload(payload) {
    const settings = await readSettings();
    if (payload) {
      settings.codexAuth = payload;
    } else {
      delete settings.codexAuth;
    }
    await writeSettings(settings);
    emitLog("storage.write", {
      hasPayload: Boolean(payload),
      provider: payload && payload.provider ? payload.provider : null,
    });
  }

  async function importFromCodexCliAuth() {
    const authFile = resolveCodexCliAuthFile();
    emitLog("import.cli.begin", { authFile });

    const readResult = readCodexCliAuthPayload();
    if (!readResult.ok) {
      emitLog(
        "import.cli.unavailable",
        {
          authFile,
          reason: readResult.error,
        },
        "warn"
      );
      return {
        ok: false,
        error: "codex_cli_auth_unavailable",
        details: readResult.error,
        authFile: readResult.authFile,
      };
    }

    const payload = buildPayloadFromCodexCli(readResult.parsed);
    if (!payload) {
      emitLog(
        "import.cli.invalid",
        {
          authFile,
          reason: "tokens.access_token not found",
        },
        "warn"
      );
      return {
        ok: false,
        error: "codex_cli_auth_invalid",
        details: "tokens.access_token not found in auth file",
        authFile: readResult.authFile,
      };
    }

    await writeStoredPayload(payload);
    emitLog("import.cli.success", {
      authFile,
      provider: payload.provider,
      hasIdentity: Boolean(payload.identity && (payload.identity.sub || payload.identity.email)),
    });
    return { ok: true, payload, authFile: readResult.authFile };
  }

  async function notifyStatus() {
    const status = await getStatus({ autoRefresh: false });
    notifyChanged(status);
    emitLog("status.notify", {
      connected: Boolean(status && status.connected),
      provider: status && status.provider ? status.provider : null,
    });
    return status;
  }

  async function exchangeToken(body) {
    const config = buildAuthConfig();
    const grantType = body && body.grant_type ? String(body.grant_type) : "unknown";
    emitLog("oauth.exchange.begin", {
      grantType,
      tokenUrl: config.tokenUrl,
    });

    const resp = await fetch(config.tokenUrl, {
      method: "POST",
      headers: {
        "content-type": "application/x-www-form-urlencoded",
        accept: "application/json",
      },
      body: new URLSearchParams(body).toString(),
    });

    const parsed = await parseJsonResponse(resp);
    if (!resp.ok || !parsed.json) {
      const reason = parsed.json && parsed.json.error ? parsed.json.error : parsed.body || "Unknown error";
      emitLog(
        "oauth.exchange.http_error",
        {
          grantType,
          status: resp.status,
          reason: String(reason).slice(0, 240),
        },
        "error"
      );
      throw new Error("Token endpoint failed (" + String(resp.status) + "). " + reason);
    }

    if (parsed.json.error) {
      const details = parsed.json.error_description
        ? parsed.json.error + ": " + parsed.json.error_description
        : parsed.json.error;
      emitLog(
        "oauth.exchange.error",
        {
          grantType,
          status: resp.status,
          reason: String(details).slice(0, 240),
        },
        "error"
      );
      throw new Error(details);
    }

    emitLog("oauth.exchange.success", {
      grantType,
      expiresIn: parseNumber(parsed.json.expires_in),
      hasRefreshToken: Boolean(parsed.json.refresh_token),
      hasIdToken: Boolean(parsed.json.id_token),
    });
    return parsed.json;
  }

  async function saveToken(tokenPayload, oldToken) {
    const token = normalizeToken(tokenPayload, oldToken);
    const payload = {
      provider: "codex-cli",
      configuredAt: new Date().toISOString(),
      token,
      identity: buildIdentity(token),
    };
    await writeStoredPayload(payload);
    emitLog("token.save", {
      provider: payload.provider,
      expiresAt: token.expiresAt,
      hasIdentity: Boolean(payload.identity && (payload.identity.sub || payload.identity.email)),
    });
    return payload;
  }

  async function refresh() {
    emitLog("refresh.begin", null);
    const config = buildAuthConfig();
    if (!config.clientId) {
      emitLog("refresh.no_client_id", null, "warn");
      return {
        ok: false,
        error: "Missing OAuth config: DOCREADER_CODEX_CLIENT_ID",
        missingConfig: ["DOCREADER_CODEX_CLIENT_ID"],
      };
    }

    const existing = await readStoredPayload();
    const refreshToken = existing && existing.token ? existing.token.refreshToken : null;
    if (!refreshToken) {
      emitLog("refresh.no_refresh_token", null, "warn");
      return { ok: false, error: "No refresh token stored. Start a new login." };
    }

    try {
      const tokenPayload = await exchangeToken({
        grant_type: "refresh_token",
        refresh_token: refreshToken,
        client_id: config.clientId,
      });
      const saved = await saveToken(tokenPayload, existing.token);
      const status = buildStatus(saved);
      notifyChanged(status);
      emitLog("refresh.success", {
        connected: status.connected,
        provider: status.provider,
      });
      return { ok: true, status };
    } catch (error) {
      const message = String((error && error.message) || error);
      emitLog("refresh.error", { message }, "error");
      return { ok: false, error: message };
    }
  }

  async function getStatus(opts) {
    const options = opts || {};
    const autoRefresh = Boolean(options.autoRefresh);
    emitLog("status.begin", { autoRefresh });

    const config = buildAuthConfig();
    const missingConfig = [];
    if (!config.clientId) missingConfig.push("DOCREADER_CODEX_CLIENT_ID");

    let payload = await readStoredPayload();

    if (payload && payload.provider === "codex-cli-local") {
      const cliAuth = readCodexCliAuthPayload();
      if (!cliAuth.ok) {
        emitLog(
          "status.local_payload_stale",
          {
            reason: cliAuth.error,
            authFile: cliAuth.authFile,
          },
          "warn"
        );
        await writeStoredPayload(null);
        payload = null;
      }
    }

    if (!payload || !payload.token || !payload.token.accessToken) {
      emitLog("status.no_stored_token", null, "warn");
    }

    if (!payload || !payload.token) {
      const disconnected = {
        connected: false,
        configured: missingConfig.length === 0,
        missingConfig,
        identity: null,
        expiresAt: null,
        tokenType: null,
        token: null,
      };
      emitLog("status.disconnected", {
        configured: disconnected.configured,
        missingConfig,
      });
      return disconnected;
    }

    let status = buildStatus(payload);

    if (autoRefresh && status.hasAccessToken && status.isExpired && payload.token.refreshToken) {
      emitLog("status.auto_refresh.try", {
        isExpired: status.isExpired,
      });
      const refreshed = await refresh();
      if (refreshed.ok) {
        status = refreshed.status;
        emitLog("status.auto_refresh.success", {
          connected: status.connected,
        });
      } else {
        status.refreshError = refreshed.error;
        emitLog(
          "status.auto_refresh.failed",
          {
            error: refreshed.error,
          },
          "warn"
        );
      }
    }

    const configured = missingConfig.length === 0 || status.provider === "codex-cli-local";
    const finalStatus = {
      ...status,
      configured,
      missingConfig,
    };

    emitLog("status.result", {
      connected: finalStatus.connected,
      configured: finalStatus.configured,
      provider: finalStatus.provider,
      isExpired: finalStatus.isExpired,
      hasAccessToken: finalStatus.hasAccessToken,
    });

    return finalStatus;
  }

  async function start(opts) {
    const options = opts || {};
    const allowLocalImport = options.allowLocalImport !== false;
    const forceDeviceAuth = Boolean(options.forceDeviceAuth);
    if (currentAuthPromise) {
      emitLog("start.already_in_progress", null, "warn");
      return { ok: false, error: "Authentication already in progress." };
    }

    const config = buildAuthConfig();
    const missing = [];
    if (!config.clientId) missing.push("DOCREADER_CODEX_CLIENT_ID");

    emitLog("start.begin", {
      hasClientId: Boolean(config.clientId),
      missing,
      allowLocalImport,
      forceDeviceAuth,
    });

    if (missing.length) {
      if (!allowLocalImport) {
        emitLog(
          "start.missing_config",
          {
            missingConfig: missing,
            allowLocalImport,
          },
          "warn"
        );
        return {
          ok: false,
          error: "Missing OAuth config: " + missing.join(", "),
          missingConfig: missing,
          requiresOAuthConfig: true,
        };
      }

      const cliCommand = resolveCodexCliCommand();
      emitLog("cli.command.resolved", { command: cliCommand });

      if (!forceDeviceAuth) {
        const importedBeforeCli = await importFromCodexCliAuth();
        if (importedBeforeCli.ok) {
          const status = buildStatus(importedBeforeCli.payload);
          notifyChanged(status);
          emitLog("start.cli_import_used", {
            connected: status.connected,
            provider: status.provider,
            source: "codex-cli-auth-file",
          });
          return { ok: true, status, source: "codex-cli-auth-file" };
        }
      }

      let alreadyLoggedIn = false;
      let cliCommandMissing = false;
      if (!forceDeviceAuth) {
        emitLog("cli.status.check.begin", null);
        const cliStatus = await checkCodexLoggedIn(cliCommand);
        alreadyLoggedIn = cliStatus.loggedIn;
        cliCommandMissing = Boolean(cliStatus.commandMissing);

        emitLog("cli.status.check.result", {
          ok: cliStatus.ok,
          code: cliStatus.code,
          alreadyLoggedIn,
          commandMissing: cliCommandMissing,
          command: cliStatus.command,
        });
      } else {
        emitLog("cli.status.check.skipped", { reason: "force_device_auth" });
        const cliProbe = await checkCodexLoggedIn(cliCommand);
        cliCommandMissing = Boolean(cliProbe.commandMissing);
        emitLog("cli.status.check.forced_probe", {
          ok: cliProbe.ok,
          code: cliProbe.code,
          commandMissing: cliCommandMissing,
          command: cliProbe.command,
        });
      }

      if (cliCommandMissing) {
        emitLog(
          "cli.command.missing",
          {
            command: cliCommand,
          },
          "warn"
        );
        return {
          ok: false,
          error: "Codex CLI not found in this system.",
          details:
            "Install Codex CLI and ensure command 'codex' is available in PATH (or set DOCREADER_CODEX_CLI_CMD). If you have OAuth configured, define DOCREADER_CODEX_CLIENT_ID.",
        };
      }

      let loginSource = "codex-cli-local";

      if (forceDeviceAuth || !alreadyLoggedIn) {
        if (forceDeviceAuth) {
          await writeStoredPayload(null);
          emitLog("storage.cleared_for_force_login", null);
          emitLog("cli.logout.begin", { reason: "force_device_auth" });
          const cliLogout = await runCommand(cliCommand, ["logout"], {
            timeoutMs: 15000,
            windowsHide: true,
            shell: process.platform === "win32",
          });
          emitLog("cli.logout.result", {
            ok: cliLogout.ok,
            code: cliLogout.code,
            error: cliLogout.error,
          });
        }

        emitLog("cli.device_auth.begin", {
          command: String(cliCommand) + " login --device-auth",
          forced: forceDeviceAuth,
          launchMode: process.platform === "win32" ? "external_terminal" : "direct",
        });

        const cliLogin = await launchCodexDeviceAuthInteractive(cliCommand);

        emitLog("cli.device_auth.launch_result", {
          ok: cliLogin.ok,
          code: cliLogin.code,
          error: cliLogin.error,
          forced: forceDeviceAuth,
        });

        if (!cliLogin.ok) {
          const commandMissing = detectCommandMissing(cliLogin);
          const details = String(cliLogin.stderr || cliLogin.stdout || cliLogin.error || "unknown error").trim();
          return {
            ok: false,
            error: commandMissing ? "Codex CLI not found in this system." : "Codex CLI login launch failed.",
            details,
          };
        }

        emitLog("cli.device_auth.waiting", {
          timeoutMs: 300000,
          pollIntervalMs: 1500,
        });

        const waited = await waitForCodexLoginCompletion({
          cliCommand,
          timeoutMs: 300000,
          intervalMs: 1500,
          onPoll: (poll) => {
            emitLog("cli.status.poll", {
              attempt: poll.attempt,
              ok: poll.ok,
              code: poll.code,
              loggedIn: poll.loggedIn,
              commandMissing: Boolean(poll.commandMissing),
            });
          },
        });

        if (!waited.ok) {
          emitLog("cli.device_auth.timeout", { error: waited.error }, "warn");
          return {
            ok: false,
            error: "Timed out waiting for Codex CLI login completion.",
            details: "Finalize o login na janela do terminal/browser e tente novamente.",
          };
        }

        emitLog("cli.device_auth.completed", {
          attempt: waited.attempt,
        });

        loginSource = forceDeviceAuth ? "codex-cli-device-auth-forced" : "codex-cli-device-auth";
      } else {
        emitLog("cli.device_auth.skipped", { reason: "already_logged_in" });
        loginSource = "codex-cli-status";
      }

      const imported = await importFromCodexCliAuth();
      if (imported.ok) {
        const status = buildStatus(imported.payload);
        notifyChanged(status);
        emitLog("start.cli_import_used", {
          connected: status.connected,
          provider: status.provider,
          source: loginSource,
        });
        return { ok: true, status, source: loginSource };
      }

      emitLog(
        "start.cli_import_failed",
        {
          error: imported.error,
          details: imported.details,
        },
        "warn"
      );
      return {
        ok: false,
        error: "Codex CLI auth file not available after login.",
        details: imported.details || imported.error,
      };
    }

    const run = (async () => {
      const pkce = createPkcePair();
      const state = randomBase64Url(32);
      const callback = await startLoopbackServer(config, state);
      emitLog("oauth.loopback.ready", { redirectUri: callback.redirectUri });

      const authUrl = new URL(config.authUrl);
      authUrl.searchParams.set("response_type", "code");
      authUrl.searchParams.set("client_id", config.clientId);
      authUrl.searchParams.set("redirect_uri", callback.redirectUri);
      authUrl.searchParams.set("scope", config.scope);
      authUrl.searchParams.set("code_challenge", pkce.challenge);
      authUrl.searchParams.set("code_challenge_method", "S256");
      authUrl.searchParams.set("state", state);
      if (config.audience) authUrl.searchParams.set("audience", config.audience);
      if (config.resource) authUrl.searchParams.set("resource", config.resource);

      await openExternal(authUrl.toString());
      emitLog("oauth.browser.opened", {
        authHost: authUrl.host,
        callbackPath: config.callbackPath,
      });

      const authorization = await callback.waitForResult();
      emitLog("oauth.callback.received", {
        hasCode: Boolean(authorization && authorization.code),
      });

      const tokenPayload = await exchangeToken({
        grant_type: "authorization_code",
        client_id: config.clientId,
        code: authorization.code,
        code_verifier: pkce.verifier,
        redirect_uri: callback.redirectUri,
      });

      const saved = await saveToken(tokenPayload, null);
      const status = buildStatus(saved);
      notifyChanged(status);

      emitLog("start.success", {
        connected: status.connected,
        provider: status.provider,
      });

      return { ok: true, status };
    })();

    currentAuthPromise = run;
    try {
      return await run;
    } catch (error) {
      const message = String((error && error.message) || error);
      emitLog("start.error", { message }, "error");
      return { ok: false, error: message };
    } finally {
      currentAuthPromise = null;
    }
  }

  async function logout() {
    emitLog("logout.begin", null);
    await writeStoredPayload(null);
    const status = await notifyStatus();
    emitLog("logout.success", {
      connected: status.connected,
    });
    return { ok: true, status };
  }

  return {
    start,
    refresh,
    getStatus,
    logout,
  };
}

module.exports = {
  createCodexAuthManager,
};
