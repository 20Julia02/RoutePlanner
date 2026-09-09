export const byId = id => document.getElementById(id);

export function escapeHtml(value) {
  const element = document.createElement("div");
  element.textContent = String(value);
  return element.innerHTML;
}

export function renderWarnings(container, warnings, label) {
  if (!warnings?.length) return;
  const details = document.createElement("details");
  details.className = "warning-details";
  const summary = document.createElement("summary");
  summary.textContent = `${label} (${warnings.length})`;
  const list = document.createElement("ul");
  warnings.forEach(message => {
    const item = document.createElement("li");
    item.textContent = message;
    list.appendChild(item);
  });
  details.append(summary, list);
  container.appendChild(details);
}

export function apiErrorMessage(data, fallback) {
  if (typeof data?.detail === "string") return data.detail;
  if (Array.isArray(data?.detail)) {
    return data.detail.map(item => item.msg || JSON.stringify(item)).join("; ");
  }
  return fallback;
}

export function safeImageUrl(value) {
  if (typeof value !== "string" || !value.trim()) return null;
  try {
    const url = new URL(value.trim(), window.location.href);
    return url.protocol === "https:" ? url.href : null;
  } catch (_) {
    return null;
  }
}

export function formatVisitDuration(seconds) {
  const totalMinutes = Math.max(0, Math.round(Number(seconds || 0) / 60));
  if (totalMinutes < 60) return `${totalMinutes} min`;
  const hours = Math.floor(totalMinutes / 60);
  const minutes = totalMinutes % 60;
  return minutes ? `${hours} h ${minutes} min` : `${hours} h`;
}

export function formatRouteDuration(seconds) {
  const value = Number(seconds);
  const roundedMinutes = Number.isFinite(value)
    ? Math.ceil(Math.max(0, value) / (10 * 60)) * 10
    : 0;
  const hours = Math.floor(roundedMinutes / 60);
  const minutes = roundedMinutes % 60;
  return minutes ? `${hours} godz. ${minutes} min` : `${hours} godz.`;
}
