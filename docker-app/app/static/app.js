const state = {
  items: [],
  selectedDestination: "",
  libraryFilter: "",
  statusFilter: "all",
  mode: "manual",
  preview: null,
  jobs: [],
  searchResults: [],
  selectedResultId: "",
  expandedJobs: new Set(),
  autopilotRun: null,
};

const $ = (id) => document.getElementById(id);

function fmtDuration(seconds) {
  if (!seconds) return "duración desconocida";
  const m = Math.floor(seconds / 60);
  const s = String(seconds % 60).padStart(2, "0");
  return `${m}:${s}`;
}

function setMessage(text, kind = "", targetId = "formMsg") {
  const el = $(targetId);
  el.textContent = text;
  el.className = `message ${kind}`;
}

async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.detail || "La petición falló");
  return data;
}

function renderLibraryTabs() {
  const box = $("libraryTabs");
  const libraries = [...new Set(state.items.map((item) => item.library))].sort();
  box.innerHTML = "";

  const allTab = document.createElement("button");
  allTab.type = "button";
  allTab.className = `libTab ${state.libraryFilter === "" ? "selected" : ""}`;
  allTab.textContent = `Todas (${state.items.length})`;
  allTab.addEventListener("click", () => {
    state.libraryFilter = "";
    renderLibraryTabs();
    renderItems();
  });
  box.appendChild(allTab);

  for (const lib of libraries) {
    const count = state.items.filter((item) => item.library === lib).length;
    const tab = document.createElement("button");
    tab.type = "button";
    tab.className = `libTab ${state.libraryFilter === lib ? "selected" : ""}`;
    tab.textContent = `${lib} (${count})`;
    tab.addEventListener("click", () => {
      state.libraryFilter = lib;
      renderLibraryTabs();
      renderItems();
    });
    box.appendChild(tab);
  }
}

function matchesStatus(item) {
  if (state.statusFilter === "missing") return !item.has_audio || !item.has_video;
  if (state.statusFilter === "has") return item.has_audio && item.has_video;
  return true;
}

function updateAutoScopeLabels() {
  if (state.libraryFilter) {
    const count = state.items.filter((i) => i.library === state.libraryFilter).length;
    $("autoScopeLibrary").textContent = `${state.libraryFilter} (${count} ítems)`;
  } else {
    $("autoScopeLibrary").textContent = "elige una pestaña de biblioteca arriba";
  }
  const selected = state.items.find((i) => i.path === state.selectedDestination);
  $("autoScopeSingle").textContent = selected ? selected.name : "—";
}

function renderItems() {
  const filter = $("destinationFilter").value.toLowerCase();
  const list = $("destinations");
  const items = state.items.filter(
    (item) =>
      (!state.libraryFilter || item.library === state.libraryFilter) &&
      matchesStatus(item) &&
      (item.name.toLowerCase().includes(filter) || item.path.toLowerCase().includes(filter))
  );
  $("itemCount").textContent = `${items.length}/${state.items.length}`;
  list.innerHTML = "";
  updateAutoScopeLabels();

  for (const item of items.slice(0, 120)) {
    const row = document.createElement("button");
    row.type = "button";
    row.className = `dest ${state.selectedDestination === item.path ? "selected" : ""}`;
    row.title = item.path;
    row.innerHTML = `
      <span class="destKind">${item.kind === "movie" ? "MOV" : "SER"}</span>
      <span class="destName">
        <strong>${item.name}</strong>
        <span>${item.library}</span>
      </span>
      <span class="badges">
        <span class="badge ${item.has_audio ? "ok" : ""}">audio</span>
        <span class="badge ${item.has_video ? "ok" : ""}">video</span>
      </span>
    `;
    row.addEventListener("click", () => {
      state.selectedDestination = item.path;
      $("autoSearchBtn").disabled = false;
      renderItems();
    });
    list.appendChild(row);
  }
}

function setMode(mode) {
  state.mode = mode;
  $("modeManualBtn").classList.toggle("selected", mode === "manual");
  $("modeAutoBtn").classList.toggle("selected", mode === "auto");
  $("manualStep2").hidden = mode !== "manual";
  $("manualStep3").hidden = mode !== "manual";
  $("autoStep2").hidden = mode !== "auto";
}

async function loadItems() {
  const data = await api("/api/items");
  state.items = data.items;
  if (!state.selectedDestination && state.items[0]) state.selectedDestination = state.items[0].path;
  $("autoSearchBtn").disabled = !state.selectedDestination;
  renderLibraryTabs();
  renderItems();
}

function renderPreview() {
  const box = $("preview");
  if (!state.preview) {
    box.className = "preview empty";
    box.innerHTML = `<div class="emptyState">Pega un enlace y previsualiza antes de instalar.</div>`;
    return;
  }
  box.className = "preview";
  const frame = state.preview.embed_url
    ? `<iframe class="previewFrame" src="${state.preview.embed_url}" allowfullscreen></iframe>`
    : `<img class="previewFrame" src="${state.preview.thumbnail || ""}" alt="">`;
  box.innerHTML = `
    ${frame}
    <div class="previewMeta">
      <strong>${state.preview.title || "Sin titulo"}</strong>
      <span>${state.preview.uploader || "canal desconocido"} · ${fmtDuration(state.preview.duration)}</span>
    </div>
  `;
}

async function previewUrl() {
  const url = $("youtubeUrl").value.trim();
  if (!url) return;
  $("previewBtn").disabled = true;
  setMessage("Leyendo metadata de YouTube...");
  try {
    state.preview = await api("/api/preview", {
      method: "POST",
      body: JSON.stringify({ url }),
    });
    renderPreview();
    setMessage("Preview lista, ya puedes añadirlo a la cola.", "success");
  } catch (err) {
    setMessage(`No se pudo previsualizar: ${err.message}`, "error");
  } finally {
    $("previewBtn").disabled = false;
  }
}

function renderSearchResults() {
  const box = $("searchResults");
  if (!state.searchResults.length) {
    box.innerHTML = "";
    box.className = "searchResults";
    return;
  }
  box.className = "searchResults hasResults";
  box.innerHTML = "";
  for (const item of state.searchResults) {
    const card = document.createElement("button");
    card.type = "button";
    card.className = `searchResult ${item.id === state.selectedResultId ? "selected" : ""}`;
    card.innerHTML = `
      <img src="${item.thumbnail || ""}" alt="" loading="lazy" />
      ${item.score != null ? `<span class="scoreBadge">${Math.round(item.score * 100)}%</span>` : ""}
      ${item.id === state.selectedResultId ? `<span class="pickedBadge">✓ elegido</span>` : ""}
      <span class="searchResultMeta">
        <strong>${item.title || "Sin título"}</strong>
        <span>${item.uploader || "canal desconocido"} · ${fmtDuration(item.duration)}</span>
      </span>
    `;
    card.addEventListener("click", () => selectSearchResult(item));
    box.appendChild(card);
  }
}

function selectSearchResult(item) {
  $("youtubeUrl").value = item.webpage_url;
  state.preview = item;
  state.selectedResultId = item.id;
  renderPreview();
  renderSearchResults();
  setMessage("Preview lista, ya puedes añadirlo a la cola (o elige otro resultado de la lista).", "success");
}

async function searchYoutube() {
  const query = $("searchQuery").value.trim();
  if (!query) return;
  $("searchBtn").disabled = true;
  setMessage("Buscando en YouTube...");
  try {
    const data = await api("/api/search", {
      method: "POST",
      body: JSON.stringify({ query }),
    });
    state.searchResults = data.results;
    state.selectedResultId = "";
    renderSearchResults();
    if (data.results.length) {
      selectSearchResult(data.results[0]);
    } else {
      setMessage("Sin resultados, prueba otra búsqueda.");
    }
  } catch (err) {
    setMessage(`La búsqueda falló: ${err.message}`, "error");
  } finally {
    $("searchBtn").disabled = false;
  }
}

async function autoSearch() {
  if (!state.selectedDestination) {
    setMessage("Elige primero una serie o película.", "error");
    return;
  }
  $("autoSearchBtn").disabled = true;
  setMessage("Buscando automáticamente (opening, español/castellano, oficial)...");
  try {
    const data = await api("/api/auto-search", {
      method: "POST",
      body: JSON.stringify({ destination: state.selectedDestination }),
    });
    state.searchResults = data.results;
    state.selectedResultId = "";
    renderSearchResults();
    if (data.results.length) {
      selectSearchResult(data.results[0]);
      setMessage(`Mejor candidato para "${data.name}" ya seleccionado (${Math.round(data.results[0].score * 100)}%). Revisa el preview o elige otro de la lista.`, "success");
    } else {
      setMessage(`Sin candidatos para "${data.name}", prueba una búsqueda manual.`);
    }
  } catch (err) {
    setMessage(`La autobúsqueda falló: ${err.message}`, "error");
  } finally {
    $("autoSearchBtn").disabled = false;
  }
}

async function enqueue() {
  const url = $("youtubeUrl").value.trim();
  const assets = [];
  if ($("assetAudio").checked) assets.push("audio");
  if ($("assetVideo").checked) assets.push("video");
  if (!url) {
    setMessage('Antes de añadir a la cola, usa "Buscar automáticamente" o la búsqueda manual y elige un resultado.', "error");
    return;
  }
  if (!state.selectedDestination) {
    setMessage("Elige a qué serie o película va este contenido.", "error");
    return;
  }
  if (!assets.length) {
    setMessage("Marca al menos audio o video.", "error");
    return;
  }
  try {
    await api("/api/jobs", {
      method: "POST",
      body: JSON.stringify({
        url,
        destination: state.selectedDestination,
        assets,
        refresh: $("refreshLibs").checked,
      }),
    });
    setMessage("Añadido a la cola.", "success");
    await loadJobs();
  } catch (err) {
    setMessage(`No se pudo encolar: ${err.message}`, "error");
  }
}

async function cancelJob(jobId) {
  try {
    await api(`/api/jobs/${jobId}/cancel`, { method: "POST" });
    await loadJobs();
  } catch (err) {
    setMessage(`No se pudo cancelar: ${err.message}`, "error");
  }
}

async function retryJob(jobId) {
  try {
    await api(`/api/jobs/${jobId}/retry`, { method: "POST" });
    setMessage("Job reencolado.", "success");
    await loadJobs();
  } catch (err) {
    setMessage(`No se pudo reintentar: ${err.message}`, "error");
  }
}

const AUTOPILOT_STATUS_LABELS = {
  running: "en curso",
  done: "completado",
  cancelled: "cancelado",
};

function renderAutopilotStatus() {
  const box = $("autopilotStatus");
  const run = state.autopilotRun;
  if (!run) {
    box.hidden = true;
    return;
  }
  box.hidden = false;
  const lastLog = (run.logs || [])[run.logs.length - 1] || "";
  const isRunning = run.status === "running";
  const pct = run.total ? Math.round((run.processed / run.total) * 100) : 0;
  box.innerHTML = `
    <div class="jobHead">
      <div class="jobTitle">
        <strong>🤖 ${run.scope}</strong>
        <span>${run.processed}/${run.total} · ${run.queued.length} encolados · ${run.skipped.length} omitidos</span>
      </div>
      <span class="status ${run.status}">${AUTOPILOT_STATUS_LABELS[run.status] || run.status}</span>
    </div>
    <div class="autopilotBar"><div class="autopilotBarFill" style="width:${pct}%"></div></div>
    <div class="jobMeta">
      <span class="lastLog">${lastLog}</span>
      ${isRunning ? `<button type="button" class="ghost small" id="autopilotCancelBtn">Detener</button>` : ""}
    </div>
  `;
  if (isRunning) {
    $("autopilotCancelBtn").addEventListener("click", cancelAutopilot);
  }
}

async function cancelAutopilot() {
  if (!state.autopilotRun) return;
  try {
    await api(`/api/autopilot/${state.autopilotRun.id}/cancel`, { method: "POST" });
  } catch (err) {
    setMessage(`No se pudo detener: ${err.message}`, "error", "autoMsg");
  }
}

async function startAutopilot() {
  const scope = document.querySelector('input[name="autoScope"]:checked').value;
  const assets = [];
  if ($("autoAssetAudio").checked) assets.push("audio");
  if ($("autoAssetVideo").checked) assets.push("video");
  if (!assets.length) {
    setMessage("Marca al menos audio o video.", "error", "autoMsg");
    return;
  }

  const body = {
    assets,
    min_score: Number($("autoThreshold").value) / 100,
    overwrite: $("autoOverwrite").checked,
    refresh: $("autoRefresh").checked,
  };
  if (scope === "library") {
    if (!state.libraryFilter) {
      setMessage("Elige una pestaña de biblioteca arriba primero.", "error", "autoMsg");
      return;
    }
    body.library = state.libraryFilter;
  } else {
    if (!state.selectedDestination) {
      setMessage("Elige una serie o película en el paso 1.", "error", "autoMsg");
      return;
    }
    body.destination = state.selectedDestination;
  }

  $("autopilotStartBtn").disabled = true;
  setMessage("Iniciando autopiloto...", "", "autoMsg");
  try {
    const data = await api("/api/autopilot", { method: "POST", body: JSON.stringify(body) });
    state.autopilotRun = data.run;
    renderAutopilotStatus();
    setMessage(`Autopiloto en marcha sobre ${data.run.total} destino(s).`, "success", "autoMsg");
  } catch (err) {
    setMessage(`No se pudo iniciar: ${err.message}`, "error", "autoMsg");
  } finally {
    $("autopilotStartBtn").disabled = false;
  }
}

const STATUS_LABELS = {
  queued: "en cola",
  running: "en curso",
  done: "completado",
  failed: "fallido",
  cancelled: "cancelado",
};

function renderJobs() {
  $("queueCount").textContent = `${state.jobs.length} jobs`;
  const box = $("jobs");
  box.innerHTML = "";
  if (!state.jobs.length) {
    box.innerHTML = `<div class="emptyState">Todavía no hay trabajos en cola. Busca un opening arriba para empezar.</div>`;
    return;
  }
  for (const job of state.jobs) {
    const el = document.createElement("article");
    el.className = "job";
    const title = job.result?.title || job.url;
    const canCancel = job.status === "queued" || job.status === "running";
    const canRetry = job.status === "failed" || job.status === "cancelled";
    const logs = job.logs || [];
    const lastLog = logs[logs.length - 1] || "";
    const expanded = state.expandedJobs.has(job.id);
    el.innerHTML = `
      <div class="jobHead">
        <div class="jobTitle">
          <strong>${title}</strong>
          <span>${job.destination}</span>
        </div>
        <span class="status ${job.status}">${STATUS_LABELS[job.status] || job.status}</span>
      </div>
      <div class="jobMeta">
        ${lastLog && !expanded ? `<span class="lastLog">${lastLog}</span>` : `<span></span>`}
        <div class="jobActions">
          ${logs.length ? `<button type="button" class="linkBtn" data-action="toggle" data-id="${job.id}">${expanded ? "Ocultar registro" : "Ver registro"}</button>` : ""}
          ${canCancel ? `<button type="button" class="ghost small" data-action="cancel" data-id="${job.id}">Cancelar</button>` : ""}
          ${canRetry ? `<button type="button" class="ghost small" data-action="retry" data-id="${job.id}">Reintentar</button>` : ""}
        </div>
      </div>
      ${expanded ? `<pre class="logs">${logs.join("\n")}</pre>` : ""}
    `;
    box.appendChild(el);
  }
}

function onJobsClick(ev) {
  const btn = ev.target.closest("button[data-action]");
  if (!btn) return;
  const { action, id } = btn.dataset;
  if (action === "cancel") cancelJob(id);
  if (action === "retry") retryJob(id);
  if (action === "toggle") {
    if (state.expandedJobs.has(id)) state.expandedJobs.delete(id);
    else state.expandedJobs.add(id);
    renderJobs();
  }
}

async function loadJobs() {
  const data = await api("/api/jobs");
  state.jobs = data.jobs;
  renderJobs();
}

function connectEvents() {
  const events = new EventSource("/api/events");
  events.addEventListener("message", (ev) => {
    const payload = JSON.parse(ev.data);
    if (payload.type === "job") {
      const job = payload.job;
      const idx = state.jobs.findIndex((j) => j.id === job.id);
      if (idx >= 0) state.jobs[idx] = job;
      else state.jobs.unshift(job);
      renderJobs();
      loadItems().catch(() => {});
    }
    if (payload.type === "autopilot") {
      if (!state.autopilotRun || state.autopilotRun.id === payload.run.id) {
        state.autopilotRun = payload.run;
        renderAutopilotStatus();
      }
    }
  });
}

$("refreshItems").addEventListener("click", loadItems);
$("destinationFilter").addEventListener("input", renderItems);
$("previewBtn").addEventListener("click", previewUrl);
$("enqueueBtn").addEventListener("click", enqueue);
$("searchBtn").addEventListener("click", searchYoutube);
$("autoSearchBtn").addEventListener("click", autoSearch);
$("searchQuery").addEventListener("keydown", (ev) => {
  if (ev.key === "Enter") {
    ev.preventDefault();
    searchYoutube();
  }
});
$("jobs").addEventListener("click", onJobsClick);
$("modeManualBtn").addEventListener("click", () => setMode("manual"));
$("modeAutoBtn").addEventListener("click", () => setMode("auto"));
document.querySelectorAll(".statusTab").forEach((btn) => {
  btn.addEventListener("click", () => {
    state.statusFilter = btn.dataset.status;
    document.querySelectorAll(".statusTab").forEach((b) => b.classList.toggle("selected", b === btn));
    renderItems();
  });
});
$("autoThreshold").addEventListener("input", () => {
  $("autoThresholdLabel").textContent = `${$("autoThreshold").value}%`;
});
$("autopilotStartBtn").addEventListener("click", startAutopilot);

loadItems().catch((err) => setMessage(err.message, "error"));
loadJobs().catch(() => {});
connectEvents();
