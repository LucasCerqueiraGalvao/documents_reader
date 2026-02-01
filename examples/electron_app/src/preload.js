const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('docReader', {
  selectFiles: () => ipcRenderer.invoke('dialog:selectFiles'),
  runPipeline: (payload) => ipcRenderer.invoke('pipeline:run', payload),
  openReport: (reportPath) => ipcRenderer.invoke('report:open', reportPath),
  getProjectRoot: () => ipcRenderer.invoke('projectRoot:get'),
  selectProjectRoot: () => ipcRenderer.invoke('projectRoot:select'),
  onPipelineLog: (handler) => {
    const listener = (_event, msg) => handler(msg);
    ipcRenderer.on('pipeline:log', listener);
    return () => ipcRenderer.removeListener('pipeline:log', listener);
  },
});
