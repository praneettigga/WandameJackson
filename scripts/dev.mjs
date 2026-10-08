import { spawn } from 'node:child_process';
import { existsSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const repositoryRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const apiDirectory = path.join(repositoryRoot, 'services', 'api');
const webDirectory = path.join(repositoryRoot, 'apps', 'web');
const apiPort = process.env.ROOMSHIFT_API_PORT ?? '8000';
const webPort = process.env.ROOMSHIFT_WEB_PORT ?? '5173';
const apiBaseUrl = `http://127.0.0.1:${apiPort}`;
const webBaseUrl = `http://127.0.0.1:${webPort}`;
const defaultPython = path.join(
  apiDirectory,
  '.venv',
  process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python',
);
const python = process.env.ROOMSHIFT_PYTHON ?? defaultPython;
const npm = process.platform === 'win32' ? 'npm.cmd' : 'npm';
const reloadArguments =
  process.env.ROOMSHIFT_RELOAD === 'true' ? ['--reload', '--reload-dir', 'roomshift_api'] : [];
let apiProcess;
let webProcess;
let stopping = false;

function stop(code) {
  if (stopping) return;
  stopping = true;
  process.exitCode = code;
  for (const child of [webProcess, apiProcess]) {
    if (child && child.exitCode === null && child.signalCode === null) child.kill('SIGINT');
  }
}

function watch(name, child) {
  child.once('error', (error) => {
    console.error(`${name} could not start: ${error.message}`);
    stop(1);
  });
  child.once('exit', (code, signal) => {
    if (stopping) return;
    console.error(`${name} exited unexpectedly (${signal ?? code ?? 'unknown'}).`);
    stop(code ?? 1);
  });
}

function delay(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

async function waitForApi() {
  const deadline = Date.now() + 15_000;
  let lastError = 'No health response received.';
  while (Date.now() < deadline && !stopping) {
    try {
      const response = await fetch(`${apiBaseUrl}/api/health`);
      const health = await response.json();
      if (response.ok && health.status === 'ok' && health.schemaVersion === '0.1.0') return;
      lastError = `Health endpoint returned HTTP ${response.status}.`;
    } catch (error) {
      lastError = error instanceof Error ? error.message : String(error);
    }
    await delay(250);
  }
  if (stopping) throw new Error('API stopped before its health endpoint became ready.');
  throw new Error(`API did not become ready at ${apiBaseUrl}/api/health within 15 seconds: ${lastError}`);
}

async function main() {
  if (!process.env.ROOMSHIFT_PYTHON && !existsSync(python)) {
    throw new Error(
      `Backend virtual environment not found at ${python}. Create it with \"cd services/api && python -m venv .venv\" and install requirements first.`,
    );
  }

  console.log(`Starting API at ${apiBaseUrl}…`);
  apiProcess = spawn(
    python,
    [
      '-m',
      'uvicorn',
      'roomshift_api.main:app',
      '--host',
      '127.0.0.1',
      '--port',
      apiPort,
      ...reloadArguments,
    ],
    { cwd: apiDirectory, stdio: 'inherit' },
  );
  watch('API', apiProcess);
  await waitForApi();

  console.log(`API connected. Starting web app at ${webBaseUrl}…`);
  webProcess = spawn(npm, ['run', 'dev', '--', '--port', webPort, '--strictPort'], {
    cwd: webDirectory,
    stdio: 'inherit',
    env: {
      ...process.env,
      VITE_API_BASE_URL: process.env.VITE_API_BASE_URL ?? apiBaseUrl,
      VITE_USE_MOCK_API: process.env.VITE_USE_MOCK_API ?? 'false',
    },
  });
  watch('Web app', webProcess);
}

process.on('SIGINT', () => stop(0));
process.on('SIGTERM', () => stop(0));

main().catch((error) => {
  console.error(error instanceof Error ? error.message : String(error));
  stop(1);
});
