"use strict";

const state = {
  items: [],
  libraries: [],
  filterLibrary: "all",
  filterStatus: "all",
  search: "",
  currentItem: null,
  currentCandidates: [],
  selectedCandidateUrl: null,
  jobs: [],
  scan: {},
};

const el = (id) => document.getElementById(id);
const escapeHtml = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

function fmtDuration(sec) {
  if (!sec && sec !== 0) return "";
  const m = Math.floor(sec / 60);
  const s = Math.round(sec % 60);
  return `${m}:${String(s).padStart(2, "0")}`;
}

function fmtWhen(iso) {
  if (!iso) return "";
  try {
    const d = new Date(iso);
    return d.toLocaleString("es-ES", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
  } catch {
    return iso;
  }
}

async function api(path, opts) {
  const res = await fetch(path, {
    headers: opts && opts.body ? { "Content-Type": "application/json" } : undefined,
    ...opts,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail || detail;
    } catch {}
    throw new Error(detail);
  }
  const ctype = res.headers.get("content-type") || "";
  return ctype.includes("application/json") ? res.json() : null;
}

function posterUrl(item) {
  return `/api/poster?item=${encodeURIComponent(item.path)}`;
}

function initials(name) {
  return (name || "?")
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((w) => w[0].toUpperCase())
    .join("");
}

function itemStatus(item) {
  if (item.has_audio && item.has_video) return "done";
  if (item.needs_review) return "review";
  return "missing";
}

// ---------------------------------------------------------------------------
// Grid
// ---------------------------------------------------------------------------

function renderLibraryChips() {
  const wrap = el("libraryChips");
  const chips = ["all", ...state.libraries]
    .map((lib) => {
      const label = lib === "all" ? "Todas" : lib;
      const selected = state.filterLibrary === lib ? "selected" : "";
      return `<button class="chip ${selected}" data-lib="${escapeHtml(lib)}" type="button">${escapeHtml(label)}</button>`;
    })
    .join("");
  wrap.innerHTML = chips;
  wrap.querySelectorAll("[data-lib]").forEach((btn) => {
    btn.addEventListener("click", () => {
      state.filterLibrary = btn.dataset.lib;
      renderLibraryChips();
      renderGrid();
    });
  });
}

function filteredItems() {
  const q = state.search.trim().toLowerCase();
  return state.items.filter((item) => {
    if (state.filterLibrary !== "all" && item.library !== state.filterLibrary) return false;
    const status = itemStatus(item);
    if (state.filterStatus === "review" && status !== "review") return false;
    if (state.filterStatus === "missing" && status === "done") return false;
    if (state.filterStatus === "done" && status !== "done") return false;
    if (q && !item.name.toLowerCase().includes(q)) return false;
    return true;
  });
}

function cardHtml(item) {
  const status = itemStatus(item);
  const badge =
    status === "done"
      ? '<span class="statusFlag done" title="Completo">✓</span>'
      : status === "review"
      ? '<span class="statusFlag review" title="Pendiente de revisión">👀</span>'
      : '<span class="statusFlag missing" title="Incompleto">–</span>';
  return `
    <button class="card" data-id="${escapeHtml(item.id)}" type="button">
      <span class="posterBox">
        <img class="posterImg" src="${posterUrl(item)}" alt="" loading="lazy"
             onerror="this.style.display='none'; this.nextElementSibling.style.display='flex';" />
        <span class="posterFallback">${escapeHtml(initials(item.name))}</span>
        ${badge}
      </span>
      <span class="cardTitle">${escapeHtml(item.name)}</span>
      <span class="cardLib">${escapeHtml(item.library)}</span>
    </button>`;
}

function renderGrid() {
  const items = filteredItems();
  el("emptyState").hidden = items.length > 0;
  el("grid").innerHTML = items.map(cardHtml).join("");
  el("grid").querySelectorAll(".card").forEach((card) => {
    card.addEventListener("click", () => openDrawer(card.dataset.id));
  });
  updateCounts();
}

function updateCounts() {
  const all = state.items.filter((i) => state.filterLibrary === "all" || i.library === state.filterLibrary);
  const review = all.filter((i) => itemStatus(i) === "review").length;
  const missing = all.filter((i) => itemStatus(i) !== "done").length;
  el("countReview").textContent = review ? `(${review})` : "";
  el("countMissing").textContent = missing ? `(${missing})` : "";
}

// ---------------------------------------------------------------------------
// Drawer (item detail: current install + candidates + manual search)
// ---------------------------------------------------------------------------

function currentInstalledHtml(item) {
  const rows = [];
  if (item.has_audio) {
    const src = item.audio_source;
    rows.push(
      `<div class="installedRow"><span class="installedIcon">🎵</span><div><strong>Audio</strong>${
        src ? `<span class="installedMeta">${escapeHtml(src.title || "")}</span>` : ""
      }</div></div>`
    );
  }
  if (item.has_video) {
    const src = item.video_source;
    rows.push(
      `<div class="installedRow"><span class="installedIcon">🎬</span><div><strong>Vídeo</strong>${
        src ? `<span class="installedMeta">${escapeHtml(src.title || "")}</span>` : ""
      }</div></div>`
    );
  }
  if (!rows.length) return '<p class="hint">Nada instalado todavía.</p>';
  return rows.join("");
}

function candidateHtml(c, idx) {
  const pct = Math.round((c.score || 0) * 100);
  const barClass = pct >= 75 ? "high" : pct >= 45 ? "mid" : "low";
  return `
    <label class="candidate">
      <input type="radio" name="candidate" value="${escapeHtml(c.webpage_url)}" data-idx="${idx}" ${idx === 0 ? "checked" : ""} />
      <img class="candidateThumb" src="${escapeHtml(c.thumbnail || "")}" alt="" loading="lazy" />
      <span class="candidateBody">
        <span class="candidateTitle">${escapeHtml(c.title || "(sin título)")}</span>
        <span class="candidateMeta">${escapeHtml(c.uploader || "")} · ${fmtDuration(c.duration)}</span>
        <span class="scoreBar"><span class="scoreFill ${barClass}" style="width:${pct}%"></span></span>
      </span>
      <a href="${escapeHtml(c.webpage_url)}" target="_blank" rel="noopener" class="ytLink" title="Ver en YouTube">↗</a>
    </label>`;
}

function renderCandidates(list) {
  state.currentCandidates = list;
  const wrap = el("candidatesList");
  if (!list.length) {
    wrap.innerHTML = '<p class="hint">Sin candidatos. Prueba a buscar manualmente abajo.</p>';
    el("installBtn").disabled = true;
    return;
  }
  wrap.innerHTML = list.map(candidateHtml).join("");
  wrap.querySelectorAll('input[name="candidate"]').forEach((r) =>
    r.addEventListener("change", () => {
      state.selectedCandidateUrl = r.value;
      el("installBtn").disabled = false;
    })
  );
  state.selectedCandidateUrl = list[0].webpage_url;
  el("installBtn").disabled = false;
}

async function openDrawer(itemId) {
  const item = state.items.find((i) => i.id === itemId);
  if (!item) return;
  state.currentItem = item;
  state.selectedCandidateUrl = null;

  el("drawerTitle").textContent = item.name;
  el("drawerLib").textContent = item.library;
  el("drawerKind").textContent = item.kind === "movie" ? "Película" : "Serie";
  el("drawerPoster").innerHTML = `<img src="${posterUrl(item)}" alt="" onerror="this.remove()" />`;
  el("drawerBadges").innerHTML = [
    item.has_audio ? '<span class="badgePill ok">🎵 audio</span>' : '<span class="badgePill">🎵 falta</span>',
    item.has_video ? '<span class="badgePill ok">🎬 vídeo</span>' : '<span class="badgePill">🎬 falta</span>',
  ].join("");
  el("installedCurrent").innerHTML = currentInstalledHtml(item);
  el("assetAudio").checked = !item.has_audio;
  el("assetVideo").checked = !item.has_video;
  el("manualPreview").innerHTML = "";
  el("manualUrl").value = "";
  el("manualQuery").value = "";
  el("drawerMsg").textContent = "";
  el("candidatesList").innerHTML = '<p class="hint">Buscando…</p>';
  el("candidatesHint").textContent = item.needs_review
    ? "Kaimaku ya buscó y no encontró nada suficientemente fiable — revisa y elige a mano."
    : "Kaimaku ya ha buscado y marcado el mejor candidato.";
  el("installBtn").disabled = true;

  el("drawerOverlay").hidden = false;

  try {
    const reviewData = await api(`/api/item/review?item=${encodeURIComponent(item.path)}`);
    if (reviewData.candidates && reviewData.candidates.length) {
      renderCandidates(reviewData.candidates);
      return;
    }
  } catch {}
  await searchCandidates();
}

async function searchCandidates() {
  if (!state.currentItem) return;
  el("candidatesList").innerHTML = '<p class="hint">Buscando…</p>';
  try {
    const data = await api(`/api/item/candidates?item=${encodeURIComponent(state.currentItem.path)}`, {
      method: "POST",
      body: JSON.stringify({ limit: 8 }),
    });
    renderCandidates(data.results || []);
  } catch (exc) {
    el("candidatesList").innerHTML = `<p class="hint">Error buscando: ${escapeHtml(exc.message)}</p>`;
  }
}

function closeDrawer() {
  el("drawerOverlay").hidden = true;
  state.currentItem = null;
}

async function installSelected() {
  if (!state.currentItem || !state.selectedCandidateUrl) return;
  const assets = [];
  if (el("assetAudio").checked) assets.push("audio");
  if (el("assetVideo").checked) assets.push("video");
  if (!assets.length) {
    el("drawerMsg").textContent = "Elige al menos audio o vídeo.";
    return;
  }
  el("installBtn").disabled = true;
  el("drawerMsg").textContent = "Instalando…";
  try {
    await api("/api/jobs", {
      method: "POST",
      body: JSON.stringify({ url: state.selectedCandidateUrl, destination: state.currentItem.path, assets, refresh: true }),
    });
    el("drawerMsg").textContent = "En cola — sigue el progreso en Actividad 🕘";
    openActivity();
  } catch (exc) {
    el("drawerMsg").textContent = `Error: ${exc.message}`;
    el("installBtn").disabled = false;
  }
}

// ---------------------------------------------------------------------------
// Manual search / paste-link
// ---------------------------------------------------------------------------

async function manualSearch() {
  const q = el("manualQuery").value.trim();
  if (!q) return;
  el("candidatesList").innerHTML = '<p class="hint">Buscando…</p>';
  try {
    const data = await api("/api/search", { method: "POST", body: JSON.stringify({ query: q, limit: 8 }) });
    renderCandidates(data.results || []);
  } catch (exc) {
    el("candidatesList").innerHTML = `<p class="hint">Error: ${escapeHtml(exc.message)}</p>`;
  }
}

async function manualPreview() {
  const url = el("manualUrl").value.trim();
  if (!url) return;
  el("manualPreview").innerHTML = '<p class="hint">Cargando…</p>';
  try {
    const data = await api("/api/preview", { method: "POST", body: JSON.stringify({ url }) });
    renderCandidates([{ ...data, score: 1, webpage_url: data.webpage_url }]);
    el("manualPreview").innerHTML = "";
  } catch (exc) {
    el("manualPreview").innerHTML = `<p class="hint">Error: ${escapeHtml(exc.message)}</p>`;
  }
}

// ---------------------------------------------------------------------------
// Activity (jobs + scan)
// ---------------------------------------------------------------------------

function jobStatusLabel(status) {
  return { queued: "En cola", running: "Descargando…", done: "✓ Completado", failed: "✗ Falló", cancelled: "Cancelado" }[status] || status;
}

function jobHtml(job) {
  const dest = job.destination.split("/").pop();
  const lastLog = job.logs[job.logs.length - 1] || "";
  return `
    <div class="jobRow status-${job.status}">
      <div class="jobHead">
        <strong>${escapeHtml(dest)}</strong>
        <span class="jobStatus">${jobStatusLabel(job.status)}</span>
      </div>
      <div class="jobMeta">${escapeHtml(job.result?.title || job.url)}</div>
      ${job.status === "running" ? `<div class="jobLog">${escapeHtml(lastLog)}</div>` : ""}
      ${job.status === "failed" ? `<div class="jobLog error">${escapeHtml(lastLog)}</div>` : ""}
      ${
        job.status === "queued" || job.status === "running"
          ? `<button class="linkBtn small" data-cancel="${job.id}" type="button">Cancelar</button>`
          : ""
      }
    </div>`;
}

function renderActivity() {
  const scan = state.scan;
  let scanText = "";
  if (scan.running) {
    scanText = "🔄 Escaneo automático en curso…";
  } else if (scan.last_summary && scan.last_finished) {
    const s = scan.last_summary;
    const parts = [];
    if (s.installed?.length) parts.push(`${s.installed.length} instalados`);
    if (s.new_review?.length) parts.push(`${s.new_review.length} a revisión`);
    if (s.errors?.length) parts.push(`${s.errors.length} errores`);
    scanText = `Último escaneo: ${fmtWhen(scan.last_finished)}${parts.length ? " — " + parts.join(", ") : " — nada que hacer"}`;
  } else {
    scanText = "Aún no se ha ejecutado ningún escaneo automático.";
  }
  el("scanSummary").textContent = scanText;

  const jobs = [...state.jobs].sort((a, b) => b.created_at - a.created_at).slice(0, 60);
  el("jobsList").innerHTML = jobs.length ? jobs.map(jobHtml).join("") : '<p class="hint">Sin actividad todavía.</p>';
  el("jobsList").querySelectorAll("[data-cancel]").forEach((btn) => {
    btn.addEventListener("click", () => api(`/api/jobs/${btn.dataset.cancel}/cancel`, { method: "POST" }).catch(() => {}));
  });

  const running = state.jobs.filter((j) => j.status === "running" || j.status === "queued").length;
  el("activityBadge").hidden = running === 0;
  el("activityBadge").textContent = running;
  el("scanDot").hidden = !scan.running;
}

function openActivity() {
  el("activityOverlay").hidden = false;
  refreshJobs();
}

function closeActivity() {
  el("activityOverlay").hidden = true;
}

async function refreshJobs() {
  try {
    const data = await api("/api/jobs");
    state.jobs = data.jobs;
    renderActivity();
  } catch {}
}

// ---------------------------------------------------------------------------
// Diagnostics
// ---------------------------------------------------------------------------

function statusRow(label, ok, detail) {
  const icon = ok ? "✅" : "⚠️";
  return `<div class="diagRow"><span>${icon} ${escapeHtml(label)}</span><span class="diagDetail">${escapeHtml(detail || "")}</span></div>`;
}

async function openDiagnostics() {
  el("statusModal").hidden = false;
  el("statusModalBody").innerHTML = '<p class="hint">Cargando…</p>';
  try {
    const s = await api("/api/status");
    const rows = [];
    rows.push(`<h4>Bibliotecas</h4>`);
    s.roots.forEach((r) => rows.push(statusRow(r.name, r.exists, r.exists ? `${r.items} elementos` : `no encontrada: ${r.path}`)));
    rows.push(`<h4>Servidores</h4>`);
    rows.push(statusRow("Jellyfin", s.jellyfin.configured && s.jellyfin.reachable, s.jellyfin.configured ? (s.jellyfin.reachable ? s.jellyfin.url : "no responde") : "no configurado"));
    rows.push(statusRow("Emby", s.emby.configured && s.emby.reachable, s.emby.configured ? (s.emby.reachable ? s.emby.url : "no responde") : "no configurado"));
    rows.push(statusRow("Telegram", s.telegram, s.telegram ? "avisos activados" : "no configurado (opcional)"));
    rows.push(`<h4>Escaneo automático</h4>`);
    rows.push(
      `<div class="diagRow"><span>Cada ${s.auto_scan.interval_hours}h</span><span class="diagDetail">umbral ${Math.round(
        s.auto_scan.min_score * 100
      )}% · ${s.auto_scan.assets.join(" + ")}</span></div>`
    );
    if (s.auto_scan.last_finished) rows.push(`<div class="diagRow"><span>Último: ${fmtWhen(s.auto_scan.last_finished)}</span></div>`);
    el("statusModalBody").innerHTML = rows.join("");
  } catch (exc) {
    el("statusModalBody").innerHTML = `<p class="hint">Error: ${escapeHtml(exc.message)}</p>`;
  }
}

// ---------------------------------------------------------------------------
// Live updates (SSE)
// ---------------------------------------------------------------------------

function connectEvents() {
  const source = new EventSource("/api/events");
  source.addEventListener("message", (ev) => {
    try {
      const payload = JSON.parse(ev.data);
      if (payload.type === "job") {
        const idx = state.jobs.findIndex((j) => j.id === payload.job.id);
        if (idx >= 0) state.jobs[idx] = payload.job;
        else state.jobs.unshift(payload.job);
        renderActivity();
        if (payload.job.status === "done") loadLibrary();
      } else if (payload.type === "scan") {
        state.scan = payload.scan;
        renderActivity();
      } else if (payload.type === "scan_progress") {
        if (payload.outcome === "installed") loadLibrary();
      }
    } catch {}
  });
  source.onerror = () => {
    source.close();
    setTimeout(connectEvents, 4000);
  };
}

// ---------------------------------------------------------------------------
// Boot
// ---------------------------------------------------------------------------

async function loadLibrary() {
  const data = await api("/api/library");
  state.items = data.items;
  state.libraries = data.libraries;
  el("brandSub").textContent = `${data.items.length} elementos en ${data.libraries.length} bibliotecas`;
  renderLibraryChips();
  renderGrid();
}

async function loadScanStatus() {
  try {
    const data = await api("/api/scan/status");
    state.scan = data.scan;
    renderActivity();
  } catch {}
}

function wireStatic() {
  el("searchInput").addEventListener("input", (e) => {
    state.search = e.target.value;
    renderGrid();
  });
  document.querySelectorAll("#statusChips .chip").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll("#statusChips .chip").forEach((b) => b.classList.remove("selected"));
      btn.classList.add("selected");
      state.filterStatus = btn.dataset.filter;
      renderGrid();
    });
  });

  el("drawerClose").addEventListener("click", closeDrawer);
  el("drawerOverlay").addEventListener("click", (e) => {
    if (e.target === el("drawerOverlay")) closeDrawer();
  });
  el("researchBtn").addEventListener("click", searchCandidates);
  el("installBtn").addEventListener("click", installSelected);
  el("manualSearchBtn").addEventListener("click", manualSearch);
  el("manualPreviewBtn").addEventListener("click", manualPreview);

  el("activityBtn").addEventListener("click", openActivity);
  el("activityClose").addEventListener("click", closeActivity);
  el("activityOverlay").addEventListener("click", (e) => {
    if (e.target === el("activityOverlay")) closeActivity();
  });

  el("statusBtn").addEventListener("click", openDiagnostics);
  el("statusModalClose").addEventListener("click", () => (el("statusModal").hidden = true));
  el("statusModal").addEventListener("click", (e) => {
    if (e.target === el("statusModal")) el("statusModal").hidden = true;
  });

  el("scanNowBtn").addEventListener("click", async () => {
    el("scanDot").hidden = false;
    try {
      await api("/api/scan/run", { method: "POST" });
      openActivity();
    } catch (exc) {
      alert(exc.message);
    }
  });
}

async function boot() {
  wireStatic();
  await loadLibrary();
  await loadScanStatus();
  await refreshJobs();
  connectEvents();
  setInterval(loadLibrary, 5 * 60 * 1000);
}

boot();
