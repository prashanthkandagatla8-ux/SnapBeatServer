/**
 * BeatCanvas desktop shell.
 *
 * The application itself is a Python program: all the listening, choreographing and
 * rendering lives there. This is the window around it, and it takes on the three jobs a
 * browser cannot do well.
 *
 * It owns the server's life. The Python process is started when the window opens and
 * stopped when the window closes, so there is no orphaned server left holding a port and
 * no chance of the window talking to a stale copy of the code -- a problem that is easy to
 * cause and hard to notice when the two are started separately.
 *
 * It owns the port. A free one is found here and handed to Python, rather than both sides
 * guessing, so a second instance or another project cannot collide with it.
 *
 * It provides real file dialogs, through the preload bridge, instead of the page asking
 * the server to open one.
 */
'use strict';

const { app, BrowserWindow, dialog, ipcMain, shell, Menu } = require('electron');
const { spawn } = require('child_process');
const fs = require('fs');
const http = require('http');
const net = require('net');
const path = require('path');

const ROOT = path.resolve(__dirname, '..');
const LOG_DIR = path.join(ROOT, '_logs');
const ICON = path.join(ROOT, '_assets', 'beatcanvas.ico');

/** Interpreters to try, best first. Must have numpy, OpenCV, FastAPI and uvicorn. */
const INTERPRETERS = [
  path.join('C:', 'Users', 'prash', 'Kiro Projects', 'GoldForge', '.venv', 'Scripts', 'python.exe'),
  path.join(ROOT, '.venv', 'Scripts', 'python.exe'),
  'python',
];

let server = null;
let window_ = null;
let serverPort = 0;
let shuttingDown = false;

function log(line) {
  const stamped = `[shell ${new Date().toISOString()}] ${line}\n`;
  try {
    fs.mkdirSync(LOG_DIR, { recursive: true });
    fs.appendFileSync(path.join(LOG_DIR, 'desktop.log'), stamped);
  } catch (ignored) { /* logging must never be the thing that breaks startup */ }
  process.stdout.write(stamped);
}

function pickInterpreter() {
  for (const candidate of INTERPRETERS) {
    if (candidate === 'python' || fs.existsSync(candidate)) return candidate;
  }
  return 'python';
}

/** A port the operating system says is free, so Python and this window agree on one. */
function freePort() {
  return new Promise((resolve, reject) => {
    const probe = net.createServer();
    probe.unref();
    probe.on('error', reject);
    probe.listen(0, '127.0.0.1', () => {
      const { port } = probe.address();
      probe.close(() => resolve(port));
    });
  });
}

function startServer(port) {
  const interpreter = pickInterpreter();
  log(`starting ${interpreter} run.py on port ${port}`);
  server = spawn(interpreter, ['run.py'], {
    cwd: ROOT,
    env: {
      ...process.env,
      BEATCANVAS_PORT: String(port),
      // The window is the interface; a browser tab as well would be a second copy.
      BEATCANVAS_NO_BROWSER: '1',
      PYTHONUNBUFFERED: '1',
    },
    windowsHide: true,
    stdio: ['ignore', 'pipe', 'pipe'],
  });

  server.stdout.on('data', (chunk) => log(`server: ${String(chunk).trimEnd()}`));
  server.stderr.on('data', (chunk) => log(`server: ${String(chunk).trimEnd()}`));
  server.on('exit', (code, signal) => {
    log(`server exited (code ${code}, signal ${signal})`);
    server = null;
    // If it died on its own rather than because we are closing, say so plainly instead of
    // leaving an empty window.
    if (!shuttingDown && window_ && !window_.isDestroyed()) {
      dialog.showErrorBox(
        'BeatCanvas stopped',
        `The engine stopped unexpectedly (exit code ${code}).\n\n` +
        `What it said last is in:\n${path.join(LOG_DIR, 'server.log')}`);
    }
  });
  server.on('error', (error) => log(`could not start the server: ${error.message}`));
}

/** Wait until the server answers, so the window never shows a connection error. */
function waitForServer(port, timeoutMs = 90000) {
  const started = Date.now();
  return new Promise((resolve, reject) => {
    const attempt = () => {
      const request = http.get(
        { host: '127.0.0.1', port, path: '/api/health', timeout: 1500 },
        (response) => {
          response.resume();
          if (response.statusCode === 200) return resolve();
          retry();
        });
      request.on('error', retry);
      request.on('timeout', () => { request.destroy(); retry(); });
    };
    const retry = () => {
      if (Date.now() - started > timeoutMs) {
        return reject(new Error(`the engine did not answer on port ${port}`));
      }
      setTimeout(attempt, 400);
    };
    attempt();
  });
}

function stopServer() {
  if (!server) return;
  const child = server;
  server = null;
  log('stopping the server');
  // Kill the whole tree: uvicorn and any ffmpeg it has running are children of it, and
  // leaving those behind is what strands a port or a half-written video.
  if (process.platform === 'win32') {
    spawn('taskkill', ['/pid', String(child.pid), '/T', '/F'], { windowsHide: true });
  } else {
    child.kill('SIGTERM');
  }
}

function buildMenu() {
  const template = [
    {
      label: 'File',
      submenu: [
        {
          label: 'Open the videos folder',
          click: () => shell.openPath(path.join(ROOT, 'output')),
        },
        {
          label: 'Show the log',
          click: () => shell.openPath(path.join(LOG_DIR, 'server.log')),
        },
        { type: 'separator' },
        { role: 'quit' },
      ],
    },
    {
      label: 'View',
      submenu: [
        { role: 'reload' },
        { role: 'forceReload' },
        { role: 'toggleDevTools' },
        { type: 'separator' },
        { role: 'resetZoom' },
        { role: 'zoomIn' },
        { role: 'zoomOut' },
        { type: 'separator' },
        { role: 'togglefullscreen' },
      ],
    },
  ];
  Menu.setApplicationMenu(Menu.buildFromTemplate(template));
}

function createWindow(port) {
  window_ = new BrowserWindow({
    width: 1180,
    height: 900,
    minWidth: 900,
    minHeight: 640,
    backgroundColor: '#0b1020',
    icon: fs.existsSync(ICON) ? ICON : undefined,
    title: 'BeatCanvas',
    show: false,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      // The page is our own, but it is still loaded over HTTP, so it gets no direct access
      // to Node. Everything it needs comes through the narrow bridge in preload.js.
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
    },
  });

  window_.once('ready-to-show', () => window_.show());
  window_.on('closed', () => { window_ = null; });

  // A link to anywhere else belongs in the real browser, not in this window.
  window_.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url);
    return { action: 'deny' };
  });

  window_.loadURL(`http://127.0.0.1:${port}/`);
}

// ---------------------------------------------------------------- dialogs

ipcMain.handle('pick', async (_event, kind, start) => {
  const startPath = start && fs.existsSync(start) ? start : undefined;
  const parent = window_ || undefined;

  if (kind === 'music') {
    const result = await dialog.showOpenDialog(parent, {
      title: 'Choose the music',
      defaultPath: startPath,
      filters: [
        { name: 'Audio and video', extensions: ['mp3', 'wav', 'm4a', 'aac', 'flac', 'ogg', 'wma', 'mp4', 'mov', 'mkv'] },
        { name: 'All files', extensions: ['*'] },
      ],
      properties: ['openFile'],
    });
    return result.canceled ? { cancelled: true } : { path: result.filePaths[0] };
  }

  if (kind === 'photos') {
    const result = await dialog.showOpenDialog(parent, {
      title: 'Choose photos',
      defaultPath: startPath,
      filters: [
        { name: 'Pictures', extensions: ['jpg', 'jpeg', 'png', 'webp', 'bmp', 'tif', 'tiff', 'heic'] },
        { name: 'All files', extensions: ['*'] },
      ],
      properties: ['openFile', 'multiSelections'],
    });
    if (result.canceled || !result.filePaths.length) return { cancelled: true };
    return { paths: result.filePaths, folder: path.dirname(result.filePaths[0]) };
  }

  const result = await dialog.showOpenDialog(parent, {
    title: kind === 'output' ? 'Choose the folder to save videos in'
                             : 'Choose the folder with your photos',
    defaultPath: startPath,
    properties: kind === 'output'
      ? ['openDirectory', 'createDirectory']
      : ['openDirectory'],
  });
  return result.canceled ? { cancelled: true } : { path: result.filePaths[0] };
});

ipcMain.handle('open-folder', async (_event, target) => {
  const wanted = path.resolve(target || path.join(ROOT, 'output'));
  // Same restriction the server applies: this opens the app's own folders, not anywhere on
  // the machine a page happens to name.
  if (wanted !== ROOT && !wanted.startsWith(ROOT + path.sep)) {
    return { error: "only this app's own folders can be opened" };
  }
  const problem = await shell.openPath(wanted);
  return problem ? { error: problem } : { opened: wanted };
});

// ---------------------------------------------------------------- lifecycle

// One instance only: a second would start a second engine and the two would fight over
// the same job database.
if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on('second-instance', () => {
    if (window_) {
      if (window_.isMinimized()) window_.restore();
      window_.focus();
    }
  });

  app.whenReady().then(async () => {
    buildMenu();
    try {
      serverPort = await freePort();
      startServer(serverPort);
      await waitForServer(serverPort);
      log(`engine ready on ${serverPort}`);
      createWindow(serverPort);
    } catch (error) {
      log(`startup failed: ${error.message}`);
      dialog.showErrorBox(
        'BeatCanvas could not start',
        `${error.message}\n\nThe details are in:\n` +
        `${path.join(LOG_DIR, 'server.log')}\n${path.join(LOG_DIR, 'desktop.log')}`);
      app.quit();
    }
  });

  app.on('window-all-closed', () => {
    shuttingDown = true;
    stopServer();
    app.quit();
  });

  // Belt and braces: whichever way the app is going down, the engine goes with it.
  app.on('before-quit', () => { shuttingDown = true; stopServer(); });
  process.on('exit', stopServer);
}
