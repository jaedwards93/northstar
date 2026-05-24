/**
 * Agent console — polls middleware /sessions and /config.
 */

import {
  applyDeliveryStateFromDetail,
  asUtcMs,
  escapeHtml,
  expiresAtMs,
  formatDeliveryFailureMessage,
  getLatestOutboundDeliveryStatus,
  getOutboundDeliveryFailure,
  isPhoneUnread,
  parseErrorDetail,
  uiStatus,
} from "./session-ui.js";

const POLL_MS = 2000;
/** Must cover outbound retries (4 × 5s) + backoffs (3.5s). */
const REPLY_TIMEOUT_MS = 35000;
const state = {
  config: null,
  /** empty = All; otherwise OR-match any selected UI status */
  statusFilter: [],
  readFilter: "all",
  /** empty = All; otherwise OR-match any selected agency tag */
  agencyFilter: [],
  selectedSessionId: null,
  sessions: [],
  detail: null,
  replyStatus: "idle",
  globalError: null,
  lastDeliveryError: null,
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

function sessionUiStatus(session) {
  return uiStatus(session, state.config);
}

function sessionExpiresAtMs(lastActivityAt) {
  return expiresAtMs(lastActivityAt, state.config);
}

function syncOutboundDeliveryFromDetail(detail) {
  const next = applyDeliveryStateFromDetail(detail, {
    replyStatus: state.replyStatus,
    lastDeliveryError: state.lastDeliveryError,
  });
  state.replyStatus = next.replyStatus;
  state.lastDeliveryError = next.lastDeliveryError;
}

function setBannerVisible(el, visible) {
  if (el) el.hidden = !visible;
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
  const remainingMs = sessionExpiresAtMs(lastActivityAt) - Date.now();
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

function agentHasLastMessage(messages) {
  const msgs = messages || [];
  return msgs.length > 0 && msgs[msgs.length - 1].direction === "outbound";
}

/** Mark read through latest inbound (or now if the thread ends on an agent message). */
function markSessionRead(sessionId, detail) {
  if (!sessionId || !detail) return;
  const inboundMs = latestInboundMs(detail);
  if (agentHasLastMessage(detail.messages)) {
    state.readUpTo[sessionId] = Math.max(
      state.readUpTo[sessionId] || 0,
      inboundMs > 0 ? inboundMs : Date.now()
    );
    return;
  }
  if (inboundMs > 0) {
    state.readUpTo[sessionId] = Math.max(state.readUpTo[sessionId] || 0, inboundMs);
  }
}

function syncReadForAgentLastMessage(row) {
  if (row.last_message_direction !== "outbound") return;
  const sessionId = row.current_session_id;
  const inboundMs = row.last_inbound_at ? asUtcMs(row.last_inbound_at) : Date.now();
  state.readUpTo[sessionId] = Math.max(state.readUpTo[sessionId] || 0, inboundMs);
}

function phoneUnreadContext() {
  return {
    selectedSessionId: state.selectedSessionId,
    detail: state.detail,
    readUpTo: state.readUpTo,
  };
}

function filteredSessions() {
  return state.sessions.filter((s) => {
    const ui = sessionUiStatus(s);
    if (state.statusFilter.length > 0 && !state.statusFilter.includes(ui)) {
      return false;
    }
    const unread = isPhoneUnread(s, phoneUnreadContext());
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

function isReplyDisabled() {
  if (!state.detail || state.replyStatus === "sending") return true;
  if (state.detail.is_reply_target === false) return true;
  return sessionUiStatus(state.detail) === "expired";
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
  for (const row of body) {
    syncReadForAgentLastMessage(row);
  }

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
    const lastMsg = body.messages?.length
      ? body.messages[body.messages.length - 1]
      : null;
    state.sessions[idx] = {
      ...state.sessions[idx],
      current_session_id: body.id,
      status: body.status,
      last_activity_at: body.last_activity_at,
      agency_tags: body.agency_tags,
      last_inbound_at:
        inboundMs > 0 ? new Date(inboundMs).toISOString() : state.sessions[idx].last_inbound_at,
      last_message_direction: lastMsg?.direction ?? state.sessions[idx].last_message_direction,
    };
    syncReadForAgentLastMessage(state.sessions[idx]);
  }
  syncAgencyCheckboxes(body.agency_tags);
  syncOutboundDeliveryFromDetail(body);
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
    const ui = sessionUiStatus(state.detail);
    if (ui === "expired") {
      expiryEl.textContent = `Expired ${formatTime(
        new Date(sessionExpiresAtMs(state.detail.last_activity_at)).toISOString()
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
  state.lastDeliveryError = null;
  render();

  let ok;
  let status;
  let body;
  try {
    ({ ok, status, body } = await fetchJson(
      `/sessions/${state.selectedSessionId}/reply`,
      {
        method: "POST",
        body: JSON.stringify({ text, timestamp: new Date().toISOString() }),
        timeoutMs: REPLY_TIMEOUT_MS,
      }
    ));
  } catch (err) {
    state.replyStatus = "failed";
    state.lastDeliveryError =
      err.name === "AbortError"
        ? "Reply timed out while delivery was retrying. Check the conversation for the message status."
        : err.message || "Connection error while sending reply.";
    await pollDetail();
    render();
    return;
  }

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

  if (body.success || body.duplicate) {
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
  state.lastDeliveryError = formatDeliveryFailureMessage({
    delivery_attempts: attempts,
    delivery_error: err,
  });
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
    const ui = sessionUiStatus(row);
    const unread = isPhoneUnread(row, phoneUnreadContext());
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
    const failed =
      msg.direction === "outbound" && msg.delivery_status === "failed";
    div.className = `msg ${role}${historical ? " msg-historical" : ""}${
      failed ? " msg-delivery-failed" : ""
    }`;
    const failedNote = failed
      ? `<div class="msg-delivery-note">Not delivered to caller</div>`
      : "";
    div.innerHTML = `
      <div class="msg-meta">${label} · ${escapeHtml(formatTime(msg.timestamp))}</div>
      <div>${escapeHtml(msg.text)}</div>
      ${failedNote}
    `;
    container.appendChild(div);
  }
}

/** If within this distance of the bottom, poll re-renders keep the view pinned there. */
const MESSAGE_SCROLL_PIN_PX = 48;

function getMessagesScrollAnchor(container) {
  const maxScroll = container.scrollHeight - container.clientHeight;
  if (maxScroll <= 0) {
    return { stick: true, ratio: 0 };
  }
  const distanceFromBottom = maxScroll - container.scrollTop;
  return {
    stick: distanceFromBottom <= MESSAGE_SCROLL_PIN_PX,
    ratio: container.scrollTop / maxScroll,
  };
}

function applyMessagesScroll(container, anchor) {
  if (anchor.stick) {
    container.scrollTop = container.scrollHeight;
    return;
  }
  const maxScroll = container.scrollHeight - container.clientHeight;
  if (maxScroll > 0) {
    container.scrollTop = anchor.ratio * maxScroll;
  }
}

function renderMessages() {
  const container = $("messages");
  const anchor =
    container.childElementCount > 0
      ? getMessagesScrollAnchor(container)
      : { stick: true, ratio: 0 };
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

  const ui = sessionUiStatus(d);
  if (ui === "expired") {
    appendSystemMessage(
      currentBlock,
      `Session expired at ${formatTime(
        new Date(sessionExpiresAtMs(d.last_activity_at)).toISOString()
      )}`
    );
  }

  const deliveryBanner =
    getOutboundDeliveryFailure(d) ||
    (state.replyStatus === "failed" ? state.lastDeliveryError : null);
  if (deliveryBanner) {
    appendSystemMessage(currentBlock, deliveryBanner);
  }

  renderMessageList(currentBlock, d.messages, false);
  container.appendChild(currentBlock);

  applyMessagesScroll(container, anchor);
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
    setBannerVisible($("expired-banner"), false);
    const globalErr = $("global-error");
    setBannerVisible(globalErr, Boolean(state.globalError));
    if (state.globalError) globalErr.textContent = state.globalError;
    $("messages").innerHTML = "";
    $("reply-input").disabled = true;
    $("send-btn").disabled = true;
    $("reply-feedback").textContent = "";
    setAgencyCheckboxesDisabled(true);
    syncAgencyCheckboxes([]);
    return;
  }

  const ui = sessionUiStatus(d);
  badge.textContent = statusLabel(ui);
  badge.className = `status-badge ${ui}`;

  if (ui === "expired") {
    const expiredAt = new Date(sessionExpiresAtMs(d.last_activity_at)).toISOString();
    expiryEl.textContent = `Expired ${formatTime(expiredAt)}`;
  } else {
    const cd = formatCountdown(d.last_activity_at);
    expiryEl.textContent = cd ? `Expires ${cd}` : "Expiring";
  }

  const expiredBanner = $("expired-banner");
  if (!d.is_reply_target) {
    setBannerVisible(expiredBanner, true);
    expiredBanner.textContent =
      ui === "expired"
        ? `This session has expired. Reply is disabled.`
        : `This is not the current session for this number. Reply is disabled.`;
  } else if (ui === "expired") {
    setBannerVisible(expiredBanner, true);
    expiredBanner.textContent = `Session expired at ${formatTime(
      new Date(sessionExpiresAtMs(d.last_activity_at)).toISOString()
    )}. Reply is disabled.`;
  } else {
    setBannerVisible(expiredBanner, false);
  }

  const globalErr = $("global-error");
  setBannerVisible(globalErr, Boolean(state.globalError));
  if (state.globalError) globalErr.textContent = state.globalError;

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
  const outboundStatus = getLatestOutboundDeliveryStatus(d);
  const deliveryFailed =
    outboundStatus === "failed" ||
    (state.replyStatus === "failed" && outboundStatus !== "delivered");
  const deliverySucceeded =
    outboundStatus === "delivered" || state.replyStatus === "delivered";
  if (state.replyStatus === "sending") {
    feedback.textContent = "Sending…";
    feedback.classList.add("sending");
  } else if (deliveryFailed) {
    feedback.textContent = "FAILED";
    feedback.classList.add("failed");
  } else if (deliverySucceeded) {
    feedback.textContent = "Delivered";
    feedback.classList.add("delivered");
  }
}

const STATUS_MULTI_FILTER = {
  detailsId: "status-filter",
  labelId: "status-filter-label",
  dataAttr: "status-filter",
  optionLabels: {
    active: "Active",
    expiring: "Expiring Soon",
    expired: "Expired",
  },
  getSelected: () => state.statusFilter,
  setSelected: (values) => {
    state.statusFilter = values;
  },
};

const AGENCY_MULTI_FILTER = {
  detailsId: "agency-filter",
  labelId: "agency-filter-label",
  dataAttr: "agency-filter",
  optionLabels: { fire: "Fire", medical: "Medical", police: "Police" },
  getSelected: () => state.agencyFilter,
  setSelected: (values) => {
    state.agencyFilter = values;
  },
};

function filterDatasetRole(el, dataAttrKebab) {
  const key = dataAttrKebab.replace(/-([a-z])/g, (_, c) => c.toUpperCase());
  return el.dataset[key];
}

function multiFilterInputs(config) {
  const root = $(config.detailsId);
  if (!root) return { root, allCb: null, tagCbs: [] };
  return {
    root,
    allCb: root.querySelector(`input[data-${config.dataAttr}="all"]`),
    tagCbs: Array.from(
      root.querySelectorAll(`input[data-${config.dataAttr}="tag"]`)
    ),
  };
}

function multiFilterLabel(selected, optionLabels) {
  if (selected.length === 0) return "All";
  return selected.map((t) => optionLabels[t] || t).join(", ");
}

function syncMultiFilterUi(config) {
  const { allCb, tagCbs } = multiFilterInputs(config);
  const selected = config.getSelected();
  const isAll = selected.length === 0;

  if (allCb) allCb.checked = isAll;
  tagCbs.forEach((el) => {
    el.checked = !isAll && selected.includes(el.value);
  });
  const labelEl = $(config.labelId);
  if (labelEl) {
    labelEl.textContent = multiFilterLabel(selected, config.optionLabels);
  }
}

function closeMultiFilter(config) {
  const details = $(config.detailsId);
  if (details) details.open = false;
}

function onMultiFilterChange(changed, config) {
  const { allCb, tagCbs } = multiFilterInputs(config);
  const role = filterDatasetRole(changed, config.dataAttr);

  if (role === "all") {
    config.setSelected([]);
    syncMultiFilterUi(config);
    render();
    return;
  }

  if (role === "tag") {
    const checkedTags = tagCbs.filter((el) => el.checked).map((el) => el.value);

    if (checkedTags.length === 0 || checkedTags.length === tagCbs.length) {
      config.setSelected([]);
    } else {
      if (allCb) allCb.checked = false;
      config.setSelected(checkedTags);
    }

    syncMultiFilterUi(config);
    render();
  }
}

function closeFilterDropdowns() {
  closeMultiFilter(STATUS_MULTI_FILTER);
  closeMultiFilter(AGENCY_MULTI_FILTER);
}

function render() {
  $("filter-read").value = state.readFilter;
  syncMultiFilterUi(STATUS_MULTI_FILTER);
  syncMultiFilterUi(AGENCY_MULTI_FILTER);
  renderSessionList();
  renderConversation();
}

function selectSession(id) {
  closeFilterDropdowns();
  state.selectedSessionId = id;
  state.replyStatus = "idle";
  state.lastDeliveryError = null;
  state.globalError = null;
  pollDetail().then(() => {
    syncOutboundDeliveryFromDetail(state.detail);
    render();
    $("messages").scrollTop = $("messages").scrollHeight;
  });
}

function bindEvents() {
  $("filter-read").addEventListener("change", (e) => {
    closeFilterDropdowns();
    state.readFilter = e.target.value;
    render();
  });
  document
    .querySelectorAll("#status-filter input[type=checkbox]")
    .forEach((el) => {
      el.addEventListener("change", () =>
        onMultiFilterChange(el, STATUS_MULTI_FILTER)
      );
    });
  document
    .querySelectorAll("#agency-filter input[type=checkbox]")
    .forEach((el) => {
      el.addEventListener("change", () =>
        onMultiFilterChange(el, AGENCY_MULTI_FILTER)
      );
    });

  document.addEventListener("click", (e) => {
    const openDetails = [STATUS_MULTI_FILTER, AGENCY_MULTI_FILTER]
      .map((c) => $(c.detailsId))
      .filter((d) => d?.open);
    if (openDetails.length === 0) return;
    if (openDetails.some((d) => d.contains(e.target))) return;
    closeFilterDropdowns();
  });

  document.querySelectorAll('input[name="agency-tag"]').forEach((el) => {
    el.addEventListener("change", () => saveSessionTags());
  });

  $("send-btn").addEventListener("click", sendReply);
  $("reply-input").addEventListener("input", () => {
    $("send-btn").disabled =
      isReplyDisabled() || !$("reply-input").value.trim();
    if (state.replyStatus === "sending") return;
    syncOutboundDeliveryFromDetail(state.detail);
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
