/**
 * Agent console — polls middleware /sessions and /config.
 */

const POLL_MS = 2000;
const REPLY_TIMEOUT_MS = 20000;
const state = {
  config: null,
  statusFilter: "all",
  readFilter: "all",
  /** empty = All; otherwise OR-match any selected agency tag */
  agencyFilter: [],
  selectedSessionId: null,
  sessions: [],
  detail: null,
  replyText: "",
  replyStatus: "idle",
  globalError: null,
  pollTimer: null,
  countdownTimer: null,
  /** current_session_id -> latest inbound timestamp (ms) the agent has seen */
  readUpTo: {},
};

const $ = (id) => document.getElementById(id);

function apiUrl(path) {
  return new URL(path, window.location.origin).href;
}

async function fetchJson(path, options = {}) {
  const controller = new AbortController();
  const timeout = options.timeoutMs
    ? setTimeout(() => controller.abort(), options.timeoutMs)
    : null;
  try {
    const res = await fetch(apiUrl(path), {
      ...options,
      signal: controller.signal,
      headers: { "Content-Type": "application/json", ...options.headers },
    });
    const body = res.status === 204 ? null : await res.json().catch(() => null);
    return { ok: res.ok, status: res.status, body };
  } finally {
    if (timeout) clearTimeout(timeout);
  }
}

function parseErrorDetail(body) {
  if (!body) return null;
  const d = body.detail;
  if (typeof d === "string") return d;
  if (d && typeof d === "object") return d.message || d.code || JSON.stringify(d);
  return null;
}

function asUtcMs(iso) {
  return new Date(iso).getTime();
}

function expiresAtMs(lastActivityAt) {
  const ttl = state.config.session_ttl_seconds * 1000;
  return asUtcMs(lastActivityAt) + ttl;
}

function uiStatus(session) {
  const cfg = state.config;
  const now = Date.now();
  const lastAt = session.last_activity_at || session.lastActivityAt;
  if (!lastAt) return "active";

  if (session.status === "expired") return "expired";

  const expires = expiresAtMs(lastAt);
  if (now >= expires) return "expired";

  const remainingSec = (expires - now) / 1000;
  if (remainingSec < cfg.session_expiring_soon_seconds) return "expiring";
  return "active";
}

function formatTime(iso) {
  if (!iso) return "—";
  return new Date(iso).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

function formatCountdown(lastActivityAt) {
  const remainingMs = expiresAtMs(lastActivityAt) - Date.now();
  if (remainingMs <= 0) return null;
  const totalSec = Math.ceil(remainingMs / 1000);
  const min = Math.floor(totalSec / 60);
  const sec = totalSec % 60;
  return `${min}:${String(sec).padStart(2, "0")} remaining`;
}

function statusLabel(ui) {
  if (ui === "expiring") return "Expiring Soon";
  if (ui === "expired") return "Expired";
  return "Active";
}

const AGENCY_DISPLAY = { fire: "FIRE", medical: "MED", police: "POL" };

function formatAgencyDeployedList(tags) {
  if (!tags || tags.length === 0) return "";
  return tags.map((t) => AGENCY_DISPLAY[t] || String(t).toUpperCase()).join(", ");
}

function latestInboundMs(detail) {
  let max = 0;
  for (const msg of detail.messages || []) {
    if (msg.direction !== "inbound") continue;
    max = Math.max(max, asUtcMs(msg.timestamp));
  }
  return max;
}

function markSessionRead(sessionId, detail) {
  if (!sessionId || !detail) return;
  const seen = latestInboundMs(detail);
  if (seen > 0) {
    state.readUpTo[sessionId] = Math.max(state.readUpTo[sessionId] || 0, seen);
  }
}

function isPhoneUnread(row) {
  if (!row.last_inbound_at) return false;
  const inboundMs = asUtcMs(row.last_inbound_at);
  const isOpen =
    state.selectedSessionId === row.current_session_id &&
    state.detail &&
    state.detail.id === row.current_session_id;
  if (isOpen) return false;
  return inboundMs > (state.readUpTo[row.current_session_id] || 0);
}

function filteredSessions() {
  return state.sessions.filter((s) => {
    const ui = uiStatus(s);
    if (state.statusFilter !== "all" && ui !== state.statusFilter) {
      return false;
    }
    const unread = isPhoneUnread(s);
    if (state.readFilter === "unread" && !unread) return false;
    if (state.readFilter === "read" && unread) return false;
    if (state.agencyFilter.length > 0) {
      const tags = s.agency_tags || [];
      if (!state.agencyFilter.some((t) => tags.includes(t))) return false;
    }
    return true;
  });
}

function getCheckedAgencyTags() {
  return Array.from(
    document.querySelectorAll('input[name="agency-tag"]:checked')
  ).map((el) => el.value);
}

function syncAgencyCheckboxes(tags) {
  const selected = new Set(tags || []);
  document.querySelectorAll('input[name="agency-tag"]').forEach((el) => {
    el.checked = selected.has(el.value);
  });
}

function setAgencyCheckboxesDisabled(disabled) {
  const fieldset = $("session-type-fieldset");
  fieldset.disabled = disabled;
  document.querySelectorAll('input[name="agency-tag"]').forEach((el) => {
    el.disabled = disabled;
  });
}

async function saveSessionTags() {
  if (!state.selectedSessionId || !state.detail?.is_reply_target) return;

  const tags = getCheckedAgencyTags();
  const { ok, status, body } = await fetchJson(
    `/sessions/${state.selectedSessionId}/tags`,
    {
      method: "PATCH",
      body: JSON.stringify({ tags }),
    }
  );

  if (!ok) {
    state.globalError =
      parseErrorDetail(body) || `Could not update session tags (HTTP ${status}).`;
    syncAgencyCheckboxes(state.detail.agency_tags);
    render();
    return;
  }

  state.detail = body;
  const idx = state.sessions.findIndex((p) => p.from === body.from);
  if (idx >= 0) {
    state.sessions[idx] = { ...state.sessions[idx], agency_tags: body.agency_tags };
  }
}

function selectedPhoneRow() {
  return (
    state.sessions.find((p) => p.current_session_id === state.selectedSessionId) ||
    null
  );
}

function isReplyDisabled() {
  if (!state.detail || state.replyStatus === "sending") return true;
  if (state.detail.is_reply_target === false) return true;
  return uiStatus(state.detail) === "expired";
}

async function loadConfig() {
  const { ok, body } = await fetchJson("/config");
  if (!ok) throw new Error("Could not load session configuration.");
  state.config = body;
}

async function pollSessions() {
  const { ok, body, status } = await fetchJson("/sessions?group_by_phone=true");
  if (!ok) {
    state.globalError = `Session list unavailable (HTTP ${status}).`;
    return;
  }
  state.globalError = null;
  state.sessions = body;

  if (state.selectedSessionId && state.detail) {
    const row = body.find((p) => p.from === state.detail.from);
    if (row && row.current_session_id !== state.selectedSessionId) {
      state.selectedSessionId = row.current_session_id;
    }
  }
}

async function pollDetail() {
  if (!state.selectedSessionId) {
    state.detail = null;
    return;
  }
  const { ok, body, status } = await fetchJson(
    `/sessions/${state.selectedSessionId}`
  );
  if (!ok) {
    if (status === 404) {
      state.selectedSessionId = null;
      state.detail = null;
    }
    return;
  }
  state.detail = body;
  markSessionRead(body.id, body);
  const idx = state.sessions.findIndex((p) => p.from === body.from);
  if (idx >= 0) {
    const inboundMs = latestInboundMs(body);
    state.sessions[idx] = {
      ...state.sessions[idx],
      current_session_id: body.id,
      status: body.status,
      last_activity_at: body.last_activity_at,
      agency_tags: body.agency_tags,
      last_inbound_at:
        inboundMs > 0 ? new Date(inboundMs).toISOString() : state.sessions[idx].last_inbound_at,
    };
  }
  syncAgencyCheckboxes(body.agency_tags);
}

async function poll() {
  if (document.hidden) return;
  try {
    await pollSessions();
    await pollDetail();
    render();
  } catch (err) {
    state.globalError = err.message || "Connection error.";
    render();
  }
}

function startPolling() {
  if (state.pollTimer) clearInterval(state.pollTimer);
  state.pollTimer = setInterval(poll, POLL_MS);
  poll();
}

function startCountdown() {
  if (state.countdownTimer) clearInterval(state.countdownTimer);
  state.countdownTimer = setInterval(() => {
    if (!state.detail || document.hidden) return;
    const expiryEl = $("session-expiry");
    if (!expiryEl) return;
    const ui = uiStatus(state.detail);
    if (ui === "expired") {
      expiryEl.textContent = `Expired ${formatTime(
        new Date(expiresAtMs(state.detail.last_activity_at)).toISOString()
      )}`;
      return;
    }
    const cd = formatCountdown(state.detail.last_activity_at);
    expiryEl.textContent = cd ? `Expires ${cd}` : "Expiring";
  }, 1000);
}

async function sendReply() {
  const text = $("reply-input").value.trim();
  if (!text || !state.selectedSessionId || isReplyDisabled()) return;

  state.replyStatus = "sending";
  render();

  const { ok, status, body } = await fetchJson(
    `/sessions/${state.selectedSessionId}/reply`,
    {
      method: "POST",
      body: JSON.stringify({ text }),
      timeoutMs: REPLY_TIMEOUT_MS,
    }
  );

  if (status === 409) {
    state.replyStatus = "failed";
    state.globalError = parseErrorDetail(body) || "Session has expired.";
    await pollDetail();
    render();
    return;
  }

  if (!ok) {
    state.replyStatus = "failed";
    state.globalError =
      parseErrorDetail(body) || `Reply failed (HTTP ${status}).`;
    render();
    return;
  }

  if (body.success) {
    state.replyStatus = "delivered";
    state.lastDeliveryError = null;
    $("reply-input").value = "";
    await pollDetail();
    render();
    return;
  }

  state.replyStatus = "failed";
  const attempts = body.delivery_attempts ?? 0;
  const err = body.error || "Unknown delivery error.";
  state.lastDeliveryError = attempts
    ? `Delivery failed after ${attempts} attempt(s). ${err} Message is in the conversation.`
    : `Delivery failed. ${err} Message is in the conversation.`;
  await pollDetail();
  render();
}

function renderSessionList() {
  const list = $("session-list");
  const items = filteredSessions();
  list.innerHTML = "";

  if (items.length === 0) {
    const li = document.createElement("li");
    li.className = "session-list-empty";
    li.textContent = "No sessions match this filter.";
    list.appendChild(li);
    return;
  }

  for (const row of items) {
    const ui = uiStatus(row);
    const unread = isPhoneUnread(row);
    const li = document.createElement("li");
    li.className = "session-item";
    if (row.current_session_id === state.selectedSessionId) {
      li.classList.add("selected");
    }
    if (unread) li.classList.add("unread");
    li.dataset.sessionId = row.current_session_id;

    li.innerHTML = `
      <div class="session-item-top">
        <span class="status-dot ${ui}" aria-hidden="true"></span>
        <span class="session-phone">${escapeHtml(row.from)}</span>
      </div>
      <div class="session-preview-row">
        <p class="session-preview">${escapeHtml(row.preview || "(no messages)")}</p>
        ${unread ? '<span class="unread-dot" aria-label="Unread"></span>' : ""}
      </div>
      <p class="session-time">${escapeHtml(formatTime(row.timestamp))}</p>
    `;

    li.addEventListener("click", () => selectSession(row.current_session_id));
    list.appendChild(li);
  }
}

function renderMessageList(container, messages, historical) {
  for (const msg of messages || []) {
    const div = document.createElement("div");
    const role = msg.direction === "inbound" ? "user" : "agent";
    const label = msg.direction === "inbound" ? "User" : "Agent";
    div.className = `msg ${role}${historical ? " msg-historical" : ""}`;
    div.innerHTML = `
      <div class="msg-meta">${label} · ${escapeHtml(formatTime(msg.timestamp))}</div>
      <div>${escapeHtml(msg.text)}</div>
    `;
    container.appendChild(div);
  }
}

function renderMessages() {
  const container = $("messages");
  container.innerHTML = "";

  if (!state.detail) return;

  const d = state.detail;

  for (const prior of d.previous_sessions || []) {
    const block = document.createElement("div");
    block.className = "session-block session-block--historical";
    appendPreviousSessionDivider(block, prior);
    renderMessageList(block, prior.messages, true);
    container.appendChild(block);
  }

  const currentBlock = document.createElement("div");
  currentBlock.className = "session-block session-block--current";

  if ((d.previous_sessions || []).length > 0) {
    const label = d.is_reply_target
      ? "Current session — replies go to this session"
      : "Viewing session (not the current session for this number)";
    appendSystemMessage(currentBlock, label);
  }

  const ui = uiStatus(d);
  if (ui === "expired") {
    appendSystemMessage(
      currentBlock,
      `Session expired at ${formatTime(
        new Date(expiresAtMs(d.last_activity_at)).toISOString()
      )}`
    );
  }

  if (state.lastDeliveryError && state.replyStatus === "failed") {
    appendSystemMessage(currentBlock, state.lastDeliveryError);
  }

  renderMessageList(currentBlock, d.messages, false);
  container.appendChild(currentBlock);

  container.scrollTop = container.scrollHeight;
}

function appendSystemMessage(container, text) {
  const div = document.createElement("div");
  div.className = "msg system";
  div.textContent = text;
  container.appendChild(div);
}

function appendPreviousSessionDivider(container, prior) {
  const div = document.createElement("div");
  div.className = "msg system";
  const expired = escapeHtml(formatTime(prior.expired_at));
  const agencies = formatAgencyDeployedList(prior.agency_tags);
  let html = `Previous session expired at ${expired}`;
  if (agencies) {
    html += ` - <span class="agency-deployed-label">Agency Deployed [${escapeHtml(agencies)}]</span>`;
  }
  div.innerHTML = html;
  container.appendChild(div);
}

function renderConversation() {
  const d = state.detail;
  const hasSelection = state.selectedSessionId && d;

  $("session-phone").textContent = hasSelection ? d.from : "—";
  const badge = $("session-status");
  const expiryEl = $("session-expiry");

  if (!hasSelection) {
    badge.textContent = "";
    badge.className = "status-badge";
    expiryEl.textContent = "";
    $("expired-banner").hidden = true;
    $("expired-banner").classList.add("hidden");
    $("global-error").hidden = !state.globalError;
    if (state.globalError) {
      $("global-error").classList.remove("hidden");
      $("global-error").textContent = state.globalError;
    } else {
      $("global-error").classList.add("hidden");
    }
    $("messages").innerHTML = "";
    $("reply-input").disabled = true;
    $("send-btn").disabled = true;
    $("reply-feedback").textContent = "";
    setAgencyCheckboxesDisabled(true);
    syncAgencyCheckboxes([]);
    return;
  }

  const ui = uiStatus(d);
  badge.textContent = statusLabel(ui);
  badge.className = `status-badge ${ui}`;

  if (ui === "expired") {
    const expiredAt = new Date(expiresAtMs(d.last_activity_at)).toISOString();
    expiryEl.textContent = `Expired ${formatTime(expiredAt)}`;
  } else {
    const cd = formatCountdown(d.last_activity_at);
    expiryEl.textContent = cd ? `Expires ${cd}` : "Expiring";
  }

  const expiredBanner = $("expired-banner");
  if (!d.is_reply_target) {
    expiredBanner.hidden = false;
    expiredBanner.classList.remove("hidden");
    expiredBanner.textContent =
      ui === "expired"
        ? `This session has expired. Reply is disabled.`
        : `This is not the current session for this number. Reply is disabled.`;
  } else if (ui === "expired") {
    expiredBanner.hidden = false;
    expiredBanner.classList.remove("hidden");
    expiredBanner.textContent = `Session expired at ${formatTime(
      new Date(expiresAtMs(d.last_activity_at)).toISOString()
    )}. Reply is disabled.`;
  } else {
    expiredBanner.hidden = true;
    expiredBanner.classList.add("hidden");
  }

  const globalErr = $("global-error");
  if (state.globalError) {
    globalErr.hidden = false;
    globalErr.classList.remove("hidden");
    globalErr.textContent = state.globalError;
  } else {
    globalErr.hidden = true;
    globalErr.classList.add("hidden");
  }

  renderMessages();

  const input = $("reply-input");
  const sendBtn = $("send-btn");
  const disabled = isReplyDisabled();
  input.disabled = disabled;
  sendBtn.disabled = disabled || !$("reply-input").value.trim();
  setAgencyCheckboxesDisabled(disabled);
  syncAgencyCheckboxes(d.agency_tags);

  const feedback = $("reply-feedback");
  feedback.className = "reply-feedback";
  feedback.textContent = "";
  if (state.replyStatus === "sending") {
    feedback.textContent = "Sending…";
    feedback.classList.add("sending");
  } else if (state.replyStatus === "delivered") {
    feedback.textContent = "Delivered";
    feedback.classList.add("delivered");
  } else if (state.replyStatus === "failed") {
    feedback.textContent = "Delivery failed";
    feedback.classList.add("failed");
  }
}

function agencyFilterLabel() {
  if (state.agencyFilter.length === 0) return "All";
  const names = { fire: "Fire", medical: "Medical", police: "Police" };
  return state.agencyFilter.map((t) => names[t] || t).join(", ");
}

function syncAgencyFilterUi() {
  const allCb = document.querySelector('input[data-agency-filter="all"]');
  const tagCbs = document.querySelectorAll('input[data-agency-filter="tag"]');
  const isAll = state.agencyFilter.length === 0;

  if (allCb) allCb.checked = isAll;
  tagCbs.forEach((el) => {
    el.checked = !isAll && state.agencyFilter.includes(el.value);
  });
  $("agency-filter-label").textContent = agencyFilterLabel();
}

function closeAgencyFilter() {
  const details = $("agency-filter");
  if (details) details.open = false;
}

function onAgencyFilterChange(changed) {
  const allCb = document.querySelector('input[data-agency-filter="all"]');
  const tagCbs = document.querySelectorAll('input[data-agency-filter="tag"]');

  if (changed.dataset.agencyFilter === "all") {
    state.agencyFilter = [];
    syncAgencyFilterUi();
    render();
    return;
  }

  if (changed.checked && allCb) allCb.checked = false;

  state.agencyFilter = Array.from(tagCbs)
    .filter((el) => el.checked)
    .map((el) => el.value);

  if (state.agencyFilter.length === 0 && allCb) {
    allCb.checked = true;
  } else if (allCb) {
    allCb.checked = false;
  }
  $("agency-filter-label").textContent = agencyFilterLabel();
  render();
}

function render() {
  $("filter-status").value = state.statusFilter;
  $("filter-read").value = state.readFilter;
  syncAgencyFilterUi();
  renderSessionList();
  renderConversation();
}

function selectSession(id) {
  closeAgencyFilter();
  state.selectedSessionId = id;
  state.replyStatus = "idle";
  state.lastDeliveryError = null;
  state.globalError = null;
  pollDetail().then(() => {
    render();
    $("messages").scrollTop = $("messages").scrollHeight;
  });
}

function escapeHtml(str) {
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function bindEvents() {
  $("filter-status").addEventListener("change", (e) => {
    closeAgencyFilter();
    state.statusFilter = e.target.value;
    render();
  });
  $("filter-read").addEventListener("change", (e) => {
    closeAgencyFilter();
    state.readFilter = e.target.value;
    render();
  });
  document
    .querySelectorAll("#agency-filter input[type=checkbox]")
    .forEach((el) => {
      el.addEventListener("change", () => onAgencyFilterChange(el));
    });

  document.addEventListener("click", (e) => {
    const details = $("agency-filter");
    if (!details?.open) return;
    if (details.contains(e.target)) return;
    closeAgencyFilter();
  });

  document.querySelectorAll('input[name="agency-tag"]').forEach((el) => {
    el.addEventListener("change", () => saveSessionTags());
  });

  $("send-btn").addEventListener("click", sendReply);
  $("reply-input").addEventListener("input", () => {
    $("send-btn").disabled =
      isReplyDisabled() || !$("reply-input").value.trim();
    if (state.replyStatus !== "sending") state.replyStatus = "idle";
  });
  $("reply-input").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendReply();
    }
  });

  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) poll();
  });
}

async function init() {
  bindEvents();
  try {
    await loadConfig();
    startCountdown();
    startPolling();
  } catch (err) {
    state.globalError = err.message;
    render();
  }
}

init();
