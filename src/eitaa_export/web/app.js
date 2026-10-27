"use strict";

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

const MEDIA_TYPES = [
  { id: "photo", label: "Photos", icon: "i-photo" },
  { id: "video", label: "Videos", icon: "i-video" },
  { id: "voice", label: "Voice", icon: "i-mic" },
  { id: "audio", label: "Audio", icon: "i-music" },
  { id: "document", label: "Files", icon: "i-file" },
  { id: "sticker", label: "Stickers", icon: "i-sticker" },
  { id: "gif", label: "GIFs", icon: "i-gif" },
];

const state = {
  authenticated: false,
  me: null,
  chats: [],
  selected: null,
  job: null,
  pollTimer: null,
  auth: { phone: null, hash: null, delivery: null },
  sample: null,
  speed: 0,
  searchTimer: null,
  toastTimer: null,
  resendTimer: null,
  exportRoot: "",
};

/* ------------------------------------------------------------------ utils */

function icon(name) {
  return `<svg class="icon" aria-hidden="true"><use href="#${name}"></use></svg>`;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function initials(name) {
  const parts = String(name || "").trim().split(/\s+/).filter(Boolean);
  if (!parts.length) return "?";
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
}

function formatBytes(bytes) {
  let value = Number(bytes) || 0;
  if (value < 1024) return `${Math.round(value)} B`;
  const units = ["KB", "MB", "GB", "TB"];
  for (const unit of units) {
    value /= 1024;
    if (value < 1024 || unit === "TB") {
      return `${value.toFixed(value >= 100 ? 0 : 1)} ${unit}`;
    }
  }
  return "0 B";
}

function formatDuration(seconds) {
  seconds = Math.max(0, Math.round(seconds || 0));
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const secs = seconds % 60;
  if (hours) return `${hours}:${String(minutes).padStart(2, "0")}:${String(secs).padStart(2, "0")}`;
  return `${minutes}:${String(secs).padStart(2, "0")}`;
}

function formatListTime(timestamp) {
  if (!timestamp) return "";
  const date = new Date(timestamp * 1000);
  const now = new Date();
  if (date.toDateString() === now.toDateString()) {
    return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  }
  const sameYear = date.getFullYear() === now.getFullYear();
  return date.toLocaleDateString([], { day: "2-digit", month: "short", year: sameYear ? undefined : "2-digit" });
}

function formatClock(timestamp) {
  const date = new Date(timestamp * 1000);
  return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function kindIcon(kind) {
  if (kind === "channel") return "i-channel";
  if (kind === "group" || kind === "supergroup") return "i-group";
  return "i-user";
}

function kindLabel(kind) {
  if (kind === "supergroup") return "Supergroup";
  if (kind === "group") return "Group";
  if (kind === "channel") return "Channel";
  if (kind === "private") return "Private chat";
  return "Chat";
}

function paintAvatar(element, name, color = 1, kind = null, sizeClass = "") {
  if (!element) return;
  const classes = ["avatar"];
  if (sizeClass) classes.push(sizeClass);
  classes.push(`c${Math.min(7, Math.max(1, Number(color) || 1))}`);
  element.className = classes.join(" ");
  element.innerHTML =
    `<span class="avatar-text">${escapeHtml(initials(name))}</span>` +
    (kind ? `<span class="kind-mark">${icon(kindIcon(kind))}</span>` : "");
}

function toast(message, type = "") {
  const element = $("#toast");
  element.textContent = message;
  element.className = `toast show ${type}`.trim();
  clearTimeout(state.toastTimer);
  state.toastTimer = setTimeout(() => {
    element.className = "toast";
  }, 3800);
}

function errorMessage(error) {
  const message = error?.message || "Something went wrong.";
  const wait = error?.data?.retry_after_seconds;
  return wait ? `${message} Try again in ${wait}s.` : message;
}

function setBusy(button, busy, text) {
  if (!button) return;
  if (busy) {
    button.dataset.label = button.innerHTML;
    button.disabled = true;
    button.innerHTML = `<span class="spinner"></span>${escapeHtml(text || "Working…")}`;
  } else {
    button.disabled = false;
    if (button.dataset.label) button.innerHTML = button.dataset.label;
  }
}

/* -------------------------------------------------------------------- api */

async function api(path, options = {}) {
  const init = { headers: { Accept: "application/json" }, ...options };
  if (init.body && typeof init.body !== "string") {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(init.body);
  }
  let response;
  try {
    response = await fetch(path, init);
  } catch (error) {
    throw new Error("Cannot reach the local Eitaa Export server. Is `eitaa-export gui` running?");
  }
  let data = {};
  try {
    data = await response.json();
  } catch (error) {
    data = {};
  }
  if (!response.ok) {
    if (response.status === 401 && !path.startsWith("/api/auth/")) {
      signedOut();
    }
    const failure = new Error(data.error || `Request failed (${response.status})`);
    failure.status = response.status;
    failure.data = data;
    throw failure;
  }
  return data;
}

/* ------------------------------------------------------------------ theme */

function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  try {
    localStorage.setItem("eitaa-theme", theme);
  } catch (error) {
    /* ignore */
  }
  const button = $("#themeBtn");
  if (button) button.innerHTML = icon(theme === "dark" ? "i-sun" : "i-moon");
}

function toggleTheme() {
  applyTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark");
}

/* ------------------------------------------------------------------- auth */

function showAuth() {
  $("#authOverlay").hidden = false;
  $("#phoneForm").hidden = false;
  $("#codeForm").hidden = true;
  $("#authError").hidden = true;
  $("#authSubtitle").textContent = "Use your phone number. Eitaa will send a verification code.";
  setTimeout(() => $("#phoneInput").focus(), 80);
}

function hideAuth() {
  $("#authOverlay").hidden = true;
}

function showAuthError(message) {
  const element = $("#authError");
  element.textContent = message;
  element.hidden = !message;
}

function signedOut() {
  state.authenticated = false;
  state.me = null;
  state.chats = [];
  state.selected = null;
  state.job = null;
  stopPolling();
  $("#profileOverlay").hidden = true;
  $("#chatList").innerHTML = "";
  $("#app").classList.remove("has-selection");
  $("#welcomeView").hidden = false;
  $("#exportView").hidden = true;
  $("#jobView").hidden = true;
  renderMe();
  showAuth();
}

async function requestCode(event) {
  event.preventDefault();
  const phone = $("#phoneInput").value.trim();
  if (!phone) {
    showAuthError("Enter your phone number.");
    return;
  }
  const button = $("#phoneSubmit");
  showAuthError("");
  setBusy(button, true, "Sending code…");
  try {
    const challenge = await api("/api/auth/request", { method: "POST", body: { phone } });
    state.auth.phone = challenge.phone_number;
    state.auth.hash = challenge.phone_code_hash;
    state.auth.delivery = challenge.delivery;
    $("#phoneForm").hidden = true;
    $("#codeForm").hidden = false;
    $("#codeTarget").textContent =
      `Code sent to ${challenge.phone_number}` +
      (challenge.delivery ? ` via ${challenge.delivery}` : "") +
      (challenge.timeout_seconds ? ` · valid for ${challenge.timeout_seconds}s` : "");
    $("#authSubtitle").textContent = "Enter the verification code to finish signing in.";
    $("#codeInput").value = "";
    setTimeout(() => $("#codeInput").focus(), 80);
    startResendCooldown();
  } catch (error) {
    showAuthError(errorMessage(error));
  } finally {
    setBusy(button, false);
  }
}

async function verifyCode(event) {
  event.preventDefault();
  const code = $("#codeInput").value.trim();
  if (!code) {
    showAuthError("Enter the verification code.");
    return;
  }
  const button = $("#codeSubmit");
  showAuthError("");
  setBusy(button, true, "Signing in…");
  try {
    const status = await api("/api/auth/verify", {
      method: "POST",
      body: {
        phone: state.auth.phone,
        phone_code_hash: state.auth.hash,
        code,
      },
    });
    state.authenticated = Boolean(status.authenticated);
    state.me = status.me;
    state.exportRoot = status.export_root || "";
    hideAuth();
    renderMe();
    await loadChats();
    const params = new URLSearchParams(location.search);
    const deepTarget = params.get("target");
    const deepChat = params.get("chat");
    const match = deepChat ? state.chats.find((chat) => chat.reference === deepChat) : null;
    if (match) {
      selectChat(match);
    } else if (deepTarget) {
      const resolved = await resolveTarget(deepTarget);
      if (!resolved) showWelcome();
    } else {
      showWelcome();
    }
    toast(`Welcome, ${state.me?.name || "signed in"}.`, "success");
  } catch (error) {
    showAuthError(errorMessage(error));
  } finally {
    setBusy(button, false);
  }
}

async function resendCode() {
  if (!state.auth.phone || !state.auth.hash) return;
  const button = $("#resendBtn");
  button.disabled = true;
  let started = false;
  try {
    const challenge = await api("/api/auth/resend", {
      method: "POST",
      body: { phone: state.auth.phone, phone_code_hash: state.auth.hash },
    });
    state.auth.hash = challenge.phone_code_hash;
    state.auth.delivery = challenge.delivery;
    started = true;
    toast("A new code was requested.", "success");
  } catch (error) {
    showAuthError(errorMessage(error));
    button.disabled = false;
  }
  if (started) startResendCooldown();
}

function startResendCooldown(seconds = 30) {
  clearInterval(state.resendTimer);
  const button = $("#resendBtn");
  let remaining = seconds;
  button.disabled = true;
  button.textContent = `Resend in ${remaining}s`;
  state.resendTimer = setInterval(() => {
    remaining -= 1;
    if (remaining <= 0) {
      clearInterval(state.resendTimer);
      button.disabled = false;
      button.textContent = "Resend code";
    } else {
      button.textContent = `Resend in ${remaining}s`;
    }
  }, 1000);
}

function changePhone() {
  clearInterval(state.resendTimer);
  state.auth = { phone: null, hash: null, delivery: null };
  $("#phoneForm").hidden = false;
  $("#codeForm").hidden = true;
  $("#authSubtitle").textContent = "Use your phone number. Eitaa will send a verification code.";
  showAuthError("");
  $("#phoneInput").focus();
}

async function logout() {
  if (state.job && state.job.status === "running") {
    toast("Cancel the active export before signing out.", "error");
    return;
  }
  if (!confirm("Sign out of this Eitaa session?")) return;
  try {
    await api("/api/auth/logout", { method: "POST", body: {} });
    signedOut();
    toast("Signed out.");
  } catch (error) {
    toast(error.message, "error");
  }
}

/* ------------------------------------------------------------- profile UI */

function renderMe() {
  const me = state.me;
  const profileButton = $("#profileBtn");
  const meRow = $("#meRow");
  if (!me) {
    profileButton.innerHTML = "";
    meRow.innerHTML = "";
    return;
  }
  paintAvatar(profileButton, me.name, me.color, null, "avatar-sm");
  meRow.innerHTML = `
    <span class="avatar avatar-sm c${me.color || 1}"><span class="avatar-text">${escapeHtml(me.initials || initials(me.name))}</span></span>
    <span class="me-copy">
      <span class="me-name">${escapeHtml(me.name)}</span>
      <span class="me-handle">${me.username ? "@" + escapeHtml(me.username) : escapeHtml(me.phone || "Eitaa account")}</span>
    </span>
  `;
}

function openProfile() {
  if (!state.me) return;
  const me = state.me;
  paintAvatar($("#profileAvatar"), me.name, me.color, null, "avatar-xl");
  $("#profileName").textContent = me.name;
  $("#profileUsername").textContent = me.username ? `@${me.username}` : "No public username";
  $("#profilePhone").textContent = me.phone || "—";
  $("#profileNameTag").textContent = me.profile || "default";
  $("#profileOverlay").hidden = false;
}

/* ------------------------------------------------------------------ chats */

function renderChatSkeleton() {
  $("#chatList").innerHTML = Array.from({ length: 7 })
    .map(
      () => `
      <div class="skeleton-row">
        <span class="skeleton avatar-sk"></span>
        <span>
          <span class="skeleton line" style="width: 55%"></span>
          <span class="skeleton line short" style="width: 78%"></span>
        </span>
      </div>`
    )
    .join("");
}

function renderChats() {
  const list = $("#chatList");
  if (!state.chats.length) {
    list.innerHTML = `
      <div class="empty-state">
        ${icon("i-users")}
        <b>No chats found</b>
        Try a different search, or paste a link in the welcome screen.
      </div>`;
    return;
  }
  list.innerHTML = state.chats
    .map((chat) => {
      const selected = state.selected && state.selected.reference === chat.reference;
      const unread = chat.unread > 0
        ? `<span class="unread-pill">${chat.unread > 99 ? "99+" : chat.unread}</span>`
        : "";
      const verified = chat.verified
        ? `<span class="verified-dot" title="Verified">${icon("i-check")}</span>`
        : "";
      return `
        <button class="chat-row${selected ? " selected" : ""}" data-reference="${escapeHtml(chat.reference)}" type="button">
          <span class="avatar c${chat.color || 1}">
            <span class="avatar-text">${escapeHtml(chat.initials || initials(chat.title))}</span>
            <span class="kind-mark">${icon(kindIcon(chat.kind))}</span>
          </span>
          <span class="chat-main">
            <span class="chat-title-line">
              <span class="chat-title">${escapeHtml(chat.title)}</span>
              ${verified}
            </span>
            <span class="chat-preview">${escapeHtml(chat.preview || "")}</span>
          </span>
          <span class="chat-side">
            <span class="chat-time">${escapeHtml(formatListTime(chat.date))}</span>
            ${unread}
          </span>
        </button>`;
    })
    .join("");
}

async function loadChats(query = "") {
  if (!state.authenticated) return;
  if (!state.chats.length) renderChatSkeleton();
  try {
    const data = await api(`/api/chats?limit=100&query=${encodeURIComponent(query)}`);
    state.chats = data.chats || [];
    renderChats();
  } catch (error) {
    if (error.status === 401) return;
    $("#chatList").innerHTML = `
      <div class="empty-state">
        ${icon("i-alert")}
        <b>Could not load chats</b>
        ${escapeHtml(error.message)}
      </div>`;
  }
}

function selectChat(chat) {
  if (state.job && state.job.status === "running") {
    toast("An export is running. Cancel it or wait for it to finish.", "error");
    return;
  }
  state.selected = chat;
  $("#app").classList.add("has-selection");
  $("#welcomeView").hidden = true;
  $("#jobView").hidden = true;
  $("#exportView").hidden = false;
  $("#targetInput").value = chat.reference || "";
  $("#outputInput").value = chat.username ? `export_${chat.username}` : "";
  $("#targetNote").hidden = true;
  paintAvatar($("#selAvatar"), chat.title, chat.color, chat.kind);
  $("#selTitle").textContent = chat.title;
  $("#selSub").textContent = chat.username ? `@${chat.username}` : kindLabel(chat.kind);
  $("#selKind").textContent = kindLabel(chat.kind);
  updateEstimate();
  renderChats();
  $("#targetInput").focus();
  $("#targetInput").select();
  window.scrollTo({ top: 0 });
}

function showWelcome() {
  if (state.job && state.job.status === "running") {
    toast("An export is running. Cancel it or wait for it to finish.", "error");
    return;
  }
  state.selected = null;
  $("#app").classList.remove("has-selection");
  $("#welcomeView").hidden = false;
  $("#exportView").hidden = true;
  $("#jobView").hidden = true;
  renderChats();
}

async function resolveTarget(target, { select = true } = {}) {
  const value = String(target || "").trim();
  if (!value) {
    toast("Paste a link, @username, or peer reference first.", "error");
    return null;
  }
  try {
    const data = await api("/api/resolve", { method: "POST", body: { target: value } });
    const resolved = data.target;
    const chat = {
      title: resolved.title,
      username: resolved.username,
      reference: resolved.reference || value,
      kind: resolved.kind || "channel",
      color: 4,
      initials: initials(resolved.title),
      preview: resolved.requires_join ? "Invite link — join required" : "Ready to export",
      date: 0,
      unread: 0,
      verified: false,
    };
    if (select) {
      state.selected = chat;
      selectChat(chat);
      if (resolved.requires_join) {
        const note = $("#targetNote");
        note.className = "inline-note";
        note.innerHTML = `${icon("i-alert")} This invite link needs <b>Join invite link</b> enabled before exporting.`;
        note.hidden = false;
        $("#joinInput").checked = true;
      }
      $("#targetInput").value = resolved.requires_join
        ? value
        : resolved.reference || value;
    }
    return chat;
  } catch (error) {
    const note = $("#targetNote");
    if (note) {
      note.className = "inline-note error";
      note.innerHTML = `${icon("i-alert")} ${escapeHtml(error.message)}`;
      note.hidden = false;
    }
    toast(error.message, "error");
    return null;
  }
}

/* ------------------------------------------------------------ export form */

function renderMediaTypes() {
  const grid = $("#typeGrid");
  grid.innerHTML = MEDIA_TYPES.map(
    (type) => `
      <label class="type-card" data-type="${type.id}">
        <input type="checkbox" value="${type.id}" checked />
        <span class="type-icon">${icon(type.icon)}</span>
        <span class="type-label">${type.label}</span>
        <span class="type-check">${icon("i-check")}</span>
      </label>`
  ).join("");
  grid.addEventListener("change", updateEstimate);
}

function selectedTypes() {
  return $$("#typeGrid input[type=checkbox]:checked").map((input) => input.value);
}

function updateEstimate() {
  const selected = selectedTypes();
  const estimate = $("#estimate");
  if (!estimate) return;
  if (!selected.length) {
    estimate.innerHTML = "<b>Text only</b> · no media will be downloaded";
    return;
  }
  const maxFile = Number($("#maxFile").value || 0);
  const unit = Number($("#maxFileUnit").value || 1048576);
  const maxLabel = maxFile > 0 ? ` · max ${formatBytes(maxFile * unit)}/file` : "";
  estimate.innerHTML = `<b>${selected.length}</b> media type${selected.length === 1 ? "" : "s"} selected${maxLabel}`;
}

function numberField(selector) {
  const value = Number($(selector).value || 0);
  return Number.isFinite(value) && value > 0 ? value : 0;
}

function collectExportRequest() {
  const target = $("#targetInput").value.trim();
  if (!target) throw new Error("Enter or select an export target.");
  const since = $("#sinceInput").value || null;
  const until = $("#untilInput").value || null;
  if (since && until && since > until) throw new Error("Start date must be earlier than end date.");
  const limitText = $("#limitInput").value.trim();
  const limit = limitText ? Number(limitText) : null;
  if (limit !== null && (!Number.isFinite(limit) || limit < 1)) {
    throw new Error("Message limit must be a positive number.");
  }
  return {
    target,
    title: state.selected?.title || target,
    output_name: $("#outputInput").value.trim(),
    media_types: selectedTypes(),
    max_file_size_bytes: Math.round(numberField("#maxFile") * Number($("#maxFileUnit").value || 1)),
    max_total_size_bytes: Math.round(numberField("#maxTotal") * Number($("#maxTotalUnit").value || 1)),
    limit,
    since,
    until,
    timezone: $("#timezoneInput").value.trim() || "Asia/Tehran",
    include_created: $("#createdInput").checked,
    join: $("#joinInput").checked,
  };
}

async function startExport(event) {
  event.preventDefault();
  let payload;
  try {
    payload = collectExportRequest();
  } catch (error) {
    toast(error.message, "error");
    return;
  }
  const button = $("#startBtn");
  setBusy(button, true, "Starting…");
  try {
    const job = await api("/api/export", { method: "POST", body: payload });
    showJob(job);
    toast("Export started.", "success");
  } catch (error) {
    const active = await api("/api/jobs/active").catch(() => null);
    if (active?.job) {
      showJob(active.job);
      toast("An export is already running.", "error");
      return;
    }
    toast(error.message, "error");
  } finally {
    setBusy(button, false);
  }
}

/* ------------------------------------------------------------------- jobs */

function phaseLabel(phase) {
  return {
    starting: "Starting",
    fetching: "Fetching history",
    downloading: "Downloading media",
    rendering: "Rendering archive",
    done: "Completed",
    failed: "Failed",
    cancelled: "Cancelled",
  }[phase] || phase || "Working";
}

function statusLabel(status) {
  return {
    running: "Running",
    completed: "Completed",
    failed: "Failed",
    cancelled: "Cancelled",
  }[status] || status;
}

function showJob(job) {
  state.job = job;
  state.sample = null;
  state.speed = 0;
  $("#app").classList.add("has-selection");
  $("#welcomeView").hidden = true;
  $("#exportView").hidden = true;
  $("#jobView").hidden = false;
  paintAvatar($("#jobAvatar"), job.title || "Export", state.selected?.color || 4, state.selected?.kind || null);
  $("#jobTitle").textContent = job.title || "Export";
  $("#jobSub").textContent = state.selected?.username ? `@${state.selected.username}` : job.target || "";
  $("#resultCard").hidden = true;
  $("#cancelBtn").hidden = false;
  $("#jobBackBtn").disabled = true;
  updateJob(job);
  startPolling(job.id);
}

function updateJob(job) {
  state.job = job;
  const status = $("#jobStatus");
  status.textContent = statusLabel(job.status);
  status.className = `status-chip ${job.status}`;

  $("#jobPhase").textContent = phaseLabel(job.phase);
  const percent = job.status === "completed" ? 100 : job.overall.percent || 0;
  $("#jobPercent").textContent = `${Math.round(percent)}%`;
  $("#overallBar").style.width = `${percent}%`;
  $("#overallBar").className = `progress-fill${job.status === "completed" ? " completed" : job.status === "failed" ? " failed" : ""}`;
  $("#jobBytes").textContent = `${formatBytes(job.overall.done)} / ${formatBytes(job.overall.total)}`;

  const now = Date.now();
  if (state.sample && now > state.sample.time && job.overall.done >= state.sample.done) {
    const instant = ((job.overall.done - state.sample.done) * 1000) / (now - state.sample.time);
    state.speed = state.speed ? state.speed * 0.65 + instant * 0.35 : instant;
  }
  state.sample = { time: now, done: job.overall.done };
  if (state.speed > 0 && job.status === "running") {
    $("#jobSpeed").textContent = `${formatBytes(state.speed)}/s`;
    const remaining = Math.max(0, job.overall.total - job.overall.done);
    $("#jobEta").textContent = remaining > 0 ? `${formatDuration(remaining / state.speed)} left` : "Almost done";
  } else {
    $("#jobSpeed").textContent = job.status === "running" ? "—" : "";
    $("#jobEta").textContent = "";
  }

  $("#fileName").textContent = job.file?.label || "Waiting for the first file…";
  const filePercent = job.status === "completed" ? 100 : job.file?.percent || 0;
  $("#filePercent").textContent = `${Math.round(filePercent)}%`;
  $("#fileBar").style.width = `${filePercent}%`;
  $("#fileBytes").textContent = `${formatBytes(job.file?.done || 0)} / ${formatBytes(job.file?.total || 0)}`;
  $("#fileStatus").textContent = job.file?.status ? job.file.status : "";

  const log = $("#eventLog");
  const rows = (job.events || []).slice(-9);
  log.innerHTML = rows
    .map(
      (event) => `<div class="event-row"><span class="event-time">${escapeHtml(formatClock(event.time))}</span><span class="event-text">${escapeHtml(event.text)}</span></div>`
    )
    .join("");
  log.scrollTop = log.scrollHeight;

  if (job.status === "completed") {
    $("#jobBackBtn").disabled = false;
    $("#cancelBtn").hidden = true;
    $("#resultCard").hidden = false;
    $("#resultTitle").textContent = "Export completed";
    $("#resultPath").textContent = job.output_dir || "";
    const stats = job.stats || {};
    $("#resultStats").innerHTML = [
      ["Messages", stats.messages || 0],
      ["Photos", stats.photos || 0],
      ["Videos", stats.videos || 0],
      ["Files", stats.documents || 0],
      ["Voice", stats.voice_messages || 0],
      ["Stickers", stats.stickers || 0],
      ["Media size", formatBytes(stats.bytes_written || 0)],
      ["Events", stats.service_messages || 0],
    ]
      .map(([label, value]) => `<div class="stat"><b>${escapeHtml(value)}</b><small>${label}</small></div>`)
      .join("");
    if (job.view_url) {
      $("#openExport").href = job.view_url;
      $("#openExport").hidden = false;
    } else {
      $("#openExport").hidden = true;
    }
    stopPolling();
    loadChats($("#searchInput").value.trim()).catch(() => {});
  } else if (job.status === "failed" || job.status === "cancelled") {
    $("#jobBackBtn").disabled = false;
    $("#cancelBtn").hidden = true;
    $("#resultCard").hidden = false;
    $("#resultTitle").textContent = job.status === "failed" ? "Export failed" : "Export cancelled";
    $("#resultPath").textContent = job.error || "The archive may be incomplete.";
    $("#resultStats").innerHTML = "";
    $("#openExport").hidden = true;
    stopPolling();
  }
}

function startPolling(jobId) {
  stopPolling();
  const tick = async () => {
    try {
      const job = await api(`/api/jobs/${encodeURIComponent(jobId)}`);
      updateJob(job);
      if (job.status === "running") {
        state.pollTimer = setTimeout(tick, 700);
      }
    } catch (error) {
      if (error.status !== 404) toast(error.message, "error");
      stopPolling();
    }
  };
  state.pollTimer = setTimeout(tick, 500);
}

function stopPolling() {
  if (state.pollTimer) clearTimeout(state.pollTimer);
  state.pollTimer = null;
}

async function loadJob(jobId) {
  try {
    const job = await api(`/api/jobs/${encodeURIComponent(jobId)}`);
    showJob(job);
    if (job.status === "running") startPolling(job.id);
  } catch (error) {
    toast(error.message, "error");
  }
}

async function cancelExport() {
  if (!state.job) return;
  if (!confirm("Cancel this export? Partial files will remain on disk.")) return;
  const button = $("#cancelBtn");
  setBusy(button, true, "Cancelling…");
  try {
    const job = await api(`/api/jobs/${encodeURIComponent(state.job.id)}/cancel`, {
      method: "POST",
      body: {},
    });
    updateJob(job);
    toast("Cancellation requested.");
  } catch (error) {
    toast(error.message, "error");
  } finally {
    setBusy(button, false);
  }
}

function backToExport() {
  if (state.job?.status === "running") {
    toast("The export is still running. Cancel it first or wait.", "error");
    return;
  }
  if (state.selected) {
    $("#jobView").hidden = true;
    $("#exportView").hidden = false;
    $("#app").classList.add("has-selection");
  } else {
    showWelcome();
  }
}

/* -------------------------------------------------------------------- init */

async function init() {
  const themeParam = new URLSearchParams(location.search).get("theme");
  applyTheme(themeParam === "light" || themeParam === "dark" ? themeParam : document.documentElement.dataset.theme || "dark");
  renderMediaTypes();
  updateEstimate();
  bindEvents();
  try {
    const status = await api("/api/status");
    state.authenticated = Boolean(status.authenticated);
    state.me = status.me;
    state.exportRoot = status.export_root || "";
    if (state.exportRoot) {
      $("#exportRootHint").textContent = `Root: ${state.exportRoot} — reusing a name resumes existing files.`;
    }
    renderMe();
    if (!state.authenticated) {
      showAuth();
      return;
    }
    hideAuth();
    await loadChats();
    if (!state.authenticated) return;
    if (status.active_job_id) {
      await loadJob(status.active_job_id);
      return;
    }
    const params = new URLSearchParams(location.search);
    const deepTarget = params.get("target");
    const deepChat = params.get("chat");
    if (deepChat) {
      const match = state.chats.find((chat) => chat.reference === deepChat);
      if (match) {
        selectChat(match);
        return;
      }
    }
    if (deepTarget) {
      const resolved = await resolveTarget(deepTarget);
      if (resolved) return;
    }
    showWelcome();
  } catch (error) {
    toast(error.message, "error");
    showAuth();
  }
}

function bindEvents() {
  $("#themeBtn").addEventListener("click", toggleTheme);
  $("#profileBtn").addEventListener("click", openProfile);
  $("#meRow").addEventListener("click", openProfile);
  $("#logoutBtn").addEventListener("click", logout);
  $("#profileLogout").addEventListener("click", logout);
  $("#profileClose").addEventListener("click", () => ($("#profileOverlay").hidden = true));
  $("#profileOverlay").addEventListener("click", (event) => {
    if (event.target === $("#profileOverlay")) $("#profileOverlay").hidden = true;
  });
  $("#searchInput").addEventListener("input", () => {
    clearTimeout(state.searchTimer);
    state.searchTimer = setTimeout(() => loadChats($("#searchInput").value.trim()), 280);
  });
  $("#refreshBtn").addEventListener("click", () => loadChats($("#searchInput").value.trim()));
  $("#chatList").addEventListener("click", (event) => {
    const row = event.target.closest(".chat-row");
    if (!row) return;
    const chat = state.chats.find((item) => item.reference === row.dataset.reference);
    if (chat) selectChat(chat);
  });

  $("#welcomeTargetBtn").addEventListener("click", () => resolveTarget($("#welcomeTarget").value));
  $("#sidebarQuickForm").addEventListener("submit", (event) => {
    event.preventDefault();
    resolveTarget($("#sidebarTarget").value);
  });
  $("#welcomeTarget").addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      resolveTarget($("#welcomeTarget").value);
    }
  });

  $("#backBtn").addEventListener("click", showWelcome);
  $("#jobBackBtn").addEventListener("click", backToExport);
  $("#newJobBtn").addEventListener("click", backToExport);
  $("#resolveBtn").addEventListener("click", async () => {
    const button = $("#resolveBtn");
    setBusy(button, true, "Checking…");
    await resolveTarget($("#targetInput").value);
    setBusy(button, false);
  });
  $("#targetInput").addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      $("#resolveBtn").click();
    }
  });

  $("#selectAllTypes").addEventListener("click", () => {
    $$("#typeGrid input[type=checkbox]").forEach((input) => (input.checked = true));
    updateEstimate();
  });
  $("#clearAllTypes").addEventListener("click", () => {
    $$("#typeGrid input[type=checkbox]").forEach((input) => (input.checked = false));
    updateEstimate();
  });
  $("#maxFile").addEventListener("input", updateEstimate);
  $("#maxFileUnit").addEventListener("change", updateEstimate);
  $("#exportForm").addEventListener("submit", startExport);
  $("#cancelBtn").addEventListener("click", cancelExport);
  $("#exportAgain").addEventListener("click", () => {
    $("#resultCard").hidden = true;
    backToExport();
  });

  $("#phoneForm").addEventListener("submit", requestCode);
  $("#codeForm").addEventListener("submit", verifyCode);
  $("#resendBtn").addEventListener("click", resendCode);
  $("#changePhoneBtn").addEventListener("click", changePhone);

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      $("#profileOverlay").hidden = true;
    }
  });
}

document.addEventListener("DOMContentLoaded", init);
