"use strict";

const $ = selector => document.querySelector(selector);
const startButton = $("#start-button");
const pauseButton = $("#pause-button");
const phaseChip = $("#phase-chip");
const phaseChipLabel = phaseChip.querySelector("span");
const statusTitle = $("#status-title");
const statusDetail = $("#status-detail");
const startupProgress = $("#startup-progress");
const startupProgressLabel = $("#startup-progress-label");
const startupProgressValue = $("#startup-progress-value");
const startupProgressTrack = $("#startup-progress-track");
const startupProgressBar = $("#startup-progress-bar");
const monitorValue = $("#monitor-value");
const performanceMode = $("#performance-mode");
const performanceModeDetail = $("#performance-mode-detail");
const sceneValue = $("#scene-value");
const activityLog = $("#activity-log");
const gameProfileSummary = $("#game-profile-summary");
const profileDialog = $("#game-profile-dialog");
const profileForm = $("#game-profile-form");
const profileSelect = $("#profile-select");
const profileName = $("#profile-name");
const profilePrompt = $("#profile-prompt");
const profileDelete = $("#profile-delete");
const profileError = $("#profile-error");
const profilePromptCount = $("#profile-prompt-count");
const memoryDocument = $("#memory-document");
const memoryDays = $("#memory-days");
const memoryState = $("#memory-state");
const memoryDot = $("#memory-dot");
const memoryImageView = $("#memory-image-view");
const memoryImagePreview = $("#memory-image-preview");
const memoryImageEmpty = $("#memory-image-empty");
const memoryImageHistory = $("#memory-image-history");
const memoryImageStamp = $("#memory-image-stamp");
const imageSettingsDialog = $("#image-settings-dialog");
const imageSettingsForm = $("#image-settings-form");
const imageBaseUrl = $("#image-base-url");
const imageApiKey = $("#image-api-key");
const imageModelName = $("#image-model-name");
const imageSettingsState = $("#image-settings-state");
const imageSettingsError = $("#image-settings-error");
let currentPhase = "idle";
let currentView = "overview";
let currentMemoryDay = "";
let today = "";
let gameProfiles = [];
let editingProfileId = "";
let persistedProfileIds = new Set();
let memoryMode = "text";
let currentMemoryImages = [];
let selectedMemoryImageId = "";
let lastLoggedDownloadPercent = -5;

const performanceModeDetails = {
  performance: "性能：每 2 秒感知一次",
  balanced: "平衡：每 10 秒感知一次",
  quiet: "安静：每 30 秒感知一次",
};

const sceneNames = { game: "游戏", course: "网课", other: "其他" };
const phaseView = {
  idle: ["待启动", "系统处于待命状态", "启动后将连接本地模型，持续理解屏幕与系统声音。"],
  starting: ["启动中", "正在启动本地 AI", "正在检查本地模型与自包含运行时。"],
  running: ["运行中", "持续感知已开启", "AI 贾维斯正在本机理解当前环境，并只在必要时介入。"],
  paused: ["已暂停", "环境感知已暂停", "屏幕和系统音频当前不会被采集。"],
  error: ["异常", "启动未完成", "请查看运行日志后重试。"],
};

function refreshIcons() {
  if (window.lucide) window.lucide.createIcons();
}

function now() {
  return new Intl.DateTimeFormat("zh-CN", { hour: "2-digit", minute: "2-digit" }).format(new Date());
}

function formatDay(value) {
  if (!value) return "--";
  const date = new Date(`${value}T00:00:00`);
  return new Intl.DateTimeFormat("zh-CN", { month: "long", day: "numeric", weekday: "short" }).format(date);
}

function addLog(message) {
  for (const line of String(message).split(/\r?\n/).filter(Boolean).slice(-3)) {
    const item = document.createElement("p");
    const time = document.createElement("time");
    const text = document.createElement("span");
    time.textContent = now();
    text.textContent = line.length > 150 ? `${line.slice(0, 150)}...` : line;
    item.append(time, text);
    activityLog.prepend(item);
  }
  while (activityLog.children.length > 6) activityLog.lastElementChild.remove();
}

function setStartupProgress(message, percent = null) {
  startupProgress.hidden = false;
  startupProgressLabel.textContent = message;
  if (Number.isFinite(percent)) {
    const value = Math.max(0, Math.min(100, Math.round(percent)));
    startupProgressTrack.classList.remove("indeterminate");
    startupProgressTrack.setAttribute("aria-valuenow", String(value));
    startupProgressBar.style.width = `${value}%`;
    startupProgressValue.textContent = `${value}%`;
    return;
  }
  startupProgressTrack.classList.add("indeterminate");
  startupProgressTrack.removeAttribute("aria-valuenow");
  startupProgressBar.style.width = "";
  startupProgressValue.textContent = "进行中";
}

function readableError(error) {
  return String(error?.message || error || "操作失败")
    .replace(/^Error invoking remote method '[^']+':\s*/i, "")
    .replace(/^Error:\s*/i, "");
}

function handleProgress(payload) {
  if (payload && typeof payload === "object" && payload.type === "download-progress") {
    setStartupProgress(payload.message || "正在下载模型", payload.percent);
    if (payload.percent >= lastLoggedDownloadPercent + 5 || payload.percent === 100) {
      lastLoggedDownloadPercent = payload.percent;
      addLog(`${payload.message || "正在下载模型"}（${payload.percent}%）`);
    }
    return;
  }
  const message = String(payload || "").trim();
  if (!message) return;
  addLog(message);
  if (currentPhase !== "starting" && currentPhase !== "running") return;
  if (/后端已就绪|本地服务已启动/.test(message)) {
    setStartupProgress("正在启用环境感知");
  } else if (/环境感知已就绪/.test(message)) {
    setStartupProgress("环境感知已就绪", 100);
  } else if (/正在初始化环境感知/.test(message)) {
    setStartupProgress(message);
  } else if (/模型准备完成/.test(message)) {
    setStartupProgress("正在加载本地模型");
  } else if (/正在校验|正在检查|正在连接|正在启动/.test(message)) {
    setStartupProgress(message);
  }
}

function render(state) {
  const phase = phaseView[state.phase] ? state.phase : "idle";
  const wasStarting = currentPhase === "starting";
  currentPhase = phase;
  const [chip, title, detail] = phaseView[phase];
  document.body.className = `phase-${phase}${state.environmentStatus === "initializing"
    ? " environment-initializing"
    : ""}`;
  phaseChipLabel.textContent = chip;
  phaseChip.className = `phase-chip${phase === "running" ? " online" : phase === "error" ? " error" : ""}`;
  const initializingEnvironment = phase === "running" && state.environmentStatus === "initializing";
  statusTitle.textContent = initializingEnvironment ? "基础监控已启动" : title;
  statusDetail.textContent = state.error || (initializingEnvironment
    ? "正在初始化环境感知模型，完成后将自动开始持续理解。"
    : detail);
  monitorValue.textContent = state.monitoring ? "感知中" : phase === "paused" ? "已暂停" : "未运行";
  performanceMode.value = state.performanceMode || performanceMode.value || "quiet";
  performanceMode.disabled = phase !== "idle" && phase !== "error";
  performanceModeDetail.textContent = performanceModeDetails[performanceMode.value];
  sceneValue.textContent = state.scene === "game" ? `游戏 · ${state.gameProfile}` : sceneNames[state.scene] || "其他";
  gameProfileSummary.textContent = `游戏方案：${state.gameProfile || "我的世界"}`;
  if (phase === "starting" || initializingEnvironment) {
    if (!wasStarting) {
      lastLoggedDownloadPercent = -5;
      setStartupProgress("正在检查本地模型");
    }
  } else {
    startupProgress.hidden = true;
  }
  startButton.hidden = phase === "running" || phase === "paused";
  startButton.disabled = false;
  const startIcon = document.createElement("i");
  const startLabel = document.createElement("span");
  startIcon.setAttribute("data-lucide", phase === "starting" ? "square" : "power");
  startLabel.textContent = phase === "starting" ? "取消启动" : "启动 AI 贾维斯";
  startButton.replaceChildren(startIcon, startLabel);
  startButton.classList.toggle("cancel-command", phase === "starting");
  pauseButton.hidden = phase !== "running" && phase !== "paused";
  const pauseIcon = document.createElement("i");
  const pauseLabel = document.createElement("span");
  pauseIcon.setAttribute("data-lucide", phase === "paused" ? "play" : "pause");
  pauseLabel.textContent = phase === "paused" ? "继续感知" : "暂停感知";
  pauseButton.replaceChildren(pauseIcon, pauseLabel);
  refreshIcons();
}

function switchView(name) {
  currentView = name;
  document.querySelectorAll(".view-tab").forEach(tab => {
    const active = tab.dataset.view === name;
    tab.classList.toggle("active", active);
    tab.setAttribute("aria-selected", String(active));
  });
  document.querySelectorAll(".app-view").forEach(view => {
    const active = view.id === `${name}-view`;
    view.classList.toggle("active", active);
    view.setAttribute("aria-hidden", String(!active));
  });
  if (name === "memory") {
    memoryDot.hidden = true;
    refreshMemory();
  }
}

function setMemoryEmpty(message) {
  const empty = document.createElement("div");
  empty.className = "empty-memory";
  const icon = document.createElement("i");
  icon.setAttribute("data-lucide", "notebook");
  const text = document.createElement("p");
  text.textContent = message;
  empty.append(icon, text);
  memoryDocument.replaceChildren(empty);
  refreshIcons();
}

function memoryImageUrl(item) {
  const value = String(item?.content_url || "");
  if (/^(?:data:|https?:\/\/)/i.test(value)) return value;
  return value ? `http://127.0.0.1:8000${value}` : "";
}

function selectMemoryImage(item) {
  selectedMemoryImageId = item?.id || "";
  const url = memoryImageUrl(item);
  memoryImagePreview.hidden = !url;
  memoryImageEmpty.hidden = Boolean(url);
  memoryImageStamp.textContent = item
    ? `${item.date} · ${new Date(item.created_at).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" })}`
    : "";
  if (url) memoryImagePreview.src = url;
  memoryImageHistory.querySelectorAll(".memory-image-thumb").forEach(button => {
    button.classList.toggle("active", button.dataset.imageId === selectedMemoryImageId);
  });
}

function renderMemoryImages(images) {
  currentMemoryImages = Array.isArray(images) ? images : [];
  memoryImageHistory.replaceChildren();
  for (const item of currentMemoryImages) {
    const button = document.createElement("button");
    const image = document.createElement("img");
    button.type = "button";
    button.className = "memory-image-thumb";
    button.dataset.imageId = item.id;
    button.title = new Date(item.created_at).toLocaleString("zh-CN");
    image.src = memoryImageUrl(item);
    image.alt = `${item.date} 日程图`;
    button.append(image);
    button.addEventListener("click", () => selectMemoryImage(item));
    memoryImageHistory.append(button);
  }
  const selected = currentMemoryImages.find(item => item.id === selectedMemoryImageId)
    || currentMemoryImages[0]
    || null;
  selectMemoryImage(selected);
}

function setMemoryMode(mode) {
  memoryMode = mode === "image" ? "image" : "text";
  document.querySelectorAll("[data-memory-mode]").forEach(button => {
    const active = button.dataset.memoryMode === memoryMode;
    button.classList.toggle("active", active);
    button.setAttribute("aria-selected", String(active));
  });
  memoryDocument.hidden = memoryMode !== "text";
  memoryImageView.hidden = memoryMode !== "image";
  refreshIcons();
}

function renderMarkdown(content) {
  const fragment = document.createDocumentFragment();
  for (const rawLine of String(content).split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line) continue;
    let element;
    if (line.startsWith("### ")) {
      element = document.createElement("h3");
      element.textContent = line.slice(4);
    } else if (line.startsWith("## ")) {
      element = document.createElement("h2");
      element.textContent = line.slice(3);
    } else if (line.startsWith("# ")) {
      element = document.createElement("h1");
      element.textContent = line.slice(2);
    } else if (line.startsWith("> ")) {
      element = document.createElement("blockquote");
      element.textContent = line.slice(2);
    } else {
      element = document.createElement("p");
      element.textContent = line;
    }
    fragment.append(element);
  }
  memoryDocument.replaceChildren(fragment);
}

async function loadMemoryDay(day) {
  currentMemoryDay = day;
  memoryDays.querySelectorAll(".memory-day").forEach(button => button.classList.toggle("active", button.dataset.day === day));
  memoryState.textContent = "正在读取";
  const [memoryResult, imagesResult] = await Promise.allSettled([
    window.jarvis.getMemoryDay(day),
    window.jarvis.getMemoryImages(day),
  ]);
  renderMemoryImages(imagesResult.status === "fulfilled" ? imagesResult.value : []);
  if (memoryResult.status === "fulfilled") {
    renderMarkdown(memoryResult.value.content);
    memoryState.textContent = `${formatDay(day)} · ${memoryResult.value.event_count} 条活动`;
  } else {
    setMemoryEmpty("这一天还没有生成记忆");
    memoryState.textContent = currentMemoryImages.length ? "已有日程图" : "暂无日程记录";
  }
}

function renderMemoryDays(days) {
  memoryDays.replaceChildren();
  for (const item of days) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "memory-day";
    button.dataset.day = item.date;
    const label = document.createElement("strong");
    const count = document.createElement("span");
    label.textContent = item.date === today ? "今天" : formatDay(item.date);
    count.textContent = `${item.event_count}`;
    button.append(label, count);
    button.addEventListener("click", () => loadMemoryDay(item.date));
    memoryDays.append(button);
  }
  if (!days.length) {
    const text = document.createElement("span");
    text.className = "memory-state";
    text.textContent = "暂无历史记录";
    memoryDays.append(text);
  }
}

async function refreshMemory() {
  memoryState.textContent = "正在同步";
  try {
    const [status, days] = await Promise.all([window.jarvis.getMemoryStatus(), window.jarvis.getMemoryDays()]);
    today = status.today;
    $("#memory-today-label").textContent = formatDay(today);
    $("#memory-today-count").textContent = String(status.today_event_count);
    renderMemoryDays(days);
    const selected = days.find(item => item.date === currentMemoryDay)
      || days.find(item => item.date === today)
      || days[0];
    if (selected) await loadMemoryDay(selected.date);
    else {
      currentMemoryDay = "";
      renderMemoryImages([]);
      setMemoryEmpty(status.today_event_count ? "点击生成今日记忆" : "今天还没有可记录的活动");
      memoryState.textContent = status.today_event_count ? "已有活动等待生成" : "今日暂无记录";
    }
  } catch (error) {
    currentMemoryDay = "";
    renderMemoryImages([]);
    setMemoryEmpty("启动 AI 贾维斯后可查看记忆");
    memoryState.textContent = "后端未连接";
  }
}

async function generateMemory(day = today) {
  if (!day) return;
  const button = $("#memory-generate");
  button.disabled = true;
  memoryState.textContent = "正在调用本地模型归纳全天活动";
  try {
    const result = await window.jarvis.generateMemoryDay(day);
    currentMemoryDay = day;
    renderMarkdown(result.content);
    memoryState.textContent = `${formatDay(day)} · 已更新`;
    await refreshMemory();
  } catch (error) {
    memoryState.textContent = error.message || "生成失败";
  } finally {
    button.disabled = false;
  }
}

async function openImageSettings() {
  const settings = await window.jarvis.getImageGenerationSettings();
  imageBaseUrl.value = settings.baseUrl || "";
  imageModelName.value = settings.modelName || "";
  imageApiKey.value = "";
  imageApiKey.placeholder = settings.hasApiKey ? "已安全保存，留空沿用" : "sk-...";
  imageApiKey.required = !settings.hasApiKey;
  imageSettingsState.textContent = settings.hasApiKey ? "配置已保存" : "等待配置";
  imageSettingsError.textContent = "";
  imageSettingsDialog.showModal();
  imageBaseUrl.focus();
}

async function generateMemoryImage(day = currentMemoryDay || today) {
  if (!day) return;
  const settings = await window.jarvis.getImageGenerationSettings();
  if (!settings.hasApiKey || !settings.baseUrl || !settings.modelName) {
    await openImageSettings();
    return;
  }
  const button = $("#memory-image-generate");
  button.disabled = true;
  memoryState.textContent = "正在整理回顾并生成日程图";
  try {
    const result = await window.jarvis.generateMemoryImage(day);
    selectedMemoryImageId = result.id;
    setMemoryMode("image");
    await refreshMemory();
    memoryState.textContent = `${formatDay(day)} · 日程图已保存`;
  } catch (error) {
    memoryState.textContent = error.message || "日程图生成失败";
  } finally {
    button.disabled = false;
  }
}

startButton.addEventListener("click", async () => {
  if (currentPhase === "starting") {
    addLog("正在取消启动");
    render(await window.jarvis.cancelStart());
    return;
  }
  addLog("已提交启动请求");
  try { render(await window.jarvis.start()); } catch (error) { addLog(readableError(error)); }
});

pauseButton.addEventListener("click", async () => {
  try {
    const state = await window.jarvis.getState();
    render(state.monitoring ? await window.jarvis.pause() : await window.jarvis.resume());
  } catch (error) { addLog(error.message); }
});

performanceMode.addEventListener("change", async () => {
  try {
    const settings = await window.jarvis.savePerformanceMode(performanceMode.value);
    performanceMode.value = settings.mode;
    performanceModeDetail.textContent = performanceModeDetails[settings.mode];
    addLog(`性能模式已切换为：${settings.mode}`);
  } catch (error) {
    addLog(readableError(error));
    const settings = await window.jarvis.getPerformanceMode();
    performanceMode.value = settings.mode;
    performanceModeDetail.textContent = performanceModeDetails[settings.mode];
  }
});

document.querySelectorAll(".view-tab").forEach(tab => tab.addEventListener("click", () => switchView(tab.dataset.view)));
$("#memory-refresh").addEventListener("click", refreshMemory);
$("#memory-generate").addEventListener("click", () => generateMemory(today));
$("#memory-image-generate").addEventListener("click", () => generateMemoryImage());
$("#memory-image-settings").addEventListener("click", openImageSettings);
document.querySelectorAll("[data-memory-mode]").forEach(button => {
  button.addEventListener("click", () => setMemoryMode(button.dataset.memoryMode));
});

$("#image-settings-close").addEventListener("click", () => imageSettingsDialog.close());
imageSettingsDialog.addEventListener("cancel", event => {
  event.preventDefault();
  imageSettingsDialog.close();
});
imageSettingsDialog.addEventListener("click", event => {
  if (event.target === imageSettingsDialog) imageSettingsDialog.close();
});
imageSettingsForm.addEventListener("submit", async event => {
  event.preventDefault();
  imageSettingsError.textContent = "正在保存";
  imageSettingsError.classList.add("neutral");
  try {
    const settings = await window.jarvis.saveImageGenerationSettings({
      baseUrl: imageBaseUrl.value,
      apiKey: imageApiKey.value,
      modelName: imageModelName.value,
    });
    imageSettingsState.textContent = settings.hasApiKey ? "配置已保存" : "等待配置";
    imageSettingsDialog.close();
  } catch (error) {
    imageSettingsError.classList.remove("neutral");
    imageSettingsError.textContent = error.message || "保存失败";
  }
});

function updateProfilePromptCount() {
  profilePromptCount.textContent = `${profilePrompt.value.length} / 8000`;
}

function setProfileDirty(dirty) {
  profileForm.classList.toggle("is-dirty", dirty);
  profileError.classList.toggle("neutral", dirty);
  profileError.textContent = dirty ? "有未保存的更改" : "";
}

function renderProfileDraft(id) {
  const profile = gameProfiles.find(item => item.id === id) || gameProfiles[0];
  if (!profile) return;
  editingProfileId = profile.id;
  profileSelect.value = profile.id;
  profileName.value = profile.name;
  profilePrompt.value = profile.prompt;
  profileDelete.disabled = gameProfiles.length <= 1;
  profileDelete.title = profileDelete.disabled ? "至少保留一个方案" : "删除方案";
  profileDelete.setAttribute("aria-label", profileDelete.title);
  updateProfilePromptCount();
  setProfileDirty(false);
  refreshIcons();
}

function renderProfileEditor(settings, syncPersisted = true) {
  gameProfiles = settings.profiles;
  if (syncPersisted) persistedProfileIds = new Set(gameProfiles.map(profile => profile.id));
  profileSelect.replaceChildren(...gameProfiles.map(profile => {
    const option = document.createElement("option");
    option.value = profile.id;
    option.textContent = profile.name;
    option.selected = profile.id === settings.selectedId;
    return option;
  }));
  renderProfileDraft(settings.selectedId);
}

function closeProfileDialog() {
  if (profileForm.classList.contains("is-dirty") && !window.confirm("放弃尚未保存的更改？")) return;
  profileDialog.close();
}

$("#game-profile-button").addEventListener("click", async () => {
  renderProfileEditor(await window.jarvis.getGameProfiles());
  profileDialog.showModal();
});
$("#profile-close").addEventListener("click", closeProfileDialog);
profileSelect.addEventListener("change", () => {
  const nextId = profileSelect.value;
  if (profileForm.classList.contains("is-dirty") && !window.confirm("放弃当前方案的未保存修改？")) {
    profileSelect.value = editingProfileId;
    return;
  }
  renderProfileDraft(nextId);
});
$("#profile-add").addEventListener("click", () => {
  const id = `custom-${Date.now()}`;
  gameProfiles.push({ id, name: "新游戏", prompt: "领域关注：本游戏的目标、资源、风险和刚发生的结果。表达风格：像熟悉游戏的朋友。", builtIn: false });
  renderProfileEditor({ selectedId: id, profiles: gameProfiles }, false);
  setProfileDirty(true);
  profileName.select();
});
profileDelete.addEventListener("click", async () => {
  const profile = gameProfiles.find(item => item.id === profileSelect.value);
  if (!profile || gameProfiles.length <= 1 || !window.confirm(`删除“${profile.name}”方案？`)) return;
  if (!persistedProfileIds.has(profile.id)) {
    gameProfiles = gameProfiles.filter(item => item.id !== profile.id);
    renderProfileEditor({ selectedId: gameProfiles[0].id, profiles: gameProfiles }, false);
    return;
  }
  renderProfileEditor(await window.jarvis.deleteGameProfile(profile.id));
});
profileName.addEventListener("input", () => setProfileDirty(true));
profilePrompt.addEventListener("input", () => {
  updateProfilePromptCount();
  setProfileDirty(true);
});
profileForm.addEventListener("submit", async event => {
  event.preventDefault();
  try {
    renderProfileEditor(await window.jarvis.saveGameProfile({ id: profileSelect.value, name: profileName.value, prompt: profilePrompt.value }));
    addLog(`已选用《${profileName.value.trim()}》游戏方案`);
    profileDialog.close();
  } catch (error) {
    profileError.classList.remove("neutral");
    profileError.textContent = error.message;
  }
});
profileDialog.addEventListener("cancel", event => {
  event.preventDefault();
  closeProfileDialog();
});
profileDialog.addEventListener("click", event => {
  if (event.target === profileDialog) closeProfileDialog();
});

window.jarvis.onState(render);
window.jarvis.onProgress(handleProgress);
window.jarvis.onMemoryUpdated(() => {
  if (currentView === "memory") refreshMemory();
  else memoryDot.hidden = false;
});

window.addEventListener("DOMContentLoaded", async () => {
  refreshIcons();
  render(await window.jarvis.getState());
});
