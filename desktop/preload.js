/**
 * The bridge between the page and the desktop shell.
 *
 * Deliberately narrow. The page is served over HTTP and gets no access to Node; it can ask
 * for a file dialog and it can ask for one of the app's own folders to be opened, and that
 * is all. Anything wider would mean a bug in the page could reach the whole machine.
 *
 * The page checks for this object and falls back to the server's own dialogs when it is
 * absent, so the same page works whether it is opened in this window or in a browser.
 */
'use strict';

const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('beatcanvas', {
  /** True when running inside the desktop shell rather than a browser tab. */
  desktop: true,

  /**
   * Show a native dialog.
   * @param {'folder'|'output'|'music'|'photos'} kind
   * @param {string} start where to open the dialog
   */
  pick: (kind, start) => ipcRenderer.invoke('pick', kind, start || ''),

  /** Open one of the app's own folders in Explorer. */
  openFolder: (target) => ipcRenderer.invoke('open-folder', target || ''),
});
