/**
 * Pure session UI helpers (testable without the DOM).
 *
 * Expiry display rules mirror shared/session_policy.py (is_expired + expiring window).
 * TTL constants come from GET /config: session_ttl_seconds, session_expiring_soon_seconds.
 */

export function asUtcMs(iso) {
  return new Date(iso).getTime();
}

export function expiresAtMs(lastActivityAt, config) {
  const ttl = config.session_ttl_seconds * 1000;
  return asUtcMs(lastActivityAt) + ttl;
}

/**
 * UI status for sidebar/detail: active | expiring | expired.
 * Aligns with server session status + TTL policy (see shared/session_policy.py).
 */
export function uiStatus(session, config, now = Date.now()) {
  const lastAt = session.last_activity_at;
  if (!lastAt) return "active";

  if (session.status === "expired") return "expired";

  const expires = expiresAtMs(lastAt, config);
  if (now >= expires) return "expired";

  const remainingSec = (expires - now) / 1000;
  if (remainingSec < config.session_expiring_soon_seconds) return "expiring";
  return "active";
}

export function parseErrorDetail(body) {
  if (!body) return null;
  const d = body.detail;
  if (typeof d === "string") return d;
  if (d && typeof d === "object") return d.message || d.code || JSON.stringify(d);
  return null;
}

export function escapeHtml(str) {
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

export function formatDeliveryFailureMessage(message) {
  const attempts = message.delivery_attempts ?? 0;
  const err = message.delivery_error || "Unknown delivery error.";
  return attempts
    ? `Delivery failed after ${attempts} attempt(s). ${err} Message is in the conversation.`
    : `Delivery failed. ${err} Message is in the conversation.`;
}

export function getLatestOutboundMessage(messages) {
  for (let i = (messages || []).length - 1; i >= 0; i--) {
    if (messages[i].direction === "outbound") return messages[i];
  }
  return null;
}

export function getOutboundDeliveryFailure(detail) {
  if (!detail) return null;
  if (detail.outbound_delivery_failure) return detail.outbound_delivery_failure;
  const latest = getLatestOutboundMessage(detail.messages);
  if (!latest || latest.delivery_status !== "failed") return null;
  return formatDeliveryFailureMessage(latest);
}

export function getLatestOutboundDeliveryStatus(detail) {
  if (!detail) return null;
  if (detail.latest_outbound_delivery_status) {
    return detail.latest_outbound_delivery_status;
  }
  const latest = getLatestOutboundMessage(detail.messages);
  return latest?.delivery_status ?? null;
}

/**
 * Sync reply bar delivery state from session detail (poll, select, after send).
 */
export function applyDeliveryStateFromDetail(detail, deliveryState) {
  const status = getLatestOutboundDeliveryStatus(detail);
  if (status === "failed") {
    return {
      replyStatus: "failed",
      lastDeliveryError: getOutboundDeliveryFailure(detail),
    };
  }
  if (status === "delivered") {
    return { replyStatus: "delivered", lastDeliveryError: null };
  }
  if (deliveryState.replyStatus === "sending") {
    return deliveryState;
  }
  if (getLatestOutboundMessage(detail?.messages)) {
    return deliveryState;
  }
  if (
    deliveryState.replyStatus === "failed" ||
    deliveryState.replyStatus === "delivered"
  ) {
    return { replyStatus: "idle", lastDeliveryError: null };
  }
  return deliveryState;
}

export function isPhoneUnread(row, { selectedSessionId, detail, readUpTo }) {
  if (row.last_message_direction === "outbound") return false;
  if (!row.last_inbound_at) return false;
  const inboundMs = asUtcMs(row.last_inbound_at);
  const isOpen =
    selectedSessionId === row.current_session_id &&
    detail &&
    detail.id === row.current_session_id;
  if (isOpen) return false;
  return inboundMs > (readUpTo[row.current_session_id] || 0);
}
