// Mail-Scope local archive UI · creator: Bálint Szabolcs
"use strict";

const state = {
  sources: [],
  folders: [],
  people: [],
  discovered: [],
  messages: [],
  conversation: null,
  offset: 0,
  selectedMessageId: null,
  pollTimer: null,
  lastPeopleRefresh: "",
  detailedSearch: { conditions: [], operator: "AND" },
  detailedResults: [],
  detailedTotal: 0,
  detailedOffset: 0,
  caseOpen: false,
  route: null,
};

const element = (id) => document.getElementById(id);

/**
 * Format a moment as "YYYY-MM-DD HH:MM" in the computer's local time zone.
 */
function formatLocalDateTime(date) {
  if (!(date instanceof Date) || Number.isNaN(date.getTime())) return "";
  const pad = (value) => String(value).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

/**
 * Format an indexed UTC Unix timestamp, or report a missing date.
 */
function formatTimestamp(seconds) {
  return Number.isFinite(seconds) ? formatLocalDateTime(new Date(seconds * 1000)) : "Dátum nélkül";
}

/**
 * Build the paperclip icon used for attachments, as inline SVG.
 */
function paperclipIcon() {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("width", "13");
  svg.setAttribute("height", "13");
  svg.setAttribute("aria-hidden", "true");
  svg.setAttribute("focusable", "false");
  const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
  path.setAttribute("d", "M21 11.5l-8.6 8.6a5.5 5.5 0 0 1-7.8-7.8l9.2-9.2a3.7 3.7 0 0 1 5.2 5.2l-9.2 9.2a1.8 1.8 0 0 1-2.6-2.6l8.5-8.5");
  path.setAttribute("fill", "none");
  path.setAttribute("stroke", "currentColor");
  path.setAttribute("stroke-width", "2");
  path.setAttribute("stroke-linecap", "round");
  path.setAttribute("stroke-linejoin", "round");
  svg.append(path);
  return svg;
}

/**
 * Paperclip and count for a message with attachments; nothing for others.
 */
function attachmentBadge(message) {
  const count = Number(message.attachment_count) || 0;
  if (count === 0) return null;
  const names = Array.isArray(message.attachment_names) ? message.attachment_names : [];
  const listed = names.slice(0, 5).join(", ");
  const rest = count - Math.min(names.length, 5);
  const suspicious = Array.isArray(message.suspicious_attachments) ? message.suspicious_attachments : [];
  const description = `${count} melléklet: ${listed}${rest > 0 ? ` … és még ${rest}` : ""}`
    + (suspicious.length ? ` · GYANÚS: ${suspicious.join(", ")}` : "");
  const badge = document.createElement("span");
  badge.className = `attachment-badge${suspicious.length ? " suspicious" : ""}`;
  badge.setAttribute("role", "img");
  badge.setAttribute("aria-label", description);
  badge.title = description;
  const number = document.createElement("span");
  number.textContent = String(count);
  number.setAttribute("aria-hidden", "true");
  badge.append(paperclipIcon(), number);
  return badge;
}

/**
 * Local calendar day ("YYYY-MM-DD") of a moment, used for timeline grouping.
 */
function localDay(date) {
  return formatLocalDateTime(date).slice(0, 10);
}

/** Workspace pages, in sidebar and Ctrl+1…5 order. */
const ROUTES = {
  ugy: { panel: "case-panel", title: "Ügy és források" },
  archivum: { panel: "archive-panel", title: "Archívum böngésző" },
  beszelgetes: { panel: "conversation-panel", title: "Beszélgetésnézet" },
  kereses: { panel: "detailed-search-panel", title: "Részletes keresés" },
  szemelyek: { panel: "people-panel", title: "Személyek" },
  melleklet: { panel: "attachment-viewer", title: "Melléklet-nézegető" },
};
const ROUTE_ORDER = Object.keys(ROUTES);

/**
 * Read the page name from a "#/name" hash, falling back to the case page.
 */
function currentRoute() {
  const viewer = window.location.hash.match(/^#\/melleklet\/(\d+)\/(\d+(?:\/\d+){0,5})$/);
  if (viewer) {
    state.viewerTarget = { messageId: Number(viewer[1]), path: viewer[2] };
    return "melleklet";
  }
  const match = window.location.hash.match(/^#\/([a-z]+)$/);
  return match && ROUTES[match[1]] && match[1] !== "melleklet" ? match[1] : "ugy";
}

/**
 * Switch to a workspace page through the URL, so Back/Forward and reload work.
 */
function navigate(route) {
  const hash = `#/${route}`;
  if (window.location.hash === hash) {
    showRoute();
  } else {
    window.location.hash = hash;
  }
}

/**
 * Show exactly one page; without an open case only the case page is reachable.
 */
function showRoute() {
  let route = currentRoute();
  if (!state.caseOpen && route !== "ugy") route = "ugy";
  const expectedHash = route === "melleklet" ? window.location.hash : `#/${route}`;
  if (window.location.hash !== expectedHash) {
    window.history.replaceState(null, "", expectedHash);
  }
  ROUTE_ORDER.forEach((name) => {
    element(ROUTES[name].panel).hidden = name !== route;
  });
  document.querySelectorAll(".side-link[data-route]").forEach((link) => {
    const active = link.dataset.route === route;
    link.classList.toggle("active", active);
    if (active) {
      link.setAttribute("aria-current", "page");
    } else {
      link.removeAttribute("aria-current");
    }
  });
  element("page-title").textContent = ROUTES[route].title;
  document.title = `${ROUTES[route].title} — Mail-Scope`;
  const changed = state.route !== null && state.route !== route;
  state.route = route;
  if (route === "melleklet") loadViewer();
  else state.viewerReturn = window.location.hash;
  if (changed) {
    window.scrollTo(0, 0);
    const heading = element(`${ROUTES[route].panel}-title`);
    // Focus moves to the heading for screen readers; the visible ring is
    // kept for keyboard navigation only.
    heading.classList.toggle("pointer-focus", state.lastInput === "pointer");
    // Chromium does not treat Ctrl+number as keyboard modality for
    // :focus-visible, so keyboard switches are marked explicitly.
    heading.classList.toggle("keyboard-focus", state.lastInput === "keyboard");
    heading.focus({ preventScroll: true });
  }
}

state.lastInput = "keyboard";
document.addEventListener("pointerdown", () => { state.lastInput = "pointer"; }, true);
document.addEventListener("keydown", () => { state.lastInput = "keyboard"; }, true);
document.addEventListener("focusout", (event) => {
  if (event.target instanceof HTMLElement) event.target.classList.remove("pointer-focus", "keyboard-focus");
});

/**
 * Disable every page except the case page until a case is open.
 */
function updateNavigationAvailability() {
  document.querySelectorAll(".side-link[data-route]").forEach((link) => {
    const disabled = !state.caseOpen && link.dataset.route !== "ugy";
    if (disabled) {
      link.setAttribute("aria-disabled", "true");
      link.title = "Előbb nyisson meg egy ügyet.";
    } else {
      link.removeAttribute("aria-disabled");
      link.removeAttribute("title");
    }
  });
}

document.querySelectorAll(".side-link[data-route]").forEach((link) => {
  link.addEventListener("click", (event) => {
    if (link.getAttribute("aria-disabled") === "true") event.preventDefault();
  });
});
window.addEventListener("hashchange", showRoute);
document.addEventListener("keydown", (event) => {
  if (!event.ctrlKey || event.altKey || event.metaKey || !/^[1-5]$/.test(event.key)) return;
  const target = event.target;
  if (target instanceof HTMLElement && (target.isContentEditable || ["INPUT", "SELECT", "TEXTAREA"].includes(target.tagName))) {
    return;
  }
  if (!element("app-shell").hidden) {
    const route = ROUTE_ORDER[Number(event.key) - 1];
    event.preventDefault();
    if (state.caseOpen || route === "ugy") navigate(route);
  }
});

/**
 * Show the welcome screen without a case, and the workspace once one is open.
 */
function setWelcomeVisible(visible) {
  const wasVisible = !element("welcome-screen").hidden;
  element("welcome-screen").hidden = !visible;
  element("app-shell").hidden = visible;
  if (visible) {
    if (window.MailScopePcb) window.MailScopePcb.mount(element("welcome-background"));
    if (!wasVisible) {
      refreshRecentCases();
      element("welcome-title").focus({ preventScroll: true });
    }
  } else if (window.MailScopePcb) {
    // No animation may keep running behind the workspace.
    window.MailScopePcb.unmount();
  }
}

/**
 * Format when a recent case was last opened, relative to today.
 */
function formatRecentDate(value) {
  const opened = new Date(value);
  if (Number.isNaN(opened.getTime())) return "";
  const startOfDay = (date) => new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime();
  const days = Math.round((startOfDay(new Date()) - startOfDay(opened)) / 86400000);
  if (days === 0) return "ma";
  if (days === 1) return "tegnap";
  const sameYear = opened.getFullYear() === new Date().getFullYear();
  return new Intl.DateTimeFormat("hu-HU", sameYear
    ? { month: "short", day: "numeric" }
    : { year: "numeric", month: "short", day: "numeric" }).format(opened);
}

/**
 * List recently opened, still existing case folders on the welcome screen.
 */
async function refreshRecentCases() {
  const section = element("recent-cases");
  const list = element("recent-cases-list");
  try {
    const cases = await requestJson("/api/recent-cases");
    list.replaceChildren();
    cases.forEach((recent) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "recent-case";
      button.title = recent.path;
      const text = document.createElement("span");
      text.className = "recent-case-text";
      const name = document.createElement("span");
      name.className = "recent-case-name";
      name.textContent = recent.name;
      const meta = document.createElement("span");
      meta.className = "recent-case-meta";
      meta.textContent = `${recent.source_count.toLocaleString("hu-HU")} forrás · ${recent.message_count.toLocaleString("hu-HU")} levél`;
      text.append(name, meta);
      const when = document.createElement("span");
      when.className = "recent-case-when";
      when.textContent = formatRecentDate(recent.opened_at);
      button.append(text, when);
      button.addEventListener("click", () => openCase(recent.path, "welcome-notice"));
      list.append(button);
    });
    section.hidden = cases.length === 0;
  } catch {
    section.hidden = true;
  }
}

/**
 * Pick a case folder with the native dialog, then open or create the case there.
 */
async function browseAndOpenCase(buttonId) {
  const button = element(buttonId);
  button.disabled = true;
  try {
    const result = await requestJson("/api/browse", {
      method: "POST",
      body: JSON.stringify({ kind: "directory", initial_path: element("welcome-case-path").value.trim() }),
    });
    if (result.path) await openCase(result.path, "welcome-notice");
  } catch (error) {
    element("welcome-notice").textContent = error.message;
    element("welcome-notice").classList.add("error");
  } finally {
    button.disabled = false;
  }
}

element("welcome-new-case").addEventListener("click", () => browseAndOpenCase("welcome-new-case"));
element("welcome-open-case").addEventListener("click", () => browseAndOpenCase("welcome-open-case"));
element("welcome-path-form").addEventListener("submit", (event) => {
  event.preventDefault();
  openCase(element("welcome-case-path").value.trim(), "welcome-notice");
});

/**
 * Request JSON from the same-origin local API and preserve server error details.
 */
async function requestJson(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { ...(options.headers || {}), ...(options.body ? { "Content-Type": "application/json" } : {}) },
  });
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(payload.error || `HTTP ${response.status}`);
  }
  return payload;
}

element("shutdown-button").addEventListener("click", shutdownApplication);

/**
 * Confirm the running-job state before safely stopping the local application.
 */
async function shutdownApplication() {
  const button = element("shutdown-button");
  button.disabled = true;
  try {
    const status = await requestJson("/api/status");
    const indexing = Boolean(status.job && status.job.running);
    const confirmationText = indexing
      ? "Indexelés fut. A leállítás biztonságosan megszakítja a folyamatot, és az eddig mentett levelek megmaradnak, így később folytatható. Leállítja az alkalmazást?"
      : "A Mail-Scope leáll. A már mentett ügyadatok és indexek a következő indításkor elérhetők. Biztosan leállítja?";
    if (!window.confirm(confirmationText)) {
      button.disabled = false;
      return;
    }
    await requestJson("/api/shutdown", {
      method: "POST",
      body: JSON.stringify({ cancel_indexing: indexing }),
    });
    const shutdownScreen = document.createElement("main");
    shutdownScreen.className = "shutdown-screen";
    shutdownScreen.setAttribute("role", "status");
    const title = document.createElement("h1");
    title.textContent = "A Mail-Scope leállítása";
    const detail = document.createElement("p");
    detail.textContent = indexing
      ? "Az indexelés biztonságosan megáll az aktuális mentési pontnál. Az eddig elmentett állapot megmarad; a böngészőfül bezárható."
      : "A mentett ügyadatok megmaradnak. A böngészőfül bezárható.";
    shutdownScreen.append(title, detail);
    document.body.replaceChildren(shutdownScreen);
  } catch (error) {
    showNotice(error.message, true);
    button.disabled = false;
  }
}

/**
 * Open a native local path picker and preserve the current value on cancel.
 */
async function browsePath(kind, inputId, buttonIds) {
  const buttons = buttonIds.map((id) => element(id));
  buttons.forEach((button) => { button.disabled = true; });
  try {
    const result = await requestJson("/api/browse", {
      method: "POST",
      body: JSON.stringify({
        kind,
        initial_path: element(inputId).value.trim(),
      }),
    });
    if (result.path) {
      element(inputId).value = result.path;
      showNotice("Az útvonal kiválasztva.");
    }
  } catch (error) {
    showNotice(error.message, true);
  } finally {
    buttons.forEach((button) => { button.disabled = false; });
  }
}

/**
 * Show a visible success or error message in the current workspace.
 */
function showNotice(message, isError = false) {
  const notice = element("case-notice");
  notice.textContent = message;
  notice.classList.toggle("error", isError);
}

/**
 * Render a single source row using text nodes only.
 */
function sourceRow(source, selectable = false) {
  const row = document.createElement("div");
  row.className = "source-row";
  if (selectable) {
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.checked = true;
    checkbox.value = source.path;
    checkbox.setAttribute("aria-label", `${source.path} kiválasztása indexelésre`);
    row.append(checkbox);
  } else {
    const spacer = document.createElement("span");
    row.append(spacer);
  }
  const path = document.createElement("span");
  path.className = "source-path";
  path.title = source.path;
  path.textContent = source.path;
  const kind = document.createElement("span");
  kind.className = "source-kind";
  kind.textContent = source.kind || "—";
  const status = document.createElement("span");
  status.className = "source-status";
  status.textContent = source.status || formatBytes(source.size);
  const hash = document.createElement("span");
  hash.className = "source-hash";
  hash.title = source.discovered_sha256 || source.sha256 || "";
  hash.textContent = source.discovered_sha256 || source.sha256 || "";
  row.append(path, kind, status, hash);
  if (!selectable && source.id !== undefined) row.append(sourceIntegrity(source));
  return row;
}

/**
 * Show the last full SHA-256 check of a registered source and offer a new one.
 */
function sourceIntegrity(source) {
  const integrity = document.createElement("div");
  integrity.className = "source-integrity";
  const info = document.createElement("span");
  info.className = "source-check";
  const verification = source.verification;
  const running = Boolean(verification && verification.running);
  if (running) {
    const percent = verification.total_bytes
      ? Math.min(100, Math.floor((100 * verification.done_bytes) / verification.total_bytes))
      : 0;
    info.textContent = `Teljes ellenőrzés folyamatban: ${percent}%`;
    const bar = document.createElement("progress");
    bar.max = 100;
    bar.value = percent;
    bar.setAttribute("aria-label", `${source.path} teljes ellenőrzése`);
    integrity.append(info, bar);
  } else {
    const results = { match: "egyezik", mismatch: "ELTÉR – a forrás megváltozott" };
    info.textContent = source.verified_at
      ? `Utolsó teljes ellenőrzés: ${formatLocalDateTime(new Date(source.verified_at))} · ${results[source.last_check_result] || "ismeretlen eredmény"}`
      : "Utolsó teljes ellenőrzés: még nem volt";
    info.classList.toggle("error", source.last_check_result === "mismatch");
    integrity.append(info);
    if (verification && verification.error) {
      const failure = document.createElement("span");
      failure.className = "source-check error";
      failure.textContent = `Az ellenőrzés nem sikerült: ${verification.error}`;
      integrity.append(failure);
    }
  }
  const button = document.createElement("button");
  button.type = "button";
  button.className = "button quiet";
  button.textContent = "Teljes újraellenőrzés";
  button.title = "A teljes forrás SHA-256 újraszámítása a háttérben";
  button.disabled = running;
  button.addEventListener("click", async () => {
    button.disabled = true;
    try {
      await requestJson(`/api/sources/${source.id}/verify`, { method: "POST", body: "{}" });
      await refreshStatus();
    } catch (error) {
      showNotice(error.message, true);
      button.disabled = false;
    }
  });
  integrity.append(button);
  return integrity;
}

/**
 * Present byte counts consistently without losing the original integer value.
 */
function formatBytes(value) {
  if (!Number.isFinite(value)) return "—";
  if (value < 1024) return `${value} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let size = value / 1024;
  let unit = units[0];
  for (let index = 1; size >= 1024 && index < units.length; index += 1) {
    size /= 1024;
    unit = units[index];
  }
  return `${size.toFixed(1)} ${unit}`;
}

/**
 * Render one safe, editable condition row for the detailed-search builder.
 */
function createSearchCondition(condition = { field: "text", value: "" }) {
  const row = document.createElement("div");
  row.className = "search-condition";

  const field = document.createElement("select");
  field.setAttribute("aria-label", "Keresési mező");
  [
    ["text", "Teljes szöveg"],
    ["subject", "Tárgy"],
    ["from", "Feladó"],
    ["to", "Címzett"],
    ["sent_after", "Kezdő dátum"],
    ["sent_before", "Záró dátum"],
    ["has_attachment", "Melléklet"],
    ["source", "Forrás útvonala"],
    ["folder", "Mappa"],
    ["attachment_type", "Melléklet típusa"],
    ["label", "Címke"],
  ].forEach(([value, label]) => field.add(new Option(label, value)));
  field.value = condition.field || "text";

  const value = document.createElement("input");
  value.setAttribute("aria-label", "Keresési érték");
  value.maxLength = 512;
  value.value = typeof condition.value === "string" ? condition.value : "";
  const updateValueControl = () => {
    value.type = ["sent_after", "sent_before"].includes(field.value) ? "date" : "search";
    value.placeholder = field.value === "has_attachment"
      ? "yes vagy no"
      : ["sent_after", "sent_before"].includes(field.value)
        ? ""
        : "Keresési érték";
    if (field.value === "has_attachment" && !["yes", "no"].includes(value.value)) {
      value.value = "yes";
    }
  };
  field.addEventListener("change", updateValueControl);
  updateValueControl();

  const remove = document.createElement("button");
  remove.type = "button";
  remove.className = "button quiet remove-condition";
  remove.textContent = "Eltávolítás";
  remove.setAttribute("aria-label", "Keresési feltétel eltávolítása");
  remove.addEventListener("click", () => {
    if (row.parentElement.children.length > 1) row.remove();
  });
  row.append(field, value, remove);
  return row;
}

/**
 * Create a condition group with its own AND/OR operator and removable rows.
 */
function createSearchGroup(group = { operator: "AND", conditions: [{ field: "text", value: "" }] }) {
  const container = document.createElement("section");
  container.className = "search-group";
  const heading = document.createElement("div");
  heading.className = "search-group-heading";
  const title = document.createElement("strong");
  title.textContent = "Feltételcsoport";

  const operator = document.createElement("select");
  operator.setAttribute("aria-label", "Csoporton belüli feltételek összekapcsolása");
  operator.add(new Option("ÉS", "AND"));
  operator.add(new Option("VAGY", "OR"));
  operator.value = group.operator === "OR" ? "OR" : "AND";

  const add = document.createElement("button");
  add.type = "button";
  add.className = "button quiet";
  add.textContent = "+ Feltétel";
  add.addEventListener("click", () => {
    if (
      conditions.children.length < 20
      && element("search-groups").querySelectorAll(".search-condition").length < 20
    ) {
      conditions.append(createSearchCondition());
    }
  });

  const remove = document.createElement("button");
  remove.type = "button";
  remove.className = "button quiet";
  remove.textContent = "Csoport eltávolítása";
  remove.addEventListener("click", () => {
    if (element("search-groups").children.length > 1) container.remove();
  });

  const conditions = document.createElement("div");
  conditions.className = "group-conditions";
  const rows = Array.isArray(group.conditions) && group.conditions.length
    ? group.conditions
    : [{ field: "text", value: "" }];
  rows.forEach((condition) => conditions.append(createSearchCondition(condition)));
  heading.append(title, operator, add, remove);
  container.append(heading, conditions);
  return container;
}

/**
 * Read the current condition builder into nested AND/OR search groups.
 */
function collectDetailedSearch() {
  return {
    has_attachment: element("attachment-only-toggle").getAttribute("aria-pressed") === "true",
    operator: element("detailed-search-operator").value,
    groups: [...element("search-groups").querySelectorAll(":scope > .search-group")].map((group) => ({
      operator: group.querySelector(".search-group-heading select").value,
      conditions: [...group.querySelectorAll(".search-condition")].map((row) => ({
        field: row.querySelector("select").value,
        value: row.querySelector("input").value.trim(),
      })),
    })),
  };
}

/**
 * Select known sender/recipient people and open the conversation workspace.
 */
function openConversationForMessage(message) {
  const addresses = new Set(
    String(message.participant_addresses || "")
      .split(",")
      .map((address) => address.trim().toLowerCase())
      .filter(Boolean),
  );
  const matchedPeople = state.people.filter((person) =>
    person.addresses.some((address) => addresses.has(address.toLowerCase())),
  );
  const selectedIds = new Set(matchedPeople.map((person) => String(person.id)));
  element("conversation-people").querySelectorAll("input[type=\"checkbox\"]").forEach((checkbox) => {
    checkbox.checked = selectedIds.has(checkbox.value);
  });
  navigate("beszelgetes");
  if (matchedPeople.length < 2) {
    element("conversation-notice").textContent =
      "A beszélgetés megnyitásához rendelje a feladót és legalább egy címzettet két külön személyhez.";
    element("conversation-notice").classList.add("error");
    return;
  }
  loadConversation();
}

/**
 * Render detailed-search hits with checkboxes and safe text nodes.
 */
function renderDetailedResults() {
  const results = element("detailed-results");
  results.replaceChildren();
  if (state.detailedResults.length === 0) {
    results.className = "detailed-results empty-state";
    results.textContent = "Nincs a feltételeknek megfelelő indexelt levél.";
  } else {
    results.className = "detailed-results";
    state.detailedResults.forEach((message) => {
      const row = document.createElement("div");
      row.className = "detailed-result-row";
      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.value = String(message.message_id);
      checkbox.setAttribute("aria-label", `${message.subject || "Levél"} kijelölése`);

      const open = document.createElement("button");
      open.type = "button";
      open.className = "detailed-result-open";
      const subject = document.createElement("strong");
      subject.textContent = message.subject || "(nincs tárgy)";
      const badge = attachmentBadge(message);
      // Before the subject, so an ellipsized long subject cannot hide it.
      if (badge) subject.prepend(badge);
      const meta = document.createElement("span");
      meta.textContent = `${message.sender || "Ismeretlen feladó"} · ${formatTimestamp(message.sent_timestamp)} · ${message.source_path}`;
      open.append(subject, meta);
      open.addEventListener("click", () => {
        navigate("archivum");
        openMessage(Number(message.message_id));
      });

      const labels = document.createElement("span");
      labels.className = "detailed-result-labels";
      labels.textContent = message.labels || "";
      const conversation = document.createElement("button");
      conversation.type = "button";
      conversation.className = "button quiet conversation-from-result";
      conversation.textContent = "Beszélgetés";
      conversation.title = "A feladó és a címzett személyeinek megnyitása a beszélgetésnézetben";
      conversation.addEventListener("click", () => openConversationForMessage(message));
      const actions = document.createElement("span");
      actions.className = "detailed-result-actions";
      if (Number(message.attachment_count) > 0) {
        const attachments = document.createElement("button");
        attachments.type = "button";
        attachments.className = "button quiet";
        attachments.textContent = "Mellékletek";
        attachments.title = "A levél mellékletei a melléklet-nézegetőben";
        attachments.addEventListener("click", () => openAttachmentViewer(Number(message.message_id)));
        actions.append(attachments);
      }
      actions.append(conversation);
      row.append(checkbox, open, labels, actions);
      results.append(row);
    });
  }
  const end = Math.min(state.detailedOffset + state.detailedResults.length, state.detailedTotal);
  const start = state.detailedTotal === 0 ? 0 : state.detailedOffset + 1;
  element("detailed-result-count").textContent =
    `· ${start.toLocaleString("hu-HU")}–${end.toLocaleString("hu-HU")} / ${state.detailedTotal.toLocaleString("hu-HU")}`;
  element("previous-search-page").disabled = state.detailedOffset === 0;
  element("next-search-page").disabled = end >= state.detailedTotal;
}

/**
 * Execute the current detailed search and update its result page.
 */
async function runDetailedSearch(offset = 0) {
  const query = collectDetailedSearch();
  try {
    const result = await requestJson("/api/search", {
      method: "POST",
      body: JSON.stringify({ ...query, limit: 50, offset }),
    });
    state.detailedSearch = query;
    state.detailedSearchRan = true;
    state.detailedResults = result.results;
    state.detailedTotal = result.total;
    state.detailedOffset = result.offset;
    renderDetailedResults();
    element("detailed-search-notice").textContent = "A keresés befejeződött.";
    element("detailed-search-notice").classList.remove("error");
  } catch (error) {
    element("detailed-search-notice").textContent = error.message;
    element("detailed-search-notice").classList.add("error");
  }
}

/**
 * Load saved searches and preserve each query as structured JSON data.
 */
async function refreshSavedSearches() {
  try {
    const saved = await requestJson("/api/searches");
    const select = element("saved-search-select");
    select.replaceChildren(new Option("Mentett keresés betöltése…", ""));
    saved.forEach((search) => select.add(new Option(search.name, search.name)));
    element("delete-search-button").disabled = true;
  } catch (error) {
    if (!error.message.includes("Előbb nyisson meg")) {
      element("detailed-search-notice").textContent = error.message;
      element("detailed-search-notice").classList.add("error");
    }
  }
}

/**
 * Apply or remove one label for all selected detailed-search results.
 */
async function updateSelectedLabels(enabled) {
  const messageIds = [...element("detailed-results").querySelectorAll("input:checked")]
    .map((checkbox) => Number(checkbox.value));
  const label = element("result-label").value.trim();
  if (!messageIds.length || !label) {
    element("detailed-search-notice").textContent = "Jelöljön ki leveleket és adjon meg egy címkét.";
    element("detailed-search-notice").classList.add("error");
    return;
  }
  try {
    const result = await requestJson("/api/labels", {
      method: "POST",
      body: JSON.stringify({ message_ids: messageIds, label, enabled }),
    });
    element("detailed-search-notice").textContent =
      `${result.label} címke frissítve (${result.occurrences_affected} forráspéldány).`;
    element("detailed-search-notice").classList.remove("error");
    await runDetailedSearch(state.detailedOffset);
    if (state.selectedMessageId) await openMessage(state.selectedMessageId);
  } catch (error) {
    element("detailed-search-notice").textContent = error.message;
    element("detailed-search-notice").classList.add("error");
  }
}

/**
 * Download a CSV or hash-manifested EML ZIP for the active detailed search.
 */
async function exportDetailedSearch(format) {
  try {
    const response = await fetch("/api/search/export", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...collectDetailedSearch(), format }),
    });
    if (!response.ok) {
      const payload = await response.json();
      throw new Error(payload.error || `HTTP ${response.status}`);
    }
    const disposition = response.headers.get("Content-Disposition") || "";
    const filename = disposition.match(/filename="([^"]+)"/)?.[1]
      || (format === "csv" ? "Mail-Scope-search.csv" : "Mail-Scope-search.eml.zip");
    const objectUrl = URL.createObjectURL(await response.blob());
    const download = document.createElement("a");
    download.href = objectUrl;
    download.download = filename;
    download.click();
    window.setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
    element("detailed-search-notice").textContent = `Export elkészült: ${filename}`;
    element("detailed-search-notice").classList.remove("error");
  } catch (error) {
    element("detailed-search-notice").textContent = error.message;
    element("detailed-search-notice").classList.add("error");
  }
}

/**
 * Prepare a complete, bounded search report for the browser's print-to-PDF dialog.
 */
async function printDetailedSearchReport() {
  const query = collectDetailedSearch();
  const report = element("search-print-report");
  const reportRows = [];
  try {
    const firstPage = await requestJson("/api/search", {
      method: "POST",
      body: JSON.stringify({ ...query, limit: 200, offset: 0 }),
    });
    if (firstPage.total > 2000) {
      throw new Error("A PDF-jelentés legfeljebb 2000 találatot tartalmazhat; szűkítse a keresést.");
    }
    reportRows.push(...firstPage.results);
    for (let offset = firstPage.results.length; offset < firstPage.total; offset += 200) {
      const page = await requestJson("/api/search", {
        method: "POST",
        body: JSON.stringify({ ...query, limit: 200, offset }),
      });
      reportRows.push(...page.results);
    }

    report.replaceChildren();
    const title = document.createElement("h1");
    title.textContent = "Mail-Scope · részletes keresési jelentés";
    const creator = document.createElement("p");
    creator.textContent = `Készítette: Bálint Szabolcs · Készült: ${new Date().toLocaleString("hu-HU")}`;
    const summary = document.createElement("p");
    const criteria = query.groups
      .map((group) => group.conditions
        .map((condition) => `${condition.field}: ${condition.value}`)
        .join(` ${group.operator} `))
      .join(` ${query.operator} `);
    summary.textContent =
      `Találatok: ${firstPage.total.toLocaleString("hu-HU")} · Feltétel: ${criteria}${query.has_attachment ? " · csak melléklettel" : ""}`;
    const table = document.createElement("table");
    const head = document.createElement("thead");
    const headerRow = document.createElement("tr");
    ["Dátum", "Tárgy", "Feladó", "Forrás", "Mappa", "Címkék"].forEach((heading) => {
      const cell = document.createElement("th");
      cell.textContent = heading;
      headerRow.append(cell);
    });
    head.append(headerRow);
    const body = document.createElement("tbody");
    reportRows.forEach((message) => {
      const row = document.createElement("tr");
      [
        Number.isFinite(message.sent_timestamp) ? formatTimestamp(message.sent_timestamp) : "",
        message.subject || "(nincs tárgy)",
        message.sender || "",
        message.source_path || "",
        [message.folder_path, message.folder_name].filter(Boolean).join("/"),
        message.labels || "",
      ].forEach((value) => {
        const cell = document.createElement("td");
        cell.textContent = String(value);
        row.append(cell);
      });
      body.append(row);
    });
    table.append(head, body);
    report.append(title, creator, summary, table);
    report.setAttribute("aria-hidden", "false");
    document.body.classList.add("printing-search-report");
    window.addEventListener("afterprint", () => {
      document.body.classList.remove("printing-search-report");
      report.setAttribute("aria-hidden", "true");
    }, { once: true });
    window.print();
  } catch (error) {
    element("detailed-search-notice").textContent = error.message;
    element("detailed-search-notice").classList.add("error");
  }
}

/**
 * Update source lists, case identity, and active indexing progress.
 */
function renderStatus(status) {
  if (status.version) {
    element("app-version").textContent = status.version;
    element("welcome-version").textContent = status.version;
  }
  state.caseOpen = Boolean(status.case);
  setWelcomeVisible(!state.caseOpen);
  updateNavigationAvailability();
  showRoute();
  document.body.classList.remove("booting");
  element("nav-indexing").hidden = !(status.job && status.job.running);
  element("case-label").textContent = status.case
    ? `${status.case.name} · ${status.case.path}`
    : "Nincs megnyitott ügy";
  element("message-count").textContent = status.message_count.toLocaleString("hu-HU");
  state.sources = status.sources || [];

  const sourceList = element("registered-sources");
  sourceList.replaceChildren();
  if (state.sources.length === 0) {
    sourceList.className = "source-list empty-state";
    sourceList.textContent = status.case ? "Még nincs indexelt forrás ebben az ügyben." : "Előbb nyisson meg egy ügyet.";
  } else {
    sourceList.className = "source-list";
    state.sources.forEach((source) => sourceList.append(sourceRow(source)));
  }
  renderSourceFilters();

  const job = status.job;
  element("cancel-button").classList.toggle("hidden", !job || !job.running);
  const progress = element("job-progress");
  progress.classList.toggle("hidden", !job);
  if (job) {
    const current = job.progress;
    if (job.running) {
      progress.textContent = current
        ? `Indexelés: ${current.source_number}/${current.source_total} forrás · ${current.messages_scanned} levél vizsgálva · ${current.subject || current.status}`
        : "Az indexelési feladat elindult…";
    } else if (job.error) {
      progress.textContent = `Az indexelés hibával leállt: ${job.error}`;
    } else if (job.summary) {
      const statusNames = {
        completed: "befejeződött",
        completed_with_errors: "befejeződött, de volt hibás forrás",
        cancelled: "megszakítva",
        source_changed: "leállt: egy forrás megváltozott",
      };
      const failed = job.summary.sources_failed
        ? ` ${job.summary.sources_failed} forrás hibás, részletek a forráslistában és a naplóban${status.log && status.log.case ? ` (${status.log.case})` : ""}.`
        : "";
      progress.textContent = `Indexelés ${statusNames[job.summary.status] || job.summary.status}: ${job.summary.messages_indexed} új levél, ${job.summary.messages_skipped} kihagyva.${failed}`;
    }
  }
  renderVerificationSetting(status);
  const verifying = state.sources.some((source) => source.verification && source.verification.running);
  if (((job && job.running) || verifying) && !state.pollTimer) {
    state.pollTimer = window.setTimeout(async () => {
      state.pollTimer = null;
      await refreshStatus();
    }, 1200);
  }
  if (job && !job.running && job.summary) {
    const summaryKey = JSON.stringify(job.summary);
    if (state.lastPeopleRefresh !== summaryKey) {
      state.lastPeopleRefresh = summaryKey;
      refreshFolders();
      refreshPeople();
      refreshArchive();
    }
  }
}

/** Measured full-hash throughput on a typical local disk, used for estimates. */
const HASH_BYTES_PER_SECOND = 140 * 1024 * 1024;

/**
 * Show the case's verification decision, ask for it while it is missing,
 * and estimate what strict mode costs for the largest source.
 */
function renderVerificationSetting(status) {
  const section = element("verification-setting");
  const mode = status.verification_mode;
  const undecided = Boolean(status.case) && !mode;
  section.classList.toggle("undecided", undecided);
  element("verification-prompt").hidden = !undecided;
  element("verification-decision").textContent = mode === "full"
    ? "· döntés: szigorú (teljes SHA-256 minden megnyitás előtt)"
    : mode === "signature"
      ? "· döntés: gyors (aláírás-ellenőrzés)"
      : "· még nincs döntés (addig gyors mód)";
  if (!state.verificationDirty) {
    document.querySelectorAll('input[name="verification-mode"]').forEach((radio) => {
      radio.checked = radio.value === (mode || "");
    });
  }
  const largest = state.sources.reduce((max, source) => Math.max(max, source.size || 0), 0);
  const seconds = largest / HASH_BYTES_PER_SECOND;
  const duration = seconds < 1
    ? "1 másodpercnél rövidebb"
    : seconds < 90
      ? `kb. ${Math.round(seconds)} másodperc`
      : `kb. ${Math.round(seconds / 60).toLocaleString("hu-HU")} perc`;
  element("verification-estimate").textContent = largest
    ? `Ebben az ügyben a legnagyobb forrás ${formatBytes(largest)}; ennek teljes ellenőrzése egy átlagos lemezen ${duration} minden megnyitáskor.`
    : "";
}

document.querySelectorAll('input[name="verification-mode"]').forEach((radio) => {
  radio.addEventListener("change", () => { state.verificationDirty = true; });
});
element("verification-save").addEventListener("click", async () => {
  const selected = document.querySelector('input[name="verification-mode"]:checked');
  if (!selected) {
    showNotice("Válassza ki az egyik ellenőrzési módot.", true);
    return;
  }
  if (selected.value === "full" && !window.confirm(
    "Szigorú módban minden levél megnyitása előtt a teljes forrásfájl SHA-256-ja újraszámolódik. "
    + "Nagy PST/OST esetén ez megnyitásonként akár percekig tarthat. Bekapcsolja?",
  )) {
    return;
  }
  try {
    state.verificationDirty = false;
    renderStatus(await requestJson("/api/settings/verification", {
      method: "POST",
      body: JSON.stringify({ mode: selected.value }),
    }));
    showNotice(selected.value === "full"
      ? "Szigorú ellenőrzés bekapcsolva: minden megnyitás előtt teljes SHA-256 készül."
      : "Gyors ellenőrzés beállítva: megnyitáskor aláírás-ellenőrzés, teljes SHA-256 a megadott esetekben.");
  } catch (error) {
    showNotice(error.message, true);
  }
});

/**
 * Refresh case state and the archive after a local API operation.
 */
async function refreshStatus() {
  try {
    const status = await requestJson("/api/status");
    renderStatus(status);
    return status;
  } catch (error) {
    if (document.body.classList.contains("booting")) {
      // Without a status the workspace still shows, so the error is visible.
      document.body.classList.remove("booting");
      setWelcomeVisible(false);
      updateNavigationAvailability();
      showRoute();
    }
    showNotice(error.message, true);
    return null;
  }
}

/**
 * Rebuild archive source and folder selectors from the active case.
 */
function renderSourceFilters() {
  const sourceSelect = element("source-filter");
  const previousSource = sourceSelect.value;
  sourceSelect.replaceChildren(new Option("Minden forrás", ""));
  state.sources.forEach((source) => {
    const option = new Option(source.path, String(source.id));
    sourceSelect.add(option);
  });
  if ([...sourceSelect.options].some((option) => option.value === previousSource)) {
    sourceSelect.value = previousSource;
  }

  const folderSelect = element("folder-filter");
  const previousFolder = folderSelect.value;
  folderSelect.replaceChildren(new Option("Minden mappa", ""));
  state.folders.forEach((folder) => {
    const option = new Option(`${folder.name} · ${folder.source_path}`, String(folder.id));
    folderSelect.add(option);
  });
  if ([...folderSelect.options].some((option) => option.value === previousFolder)) {
    folderSelect.value = previousFolder;
  }
}

/**
 * Load all original folder mappings for the selected case.
 */
async function refreshFolders() {
  try {
    const folders = await requestJson("/api/folders");
    state.folders = folders;
    renderSourceFilters();
  } catch (error) {
    if (!error.message.includes("Előbb nyisson meg")) showNotice(error.message, true);
  }
}

/**
 * Load curated people and observed addresses for the active case.
 */
async function refreshPeople() {
  try {
    const result = await requestJson("/api/people");
    state.people = result.people;
    renderPeople(result.observed_addresses);
  } catch (error) {
    if (!error.message.includes("Előbb nyisson meg")) {
      element("people-notice").textContent = error.message;
    }
  }
}

/**
 * Render the people list, the person editor, conversation choices and addresses.
 */
function renderPeople(observedAddresses) {
  state.observedAddresses = observedAddresses;
  if (!state.people.some((person) => person.id === state.selectedPersonId)) {
    state.selectedPersonId = null;
  }
  renderPeopleList();
  renderPersonEditor();
  renderConversationPeople();
  renderObservedAddresses();
}

/**
 * True when a person's name or any of their addresses contains the filter text.
 */
function personMatches(person, filter) {
  if (!filter) return true;
  const needle = filter.toLocaleLowerCase();
  return person.display_name.toLocaleLowerCase().includes(needle)
    || person.addresses.some((address) => address.includes(needle));
}

/**
 * List people as compact, selectable rows filtered by name or address.
 */
function renderPeopleList() {
  const list = element("people-list");
  list.replaceChildren();
  const filter = element("people-filter").value.trim();
  const visible = state.people.filter((person) => personMatches(person, filter));
  const automatic = state.people.filter((person) => person.origin === "auto").length;
  element("people-count").textContent =
    `· ${state.people.length.toLocaleString("hu-HU")} (${automatic.toLocaleString("hu-HU")} automatikus)`;
  if (visible.length === 0) {
    list.className = "people-list empty-state";
    list.textContent = state.people.length
      ? "Nincs a szűrésnek megfelelő személy."
      : "Még nincs személy. Indexelés után a címek automatikusan személyekhez kerülnek.";
    return;
  }
  list.className = "people-list";
  visible.slice(0, 500).forEach((person) => {
    const row = document.createElement("button");
    row.type = "button";
    row.className = "person-row";
    row.classList.toggle("selected", person.id === state.selectedPersonId);
    row.setAttribute("aria-pressed", String(person.id === state.selectedPersonId));
    const name = document.createElement("strong");
    name.textContent = person.display_name;
    const badge = document.createElement("span");
    badge.className = `origin-badge ${person.origin}`;
    badge.textContent = person.origin === "auto" ? "automatikus" : "kézi";
    const addresses = document.createElement("small");
    addresses.textContent = person.addresses.join(", ") || "Nincs hozzárendelt e-mail-cím";
    row.append(name, badge, addresses);
    row.addEventListener("click", () => {
      state.selectedPersonId = person.id;
      renderPeopleList();
      renderPersonEditor();
    });
    list.append(row);
  });
  if (visible.length > 500) {
    const more = document.createElement("p");
    more.className = "muted";
    more.textContent = `További ${visible.length - 500} személy – szűkítse a szűrést.`;
    list.append(more);
  }
}

/**
 * Show the editor of the selected person with all alias and merge actions.
 */
function renderPersonEditor() {
  const person = state.people.find((item) => item.id === state.selectedPersonId);
  element("person-editor-empty").hidden = Boolean(person);
  element("person-editor-body").hidden = !person;
  if (!person) return;
  element("person-edit-name").value = person.display_name;
  const badge = element("person-origin");
  badge.className = `origin-badge ${person.origin}`;
  badge.textContent = person.origin === "auto" ? "automatikus" : "kézi";

  const addressList = element("person-edit-addresses");
  addressList.replaceChildren();
  person.addresses.forEach((address) => {
    const row = document.createElement("div");
    row.className = "person-address";
    const text = document.createElement("span");
    text.textContent = address;
    const split = document.createElement("button");
    split.type = "button";
    split.className = "button quiet";
    split.textContent = "Leválasztás új személybe";
    split.disabled = person.addresses.length < 2;
    split.addEventListener("click", () => editPerson(person.id, "split", { address }, "A cím új személybe került."));
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "button quiet";
    remove.textContent = "Eltávolítás";
    remove.title = "A cím személy nélkül marad, és az automatika sem rendeli újra.";
    remove.addEventListener("click", () => editPerson(person.id, "remove-address", { address }, "A cím eltávolítva."));
    row.append(text, split, remove);
    addressList.append(row);
  });

  const addressOptions = element("person-address-options");
  addressOptions.replaceChildren();
  state.observedAddresses
    .filter((item) => !person.addresses.includes(item.address))
    .forEach((item) => {
      const option = document.createElement("option");
      option.value = item.address;
      option.label = item.person_name ? `jelenleg: ${item.person_name}` : (item.display_name || "személy nélkül");
      addressOptions.append(option);
    });
  const mergeOptions = element("person-merge-options");
  mergeOptions.replaceChildren();
  state.people
    .filter((item) => item.id !== person.id)
    .forEach((item) => {
      const option = document.createElement("option");
      option.value = `${item.display_name} (#${item.id})`;
      option.label = item.addresses.join(", ");
      mergeOptions.append(option);
    });
}

/**
 * Send one person edit to the server and refresh people everywhere.
 */
async function editPerson(personId, action, payload, successMessage) {
  const notice = element("people-notice");
  try {
    const path = personId === null ? "/api/people/auto" : `/api/people/${personId}/${action}`;
    const result = await requestJson(path, { method: "POST", body: JSON.stringify(payload) });
    state.people = result.people;
    renderPeople(result.observed_addresses);
    notice.textContent = successMessage;
    notice.classList.remove("error");
  } catch (error) {
    notice.textContent = error.message;
    notice.classList.add("error");
  }
}

/**
 * Offer people as conversation participants, keeping earlier choices.
 */
function renderConversationPeople() {
  const conversationPeople = element("conversation-people");
  const checked = new Set(
    [...conversationPeople.querySelectorAll("input:checked")].map((checkbox) => checkbox.value),
  );
  conversationPeople.replaceChildren();
  if (state.people.length === 0) {
    conversationPeople.className = "conversation-people empty-state";
    conversationPeople.textContent = "A beszélgetéshez legalább két személy kell; indexelés után automatikusan létrejönnek.";
    return;
  }
  conversationPeople.className = "conversation-people";
  state.people.forEach((person) => {
    const label = document.createElement("label");
    label.className = "conversation-person";
    label.dataset.search = `${person.display_name} ${person.addresses.join(" ")}`.toLocaleLowerCase();
    label.title = person.addresses.join(", ");
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.value = String(person.id);
    checkbox.dataset.personName = person.display_name;
    checkbox.checked = checked.has(checkbox.value);
    label.append(checkbox, document.createTextNode(person.display_name));
    conversationPeople.append(label);
  });
  filterConversationPeople();
}

/**
 * Hide conversation participants that do not match the filter; checked ones stay.
 */
function filterConversationPeople() {
  const needle = element("conversation-people-filter").value.trim().toLocaleLowerCase();
  element("conversation-people").querySelectorAll(".conversation-person").forEach((label) => {
    const checked = label.querySelector("input").checked;
    label.hidden = Boolean(needle) && !checked && !label.dataset.search.includes(needle);
  });
}

/**
 * List observed addresses for a hand-made person; automatic ones can be taken over.
 */
function renderObservedAddresses() {
  const observedAddresses = state.observedAddresses || [];
  const addressList = element("observed-addresses");
  addressList.replaceChildren();
  if (observedAddresses.length === 0) {
    addressList.className = "observed-addresses empty-state";
    addressList.textContent = "Az indexelés után az e-mail-címek itt jelennek meg.";
    return;
  }
  addressList.className = "observed-addresses";
  observedAddresses.forEach((address) => {
    const label = document.createElement("label");
    label.className = "observed-row";
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.value = address.address;
    // Addresses of automatic people can be taken over; hand-made ones stay.
    checkbox.disabled = address.person_id !== null && address.person_origin !== "auto";
    const details = document.createElement("span");
    details.append(document.createTextNode(address.address));
    if (address.person_name || address.display_name) {
      const note = document.createElement("small");
      note.textContent = address.person_name
        ? `${address.person_origin === "auto" ? "Automatikusan" : "Kézzel"}: ${address.person_name}`
        : `Megjelenő név: ${address.display_name}`;
      details.append(note);
    }
    label.append(checkbox, details);
    addressList.append(label);
  });
}

element("people-filter").addEventListener("input", renderPeopleList);
element("conversation-people-filter").addEventListener("input", filterConversationPeople);
element("people-auto-button").addEventListener("click", () => {
  editPerson(null, "auto", {}, "Az automatikus csoportosítás lefutott.");
});
element("person-rename-button").addEventListener("click", () => {
  editPerson(state.selectedPersonId, "rename", { display_name: element("person-edit-name").value.trim() }, "Átnevezve.");
});
element("person-edit-name").addEventListener("keydown", (event) => {
  if (event.key === "Enter") element("person-rename-button").click();
});
element("person-add-button").addEventListener("click", () => {
  const address = element("person-add-address").value.trim().toLocaleLowerCase();
  if (!address) return;
  element("person-add-address").value = "";
  editPerson(state.selectedPersonId, "addresses", { address }, "A cím hozzáadva.");
});
element("person-merge-button").addEventListener("click", () => {
  const match = element("person-merge-source").value.match(/#(\d+)\)\s*$/);
  if (!match) {
    element("people-notice").textContent = "Válassza ki a beolvasztandó személyt a listából.";
    element("people-notice").classList.add("error");
    return;
  }
  element("person-merge-source").value = "";
  editPerson(state.selectedPersonId, "merge", { source_id: Number(match[1]) }, "A két személy összevonva.");
});
element("person-delete-button").addEventListener("click", () => {
  const person = state.people.find((item) => item.id === state.selectedPersonId);
  if (!person || !window.confirm(`Törli a(z) „${person.display_name}” személyt? A címei személy nélkül maradnak, és az automatika sem rendeli őket újra.`)) {
    return;
  }
  editPerson(person.id, "delete", {}, "A személy törölve.");
});

/**
 * Collect current archive filters and fetch one page of indexed occurrences.
 */
async function refreshArchive(append = false) {
  const params = new URLSearchParams();
  const query = element("archive-query").value.trim();
  const sourceId = element("source-filter").value;
  const folderId = element("folder-filter").value;
  const itemType = element("type-filter").value;
  if (query) params.set("q", query);
  if (sourceId) params.set("source_id", sourceId);
  if (folderId) params.set("folder_id", folderId);
  if (itemType) params.set("item_type", itemType);
  if (element("attachment-filter").checked) params.set("has_attachments", "1");
  if (element("deleted-filter").checked) params.set("deleted_only", "1");
  if (element("duplicates-filter").checked) params.set("include_duplicates", "1");
  params.set("limit", "100");
  params.set("offset", String(append ? state.offset : 0));
  try {
    const result = await requestJson(`/api/archive?${params.toString()}`);
    state.messages = append ? [...state.messages, ...result.messages] : result.messages;
    state.offset = state.messages.length;
    renderMessages();
    element("archive-result-count").textContent = `· ${state.messages.length.toLocaleString("hu-HU")}`;
    element("load-more-button").classList.toggle("hidden", result.messages.length < 100);
    element("archive-notice").textContent = "";
    element("archive-notice").classList.remove("error");
  } catch (error) {
    element("archive-notice").textContent = error.message;
    element("archive-notice").classList.add("error");
  }
}

/**
 * Display the current indexed result set with safe DOM text rendering.
 */
function renderMessages() {
  const list = element("message-list");
  list.replaceChildren();
  if (state.messages.length === 0) {
    list.className = "message-list empty-state";
    list.textContent = "Nincs a feltételeknek megfelelő indexelt levél.";
    return;
  }
  list.className = "message-list";
  state.messages.forEach((message) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "message-row";
    if (Number(message.message_id) === state.selectedMessageId) button.classList.add("selected");
    button.addEventListener("click", () => openMessage(Number(message.message_id)));

    const subject = document.createElement("div");
    subject.className = "message-subject";
    subject.textContent = message.subject || "(nincs tárgy)";
    const badge = attachmentBadge(message);
    if (badge) subject.prepend(badge);
    const meta = document.createElement("div");
    meta.className = "message-meta";
    const sender = document.createElement("span");
    sender.className = "message-sender";
    sender.textContent = message.sender || "Ismeretlen feladó";
    const date = document.createElement("time");
    date.textContent = formatTimestamp(message.sent_timestamp);
    if (Number.isFinite(message.sent_timestamp)) {
      date.dateTime = new Date(message.sent_timestamp * 1000).toISOString();
      date.title = message.sent_date || "";
    }
    meta.append(sender, date);
    const folder = document.createElement("div");
    folder.className = "message-folder";
    folder.textContent = `${message.folder_path || "—"} · ${message.source_path}`;
    button.append(subject, meta, folder);
    list.append(button);
  });
}

/**
 * Load the chosen message from its unchanged original source and render evidence.
 */
async function openMessage(messageId) {
  state.selectedMessageId = messageId;
  renderMessages();
  const pane = element("reader-pane");
  pane.textContent = "A levél betöltése és a forrás SHA-256 ellenőrzése…";
  try {
    const result = await requestJson(`/api/messages/${messageId}`);
    renderMessageDetail(result);
  } catch (error) {
    pane.replaceChildren();
    const message = document.createElement("p");
    message.className = "notice error";
    message.textContent = error.message;
    pane.append(message);
  }
}

/**
 * Render original message headers, plain text, attachments, labels, and locators.
 */
function renderMessageDetail(detail) {
  const pane = element("reader-pane");
  pane.replaceChildren();
  const top = document.createElement("div");
  top.className = "reader-top";
  const title = document.createElement("h3");
  title.textContent = detail.headers.subject || detail.message.subject || "(nincs tárgy)";
  const conversation = document.createElement("button");
  conversation.type = "button";
  conversation.className = "button quiet reader-conversation";
  conversation.textContent = "Teljes beszélgetés";
  conversation.title = "A feladó és a címzettek összes levele a beszélgetésnézetben";
  conversation.addEventListener("click", () => openConversationForMessage({
    participant_addresses: detail.participants
      .filter((participant) => ["from", "to"].includes(participant.role))
      .map((participant) => participant.address)
      .join(","),
  }));
  top.append(title, conversation);
  ["from", "to", "cc", "bcc", "date", "message-id"].forEach((name) => {
    if (!detail.headers[name]) return;
    const row = document.createElement("div");
    row.className = "header-row";
    const label = document.createElement("strong");
    label.textContent = name.toUpperCase();
    const value = document.createElement("span");
    // The original Date header stays unchanged in the forensic block below.
    value.textContent = name === "date" && Number.isFinite(detail.message.sent_timestamp)
      ? formatTimestamp(detail.message.sent_timestamp)
      : detail.headers[name];
    row.append(label, value);
    top.append(row);
  });
  pane.append(top);

  const body = document.createElement("div");
  body.className = "reader-body";
  body.textContent = detail.body || "(Nincs megjeleníthető levéltörzs.)";
  pane.append(body);
  if (detail.attachments.length) {
    const section = document.createElement("section");
    section.className = "reader-section";
    const heading = document.createElement("h4");
    heading.textContent = "MELLÉKLETEK";
    section.append(heading);
    detail.attachments.forEach((file) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "badge attachment-link";
      button.textContent = `${file.filename} · ${file.content_type || "ismeretlen típus"}`;
      button.title = "Megnyitás a melléklet-nézegetőben";
      button.addEventListener("click", () => openAttachmentViewer(Number(detail.message.id), { name: file.filename }));
      section.append(button);
    });
    pane.append(section);
  }
  appendBadgeSection(pane, "CÍMKÉK", detail.labels);

  const forensic = document.createElement("section");
  forensic.className = "reader-section";
  const heading = document.createElement("h4");
  heading.textContent = "Forenzikus adatok";
  forensic.append(heading);
  detail.occurrences.forEach((occurrence) => {
    const block = document.createElement("div");
    block.className = "forensic-row";
    const lines = [
      ["Forrás", occurrence.source_path],
      ["Forrásmappa", occurrence.folder_path || "—"],
      ["Lokátor", `eltolás ${occurrence.locator_offset}, hossz ${occurrence.locator_length}, elem ${occurrence.locator_item_id || occurrence.locator_item_index}`],
      ["Message-ID", detail.message.internet_message_id || "—"],
      ["Date fejléc (eredeti)", detail.headers.date || "—"],
      ["Levél SHA-256", occurrence.message_hash],
      ["Forrás SHA-256", occurrence.discovered_sha256],
      ["Példány", `${occurrence.id}${occurrence.id === detail.opened_from_occurrence ? " · ebből nyílt meg" : ""}`],
    ];
    lines.forEach(([label, value]) => {
      const line = document.createElement("div");
      const strong = document.createElement("strong");
      strong.textContent = `${label}: `;
      line.append(strong, document.createTextNode(value || "—"));
      block.append(line);
    });
    forensic.append(block);
  });
  pane.append(forensic);
}

/**
 * Append a compact section of attachment or label badges when values exist.
 */
function appendBadgeSection(parent, title, values) {
  if (values.length === 0) return;
  const section = document.createElement("section");
  section.className = "reader-section";
  const heading = document.createElement("h4");
  heading.textContent = title;
  section.append(heading);
  values.forEach((value) => {
    const badge = document.createElement("span");
    badge.className = "badge";
    badge.textContent = value;
    section.append(badge);
  });
  parent.append(section);
}

/**
 * Build the selected people, mode, date, thread, and text search parameters.
 */
function conversationParams() {
  const personIds = [...element("conversation-people").querySelectorAll("input:checked")]
    .map((checkbox) => checkbox.value);
  const params = new URLSearchParams();
  params.set("people", personIds.join(","));
  params.set("mode", element("conversation-mode").value);
  params.set("include_cc_bcc", element("conversation-cc-bcc").checked ? "1" : "0");
  params.set("date_from", element("conversation-date-from").value);
  params.set("date_to", element("conversation-date-to").value);
  params.set("q", element("conversation-query").value.trim());
  params.set("thread_key", element("conversation-thread").value);
  return params;
}

/**
 * Load the selected people conversation and update its topic selector.
 */
async function loadConversation() {
  const params = conversationParams();
  if (params.get("people").split(",").filter(Boolean).length < 2) {
    element("conversation-notice").textContent = "Válasszon legalább két személyt.";
    element("conversation-notice").classList.add("error");
    return;
  }
  element("conversation-notice").textContent = "";
  element("conversation-notice").classList.remove("error");
  const button = element("conversation-run-button");
  button.disabled = true;
  try {
    state.conversation = await requestJson(`/api/conversation?${params.toString()}`);
    renderConversation(state.conversation);
    const threadSelect = element("conversation-thread");
    const selectedThread = threadSelect.value;
    threadSelect.replaceChildren(new Option("Minden téma", ""));
    state.conversation.threads.forEach((thread) => {
      threadSelect.add(new Option(thread.subject, thread.key));
    });
    if ([...threadSelect.options].some((option) => option.value === selectedThread)) {
      threadSelect.value = selectedThread;
    }
    const count = state.conversation.messages.length;
    element("conversation-notice").textContent = `${count.toLocaleString("hu-HU")} levél · ${state.conversation.threads.length} téma`;
    element("conversation-notice").classList.remove("error");
  } catch (error) {
    element("conversation-notice").textContent = error.message;
    element("conversation-notice").classList.add("error");
    state.conversation = null;
  } finally {
    button.disabled = false;
  }
}

/**
 * Render month activity links and a date-grouped, person-colored timeline.
 */
function renderConversation(result) {
  const container = element("conversation-results");
  container.replaceChildren();
  if (result.messages.length === 0) {
    container.className = "conversation-results empty-state";
    container.textContent = "Nincs a feltételeknek megfelelő levél.";
    return;
  }
  container.className = "conversation-results";
  const summary = document.createElement("div");
  summary.className = "conversation-summary";
  summary.textContent = `${result.messages.length.toLocaleString("hu-HU")} levél · ${result.people.map((person) => person.display_name).join(" · ")}`;
  container.append(summary);

  // Days, silences and months follow the computer's local time, like the
  // displayed times; the server's UTC grouping would split local days.
  const months = new Map();
  result.messages.forEach((message) => {
    message.localSent = message.sent_at ? new Date(message.sent_at) : null;
    message.localDay = message.localSent ? localDay(message.localSent) : null;
    if (message.localDay) {
      const month = message.localDay.slice(0, 7);
      months.set(month, (months.get(month) || 0) + 1);
    }
  });

  const activity = document.createElement("nav");
  activity.className = "activity-strip";
  activity.setAttribute("aria-label", "Havi levélaktivitás");
  [...months.entries()].sort().map(([month, count]) => ({ month, count })).forEach((month) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "activity-month";
    button.dataset.month = month.month;
    const label = document.createElement("span");
    label.textContent = formatMonth(month.month);
    const count = document.createElement("strong");
    count.textContent = String(month.count);
    button.append(label, count);
    button.addEventListener("click", () => {
      container.querySelector(`[data-day^="${month.month}"]`)?.scrollIntoView({ behavior: "smooth", block: "start" });
    });
    activity.append(button);
  });
  container.append(activity);

  const timeline = document.createElement("div");
  timeline.className = "timeline";
  const palette = ["#7ce0a4", "#77b7f3", "#eea37b", "#c5a3f0", "#eed576", "#e98fbd", "#71d6d2", "#b7c47b"];
  const personColors = new Map(result.people.map((person, index) => [person.id, palette[index % palette.length]]));
  let currentDay = "";
  result.messages.forEach((message) => {
    if (message.localDay && message.localDay !== currentDay) {
      const silentDays = currentDay
        ? Math.round((Date.parse(`${message.localDay}T12:00:00Z`) - Date.parse(`${currentDay}T12:00:00Z`)) / 86400000) - 1
        : 0;
      currentDay = message.localDay;
      const heading = document.createElement("h3");
      heading.className = "timeline-day";
      heading.dataset.day = message.localDay;
      heading.textContent = message.localDay;
      timeline.append(heading);
      if (silentDays > 0) {
        const gap = document.createElement("div");
        gap.className = "silence-gap";
        gap.textContent = `${silentDays} nap csend`;
        timeline.append(gap);
      }
    }
    timeline.append(conversationCard(message, result.people, personColors));
  });
  container.append(timeline);
}

/**
 * Render one timeline entry without inserting untrusted message content as HTML.
 */
function conversationCard(message, people, personColors) {
  const card = document.createElement("article");
  card.className = "timeline-card";
  card.id = `conversation-message-${message.message_id}`;
  const sender = message.participants.find((participant) => participant.role === "from");
  const senderIndex = people.findIndex((person) => person.id === sender?.person_id);
  card.classList.add(senderIndex >= 0 && senderIndex % 2 === 1 ? "side-right" : "side-left");
  card.style.setProperty(
    "--speaker-color",
    personColors.get(sender?.person_id) || "#7ce0a4",
  );

  const subject = document.createElement("div");
  subject.className = "message-subject";
  appendHighlightedText(subject, message.subject || "(nincs tárgy)", element("conversation-query").value.trim());
  const badge = attachmentBadge(message);
  if (badge) {
    const open = document.createElement("button");
    open.type = "button";
    open.className = "attachment-open";
    open.title = `${badge.title} – megnyitás a melléklet-nézegetőben`;
    open.setAttribute("aria-label", `${badge.getAttribute("aria-label")} – megnyitás a melléklet-nézegetőben`);
    badge.removeAttribute("role");
    badge.removeAttribute("aria-label");
    open.append(badge);
    open.addEventListener("click", () => openAttachmentViewer(Number(message.message_id)));
    subject.append(open);
  }
  if (message.thread_key) {
    const thread = document.createElement("span");
    thread.className = "thread-chip";
    thread.textContent = message.thread_key;
    subject.append(thread);
  }
  const metadata = document.createElement("div");
  metadata.className = "timeline-meta";
  const senderName = document.createElement("span");
  senderName.textContent = sender
    ? `${sender.person_name} <${sender.address}>`
    : "Ismeretlen feladó";
  const time = document.createElement("time");
  time.textContent = message.localSent ? formatLocalDateTime(message.localSent) : "Dátum nélkül";
  if (message.localSent) {
    time.dateTime = message.sent_at;
    time.title = message.sent_date || "";
  }
  metadata.append(senderName, time);
  const otherParticipants = message.participants.filter((participant) =>
    participant.role !== "from"
    && (element("conversation-cc-bcc").checked || !["cc", "bcc"].includes(participant.role)),
  );
  const recipients = document.createElement("div");
  recipients.className = "timeline-addresses";
  recipients.textContent = otherParticipants.length
    ? otherParticipants.map((participant) => {
      const role = { to: "Címzett", cc: "CC", bcc: "BCC" }[participant.role] || participant.role;
      return `${role}: ${participant.person_name} <${participant.address}>`;
    }).join(" · ")
    : "A résztvevők nincsenek hozzárendelve.";

  const body = document.createElement("p");
  body.className = "timeline-body collapsed";
  appendHighlightedText(body, message.body || "(Nincs indexelt levéltörzs.)", element("conversation-query").value.trim());
  const expand = document.createElement("button");
  expand.type = "button";
  expand.className = "timeline-open";
  expand.textContent = "Teljes üzenet megjelenítése";
  expand.addEventListener("click", () => {
    const collapsed = body.classList.toggle("collapsed");
    expand.textContent = collapsed ? "Teljes üzenet megjelenítése" : "Üzenet összecsukása";
  });
  const openOriginal = document.createElement("button");
  openOriginal.type = "button";
  openOriginal.className = "timeline-open";
  openOriginal.textContent = "Eredeti levél megnyitása";
  openOriginal.addEventListener("click", () => {
    navigate("archivum");
    openMessage(Number(message.message_id));
  });
  card.append(subject, metadata, recipients, body, expand, openOriginal);
  return card;
}

/**
 * Highlight literal search text using text nodes and mark elements.
 */
function appendHighlightedText(parent, text, search) {
  if (!search) {
    parent.append(document.createTextNode(text));
    return;
  }
  const normalizedText = text.toLocaleLowerCase();
  const normalizedSearch = search.toLocaleLowerCase();
  let cursor = 0;
  let match = normalizedText.indexOf(normalizedSearch, cursor);
  while (match !== -1) {
    parent.append(document.createTextNode(text.slice(cursor, match)));
    const marker = document.createElement("mark");
    marker.textContent = text.slice(match, match + search.length);
    parent.append(marker);
    cursor = match + search.length;
    match = normalizedText.indexOf(normalizedSearch, cursor);
  }
  parent.append(document.createTextNode(text.slice(cursor)));
}

/**
 * Format YYYY-MM activity keys into a compact localized month label.
 */
function formatMonth(value) {
  const [year, month] = value.split("-").map(Number);
  return new Intl.DateTimeFormat("hu-HU", { year: "numeric", month: "short", timeZone: "UTC" })
    .format(new Date(Date.UTC(year, month - 1, 15)));
}

/**
 * Download an EML ZIP and its SHA-256 manifest for the active conversation.
 */
async function exportConversationEml() {
  if (!state.conversation) {
    element("conversation-notice").textContent = "Előbb töltsön be egy beszélgetést.";
    element("conversation-notice").classList.add("error");
    return;
  }
  try {
    const response = await fetch(`/api/conversation/export?${conversationParams().toString()}`);
    if (!response.ok) {
      const payload = await response.json();
      throw new Error(payload.error || `HTTP ${response.status}`);
    }
    const download = document.createElement("a");
    download.href = URL.createObjectURL(await response.blob());
    download.download = "Mail-Scope-conversation.eml.zip";
    download.click();
    URL.revokeObjectURL(download.href);
  } catch (error) {
    element("conversation-notice").textContent = error.message;
    element("conversation-notice").classList.add("error");
  }
}

/**
 * Discover sources under a user-selected file or directory.
 */
element("discover-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = element("discover-button");
  button.disabled = true;
  showNotice("");
  try {
    const result = await requestJson("/api/discover", {
      method: "POST",
      body: JSON.stringify({ path: element("source-path").value.trim() }),
    });
    state.discovered = result.sources;
    element("discovered-panel").classList.remove("hidden");
    element("discovered-count").textContent = `${result.sources.length} felismert fájl`;
    const list = element("discovered-list");
    list.replaceChildren();
    if (result.sources.length === 0) {
      list.className = "source-list empty-state";
      list.textContent = "A megadott útvonalon nem található támogatott levélforrás.";
    } else {
      list.className = "source-list";
      result.sources.forEach((source) => list.append(sourceRow(source, true)));
    }
    showNotice(`Felismerés kész: ${result.sources.length} forrás.`);
  } catch (error) {
    showNotice(error.message, true);
  } finally {
    button.disabled = false;
  }
});

element("case-browse-button").addEventListener("click", () => {
  browsePath("directory", "case-path", ["case-browse-button"]);
});
element("source-file-browse-button").addEventListener("click", () => {
  browsePath("file", "source-path", ["source-file-browse-button", "source-folder-browse-button"]);
});
element("source-folder-browse-button").addEventListener("click", () => {
  browsePath("directory", "source-path", ["source-file-browse-button", "source-folder-browse-button"]);
});

/**
 * Create or open the selected local case.
 */
element("case-form").addEventListener("submit", (event) => {
  event.preventDefault();
  openCase(element("case-path").value.trim(), "case-notice");
});

/**
 * Open or create a case, reset case-bound state, and show the workspace.
 */
async function openCase(path, noticeId) {
  const notice = element(noticeId);
  try {
    const status = await requestJson("/api/case", {
      method: "POST",
      body: JSON.stringify({ path }),
    });
    notice.textContent = "";
    notice.classList.remove("error");
    element("case-path").value = status.case ? status.case.path : path;
    state.verificationDirty = false;
    state.lastPeopleRefresh = "";
    state.people = [];
    state.folders = [];
    state.detailedResults = [];
    state.detailedTotal = 0;
    state.detailedOffset = 0;
    renderStatus(status);
    renderDetailedResults();
    await refreshFolders();
    await refreshPeople();
    await refreshSavedSearches();
    await refreshArchive();
    showNotice("Az ügy megnyitva. A források most már felismerhetők és indexelhetők.");
  } catch (error) {
    notice.textContent = error.message;
    notice.classList.add("error");
  }
}

/**
 * Start background indexing for the checked, previously discovered sources.
 */
element("index-button").addEventListener("click", async () => {
  const paths = [...element("discovered-list").querySelectorAll("input:checked")]
    .map((checkbox) => checkbox.value);
  try {
    state.lastPeopleRefresh = "";
    renderStatus(await requestJson("/api/index", {
      method: "POST",
      body: JSON.stringify({ paths }),
    }));
    showNotice("Az indexelés a háttérben elindult.");
  } catch (error) {
    showNotice(error.message, true);
  }
});

/**
 * Request resumable cancellation while keeping already indexed messages.
 */
element("cancel-button").addEventListener("click", async () => {
  try {
    renderStatus(await requestJson("/api/index/cancel", { method: "POST", body: "{}" }));
    showNotice("A megszakítási kérés elküldve; az eddig indexelt levelek megmaradnak.");
  } catch (error) {
    showNotice(error.message, true);
  }
});

/**
 * Apply archive search filters and reset to the first page.
 */
element("archive-filters").addEventListener("submit", (event) => {
  event.preventDefault();
  refreshArchive();
});

["source-filter", "folder-filter", "type-filter", "attachment-filter", "deleted-filter", "duplicates-filter"]
  .forEach((id) => element(id).addEventListener("change", () => refreshArchive()));
element("load-more-button").addEventListener("click", () => refreshArchive(true));

/**
 * Create one curated person from selected, previously observed email aliases.
 */
element("person-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const addresses = [...element("observed-addresses").querySelectorAll("input:checked")]
    .map((checkbox) => checkbox.value);
  if (addresses.length === 0) {
    element("people-notice").textContent = "Jelöljön ki legalább egy megfigyelt e-mail-címet.";
    element("people-notice").classList.add("error");
    return;
  }
  try {
    const person = await requestJson("/api/people", {
      method: "POST",
      body: JSON.stringify({
        display_name: element("person-name").value.trim(),
        addresses,
      }),
    });
    element("person-name").value = "";
    element("people-notice").textContent = `${person.display_name} személy és ${person.addresses.length} alias mentve.`;
    element("people-notice").classList.remove("error");
    await refreshPeople();
  } catch (error) {
    element("people-notice").textContent = error.message;
    element("people-notice").classList.add("error");
  }
});

/**
 * Load a conversation only after explicit participant selection and user action.
 */
element("conversation-run-button").addEventListener("click", loadConversation);
element("conversation-thread").addEventListener("change", loadConversation);
element("conversation-eml-button").addEventListener("click", exportConversationEml);
element("conversation-pdf-button").addEventListener("click", () => {
  if (!state.conversation) {
    element("conversation-notice").textContent = "Előbb töltsön be egy beszélgetést.";
    element("conversation-notice").classList.add("error");
    return;
  }
  window.print();
});

/**
 * Add, run, save, load, and paginate detailed-search condition groups.
 */
element("add-condition-button").addEventListener("click", () => {
  if (
    element("search-groups").children.length < 10
    && element("search-groups").querySelectorAll(".search-condition").length < 20
  ) {
    element("search-groups").append(createSearchGroup());
  }
});
element("run-detailed-search-button").addEventListener("click", () => runDetailedSearch());

/**
 * Switch the whole-query "only with attachments" filter on or off.
 */
function setAttachmentOnly(enabled) {
  const toggle = element("attachment-only-toggle");
  toggle.setAttribute("aria-pressed", String(enabled));
  toggle.classList.toggle("active", enabled);
}

element("attachment-only-toggle").addEventListener("click", () => {
  const enabled = element("attachment-only-toggle").getAttribute("aria-pressed") !== "true";
  setAttachmentOnly(enabled);
  // A toggle re-runs an existing search from its first page.
  if (state.detailedSearchRan) runDetailedSearch(0);
});
element("save-search-button").addEventListener("click", async () => {
  const query = collectDetailedSearch();
  try {
    const saved = await requestJson("/api/searches", {
      method: "POST",
      body: JSON.stringify({ ...query, name: element("saved-search-name").value.trim() }),
    });
    await refreshSavedSearches();
    element("saved-search-select").value = saved.name;
    element("delete-search-button").disabled = false;
    element("detailed-search-notice").textContent = `A(z) „${saved.name}” keresés mentve.`;
    element("detailed-search-notice").classList.remove("error");
  } catch (error) {
    element("detailed-search-notice").textContent = error.message;
    element("detailed-search-notice").classList.add("error");
  }
});
element("saved-search-select").addEventListener("change", async () => {
  const name = element("saved-search-select").value;
  element("delete-search-button").disabled = !name;
  if (!name) return;
  try {
    const saved = await requestJson("/api/searches");
    const selected = saved.find((item) => item.name === name);
    if (!selected) throw new Error("A mentett keresés nem található.");
    element("detailed-search-operator").value = selected.query.operator;
    const groups = selected.query.groups || [{
      operator: selected.query.operator || "AND",
      conditions: selected.query.conditions || [{ field: "text", value: "" }],
    }];
    element("search-groups").replaceChildren(
      ...groups.map((group) => createSearchGroup(group)),
    );
    setAttachmentOnly(selected.query.has_attachment === true);
    element("saved-search-name").value = selected.name;
    await runDetailedSearch();
  } catch (error) {
    element("detailed-search-notice").textContent = error.message;
    element("detailed-search-notice").classList.add("error");
  }
});
element("delete-search-button").addEventListener("click", async () => {
  const name = element("saved-search-select").value;
  if (!name) return;
  try {
    await requestJson(`/api/searches/${encodeURIComponent(name)}`, {
      method: "POST",
      body: JSON.stringify({}),
    });
    element("saved-search-name").value = "";
    await refreshSavedSearches();
    element("detailed-search-notice").textContent = `A(z) „${name}” mentett keresés törölve.`;
    element("detailed-search-notice").classList.remove("error");
  } catch (error) {
    element("detailed-search-notice").textContent = error.message;
    element("detailed-search-notice").classList.add("error");
  }
});
element("apply-label-button").addEventListener("click", () => updateSelectedLabels(true));
element("remove-label-button").addEventListener("click", () => updateSelectedLabels(false));
element("previous-search-page").addEventListener("click", () => {
  runDetailedSearch(Math.max(0, state.detailedOffset - 50));
});
element("next-search-page").addEventListener("click", () => {
  runDetailedSearch(state.detailedOffset + 50);
});
element("export-search-csv-button").addEventListener("click", () => exportDetailedSearch("csv"));
element("export-search-eml-button").addEventListener("click", () => exportDetailedSearch("eml_zip"));
element("export-search-pdf-button").addEventListener("click", printDetailedSearchReport);

/**
 * Open the attachment viewer for a message, at an index or a file name.
 */
function openAttachmentViewer(messageId, target = {}) {
  if (typeof target.path === "string") {
    state.viewerPreferredName = null;
    window.location.hash = `#/melleklet/${messageId}/${target.path}`;
    return;
  }
  let index = Number.isInteger(target.index) ? target.index : 0;
  state.viewerPreferredName = null;
  if (target.name) {
    // With this message's list already loaded the name resolves at once;
    // otherwise loadViewer resolves it after fetching the list.
    if (state.viewer && state.viewer.messageId === messageId) {
      index = Math.max(0, state.viewer.attachments.findIndex((item) => item.name === target.name));
    } else {
      state.viewerPreferredName = target.name;
    }
  }
  window.location.hash = `#/melleklet/${messageId}/${index}`;
}

/**
 * Load the attachment list of the viewed message, then the selected attachment.
 */
async function loadViewer() {
  const target = state.viewerTarget;
  if (!target) return;
  const notice = element("viewer-notice");
  notice.textContent = "";
  notice.classList.remove("error");
  try {
    if (!state.viewer || state.viewer.messageId !== target.messageId) {
      element("viewer-preview").replaceChildren(viewerMessage("A mellékletek betöltése és a forrás ellenőrzése…"));
      const listing = await requestJson(`/api/messages/${target.messageId}/attachments`);
      state.viewer = {
        messageId: target.messageId, ...listing, zoom: 100, page: 0,
        nodes: new Map(listing.attachments.map((item) => [item.path, item])),
        expansions: new Map(),
        expanded: new Set(),
      };
      if (state.viewerPreferredName) {
        const index = listing.attachments.findIndex((item) => item.name === state.viewerPreferredName);
        state.viewerPreferredName = null;
        if (index > 0) {
          window.history.replaceState(null, "", `#/melleklet/${target.messageId}/${index}`);
          state.viewerTarget = { messageId: target.messageId, path: String(index) };
        }
      }
    }
    const viewer = state.viewer;
    const summary = viewer.message;
    element("viewer-message-summary").textContent = [
      summary.subject || "(nincs tárgy)",
      `${(summary.from || []).join(", ") || "?"} → ${(summary.to || []).join(", ") || "?"}`,
      formatTimestamp(summary.sent_timestamp),
    ].join(" · ");
    if (viewer.attachments.length === 0) {
      element("attachment-viewer-title").textContent = "A levélnek nincs melléklete";
      element("viewer-preview").replaceChildren(viewerMessage("Ennek a levélnek nincs melléklete."));
      element("viewer-sheet").replaceChildren();
      renderViewerTree();
      return;
    }
    const path = state.viewerTarget.path;
    await ensureViewerPath(path);
    if (viewer.path !== path) {
      viewer.path = path;
      viewer.page = 0;
      viewer.sheet = 0;
      viewer.zoom = 100;
      viewer.tab = null;
      viewer.cell = null;
      viewer.showFormulas = false;
      viewer.pdfText = null;
      const selected = viewer.nodes.get(path);
      // Formats without a built-in view (DOC, RTF, PPT…) open in the exact view when LibreOffice is there.
      viewer.exactView = Boolean(selected && selected.exact && selected.view === "none" && viewer.exact_available);
    }
    renderViewerTree();
    const entry = viewer.nodes.get(path) || viewer.attachments[0];
    const siblings = viewerSiblings(entry.path);
    const position = siblings.findIndex((item) => item.path === entry.path);
    element("attachment-viewer-title").textContent = entry.name;
    element("viewer-position").textContent = `${position + 1} / ${siblings.length}`;
    element("viewer-prev").disabled = position <= 0;
    element("viewer-next").disabled = position >= siblings.length - 1;
    element("viewer-zoom").textContent = `${viewer.zoom}%`;
    if (viewer.exactView) element("viewer-preview").replaceChildren(viewerMessage(EXACT_LOADING_TEXT));
    const [info, preview] = await Promise.all([
      requestJson(`/api/attachments/${viewer.messageId}/${path}/info`),
      // For types without a preview the server only explains why; nothing is rendered.
      requestJson(viewerPreviewUrl()).catch((error) => ({ kind: "error", reason: error.message })),
    ]);
    viewer.preview = preview || { kind: "none", reason: "" };
    if (preview && preview.kind === "archive") registerViewerChildren(entry.path, preview);
    renderViewerTree();
    renderViewerSheet(info);
    renderViewerPreview(entry, info);
  } catch (error) {
    notice.textContent = error.message;
    notice.classList.add("error");
    element("viewer-preview").replaceChildren(viewerMessage(error.message, true));
  }
}

/**
 * A centred message card inside the preview area.
 */
function viewerMessage(text, isError = false) {
  const card = document.createElement("div");
  card.className = `viewer-message${isError ? " error" : ""}`;
  card.textContent = text;
  return card;
}

/**
 * Remember the files of an unpacked archive or attached e-mail in the tree.
 */
function registerViewerChildren(path, result) {
  const viewer = state.viewer;
  viewer.expansions.set(path, result);
  viewer.expanded.add(path);
  result.entries.forEach((item) => viewer.nodes.set(item.path, item));
}

/**
 * Fetch (once) the files inside an archive or attached e-mail.
 */
async function fetchViewerChildren(path) {
  const viewer = state.viewer;
  if (!viewer.expansions.has(path)) {
    const result = await requestJson(`/api/attachments/${viewer.messageId}/${path}/children`);
    if (state.viewer !== viewer) return;
    registerViewerChildren(path, result);
  }
  viewer.expanded.add(path);
}

/**
 * Unpack every archive level above a nested path (e.g. opened from a link).
 */
async function ensureViewerPath(path) {
  const parts = path.split("/");
  for (let depth = 1; depth < parts.length; depth += 1) {
    await fetchViewerChildren(parts.slice(0, depth).join("/"));
  }
}

/**
 * The attachments on the same level as a path: the message's or a container's.
 */
function viewerSiblings(path) {
  const viewer = state.viewer;
  const parent = path.includes("/") ? path.slice(0, path.lastIndexOf("/")) : null;
  if (parent === null) return viewer.attachments;
  const expansion = viewer.expansions.get(parent);
  return expansion ? expansion.entries : [];
}

/**
 * List the message's attachments as a tree; archives and attached e-mails
 * open into branches, and risky files carry a warning marker.
 */
function renderViewerTree() {
  const viewer = state.viewer;
  const list = element("viewer-tree-list");
  list.replaceChildren();
  element("viewer-count").textContent = `(${viewer.attachments.length})`;
  const addLevel = (entries, depth) => {
    entries.forEach((entry) => {
      const row = document.createElement("div");
      row.className = "viewer-tree-row";
      row.style.setProperty("--depth", String(depth));
      if (entry.container && entry.unpacked !== false) {
        const toggle = document.createElement("button");
        toggle.type = "button";
        toggle.className = "viewer-tree-toggle";
        const open = viewer.expanded.has(entry.path);
        toggle.textContent = open ? "▾" : "▸";
        toggle.setAttribute("aria-expanded", String(open));
        toggle.setAttribute("aria-label", `${entry.name} ${open ? "összecsukása" : "kibontása"}`);
        toggle.addEventListener("click", async () => {
          if (viewer.expanded.has(entry.path)) {
            viewer.expanded.delete(entry.path);
          } else {
            toggle.textContent = "…";
            try {
              await fetchViewerChildren(entry.path);
            } catch (error) {
              element("viewer-notice").textContent = error.message;
              element("viewer-notice").classList.add("error");
            }
          }
          renderViewerTree();
        });
        row.append(toggle);
      } else {
        const spacer = document.createElement("span");
        spacer.className = "viewer-tree-toggle placeholder";
        row.append(spacer);
      }
      const item = document.createElement("button");
      item.type = "button";
      item.className = "viewer-tree-item";
      const selected = entry.path === state.viewerTarget.path;
      item.classList.toggle("selected", selected);
      item.classList.toggle("warning", entry.warnings.length > 0);
      item.classList.toggle("locked", entry.unpacked === false);
      item.setAttribute("aria-current", selected ? "true" : "false");
      const kind = document.createElement("span");
      kind.className = "viewer-kind";
      if (entry.executable) kind.textContent = "EXE";
      else if (entry.unpacked === false) kind.textContent = entry.encrypted ? "JELSZÓ" : "—";
      else kind.textContent = entry.real_kind === "email" ? "EML" : entry.real_kind.toUpperCase().slice(0, 4);
      const name = document.createElement("span");
      name.className = "viewer-tree-name";
      name.textContent = entry.name;
      name.title = entry.inner_path || entry.name;
      const meta = document.createElement("small");
      const inner = entry.inner_path || "";
      const folder = inner.includes("/") ? `${inner.slice(0, inner.lastIndexOf("/"))}/ · ` : "";
      meta.textContent = `${folder}${formatBytes(entry.size)}${entry.encrypted ? " · jelszavas" : ""}${entry.warnings.length ? " · figyelmeztetés" : ""}`;
      item.append(kind, name, meta);
      item.addEventListener("click", () => {
        window.location.hash = `#/melleklet/${viewer.messageId}/${entry.path}`;
      });
      row.append(item);
      list.append(row);
      const expansion = viewer.expansions.get(entry.path);
      if (expansion && viewer.expanded.has(entry.path)) {
        if (expansion.stopped || expansion.needs_password || !expansion.entries.length) {
          const note = document.createElement("p");
          note.className = `viewer-tree-note${expansion.stopped ? " error" : ""}`;
          note.style.setProperty("--depth", String(depth + 1));
          note.textContent = expansion.stopped
            || (expansion.needs_password ? "Jelszóval védett – a jelszót az archívum nézetében adhatja meg." : "Nincs benne fájl.");
          list.append(note);
        }
        addLevel(expansion.entries, depth + 1);
      }
    });
  };
  addLevel(viewer.attachments, 0);
}

/**
 * Render the analyst data sheet: file facts, type check, warnings, duplicates.
 */
function renderViewerSheet(info) {
  const sheet = element("viewer-sheet");
  sheet.replaceChildren();
  const section = (title) => {
    const block = document.createElement("section");
    block.className = "viewer-sheet-section";
    const heading = document.createElement("h4");
    heading.textContent = title;
    block.append(heading);
    sheet.append(block);
    return block;
  };
  const row = (block, label, value, className = "") => {
    const line = document.createElement("div");
    line.className = `viewer-sheet-row ${className}`;
    const key = document.createElement("span");
    key.textContent = label;
    const text = document.createElement("strong");
    text.textContent = value;
    line.append(key, text);
    block.append(line);
    return line;
  };
  const file = section("Fájl");
  row(file, "Név", info.name);
  row(file, "Méret", `${formatBytes(info.size)} (${info.size.toLocaleString("hu-HU")} bájt)`);
  row(file, "Megadott típus", info.declared_type || "—");
  const typeMismatch = info.warnings.some((warning) => /álcázott|mást ígér|megadott típus|nem ismerhető fel/.test(warning));
  row(file, "Valódi típus", `${info.real_label} · ${typeMismatch ? "ELTÉR a megadottól" : "egyezik"}`, typeMismatch ? "error" : "ok");
  row(file, "SHA-256", info.sha256 || "— (a fájl nincs kibontva)", "mono");
  if (info.inner_path) row(file, "Útvonal", info.inner_path);
  if (info.modified) row(file, "Módosítva", info.modified);
  row(file, "Hely", info.location);

  if (info.metadata && Object.keys(info.metadata).length) {
    const block = section("Dokumentum-metaadatok");
    Object.entries(info.metadata).forEach(([key, value]) => row(block, key, value));
  }
  if (info.security && Object.keys(info.security).length) {
    const block = section("Biztonság");
    Object.entries(info.security).forEach(([key, value]) => row(block, key, value, value === "van" ? "error" : "ok"));
    const note = document.createElement("p");
    note.className = "muted";
    note.textContent = "Az előnézet nem futtat kódot, és nem tölt be semmit az internetről.";
    block.append(note);
  }

  if (state.viewer.preview && state.viewer.preview.kind === "image") {
    const image = state.viewer.preview;
    const block = section("Képadatok");
    row(block, "Méret", `${image.width} × ${image.height} képpont${image.pages > 1 ? ` · ${image.pages} oldal` : ""}`);
    Object.entries(image.exif || {}).forEach(([key, value]) => row(block, key, value));
    if (image.gps) row(block, "GPS-koordináta", image.gps, "error");
  }

  const signals = section("Elemzői jelzések");
  const allSignals = [...info.warnings, ...(info.document_signals || [])];
  if (info.analysis_error) allSignals.push(`A dokumentum elemzése nem sikerült: ${info.analysis_error}`);
  if (allSignals.length === 0) {
    row(signals, "Figyelmeztetés", "nincs");
  } else {
    allSignals.forEach((warning) => {
      const line = document.createElement("p");
      line.className = "viewer-warning";
      line.textContent = warning;
      signals.append(line);
    });
  }
  if (state.viewer.preview && state.viewer.preview.gps) {
    const line = document.createElement("p");
    line.className = "viewer-warning";
    line.textContent = "A kép GPS-helyadatot tartalmaz.";
    signals.append(line);
  }

  const duplicates = section("Ugyanez a fájl máshol");
  if (info.same_elsewhere.length === 0) {
    const empty = document.createElement("p");
    empty.className = "muted";
    empty.textContent = "Nincs más ismert levélben ezzel a SHA-256-tal (csak a már megnyitott mellékletek hash-e ismert).";
    duplicates.append(empty);
  } else {
    info.same_elsewhere.forEach((item) => {
      const link = document.createElement("button");
      link.type = "button";
      link.className = "viewer-duplicate";
      link.textContent = `${item.subject || "(nincs tárgy)"} · ${item.sender || "?"} · ${formatTimestamp(item.sent_timestamp)}`;
      link.addEventListener("click", () => openAttachmentViewer(item.message_id, { path: String(item.position) }));
      duplicates.append(link);
    });
  }
  const note = document.createElement("p");
  note.className = "muted viewer-sheet-note";
  note.textContent = "A melléklet az eredeti forrásból, a memóriában nyílik meg. Makrók, szkriptek, külső hivatkozások és beágyazott programok nem futnak.";
  sheet.append(note);
}

/**
 * Show the preview that matches the attachment's real type.
 */
function renderViewerPreview(entry, info) {
  const area = element("viewer-preview");
  area.replaceChildren();
  const preview = state.viewer.preview;
  const viewer = state.viewer;
  if (entry.exact) area.append(exactViewSwitch(entry));
  if (!preview || preview.kind === "none" || preview.kind === "error") {
    const card = viewerMessage(preview && preview.reason ? preview.reason : "Ehhez a melléklethez nincs előnézet.", preview && preview.kind === "error");
    if (entry.executable || info.warnings.length) card.classList.add("warning");
    area.append(card);
    return;
  }
  const query = element("viewer-search").value.trim();
  if (preview.kind === "archive") {
    renderArchivePreview(area, entry, preview, query);
  } else if (preview.kind === "email") {
    renderEmailPreview(area, entry, preview, query);
  } else if (preview.kind === "pdf") {
    renderPdfPreview(area, entry, preview, query);
  } else if (preview.kind === "sheet") {
    renderSheetPreview(area, preview, query);
  } else if (preview.formatted) {
    renderFormattedPreview(area, entry, preview, query);
  } else if (preview.kind === "text") {
    area.append(textToolbar(preview), textBlock(preview.text, query));
  } else if (preview.kind === "table") {
    const meta = document.createElement("p");
    meta.className = "muted";
    const first = preview.page * preview.page_size + 1;
    const last = Math.min(preview.total_rows, first + preview.rows.length - 1);
    meta.textContent = `Kódolás: ${preview.encoding} · elválasztó: ${preview.delimiter} · sorok ${first}–${last} / ${preview.total_rows}`;
    const wrapper = document.createElement("div");
    wrapper.className = "viewer-table-wrapper";
    const table = document.createElement("table");
    table.className = "viewer-table";
    table.style.fontSize = `${Math.round(11 * viewer.zoom / 100)}px`;
    preview.rows.forEach((cells, rowIndex) => {
      const tr = document.createElement("tr");
      const number = document.createElement("th");
      number.textContent = String(first + rowIndex);
      tr.append(number);
      cells.forEach((cell) => {
        const td = document.createElement(rowIndex === 0 && preview.page === 0 ? "th" : "td");
        appendHighlightedText(td, cell, query);
        tr.append(td);
      });
      table.append(tr);
    });
    wrapper.append(table);
    area.append(meta, wrapper, viewerPager(preview.page, Math.ceil(preview.total_rows / preview.page_size)));
  } else if (preview.kind === "image") {
    const figure = document.createElement("div");
    figure.className = "viewer-image";
    const image = document.createElement("img");
    image.src = preview.image_url;
    image.alt = `${entry.name} – ${preview.page + 1}. oldal`;
    image.style.width = `${Math.round(preview.width * viewer.zoom / 100)}px`;
    figure.append(image);
    area.append(figure);
    if (preview.pages > 1) area.append(viewerPager(preview.page, preview.pages));
  }
  const firstMark = area.querySelector("mark");
  if (firstMark) firstMark.scrollIntoView({ block: "center" });
}

/**
 * Archive contents: totals, bomb or password state, and a clickable file table.
 */
function renderArchivePreview(area, entry, preview, query) {
  const viewer = state.viewer;
  const meta = document.createElement("p");
  meta.className = "muted";
  meta.textContent = `${preview.format}-archívum · ${preview.total_entries} fájl · kibontva ${formatBytes(preview.total_size)} · tömörítési arány ${preview.ratio}:1`;
  area.append(meta);
  if (preview.stopped) area.append(viewerMessage(preview.stopped, true));
  (preview.warnings || []).forEach((warning) => {
    const line = document.createElement("p");
    line.className = "viewer-warning";
    line.textContent = warning;
    area.append(line);
  });
  if (preview.needs_password || preview.password_error) {
    const form = document.createElement("form");
    form.className = "viewer-password";
    const label = document.createElement("label");
    label.textContent = preview.password_error ? "A jelszó hibás. Próbálja újra:" : "Az archívum jelszóval védett. Ismert jelszó:";
    const input = document.createElement("input");
    input.type = "password";
    input.autocomplete = "off";
    input.id = "viewer-password-input";
    label.htmlFor = input.id;
    const button = document.createElement("button");
    button.type = "submit";
    button.className = "button";
    button.textContent = "Kibontás jelszóval";
    const note = document.createElement("small");
    note.className = "muted";
    note.textContent = "A jelszó nem kerül mentésre és naplóba: csak a program bezárásáig él a memóriában.";
    form.append(label, input, button, note);
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      button.disabled = true;
      try {
        const result = await requestJson(`/api/attachments/${viewer.messageId}/${entry.path}/password`, {
          method: "POST",
          body: JSON.stringify({ password: input.value }),
        });
        [...viewer.expansions.keys()]
          .filter((path) => path.startsWith(`${entry.path}/`))
          .forEach((path) => viewer.expansions.delete(path));
        registerViewerChildren(entry.path, result);
        viewer.preview = { kind: "archive", ...result };
        renderViewerTree();
        renderViewerPreview(entry, { warnings: entry.warnings });
      } catch (error) {
        button.disabled = false;
        element("viewer-notice").textContent = error.message;
        element("viewer-notice").classList.add("error");
      }
    });
    area.append(form);
  }
  if (!preview.entries.length) return;
  const wrapper = document.createElement("div");
  wrapper.className = "viewer-table-wrapper";
  const table = document.createElement("table");
  table.className = "viewer-table viewer-archive-table";
  const head = document.createElement("tr");
  ["Fájl", "Méret", "Tömörítve", "Valódi típus", "Módosítva", "Állapot"].forEach((title) => {
    const th = document.createElement("th");
    th.textContent = title;
    head.append(th);
  });
  table.append(head);
  preview.entries.forEach((item) => {
    const tr = document.createElement("tr");
    if (item.warnings.length) tr.classList.add("warning");
    const nameCell = document.createElement("td");
    const open = document.createElement("button");
    open.type = "button";
    open.className = "viewer-link";
    appendHighlightedText(open, item.inner_path || item.name, query);
    open.addEventListener("click", () => { window.location.hash = `#/melleklet/${viewer.messageId}/${item.path}`; });
    nameCell.append(open);
    const status = item.unpacked === false
      ? (item.reason || "nincs kibontva")
      : (item.warnings.length ? item.warnings.join(" ") : "rendben");
    [formatBytes(item.size), item.compressed == null ? "—" : formatBytes(item.compressed), item.real_label, item.modified || "—", status]
      .forEach((value) => {
        const td = document.createElement("td");
        td.textContent = value;
        tr.append(td);
      });
    tr.prepend(nameCell);
    table.append(tr);
  });
  wrapper.append(table);
  area.append(wrapper);
  if (preview.stopped && preview.total_entries > preview.entries.length) {
    const more = document.createElement("p");
    more.className = "muted";
    more.textContent = `Az első ${preview.entries.length} bejegyzés látszik a ${preview.total_entries}-ból.`;
    area.append(more);
  }
}

/**
 * Attached e-mail (EML or MSG): headers, body (formatted in the sandbox or as
 * text) and its own attachments, which also appear in the tree.
 */
function renderEmailPreview(area, entry, preview, query) {
  const viewer = state.viewer;
  const card = document.createElement("div");
  card.className = "viewer-email-headers";
  preview.headers.forEach(([name, value]) => {
    const line = document.createElement("div");
    line.className = "viewer-sheet-row";
    const key = document.createElement("span");
    key.textContent = { From: "Feladó", To: "Címzett", Cc: "Másolat", Bcc: "Titkos másolat", "Reply-To": "Válaszcím", Date: "Dátum", Subject: "Tárgy", "Message-ID": "Message-ID", "In-Reply-To": "Válasz erre" }[name] || name;
    const text = document.createElement("strong");
    appendHighlightedText(text, value, query);
    line.append(key, text);
    card.append(line);
  });
  area.append(card);
  if (preview.converted) {
    const note = document.createElement("p");
    note.className = "muted";
    note.textContent = "Outlook MSG: a fejlécek és a törzs a fájl MAPI-tulajdonságaiból állnak össze.";
    area.append(note);
  }
  if (preview.attachments.length) {
    const list = document.createElement("div");
    list.className = "viewer-hits";
    list.textContent = "Mellékletei: ";
    preview.attachments.forEach((name, index) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "button quiet";
      button.textContent = name;
      button.addEventListener("click", () => { window.location.hash = `#/melleklet/${viewer.messageId}/${entry.path}/${index}`; });
      list.append(button);
    });
    area.append(list);
  }
  if (preview.formatted) {
    renderFormattedPreview(area, entry, preview, query);
  } else {
    area.append(textToolbar(preview), textBlock(preview.text, query));
  }
}

/**
 * Encoding note and, for HTML, the cleaned/source switch above a text preview.
 */
function textToolbar(preview) {
  const toolbar = document.createElement("div");
  toolbar.className = "viewer-text-toolbar";
  const meta = document.createElement("span");
  meta.className = "muted";
  meta.textContent = preview.encoding
    ? `Kódolás: ${preview.encoding}${preview.truncated ? " · csak az első 5 MB látszik; a teljes szöveghez mentse a fájlt" : ""}`
    : (preview.truncated ? "Csak az első 2 MB szöveg látszik; a teljes szöveghez mentse a fájlt." : "");
  toolbar.append(meta);
  return toolbar;
}

/**
 * Text with line numbers and search highlighting.
 */
function textBlock(text, query) {
  const pre = document.createElement("pre");
  pre.className = "viewer-text";
  pre.style.fontSize = `${Math.round(11 * state.viewer.zoom / 100)}px`;
  (text || "").split("\n").forEach((line, index) => {
    const number = document.createElement("span");
    number.className = "line-number";
    number.textContent = String(index + 1).padStart(4, " ");
    pre.append(number);
    appendHighlightedText(pre, `${line}\n`, query);
  });
  return pre;
}

/**
 * Tabs for formatted (sandboxed iframe), plain text and, for HTML, source views.
 */
function renderFormattedPreview(area, entry, preview, query) {
  const viewer = state.viewer;
  const tabs = [["formatted", "Formázott"], ["text", "Szöveg"]];
  if (preview.source) tabs.push(["source", "Forrás"]);
  // A search switches to the text view: the sandboxed document cannot be highlighted.
  let tab = viewer.tab || "formatted";
  if (query && tab === "formatted") tab = "text";
  const bar = document.createElement("div");
  bar.className = "viewer-tabs";
  bar.setAttribute("role", "tablist");
  tabs.forEach(([key, label]) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "viewer-tab";
    button.setAttribute("role", "tab");
    button.setAttribute("aria-selected", String(key === tab));
    button.textContent = label;
    button.addEventListener("click", () => {
      viewer.tab = key;
      if (key === "formatted") element("viewer-search").value = "";
      renderViewerPreview(entry, { warnings: entry.warnings });
    });
    bar.append(button);
  });
  area.append(bar);
  if (tab === "formatted") {
    const frame = document.createElement("iframe");
    // Empty sandbox: no scripts, no forms, no navigation, unique origin.
    frame.setAttribute("sandbox", "");
    frame.setAttribute("referrerpolicy", "no-referrer");
    frame.className = "viewer-frame";
    frame.title = `${entry.name} – formázott, biztonságos nézet`;
    frame.src = preview.document_url;
    frame.style.zoom = String(viewer.zoom / 100);
    area.append(frame);
  } else {
    if (preview.messages && preview.messages.length) {
      const note = document.createElement("p");
      note.className = "muted";
      note.textContent = `Átalakítási megjegyzések: ${preview.messages.join(" · ")}`;
      area.append(note);
    }
    area.append(textToolbar(preview), textBlock(tab === "source" ? preview.source : preview.text, query));
  }
}

/**
 * PDF page image with page navigation, its text layer and cross-page search hits.
 */
function renderPdfPreview(area, entry, preview, query) {
  const viewer = state.viewer;
  const pager = viewerPager(preview.page, preview.pages);
  const hits = document.createElement("div");
  hits.className = "viewer-hits";
  if (query && viewer.pdfText) {
    const needle = query.toLocaleLowerCase();
    const pages = viewer.pdfText
      .map((text, index) => (text.toLocaleLowerCase().includes(needle) ? index : -1))
      .filter((index) => index >= 0);
    hits.textContent = pages.length ? "Találat: " : "Nincs találat a dokumentum szövegében.";
    pages.forEach((index) => {
      const jump = document.createElement("button");
      jump.type = "button";
      jump.className = "button quiet";
      jump.textContent = `${index + 1}. oldal`;
      jump.disabled = index === preview.page;
      jump.addEventListener("click", () => { viewer.page = index; refreshViewerPreview(); });
      hits.append(jump);
    });
  } else if (query && !viewer.pdfText) {
    hits.textContent = "Keresés a dokumentumban…";
    requestJson(`/api/attachments/${viewer.messageId}/${viewer.path}/text${viewer.exactView ? "?exact=1" : ""}`)
      .then((result) => { viewer.pdfText = result.pages || []; renderViewerPreview(entry, { warnings: entry.warnings }); })
      .catch((error) => { hits.textContent = error.message; });
  }
  const figure = document.createElement("div");
  figure.className = "viewer-image viewer-pdf-page";
  const image = document.createElement("img");
  image.src = preview.image_url;
  image.alt = `${entry.name} – ${preview.page + 1}. oldal`;
  image.style.width = `${Math.round(preview.width / 2 * viewer.zoom / 100)}px`;
  figure.append(image);
  const layer = document.createElement("details");
  layer.className = "viewer-text-layer";
  layer.open = Boolean(query);
  const summary = document.createElement("summary");
  summary.textContent = preview.text.trim()
    ? `${preview.page + 1}. oldal szövege (szövegréteg: kijelölhető, kereshető)`
    : `${preview.page + 1}. oldal: nincs szövegréteg (OCR nélkül nem kinyerhető)`;
  layer.append(summary, textBlock(preview.text, query));
  area.append(pager, hits, figure, layer);
}

/**
 * Spreadsheet view: sheet tabs, formula bar, values/formulas switch, hidden marks.
 */
function renderSheetPreview(area, preview, query) {
  const viewer = state.viewer;
  const tabs = document.createElement("div");
  tabs.className = "viewer-tabs";
  tabs.setAttribute("role", "tablist");
  const stateNames = { hidden: "rejtett", veryHidden: "nagyon rejtett" };
  preview.sheets.forEach((sheet, index) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `viewer-tab${sheet.state !== "visible" ? " hidden-sheet" : ""}`;
    button.setAttribute("role", "tab");
    button.setAttribute("aria-selected", String(index === preview.sheet));
    button.textContent = sheet.name;
    if (stateNames[sheet.state]) {
      const tag = document.createElement("span");
      tag.className = "sheet-state";
      tag.textContent = stateNames[sheet.state];
      button.append(tag);
    }
    button.addEventListener("click", () => {
      viewer.sheet = index;
      viewer.page = 0;
      refreshViewerPreview();
    });
    tabs.append(button);
  });

  const bar = document.createElement("div");
  bar.className = "viewer-formula-bar";
  const cellName = document.createElement("strong");
  cellName.textContent = viewer.cell || "—";
  const fx = document.createElement("span");
  fx.className = "fx";
  fx.textContent = "ƒx";
  const content = document.createElement("span");
  content.className = "formula-content";
  const toggle = document.createElement("button");
  toggle.type = "button";
  toggle.className = `button quiet toggle-button${viewer.showFormulas ? " active" : ""}`;
  toggle.setAttribute("aria-pressed", String(Boolean(viewer.showFormulas)));
  toggle.textContent = "Képletek";
  toggle.disabled = preview.legacy;
  toggle.title = preview.legacy ? "Régi XLS-ben a képletek csak a Pontos nézetben látszanak." : "Képletek mutatása a tárolt értékek helyett";
  toggle.addEventListener("click", () => { viewer.showFormulas = !viewer.showFormulas; renderSheetPreview(area.replaceChildren() || area, preview, query); });
  bar.append(cellName, fx, content, toggle);

  const showCell = (coordinate, value) => {
    viewer.cell = coordinate;
    cellName.textContent = coordinate;
    const formula = preview.formulas[coordinate];
    content.textContent = formula
      ? `${formula}   · tárolt érték: ${value || "(nincs tárolt érték)"}`
      : value;
  };

  const wrapper = document.createElement("div");
  wrapper.className = "viewer-table-wrapper";
  const table = document.createElement("table");
  table.className = "viewer-table viewer-sheet-table";
  table.style.fontSize = `${Math.round(11 * viewer.zoom / 100)}px`;
  const head = document.createElement("tr");
  head.append(document.createElement("th"));
  preview.columns.forEach((letter) => {
    const th = document.createElement("th");
    th.textContent = letter;
    if (preview.hidden_columns.includes(letter)) {
      th.classList.add("hidden-mark");
      th.title = "Rejtett oszlop";
    }
    head.append(th);
  });
  table.append(head);
  preview.rows.forEach((cells, rowIndex) => {
    const rowNumber = preview.first_row + rowIndex;
    const tr = document.createElement("tr");
    const number = document.createElement("th");
    number.textContent = String(rowNumber);
    if (preview.hidden_rows.includes(rowNumber)) {
      tr.classList.add("hidden-row");
      number.title = "Rejtett sor";
    }
    tr.append(number);
    cells.forEach((value, columnIndex) => {
      const coordinate = `${preview.columns[columnIndex]}${rowNumber}`;
      const td = document.createElement("td");
      const formula = preview.formulas[coordinate];
      const shown = viewer.showFormulas && formula ? formula : (value || (formula && preview.values_missing ? formula : value));
      appendHighlightedText(td, shown || "", query);
      if (formula) td.classList.add("formula-cell");
      if (preview.hidden_columns.includes(preview.columns[columnIndex])) td.classList.add("hidden-col");
      if (preview.comments[coordinate]) {
        td.classList.add("comment-cell");
        td.title = `Megjegyzés – ${preview.comments[coordinate]}`;
      }
      td.tabIndex = 0;
      td.addEventListener("click", () => showCell(coordinate, value));
      td.addEventListener("focus", () => showCell(coordinate, value));
      tr.append(td);
    });
    table.append(tr);
  });
  wrapper.append(table);
  const meta = document.createElement("p");
  meta.className = "muted";
  const last = preview.first_row + preview.rows.length - 1;
  meta.textContent = `Sorok ${preview.first_row}–${Math.max(preview.first_row, last)} / ${preview.total_rows}`
    + (preview.hidden_rows.length || preview.hidden_columns.length ? " · a rejtett sorok és oszlopok halványan, jelölve látszanak" : "")
    + (preview.values_missing ? " · a fájl nem tartalmaz tárolt értéket a képletekhez, ezért a képlet látszik" : "");
  area.append(tabs, bar, wrapper, meta, viewerPager(preview.page, Math.ceil(preview.total_rows / preview.page_size)));
}

/**
 * Previous/next page buttons for multi-page tables and images.
 */
function viewerPager(page, pages) {
  const pager = document.createElement("div");
  pager.className = "viewer-pager";
  const previous = document.createElement("button");
  previous.type = "button";
  previous.className = "button quiet";
  previous.textContent = "‹ Előző oldal";
  previous.disabled = page <= 0;
  previous.addEventListener("click", () => { state.viewer.page = page - 1; refreshViewerPreview(); });
  const label = document.createElement("span");
  label.textContent = `${page + 1} / ${Math.max(1, pages)}`;
  const next = document.createElement("button");
  next.type = "button";
  next.className = "button quiet";
  next.textContent = "Következő oldal ›";
  next.disabled = page >= pages - 1;
  next.addEventListener("click", () => { state.viewer.page = page + 1; refreshViewerPreview(); });
  pager.append(previous, label, next);
  return pager;
}

const EXACT_LOADING_TEXT = "Átalakítás LibreOffice-szal a Pontos nézethez… (legfeljebb 60 másodperc)";

/**
 * Preview address of the current attachment, in the built-in or the exact view.
 */
function viewerPreviewUrl() {
  const viewer = state.viewer;
  const exact = viewer.exactView ? "&exact=1" : "";
  return `/api/attachments/${viewer.messageId}/${viewer.path}/preview?page=${viewer.page}&sheet=${viewer.sheet || 0}${exact}`;
}

/**
 * Built-in / exact (LibreOffice) view switch for Office attachments.
 */
function exactViewSwitch(entry) {
  const viewer = state.viewer;
  const bar = document.createElement("div");
  bar.className = "viewer-tabs viewer-mode";
  bar.setAttribute("role", "group");
  bar.setAttribute("aria-label", "Nézet");
  [[false, "Beépített nézet"], [true, "Pontos nézet (LibreOffice)"]].forEach(([exact, label]) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "viewer-tab";
    button.setAttribute("aria-pressed", String(Boolean(viewer.exactView) === exact));
    button.textContent = label;
    if (exact && !viewer.exact_available) {
      button.disabled = true;
      button.title = "A Pontos nézethez LibreOffice kell, de a gépen nem található.";
    }
    button.addEventListener("click", () => {
      if (Boolean(viewer.exactView) === exact) return;
      viewer.exactView = exact;
      viewer.page = 0;
      viewer.pdfText = null;
      if (exact) element("viewer-preview").replaceChildren(exactViewSwitch(entry), viewerMessage(EXACT_LOADING_TEXT));
      refreshViewerPreview();
    });
    bar.append(button);
  });
  if (!viewer.exact_available) {
    const locate = document.createElement("button");
    locate.type = "button";
    locate.className = "button quiet";
    locate.textContent = "LibreOffice megkeresése…";
    locate.title = "Ha a LibreOffice nem a szokásos helyre van telepítve, itt kiválaszthatja a soffice.exe programját.";
    locate.addEventListener("click", async () => {
      try {
        const status = await requestJson("/api/settings/libreoffice", { method: "POST", body: JSON.stringify({ pick: true }) });
        viewer.exact_available = Boolean(status.path);
        renderViewerPreview(entry, { warnings: entry.warnings });
      } catch (error) {
        element("viewer-notice").textContent = error.message;
        element("viewer-notice").classList.add("error");
      }
    });
    bar.append(locate);
  }
  return bar;
}

/**
 * Reload only the preview (page change) of the current attachment.
 */
async function refreshViewerPreview() {
  const viewer = state.viewer;
  const entry = viewer.nodes.get(viewer.path);
  try {
    viewer.preview = await requestJson(viewerPreviewUrl());
    renderViewerPreview(entry, { warnings: entry.warnings });
  } catch (error) {
    if (viewer.exactView) {
      // A failed conversion replaces the waiting card; the switch stays to go back.
      viewer.preview = { kind: "error", reason: error.message };
      renderViewerPreview(entry, { warnings: entry.warnings });
    }
    element("viewer-notice").textContent = error.message;
    element("viewer-notice").classList.add("error");
  }
}

/**
 * Step to the previous or next attachment of the message.
 */
function stepViewer(delta) {
  const viewer = state.viewer;
  if (!viewer || viewer.attachments.length === 0) return;
  const siblings = viewerSiblings(state.viewerTarget.path);
  const index = siblings.findIndex((item) => item.path === state.viewerTarget.path) + delta;
  if (index < 0 || index >= siblings.length) return;
  window.location.hash = `#/melleklet/${viewer.messageId}/${siblings[index].path}`;
}

/**
 * Change the preview zoom between 25% and 400%.
 */
function zoomViewer(delta) {
  const viewer = state.viewer;
  if (!viewer) return;
  viewer.zoom = Math.min(400, Math.max(25, viewer.zoom + delta));
  element("viewer-zoom").textContent = `${viewer.zoom}%`;
  const entry = viewer.nodes.get(viewer.path);
  if (entry) renderViewerPreview(entry, { warnings: entry.warnings });
}

/**
 * Leave the viewer towards the page it was opened from.
 */
function closeViewer() {
  const target = state.viewerReturn && !state.viewerReturn.startsWith("#/melleklet") ? state.viewerReturn : "#/archivum";
  window.location.hash = target;
}

/**
 * Save one attachment or all of them through the native save dialog.
 */
async function saveFromViewer(all) {
  const viewer = state.viewer;
  if (!viewer) return;
  const notice = element("viewer-notice");
  try {
    const result = await requestJson(
      all ? `/api/messages/${viewer.messageId}/attachments/save-all` : `/api/attachments/${viewer.messageId}/${viewer.path}/save`,
      { method: "POST", body: "{}" },
    );
    notice.classList.remove("error");
    notice.textContent = result.cancelled
      ? "A mentés megszakítva."
      : `Mentve: ${result.saved_path}${all ? " (MANIFEST.txt-vel)" : " (mellette .sha256 és .eredet.txt)"}`;
  } catch (error) {
    notice.textContent = error.message;
    notice.classList.add("error");
  }
}

element("viewer-back").addEventListener("click", closeViewer);
element("viewer-prev").addEventListener("click", () => stepViewer(-1));
element("viewer-next").addEventListener("click", () => stepViewer(1));
element("viewer-zoom-in").addEventListener("click", () => zoomViewer(25));
element("viewer-zoom-out").addEventListener("click", () => zoomViewer(-25));
element("viewer-save").addEventListener("click", () => saveFromViewer(false));
element("viewer-save-all").addEventListener("click", () => saveFromViewer(true));
element("viewer-search").addEventListener("input", () => {
  const viewer = state.viewer;
  const entry = viewer && viewer.nodes.get(viewer.path);
  if (entry) renderViewerPreview(entry, { warnings: entry.warnings });
});
document.addEventListener("keydown", (event) => {
  if (state.route !== "melleklet") return;
  const target = event.target;
  const typing = target instanceof HTMLElement && ["INPUT", "SELECT", "TEXTAREA"].includes(target.tagName);
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "f") {
    event.preventDefault();
    element("viewer-search").focus();
    element("viewer-search").select();
  } else if (event.key === "Escape") {
    if (typing && target.value) return;
    event.preventDefault();
    closeViewer();
  } else if (!typing && !event.ctrlKey && !event.altKey) {
    if (event.key === "ArrowLeft") stepViewer(-1);
    else if (event.key === "ArrowRight") stepViewer(1);
    else if (event.key === "+" || event.key === "=") zoomViewer(25);
    else if (event.key === "-") zoomViewer(-25);
  }
});

/**
 * Load the case currently selected when the local page opens.
 */
async function initialize() {
  element("search-groups").append(createSearchGroup());
  const status = await refreshStatus();
  if (!status || !status.case) return;
  await refreshFolders();
  await refreshPeople();
  await refreshSavedSearches();
  await refreshArchive();
}

initialize();
