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
  const welcome = state.items.length === 0 && state.needsSetup;
  el("welcomeState").hidden = !welcome;
  el("emptyState").hidden = welcome || items.length > 0;
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
  el("drawerMsg").textContent = item.last_error ? `Último intento automático falló: ${item.last_error}` : "";
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
    if (!s.roots.length) rows.push(statusRow("Ninguna configurada", false, "usa el botón 🛠"));
    s.roots.forEach((r) => rows.push(statusRow(r.name, r.exists, r.exists ? `${r.items} elementos · ${r.path}` : `no encontrada: ${r.path}`)));
    rows.push(`<h4>Servidores</h4>`);
    if (!s.servers.length) rows.push(statusRow("Jellyfin / Emby", false, "sin conectar (sin pósters ni refresco automático)"));
    s.servers.forEach((srv) =>
      rows.push(
        statusRow(
          `${KIND_LABEL[srv.kind]} «${srv.name || ""}»`,
          srv.auth,
          srv.auth ? srv.url : srv.reachable ? "responde pero rechaza el acceso: vuelve a conectarlo en 🛠" : `no responde: ${srv.url}`
        )
      )
    );
    rows.push(statusRow("Telegram", s.telegram, s.telegram ? "avisos activados" : "no configurado (opcional)"));
    rows.push(`<h4>Escaneo automático</h4>`);
    rows.push(
      `<div class="diagRow"><span>${s.auto_scan.interval_hours > 0 ? `Cada ${s.auto_scan.interval_hours}h` : "Desactivado"}</span><span class="diagDetail">umbral ${Math.round(
        s.auto_scan.min_score * 100
      )}% · ${s.auto_scan.assets.join(" + ")}</span></div>`
    );
    rows.push(`<h4>Sistema</h4>`);
    rows.push(statusRow(`Kaimaku ${s.version}`, true, `archivos creados como: ${s.running_as}`));
    if (s.auto_scan.last_finished) rows.push(`<div class="diagRow"><span>Último: ${fmtWhen(s.auto_scan.last_finished)}</span></div>`);
    el("statusModalBody").innerHTML = rows.join("");
  } catch (exc) {
    el("statusModalBody").innerHTML = `<p class="hint">Error: ${escapeHtml(exc.message)}</p>`;
  }
}

// ---------------------------------------------------------------------------
// Setup wizard: servidor -> bibliotecas -> listo
// ---------------------------------------------------------------------------

const setup = {
  step: 1,
  state: null,
  discovered: null, // null = aún buscando
  chosenUrl: "",
  useKey: false,
  manual: false, // sin Jellyfin/Emby: carpetas elegidas a mano
  map: null,
  libs: [],
  browser: null, // {path, data, onPick}
  busy: false,
};

const KIND_LABEL = { jellyfin: "Jellyfin", emby: "Emby" };
const TYPE_LABEL = { movies: "Películas", tvshows: "Series", mixed: "Mixta", "": "Mixta" };

function wizMsg(text, isError) {
  el("wizMsg").textContent = text || "";
  el("wizMsg").classList.toggle("error", !!isError);
}

async function openSetup(step) {
  el("setupModal").hidden = false;
  setup.browser = null;
  setup.map = null;
  setup.manual = false;
  wizMsg("");
  el("wizBody").innerHTML = '<p class="hint">Cargando…</p>';
  try {
    setup.state = await api("/api/setup/state");
  } catch (exc) {
    el("wizBody").innerHTML = `<p class="hint">Error: ${escapeHtml(exc.message)}</p>`;
    return;
  }
  const s = setup.state;
  if (!s.mounts.host.mounted && !s.mounts.media.mounted) setup.step = 0;
  else setup.step = step || 1;
  if (setup.step === 1 && setup.discovered === null) discoverServers();
  renderSetup();
}

function closeSetup() {
  el("setupModal").hidden = true;
}

async function reloadSetupState() {
  setup.state = await api("/api/setup/state");
}

async function discoverServers() {
  setup.discovered = null;
  renderSetup();
  try {
    const data = await api("/api/setup/discover");
    setup.discovered = data.servers || [];
    const connected = new Set((setup.state?.servers || []).map((s) => s.kind));
    const first = setup.discovered.find((d) => !connected.has(d.kind));
    if (first && !setup.chosenUrl) setup.chosenUrl = first.url;
  } catch {
    setup.discovered = [];
  }
  renderSetup();
}

function renderSetup() {
  document.querySelectorAll("#wizSteps li").forEach((li) => {
    const n = Number(li.dataset.step);
    li.classList.toggle("active", n === setup.step);
    li.classList.toggle("done", n < setup.step);
  });
  el("wizSteps").hidden = setup.step === 0;
  el("wizBack").hidden = setup.step <= 1 || !!setup.browser;
  el("wizNext").hidden = !!setup.browser;
  const body = el("wizBody");
  if (setup.browser) return renderBrowser(body);
  if (setup.step === 0) return renderStepMount(body);
  if (setup.step === 1) return renderStepServer(body);
  if (setup.step === 2) return renderStepLibraries(body);
  return renderStepDone(body);
}

// --- paso 0: no hay nada montado ----------------------------------------------

function renderStepMount(body) {
  body.innerHTML = `
    <h2>Kaimaku no ve tus archivos</h2>
    <p>Falta decirle a Docker dónde están tus series y películas. En tu <code>docker-compose.yml</code>
       (o en la plantilla de Unraid), la línea del volumen tiene que apuntar a la carpeta grande donde guardas todo:</p>
    <table class="mountTable">
      <tr><td>Unraid</td><td><code>- /mnt/user:/host</code></td></tr>
      <tr><td>Synology</td><td><code>- /volume1:/host</code></td></tr>
      <tr><td>TrueNAS / OMV</td><td><code>- /mnt:/host</code></td></tr>
      <tr><td>Linux</td><td><code>- /home:/host</code> <span class="hint">(o donde estén tus medios)</span></td></tr>
      <tr><td>Windows</td><td><code>- D:/:/host</code> <span class="hint">(la unidad de tus medios)</span></td></tr>
    </table>
    <p class="hint">No hace falta afinar: Kaimaku buscará dentro tus bibliotecas. Después vuelve a crear el contenedor
       (<code>docker compose up -d</code>) y pulsa «Comprobar de nuevo».</p>`;
  el("wizNext").textContent = "Comprobar de nuevo";
  el("wizNext").disabled = false;
}

// --- paso 1: servidor -----------------------------------------------------------

function renderStepServer(body) {
  const s = setup.state;
  const connected = s.servers || [];
  const connectedKinds = new Set(connected.map((c) => c.kind));
  const rows = connected
    .map(
      (c) => `
      <div class="serverRow ok">
        <span>✅ <strong>${KIND_LABEL[c.kind]}</strong> «${escapeHtml(c.name || "")}»
          <span class="hint">${escapeHtml(c.url)}${c.user ? " · " + escapeHtml(c.user) : ""}</span></span>
        ${c.source === "web" ? `<button class="linkBtn" data-forget="${c.kind}" type="button">Desconectar</button>` : '<span class="hint">(docker-compose)</span>'}
      </div>`
    )
    .join("");

  let found = "";
  if (setup.discovered === null) {
    found = '<p class="hint">🔎 Buscando tu servidor en este equipo…</p>';
  } else {
    const others = setup.discovered.filter((d) => !connectedKinds.has(d.kind));
    found = others.length
      ? others
          .map(
            (d) => `
          <label class="serverRow pick ${setup.chosenUrl === d.url ? "selected" : ""}">
            <input type="radio" name="srvPick" value="${escapeHtml(d.url)}" ${setup.chosenUrl === d.url ? "checked" : ""} />
            <span><strong>${KIND_LABEL[d.kind]}</strong> «${escapeHtml(d.name)}» <span class="hint">${escapeHtml(d.version || "")} · ${escapeHtml(d.url)}</span></span>
          </label>`
          )
          .join("")
      : connected.length
      ? ""
      : '<p class="hint">No he encontrado ningún servidor automáticamente. Escribe su dirección abajo (la IP del equipo donde está Jellyfin/Emby).</p>';
  }

  const showForm = connected.length < 2;
  body.innerHTML = `
    <h2>Conecta tu Jellyfin o Emby</h2>
    <p class="hint">Kaimaku le pregunta a tu servidor qué bibliotecas tienes y dónde están. Necesita un usuario
       <strong>administrador</strong>. La contraseña no se guarda: solo se usa para obtener un acceso propio para Kaimaku.</p>
    ${rows}
    ${showForm ? found : ""}
    ${
      showForm
        ? `
    <div class="loginBox">
      <label class="field"><span>Dirección del servidor</span>
        <input id="srvUrl" type="text" placeholder="192.168.1.10:8096" value="${escapeHtml(setup.chosenUrl)}" /></label>
      ${
        setup.useKey
          ? `<label class="field"><span>API key</span><input id="srvKey" type="password" autocomplete="off" /></label>`
          : `<div class="twoCols">
               <label class="field"><span>Usuario administrador</span><input id="srvUser" type="text" autocomplete="username" /></label>
               <label class="field"><span>Contraseña</span><input id="srvPass" type="password" autocomplete="current-password" /></label>
             </div>`
      }
      <div class="loginActions">
        <button id="srvLogin" class="primary" type="button">Conectar</button>
        <button id="srvToggleKey" class="linkBtn" type="button">${setup.useKey ? "Usar usuario y contraseña" : "Prefiero usar una API key"}</button>
        <button id="srvRescan" class="linkBtn" type="button">↻ Buscar otra vez</button>
      </div>
    </div>`
        : ""
    }
    <p class="hint manualLink"><button id="srvManual" class="linkBtn" type="button">No uso Jellyfin ni Emby → elegiré las carpetas a mano</button></p>`;

  body.querySelectorAll('input[name="srvPick"]').forEach((r) =>
    r.addEventListener("change", () => {
      setup.chosenUrl = r.value;
      renderSetup();
    })
  );
  body.querySelectorAll("[data-forget]").forEach((b) =>
    b.addEventListener("click", async () => {
      await api("/api/setup/forget", { method: "POST", body: JSON.stringify({ kind: b.dataset.forget }) });
      await reloadSetupState();
      renderSetup();
    })
  );
  if (showForm) {
    el("srvUrl").addEventListener("input", (e) => (setup.chosenUrl = e.target.value));
    el("srvLogin").addEventListener("click", serverLogin);
    el("srvToggleKey").addEventListener("click", () => {
      setup.useKey = !setup.useKey;
      renderSetup();
    });
    el("srvRescan").addEventListener("click", discoverServers);
    body.querySelectorAll(".loginBox input").forEach((i) =>
      i.addEventListener("keydown", (e) => e.key === "Enter" && serverLogin())
    );
  }
  el("srvManual").addEventListener("click", () => {
    setup.manual = true;
    setup.step = 2;
    renderSetup();
  });
  el("wizNext").textContent = "Siguiente →";
  el("wizNext").disabled = !connected.length;
}

async function serverLogin() {
  const url = (el("srvUrl").value || "").trim();
  if (!url) return wizMsg("Escribe la dirección de tu servidor.", true);
  const body = setup.useKey
    ? { url, api_key: el("srvKey").value.trim() }
    : { url, username: el("srvUser").value.trim(), password: el("srvPass").value };
  el("srvLogin").disabled = true;
  wizMsg("Conectando…");
  try {
    const res = await api("/api/setup/login", { method: "POST", body: JSON.stringify(body) });
    wizMsg(`Conectado a ${KIND_LABEL[res.server.kind]} «${res.server.name}» ✓`);
    setup.chosenUrl = "";
    await reloadSetupState();
    const connected = new Set(setup.state.servers.map((s) => s.kind));
    const next = (setup.discovered || []).find((d) => !connected.has(d.kind));
    if (next) setup.chosenUrl = next.url;
    renderSetup();
  } catch (exc) {
    wizMsg(exc.message, true);
    el("srvLogin").disabled = false;
  }
}

// --- paso 2: bibliotecas -------------------------------------------------------

function libsFromState() {
  // Lo ya guardado (al reabrir el asistente) se respeta tal cual.
  return (setup.state.libraries || []).map((l) => ({ ...l, confidence: 1, matched: null, total: null }));
}

async function runMapping() {
  setup.map = "loading";
  renderSetup();
  try {
    const data = await api("/api/setup/map", { method: "POST", body: "{}" });
    setup.map = data;
    const saved = new Map((setup.state.libraries || []).map((l) => [l.path, l]));
    setup.libs = data.libraries.map((l) => {
      const prev = l.path && saved.get(l.path);
      return { ...l, enabled: prev ? prev.enabled : !!l.path };
    });
    // carpetas añadidas a mano en una configuración anterior
    for (const prev of saved.values()) {
      if (!setup.libs.some((l) => l.path === prev.path)) setup.libs.push({ ...prev, manual: true });
    }
  } catch (exc) {
    setup.map = { error: exc.message };
  }
  renderSetup();
}

function renderStepLibraries(body) {
  if (!setup.manual && setup.map === null) {
    runMapping();
    return;
  }
  if (setup.manual && setup.map === null) {
    setup.map = { libraries: [], errors: [] };
    setup.libs = libsFromState();
  }
  if (setup.map === "loading") {
    body.innerHTML = `
      <h2>Buscando tus bibliotecas…</h2>
      <p class="hint">Kaimaku está mirando dentro de las carpetas que has montado para localizar cada biblioteca de tu servidor.
         Suele tardar unos segundos (algo más la primera vez en discos grandes).</p>
      <div class="spinner"></div>`;
    el("wizNext").disabled = true;
    return;
  }
  if (setup.map.error) {
    body.innerHTML = `<h2>Tus bibliotecas</h2><p class="message error">${escapeHtml(setup.map.error)}</p>
      <button id="libRetry" class="ghostBtn" type="button">↻ Reintentar</button>`;
    el("libRetry").addEventListener("click", runMapping);
    el("wizNext").disabled = true;
    return;
  }

  const rows = setup.libs
    .map((l, i) => {
      const where = l.path
        ? `<span class="libPath ok">✓ ${escapeHtml(l.path)}${
            l.total ? ` · ${l.matched}/${l.total} títulos coinciden` : ""
          }</span>`
        : `<span class="libPath warn">⚠ No la encuentro en lo que has montado${
            l.guess ? ` (¿quizá ${escapeHtml(l.guess)}?)` : ""
          }</span>`;
      const servers = Object.keys(l.servers || {})
        .map((k) => KIND_LABEL[k])
        .join(" + ");
      return `
        <div class="libRow ${l.enabled ? "" : "off"}">
          <input type="checkbox" data-lib="${i}" ${l.enabled ? "checked" : ""} ${l.path ? "" : "disabled"} />
          <div class="libInfo">
            <strong>${escapeHtml(l.label)}</strong>
            <span class="hint">${escapeHtml(TYPE_LABEL[l.type] ?? "")}${servers ? " · " + servers : l.manual ? " · añadida a mano" : ""}</span>
            ${where}
          </div>
          <button class="linkBtn" data-pick="${i}" type="button">${l.path ? "Cambiar" : "Elegir carpeta…"}</button>
        </div>`;
    })
    .join("");

  const errors = (setup.map.errors || []).map((e) => `<p class="message error">${escapeHtml(e)}</p>`).join("");
  const missing = setup.libs.filter((l) => !l.path).length;
  body.innerHTML = `
    <h2>Tus bibliotecas</h2>
    <p class="hint">${
      setup.manual
        ? "Añade las carpetas donde están tus series o películas (cada una con una carpeta por título dentro)."
        : missing
        ? `He localizado ${setup.libs.length - missing} de ${setup.libs.length}. Las que faltan puedes elegirlas a mano o dejarlas sin marcar.`
        : "He localizado todas tus bibliotecas. Desmarca las que no quieras que Kaimaku toque."
    }</p>
    ${errors}
    <div class="libList">${rows || '<p class="hint">Todavía no hay ninguna carpeta.</p>'}</div>
    <button id="libAdd" class="ghostBtn" type="button">＋ Añadir una carpeta a mano</button>
    ${setup.map.index?.truncated ? '<p class="hint">Lo montado es muy grande y no lo he recorrido entero: si falta alguna, elígela a mano.</p>' : ""}`;

  body.querySelectorAll("[data-lib]").forEach((cb) =>
    cb.addEventListener("change", () => {
      setup.libs[Number(cb.dataset.lib)].enabled = cb.checked;
      renderSetup();
    })
  );
  body.querySelectorAll("[data-pick]").forEach((b) =>
    b.addEventListener("click", () => {
      const lib = setup.libs[Number(b.dataset.pick)];
      openBrowser(lib.path || lib.guess || "", (path) => {
        lib.path = path;
        lib.enabled = true;
        lib.total = null;
      });
    })
  );
  el("libAdd").addEventListener("click", () =>
    openBrowser("", (path) => {
      if (setup.libs.some((l) => l.path === path)) return;
      setup.libs.push({ path, label: path.split("/").pop(), type: "", servers: {}, enabled: true, manual: true });
    })
  );
  el("wizNext").textContent = "Siguiente →";
  el("wizNext").disabled = !setup.libs.some((l) => l.enabled && l.path);
}

// --- explorador de carpetas -------------------------------------------------------

async function openBrowser(path, onPick) {
  setup.browser = { path, data: null, onPick };
  renderSetup();
  await browseTo(path);
}

async function browseTo(path) {
  try {
    setup.browser.data = await api(`/api/setup/browse?path=${encodeURIComponent(path || "")}`);
    setup.browser.path = setup.browser.data.path;
  } catch (exc) {
    setup.browser.data = { error: exc.message, dirs: [] };
  }
  renderSetup();
}

function renderBrowser(body) {
  const b = setup.browser;
  const d = b.data;
  if (!d) {
    body.innerHTML = '<p class="hint">Cargando…</p>';
    return;
  }
  const list = (d.dirs || [])
    .map((x) => `<button class="dirRow" data-dir="${escapeHtml(x.path)}" type="button">📁 ${escapeHtml(x.name)}</button>`)
    .join("");
  body.innerHTML = `
    <h2>Elige la carpeta de la biblioteca</h2>
    <p class="hint">Entra hasta la carpeta que contiene una subcarpeta por cada serie o película.</p>
    <div class="crumb">${d.path ? escapeHtml(d.path) : "Carpetas montadas"}</div>
    ${d.error ? `<p class="message error">${escapeHtml(d.error)}</p>` : ""}
    <div class="dirList">
      ${d.parent ? `<button class="dirRow up" data-dir="${escapeHtml(d.parent)}" type="button">⬆ Subir</button>` : ""}
      ${list || '<p class="hint">Sin subcarpetas.</p>'}
    </div>
    ${d.path ? `<p class="hint">${d.count} carpetas dentro${d.sample?.length ? ": " + escapeHtml(d.sample.join(", ")) + "…" : ""}</p>` : ""}
    <div class="loginActions">
      <button id="brUse" class="primary" type="button" ${d.path ? "" : "disabled"}>Usar esta carpeta</button>
      <button id="brCancel" class="linkBtn" type="button">Cancelar</button>
    </div>`;
  body.querySelectorAll("[data-dir]").forEach((x) => x.addEventListener("click", () => browseTo(x.dataset.dir)));
  el("brUse").addEventListener("click", () => {
    b.onPick(d.path);
    setup.browser = null;
    renderSetup();
  });
  el("brCancel").addEventListener("click", () => {
    setup.browser = null;
    renderSetup();
  });
}

// --- paso 3: ajustes y guardar ------------------------------------------------------

function renderStepDone(body) {
  const st = setup.state.settings;
  const locked = new Set(st.locked || []);
  const lock = (k) => (locked.has(k) ? 'disabled title="Fijado en el docker-compose"' : "");
  const enabled = setup.libs.filter((l) => l.enabled && l.path);
  const opts = [
    [6, "Cada 6 horas"],
    [12, "Cada 12 horas (recomendado)"],
    [24, "Una vez al día"],
    [0, "Nunca (solo cuando yo lo pida)"],
  ];
  if (!opts.some(([v]) => v === Number(st.scan_interval_hours))) opts.push([st.scan_interval_hours, `Cada ${st.scan_interval_hours} horas`]);
  body.innerHTML = `
    <h2>¡Listo!</h2>
    <p>Kaimaku cuidará de <strong>${enabled.length} ${enabled.length === 1 ? "biblioteca" : "bibliotecas"}</strong>:
       buscará en segundo plano el opening o tema de cada serie y película, instalará solo lo que tenga claro
       y te dejará el resto en «Revisión» para que elijas tú.</p>
    <label class="field"><span>¿Cada cuánto busca temas nuevos?</span>
      <select id="setInterval" ${lock("scan_interval_hours")}>
        ${opts.map(([v, t]) => `<option value="${v}" ${Number(st.scan_interval_hours) === v ? "selected" : ""}>${t}</option>`).join("")}
      </select></label>
    <div class="field"><span>¿Qué instala?</span>
      <div class="inline">
        <label class="chip small"><input id="setAudio" type="checkbox" ${st.assets.includes("audio") ? "checked" : ""} ${lock("assets")} /> 🎵 Música</label>
        <label class="chip small"><input id="setVideo" type="checkbox" ${st.assets.includes("video") ? "checked" : ""} ${lock("assets")} /> 🎬 Vídeo de fondo</label>
      </div></div>
    <details class="advanced">
      <summary>Avisos por Telegram (opcional)</summary>
      <label class="field"><span>Token del bot</span><input id="setTgToken" type="text" value="${escapeHtml(st.tg_bot_token)}" ${lock("tg_bot_token")} /></label>
      <div class="twoCols">
        <label class="field"><span>Chat ID</span><input id="setTgChat" type="text" value="${escapeHtml(st.tg_chat_id)}" ${lock("tg_chat_id")} /></label>
        <label class="field"><span>Tema (opcional)</span><input id="setTgTopic" type="text" value="${escapeHtml(st.tg_topic_id)}" ${lock("tg_topic_id")} /></label>
      </div>
    </details>`;
  el("wizNext").textContent = "Guardar y empezar";
  el("wizNext").disabled = false;
}

async function saveSetup() {
  const assets = [];
  if (el("setAudio").checked) assets.push("audio");
  if (el("setVideo").checked) assets.push("video");
  const payload = {
    libraries: setup.libs
      .filter((l) => l.path)
      .map((l) => ({ path: l.path, label: l.label, type: l.type, servers: l.servers || {}, enabled: !!l.enabled })),
    settings: {
      scan_interval_hours: Number(el("setInterval").value),
      assets,
      tg_bot_token: el("setTgToken").value,
      tg_chat_id: el("setTgChat").value,
      tg_topic_id: el("setTgTopic").value,
    },
  };
  el("wizNext").disabled = true;
  wizMsg("Guardando…");
  try {
    const res = await api("/api/setup/save", { method: "POST", body: JSON.stringify(payload) });
    wizMsg("Cargando tu biblioteca…");
    await loadLibrary();
    closeSetup();
    wizMsg("");
    el("brandSub").textContent = `${res.items} elementos — Kaimaku empieza a buscar temas en segundo plano`;
  } catch (exc) {
    wizMsg(exc.message, true);
    el("wizNext").disabled = false;
  }
}

async function wizNext() {
  wizMsg("");
  if (setup.step === 0) return openSetup();
  if (setup.step === 1) {
    setup.step = 2;
    setup.map = null;
    return renderSetup();
  }
  if (setup.step === 2) {
    setup.step = 3;
    return renderSetup();
  }
  return saveSetup();
}

function wizBack() {
  wizMsg("");
  if (setup.step === 2) {
    setup.step = 1;
    setup.manual = false;
    if (setup.discovered === null) discoverServers();
  } else if (setup.step === 3) setup.step = 2;
  renderSetup();
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
  state.needsSetup = !data.roots.length;
  el("brandSub").textContent = state.needsSetup
    ? "Sin configurar todavía"
    : `${data.items.length} elementos en ${data.libraries.length} bibliotecas`;
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

  el("setupBtn").addEventListener("click", () => openSetup());
  el("welcomeBtn").addEventListener("click", () => openSetup());
  el("setupClose").addEventListener("click", closeSetup);
  el("wizNext").addEventListener("click", wizNext);
  el("wizBack").addEventListener("click", wizBack);

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
  if (state.needsSetup) openSetup(); // primera vez: directo al asistente
  await loadScanStatus();
  await refreshJobs();
  connectEvents();
  setInterval(loadLibrary, 5 * 60 * 1000);
}

boot();
