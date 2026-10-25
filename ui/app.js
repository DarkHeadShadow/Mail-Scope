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
  element("conversation-panel").scrollIntoView({ behavior: "smooth", block: "start" });
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
      const meta = document.createElement("span");
      meta.textContent = `${message.sender || "Ismeretlen feladó"} · ${message.sent_date || "Dátum nélkül"} · ${message.source_path}`;
      open.append(subject, meta);
      open.addEventListener("click", () => openMessage(Number(message.message_id)));

      const labels = document.createElement("span");
      labels.className = "detailed-result-labels";
      labels.textContent = message.labels || "";
      const conversation = document.createElement("button");
      conversation.type = "button";
      conversation.className = "button quiet conversation-from-result";
      conversation.textContent = "Beszélgetés";
      conversation.title = "A feladó és a címzett személyeinek megnyitása a beszélgetésnézetben";
      conversation.addEventListener("click", () => openConversationForMessage(message));
      row.append(checkbox, open, labels, conversation);
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
    const summaryKey = JSON.stringify(job.summary);
    if (state.lastPeopleRefresh !== summaryKey) {
      state.lastPeopleRefresh = summaryKey;
      refreshFolders();
      refreshPeople();
    }
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
 * Render person checkboxes and still-unassigned indexed addresses.
 */
function renderPeople(observedAddresses) {
  const peopleList = element("people-list");
  const conversationPeople = element("conversation-people");
  peopleList.replaceChildren();
  conversationPeople.replaceChildren();
  if (state.people.length === 0) {
    peopleList.className = "people-list empty-state";
    peopleList.textContent = "Még nincs személy. Hozzon létre személyt az indexelt címekből.";
    conversationPeople.className = "conversation-people empty-state";
    conversationPeople.textContent = "A beszélgetéshez előbb legalább két személyt rendeljen hozzá.";
  } else {
    peopleList.className = "people-list";
    conversationPeople.className = "conversation-people";
    state.people.forEach((person) => {
      const label = document.createElement("label");
      label.className = "person-row";
      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.value = String(person.id);
      checkbox.dataset.personName = person.display_name;
      const details = document.createElement("span");
      const name = document.createElement("strong");
      name.textContent = person.display_name;
      const addresses = document.createElement("small");
      addresses.textContent = person.addresses.length
        ? person.addresses.join(", ")
        : "Nincs hozzárendelt e-mail-cím";
      details.append(name, addresses);
      label.append(checkbox, details);
      peopleList.append(label);

      const conversationLabel = document.createElement("label");
      conversationLabel.className = "conversation-person";
      const conversationCheckbox = checkbox.cloneNode();
      conversationLabel.append(conversationCheckbox, document.createTextNode(person.display_name));
      conversationPeople.append(conversationLabel);
    });
  }

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
    checkbox.disabled = address.person_id !== null;
    const details = document.createElement("span");
    details.append(document.createTextNode(address.address));
    if (address.person_name || address.display_name) {
      const note = document.createElement("small");
      note.textContent = address.person_name
        ? `Hozzárendelve: ${address.person_name}`
        : `Megjelenő név: ${address.display_name}`;
      details.append(note);
    }
    label.append(checkbox, details);
    addressList.append(label);
  });
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

  const activity = document.createElement("nav");
  activity.className = "activity-strip";
  activity.setAttribute("aria-label", "Havi levélaktivitás");
  result.months.forEach((month) => {
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
    if (message.day && message.day !== currentDay) {
      currentDay = message.day;
      const heading = document.createElement("h3");
      heading.className = "timeline-day";
      heading.dataset.day = message.day;
      heading.textContent = new Intl.DateTimeFormat("hu-HU", {
        year: "numeric", month: "long", day: "numeric", timeZone: "UTC",
      }).format(new Date(`${message.day}T12:00:00Z`));
      timeline.append(heading);
      if (message.silent_days_before > 0) {
        const gap = document.createElement("div");
        gap.className = "silence-gap";
        gap.textContent = `${message.silent_days_before} nap csend`;
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
  time.textContent = message.sent_at
    ? new Intl.DateTimeFormat("hu-HU", { hour: "2-digit", minute: "2-digit", timeZone: "UTC" })
      .format(new Date(message.sent_at))
    : "Dátum nélkül";
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
    openMessage(Number(message.message_id));
    element("archive-panel").scrollIntoView({ behavior: "smooth", block: "start" });
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
