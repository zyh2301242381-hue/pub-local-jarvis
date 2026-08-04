"use strict";

const { contextBridge, ipcRenderer } = require("electron");

const subscribe = channel => callback => {
  const listener = (_event, payload) => callback(payload);
  ipcRenderer.on(channel, listener);
  return () => ipcRenderer.removeListener(channel, listener);
};

contextBridge.exposeInMainWorld("jarvis", {
  start: () => ipcRenderer.invoke("jarvis:start"),
  cancelStart: () => ipcRenderer.invoke("jarvis:cancel-start"),
  pause: () => ipcRenderer.invoke("jarvis:pause"),
  resume: () => ipcRenderer.invoke("jarvis:resume"),
  openOutput: outputPath => ipcRenderer.invoke("jarvis:open-output", outputPath),
  getState: () => ipcRenderer.invoke("jarvis:get-state"),
  getPerformanceMode: () => ipcRenderer.invoke("jarvis:performance-get"),
  savePerformanceMode: mode => ipcRenderer.invoke("jarvis:performance-save", mode),
  getGameProfiles: () => ipcRenderer.invoke("jarvis:get-game-profiles"),
  saveGameProfile: profile => ipcRenderer.invoke("jarvis:save-game-profile", profile),
  selectGameProfile: id => ipcRenderer.invoke("jarvis:select-game-profile", id),
  deleteGameProfile: id => ipcRenderer.invoke("jarvis:delete-game-profile", id),
  getMemoryStatus: () => ipcRenderer.invoke("jarvis:memory-status"),
  getMemoryDays: () => ipcRenderer.invoke("jarvis:memory-days"),
  getMemoryDay: day => ipcRenderer.invoke("jarvis:memory-day", day),
  generateMemoryDay: day => ipcRenderer.invoke("jarvis:memory-generate", day),
  getMemoryImages: day => ipcRenderer.invoke("jarvis:memory-images", day),
  generateMemoryImage: day => ipcRenderer.invoke("jarvis:memory-image-generate", day),
  getImageGenerationSettings: () => ipcRenderer.invoke("jarvis:image-settings-get"),
  saveImageGenerationSettings: value => ipcRenderer.invoke("jarvis:image-settings-save", value),
  toggleScreenPrivacy: () => ipcRenderer.invoke("jarvis:toggle-screen-privacy"),
  chat: message => ipcRenderer.invoke("jarvis:pet-chat", message),
  setPetChatVisible: visible => ipcRenderer.invoke("jarvis:set-pet-chat-visible", visible),
  startPetDrag: () => ipcRenderer.send("jarvis:pet-drag-start"),
  stopPetDrag: () => ipcRenderer.send("jarvis:pet-drag-stop"),
  onState: subscribe("jarvis:state"),
  onProgress: subscribe("jarvis:progress"),
  onMemoryUpdated: subscribe("jarvis:memory-updated"),
  onPetScene: subscribe("jarvis:pet-scene"),
  onScreenPrivacy: subscribe("jarvis:screen-privacy"),
  onBubble: subscribe("jarvis:bubble"),
  onBarrage: subscribe("jarvis:barrage"),
  onPetChatVisibility: subscribe("jarvis:pet-chat-visibility"),
});
