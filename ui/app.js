// Mail-Scope local archive UI · creator: Bálint Szabolcs
"use strict";

const state = {
  sources: [],
  folders: [],
  discovered: [],
  messages: [],
  offset: 0,
  selectedMessageId: null,
  pollTimer: null,
};

const element = (id) => document.getElementById(id);

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
  return row;
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
 * Update source lists, case identity, and active indexing progress.
 */
function renderStatus(status) {
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
      progress.textContent = `Indexelés ${job.summary.status}: ${job.summary.messages_indexed} új levél, ${job.summary.messages_skipped} kihagyva.`;
    }
  }
  if (job && job.running && !state.pollTimer) {
    state.pollTimer = window.setTimeout(async () => {
      state.pollTimer = null;
      await refreshStatus();
    }, 1200);
  }
  if (job && !job.running && job.summary) {
    refreshArchive();
  }
}

/**
 * Refresh case state and the archive after a local API operation.
 */
async function refreshStatus() {
  try {
    const status = await requestJson("/api/status");
    renderStatus(status);
    return status;
  } catch (error) {
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
  } catch (error) {
    showNotice(error.message, true);
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
    const meta = document.createElement("div");
    meta.className = "message-meta";
    const sender = document.createElement("span");
    sender.className = "message-sender";
    sender.textContent = message.sender || "Ismeretlen feladó";
    const date = document.createElement("time");
    date.textContent = message.sent_date || "Dátum nélkül";
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
  top.append(title);
  ["from", "to", "cc", "bcc", "date", "message-id"].forEach((name) => {
    if (!detail.headers[name]) return;
    const row = document.createElement("div");
    row.className = "header-row";
    const label = document.createElement("strong");
    label.textContent = name.toUpperCase();
    const value = document.createElement("span");
    value.textContent = detail.headers[name];
    row.append(label, value);
    top.append(row);
  });
  pane.append(top);

  const body = document.createElement("div");
  body.className = "reader-body";
  body.textContent = detail.body || "(Nincs megjeleníthető levéltörzs.)";
  pane.append(body);
  appendBadgeSection(pane, "MELLÉKLETEK", detail.attachments.map((file) => `${file.filename} · ${file.content_type || "ismeretlen típus"}`));
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

/**
 * Create or open the selected local case.
 */
element("case-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const status = await requestJson("/api/case", {
      method: "POST",
      body: JSON.stringify({ path: element("case-path").value.trim() }),
    });
    renderStatus(status);
    await refreshFolders();
    await refreshArchive();
    showNotice("Az ügy megnyitva. A források most már felismerhetők és indexelhetők.");
  } catch (error) {
    showNotice(error.message, true);
  }
});

/**
 * Start background indexing for the checked, previously discovered sources.
 */
element("index-button").addEventListener("click", async () => {
  const paths = [...element("discovered-list").querySelectorAll("input:checked")]
    .map((checkbox) => checkbox.value);
  try {
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
 * Load the case currently selected when the local page opens.
 */
async function initialize() {
  const status = await refreshStatus();
  if (!status || !status.case) return;
  await refreshFolders();
  await refreshArchive();
}

initialize();
