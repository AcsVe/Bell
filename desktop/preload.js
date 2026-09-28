'use strict';
const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('electronBell', {
  getCode: () => ipcRenderer.invoke('bell:getCode'),
  saveLogin: (code, server) => ipcRenderer.invoke('bell:saveLogin', code, server),
  logout: () => ipcRenderer.invoke('bell:logout'),
  setSchedule: (schedule, lang) => ipcRenderer.invoke('bell:setSchedule', schedule, lang),
  test: () => ipcRenderer.invoke('bell:test'),
  alertPayload: () => ipcRenderer.invoke('bell:alertPayload'),
  closeAlert: () => ipcRenderer.invoke('bell:closeAlert'),
});
