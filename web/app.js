const LAYOUT = {
  coordinator: [{ x: 20, y: 11 }, { x: 80, y: 11 }],
  builder: [{ x: 10, y: 33 }, { x: 30, y: 33 }, { x: 50, y: 33 }, { x: 70, y: 33 }, { x: 90, y: 33 }, { x: 50, y: 56 }],
  reviewer: [{ x: 50, y: 73 }],
  scout: [{ x: 35, y: 89 }, { x: 65, y: 89 }],
};

const DEFAULT_POLICY = {
  coordinator: "gpt-5.3",
  builderFast: "gpt-5.3-codex",
  builderStrong: "gpt-5.4",
  builderStrongShare: 0.33,
  reviewer: "gpt-5.4",
  scout: "gpt-5.2",
};

const STORAGE = {
  policy: "luminax.swarm.modelPolicy.v1",
  apiBase: "luminax.swarm.apiBase.v1",
  projectKey: "luminax.swarm.projectKey.v1",
};

const DEMO_NODES = [
  { id: "coord-1", title: "Coordinator 1", role: "coordinator", priority: "P1", status: "running", runtimeSec: 144, task: "Shard mission plans", log: "Booting CLI session" },
  { id: "coord-2", title: "Coordinator 2", role: "coordinator", priority: "P2", status: "running", runtimeSec: 143, task: "Orchestrating multi-repo patch", log: "Publishing job graph" },
  { id: "builder-1", title: "Builder 1", role: "builder", priority: "P2", status: "running", runtimeSec: 148, task: "Auth middleware patch", log: "Applying endpoint guardrails" },
  { id: "builder-2", title: "Builder 2", role: "builder", priority: "P1", status: "running", runtimeSec: 146, task: "Refactor config pathing", log: "Syncing schema contracts" },
  { id: "builder-3", title: "Builder 3", role: "builder", priority: "P1", status: "running", runtimeSec: 145, task: "Drafting infra checks", log: "Compiling deployment script" },
  { id: "builder-4", title: "Builder 4", role: "builder", priority: "P2", status: "running", runtimeSec: 141, task: "Patching portal API", log: "Staging migration plan" },
  { id: "builder-5", title: "Builder 5", role: "builder", priority: "P2", status: "running", runtimeSec: 141, task: "Hardening telemetry", log: "Tracing health edges" },
  { id: "builder-6", title: "Builder 6", role: "builder", priority: "P1", status: "reviewing", runtimeSec: 147, task: "Merge candidate bundle", log: "Running integration checks" },
  { id: "reviewer-1", title: "Reviewer 1", role: "reviewer", priority: "P1", status: "running", runtimeSec: 145, task: "Security and regression gate", log: "No blockers in current pass" },
  { id: "scout-1", title: "Scout 1", role: "scout", priority: "P1", status: "running", runtimeSec: 144, task: "Dependency risk scouting", log: "Quiet - no critical drift" },
  { id: "scout-2", title: "Scout 2", role: "scout", priority: "P1", status: "running", runtimeSec: 143, task: "Edge-case scenario scans", log: "Finding replay regressions" },
];

const els = {
  events: document.getElementById("eventsList"),
  dispatchStatus: document.getElementById("dispatchStatus"),
  policyStatus: document.getElementById("policyStatus"),
  network: document.getElementById("swarmNetwork"),
  links: document.getElementById("swarmLinks"),
  nodes: document.getElementById("swarmNodes"),
  modeBadge: document.getElementById("dataModeBadge"),
  modeHint: document.getElementById("dataModeHint"),
};

let state = { nodes: [], links: [], completed: 0, mode: "demo", events: [], guardrails: {} };
let policy = loadPolicy();
let livePollingEnabled = false;

function clone(v) { return JSON.parse(JSON.stringify(v)); }
function now() { const d = new Date(); return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}:${String(d.getSeconds()).padStart(2, "0")}`; }
function esc(v) { return String(v ?? "").replace(/[&<>\"']/g, (m) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[m])); }
function runtime(sec) { const s = Math.max(0, Number(sec) || 0); return `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, "0")}s`; }
function clamp(n, min, max, fallback) { const x = Number(n); return Number.isNaN(x) ? fallback : Math.max(min, Math.min(max, x)); }
function random(arr) { return arr[Math.floor(Math.random() * arr.length)]; }
function secSince(ts) { const n = Number(ts) || 0; if (!n) return 0; return Math.max(0, Math.min(Math.floor(Date.now() / 1000) - n, 86400)); }
function apiBase(raw) { try { const u = new URL((raw || "/").trim(), location.origin); return `${u.protocol}//${u.host}`; } catch { return location.origin; } }

function loadPolicy() {
  try {
    const parsed = JSON.parse(localStorage.getItem(STORAGE.policy) || "{}");
    return {
      coordinator: String(parsed.coordinator || DEFAULT_POLICY.coordinator),
      builderFast: String(parsed.builderFast || DEFAULT_POLICY.builderFast),
      builderStrong: String(parsed.builderStrong || DEFAULT_POLICY.builderStrong),
      builderStrongShare: clamp(parsed.builderStrongShare, 0, 1, DEFAULT_POLICY.builderStrongShare),
      reviewer: String(parsed.reviewer || DEFAULT_POLICY.reviewer),
      scout: String(parsed.scout || DEFAULT_POLICY.scout),
    };
  } catch {
    return clone(DEFAULT_POLICY);
  }
}

function savePolicy() {
  localStorage.setItem(STORAGE.policy, JSON.stringify(policy));
}

function mode(status, hint) {
  state.mode = status;
  if (els.modeBadge) {
    els.modeBadge.className = `data-badge ${status}`;
    els.modeBadge.textContent = status === "live" ? "Live API Data" : "Demo Fallback";
  }
  if (els.modeHint) {
    els.modeHint.textContent = hint;
  }
}

function addEvent(text) {
  state.events.unshift({ t: now(), text });
  state.events = state.events.slice(0, 36);
  if (!els.events) return;
  els.events.innerHTML = state.events.map((e) => `<li><span class="event-time">${esc(e.t)}</span><span class="event-text">${esc(e.text)}</span></li>`).join("");
}

function assignLayoutAndModels() {
  const byRole = {
    coordinator: state.nodes.filter((n) => n.role === "coordinator"),
    builder: state.nodes.filter((n) => n.role === "builder"),
    reviewer: state.nodes.filter((n) => n.role === "reviewer"),
    scout: state.nodes.filter((n) => n.role === "scout"),
  };
  Object.entries(byRole).forEach(([role, list]) => {
    list.forEach((node, i) => {
      const p = (LAYOUT[role] || [])[i] || { x: 12 + (i % 5) * 18, y: 35 + Math.floor(i / 5) * 14 };
      node.x = p.x;
      node.y = p.y;
    });
  });
  const strong = Math.max(1, Math.round(byRole.builder.length * clamp(policy.builderStrongShare, 0, 1, 0.33)));
  byRole.coordinator.forEach((n) => { n.model = policy.coordinator; });
  byRole.builder.forEach((n, i) => { n.model = i < strong ? policy.builderStrong : policy.builderFast; });
  byRole.reviewer.forEach((n) => { n.model = policy.reviewer; });
  byRole.scout.forEach((n) => { n.model = policy.scout; });
}

function linksForNodes() {
  const out = [];
  const c = state.nodes.filter((n) => n.role === "coordinator");
  const b = state.nodes.filter((n) => n.role === "builder");
  const r = state.nodes.filter((n) => n.role === "reviewer");
  const s = state.nodes.filter((n) => n.role === "scout");
  c.forEach((co) => b.forEach((bu) => out.push({ from: co.id, to: bu.id, tone: "command" })));
  if (r[0]) {
    b.forEach((bu) => out.push({ from: bu.id, to: r[0].id, tone: "build" }));
    s.forEach((sc) => out.push({ from: r[0].id, to: sc.id, tone: "scout" }));
  }
  if (c.length) s.forEach((sc, i) => out.push({ from: sc.id, to: c[i % c.length].id, tone: "review" }));
  state.links = out;
}

function laneLabels() {
  const count = (role) => state.nodes.filter((n) => n.role === role).length;
  return [
    { text: `Coordinators (${count("coordinator")})`, x: 50, y: 4.8 },
    { text: `Builders (${count("builder")})`, x: 50, y: 24.5 },
    { text: `Reviewers (${count("reviewer")})`, x: 50, y: 66.2 },
    { text: `Scouts (${count("scout")})`, x: 50, y: 82.1 },
  ];
}

function renderNodes() {
  if (!els.nodes) return;
  const labels = laneLabels().map((l) => `<div class="swarm-lane-label" style="left:${l.x}%;top:${l.y}%;">${esc(l.text)}</div>`).join("");
  const cards = state.nodes.map((n) => `
    <article class="swarm-node ${esc(n.role)}" data-node-id="${esc(n.id)}" style="left:${n.x}%;top:${n.y}%;">
      <header class="swarm-node-header"><h3 class="swarm-node-title">${esc(n.title)}</h3><span class="swarm-node-priority">${esc(n.priority)}</span></header>
      <div class="swarm-meta"><span class="swarm-role">${esc(n.role.toUpperCase())}</span><span class="swarm-status ${esc(n.status)}">${esc(n.status)}</span><span class="swarm-runtime">${runtime(n.runtimeSec)}</span><span class="swarm-model">${esc(n.model || "unassigned")}</span></div>
      <p class="swarm-task">${esc(n.task)}</p><p class="swarm-log">${esc(n.log)}</p><button class="swarm-terminal" type="button">> Terminal</button>
    </article>`).join("");
  els.nodes.innerHTML = labels + cards;
}

function drawLinks() {
  if (!els.network || !els.links) return;
  const width = els.network.clientWidth;
  const height = els.network.clientHeight;
  els.links.setAttribute("viewBox", `0 0 ${width} ${height}`);
  els.links.innerHTML = "";
  state.links.forEach((l, i) => {
    const a = document.querySelector(`[data-node-id="${l.from}"]`);
    const b = document.querySelector(`[data-node-id="${l.to}"]`);
    if (!a || !b) return;
    const x1 = a.offsetLeft + a.offsetWidth / 2;
    const y1 = a.offsetTop + a.offsetHeight / 2;
    const x2 = b.offsetLeft + b.offsetWidth / 2;
    const y2 = b.offsetTop + b.offsetHeight / 2;
    const dY = y2 >= y1 ? 1 : -1;
    const c = Math.max(80, Math.abs(y2 - y1) * 0.45 + Math.abs(x2 - x1) * 0.08);
    const d = `M ${x1} ${y1} C ${x1} ${y1 + c * dY}, ${x2} ${y2 - c * dY}, ${x2} ${y2}`;
    const p = document.createElementNS("http://www.w3.org/2000/svg", "path");
    p.setAttribute("d", d);
    p.setAttribute("class", `swarm-link ${l.tone}`);
    p.style.animationDelay = `${(i % 9) * 0.3}s`;
    els.links.appendChild(p);
  });
}

function updateMetrics() {
  const agents = state.nodes.length;
  const inFlight = state.nodes.filter((n) => !["idle", "complete", "completed"].includes(String(n.status).toLowerCase())).length;
  const critical = state.nodes.filter((n) => n.priority === "P1" && !["idle", "complete", "completed"].includes(String(n.status).toLowerCase())).length;
  const set = (id, value) => { const el = document.getElementById(id); if (el) el.textContent = String(value); };
  set("metricAgents", agents);
  set("metricInFlight", inFlight);
  set("metricCompleted", state.completed);
  set("metricCritical", critical);
}

function updateGuardrails() {
  const guardrails = state.guardrails || {};
  const maintenance = guardrails.maintenance || {};
  const contracts = guardrails.contracts || {};
  const incidents = guardrails.incidents || {};
  const checkouts = guardrails.checkouts || {};
  const set = (id, value) => { const el = document.getElementById(id); if (el) el.textContent = String(value); };
  set("intelMaintenanceMode", maintenance.mode || "normal");
  set("intelMaintenanceReason", maintenance.reason || (maintenance.policy?.description || "No maintenance override active."));
  set("intelIncidentCount", incidents.open_count || 0);
  set("intelIncidentSummary", incidents.open_count ? `${incidents.critical_count || 0} critical | ${(incidents.rows || [])[0]?.summary || "review incident queue"}` : "No open incidents reported.");
  set("intelContractStatus", (contracts.degraded_count || 0) > 0 ? "degraded" : "ok");
  set("intelContractSummary", `${contracts.preserved_count ?? 0}/${contracts.feature_count ?? 0} preserved | missing ${(contracts.missing_check_count ?? 0)}`);
  set("intelCheckoutCount", checkouts.active_lock_count || 0);
  set("intelCheckoutSummary", checkouts.active_lock_count ? `${checkouts.tree_lock_count || 0} tree locks | waiting ${checkouts.waiting_agent_count || 0}` : "No active write locks.");
}

function renderAll() {
  assignLayoutAndModels();
  linksForNodes();
  renderNodes();
  drawLinks();
  updateMetrics();
  updateGuardrails();
}

function statusFromText(text) {
  const t = String(text || "").toLowerCase();
  if (t.includes("fail") || t.includes("error")) return "reviewing";
  if (t.includes("idle")) return "idle";
  if (t.includes("review")) return "reviewing";
  return "running";
}

function priority(status, outcome, open) {
  const s = String(status || "").toLowerCase();
  const o = String(outcome || "").toLowerCase();
  const n = Number(open) || 0;
  if (s.includes("fail") || o.includes("fail")) return "P1";
  if (n > 8) return "P1";
  if (n > 3) return "P2";
  return "P3";
}

function liveStateFromDashboard(data) {
  const sessions = Array.isArray(data?.sessions) ? data.sessions : [];
  const runs = Array.isArray(data?.recent_runs) ? data.recent_runs : [];
  const projects = Array.isArray(data?.active_projects) ? data.active_projects : [];
  if (!sessions.length && !runs.length && !projects.length) return null;

  const coordinators = sessions.slice(0, 2).map((s, i) => ({
    id: `coord-live-${i + 1}`,
    title: `Coordinator ${i + 1}`,
    role: "coordinator",
    priority: priority(s.last_status, s.last_outcome, 0),
    status: statusFromText(s.last_status),
    runtimeSec: secSince(s.created_at || s.updated_at),
    task: s.goal || s.title || "Coordinating mission planning",
    log: s.last_summary || s.last_message || "Awaiting directive",
  }));
  if (!coordinators.length) coordinators.push({ id: "coord-live-1", title: "Coordinator 1", role: "coordinator", priority: "P2", status: "running", runtimeSec: 0, task: "Monitoring queue", log: "No active sessions yet" });

  const builders = runs.slice(0, 6).map((r, i) => ({
    id: `builder-live-${i + 1}`,
    title: `Builder ${i + 1}`,
    role: "builder",
    priority: priority(r.status, r.outcome, 0),
    status: statusFromText(r.status || r.outcome),
    runtimeSec: Math.max(1, Number(r.duration_sec) || secSince(r.created_at)),
    task: r.summary || `Codex run ${r.run_id || ""}`.trim(),
    log: `${r.project_key || "unscoped"} | files: ${(r.files_touched || []).length || 0} | outcome: ${r.outcome || "unknown"}`,
  }));
  if (!builders.length) builders.push({ id: "builder-live-1", title: "Builder 1", role: "builder", priority: "P2", status: "idle", runtimeSec: 0, task: "Waiting for first Codex run", log: "No recent runs" });

  const reviewerSource = runs[0] || sessions[0] || {};
  const reviewer = [{
    id: "reviewer-live-1",
    title: "Reviewer 1",
    role: "reviewer",
    priority: priority(reviewerSource.status || reviewerSource.last_status, reviewerSource.outcome || reviewerSource.last_outcome, 0),
    status: statusFromText(reviewerSource.status || reviewerSource.last_status),
    runtimeSec: Math.max(1, Number(reviewerSource.duration_sec) || secSince(reviewerSource.updated_at || reviewerSource.created_at)),
    task: reviewerSource.summary || reviewerSource.last_summary || "Reviewing recent run quality and risk",
    log: `questions: ${(reviewerSource.questions || []).length || 0} | return: ${reviewerSource.returncode ?? "n/a"}`,
  }];

  const scouts = projects.slice(0, 2).map((p, i) => ({
    id: `scout-live-${i + 1}`,
    title: `Scout ${i + 1}`,
    role: "scout",
    priority: priority(p.latest_task_status, "", p.open_task_count),
    status: Number(p.open_task_count) > 0 ? "running" : "idle",
    runtimeSec: secSince(p.updated_at),
    task: p.latest_task_goal || `Scan ${p.project_key}`,
    log: `${p.project_key || "unknown"} | open tasks: ${p.open_task_count || 0}`,
  }));
  while (scouts.length < 2) scouts.push({ id: `scout-live-${scouts.length + 1}`, title: `Scout ${scouts.length + 1}`, role: "scout", priority: "P3", status: "idle", runtimeSec: 0, task: "No project risk queue", log: "Waiting for telemetry" });

  return {
    nodes: [...coordinators, ...builders, ...reviewer, ...scouts],
    completed: Number(data?.summary?.run_count) || runs.length,
    events: [
      ...runs.slice(0, 6).map((r) => `run ${r.run_id || "unknown"} (${r.outcome || r.status || "unknown"}): ${r.summary || "no summary"}`),
      ...((data?.guardrails?.incidents?.rows || []).slice(0, 2).map((row) => `incident ${row.incident_key || "unknown"} (${row.severity || "medium"}): ${row.summary || "no summary"}`)),
    ],
    guardrails: data?.guardrails || {},
  };
}

async function getJson(url) {
  const res = await fetch(url, { cache: "no-store" });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || `Request failed (${res.status})`);
  return data;
}

async function postJson(url, payload) {
  const res = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || `Request failed (${res.status})`);
  return data;
}

async function refreshLive(announce) {
  const baseInput = document.getElementById("apiBaseUrl");
  const base = apiBase(baseInput?.value || "/");
  if (baseInput) baseInput.value = base;
  localStorage.setItem(STORAGE.apiBase, base);
  try {
    const url = new URL("/api/codex/bridge/dashboard", base);
    url.searchParams.set("limit", "40");
    const dashboard = await getJson(url.toString());
    const live = liveStateFromDashboard(dashboard);
    if (!live) throw new Error("Dashboard returned no swarm-compatible rows");
    state.nodes = live.nodes;
    state.completed = live.completed;
    state.guardrails = live.guardrails || {};
    if (live.events.length) {
      state.events = [];
      live.events.forEach((e) => addEvent(e));
    }
    renderAll();
    livePollingEnabled = true;
    mode("live", `Connected to ${base} using /api/codex/bridge/dashboard`);
    if (announce) addEvent(`live refresh complete from ${base}`);
    return true;
  } catch (err) {
    livePollingEnabled = false;
    state.guardrails = {};
    updateGuardrails();
    mode("demo", `Live feed unavailable (${err.message}). Showing demo topology.`);
    if (announce) addEvent(`live refresh failed: ${err.message}`);
    return false;
  }
}

function simulateTick(reason = "tick") {
  if (!state.nodes.length) return;
  const target = random(state.nodes);
  const tasks = ["rebasing patch stack", "running CI checks", "verifying route contracts", "reviewing API diff", "profiling regressions", "shipping migration draft"];
  const logs = ["quiet - no recent output", "patch bundle assembled", "diffset uploaded for review", "test lane stable", "risk notes posted to coordinator", "awaiting mission context"];
  const statuses = ["running", "reviewing", "running", "running", "idle"];
  const priorities = ["P1", "P1", "P2", "P2", "P3", "P4"];
  target.status = random(statuses);
  target.task = `${reason}: ${random(tasks)}`;
  target.log = random(logs);
  target.priority = random(priorities);
  if (target.status === "reviewing") state.completed += 1;
  renderAll();
  addEvent(`${target.title} (${target.role}) ${target.status} - ${target.task}`);
}

function modelForLane(lane) {
  const role = String(lane || "").trim().toLowerCase();
  if (role === "coordinator") return policy.coordinator;
  if (role === "reviewer") return policy.reviewer;
  if (role === "scout") return policy.scout;
  if (role === "builder") {
    const threshold = clamp(policy.builderStrongShare, 0, 1, 0.33);
    return Math.random() < threshold ? policy.builderStrong : policy.builderFast;
  }
  return policy.builderFast;
}

function hydrateInputs() {
  const base = localStorage.getItem(STORAGE.apiBase) || location.origin;
  const project = localStorage.getItem(STORAGE.projectKey) || "";
  const set = (id, val) => { const el = document.getElementById(id); if (el) el.value = val; };
  set("apiBaseUrl", apiBase(base));
  set("projectKey", project);
  set("modelCoordinator", policy.coordinator);
  set("modelBuilderFast", policy.builderFast);
  set("modelBuilderStrong", policy.builderStrong);
  set("builderStrongShare", policy.builderStrongShare);
  set("modelReviewer", policy.reviewer);
  set("modelScout", policy.scout);
}

function bindUi() {
  document.getElementById("clearEventsBtn")?.addEventListener("click", () => {
    state.events = [];
    if (els.events) els.events.innerHTML = "";
    addEvent("event stream reset");
  });

  document.getElementById("simulateTickBtn")?.addEventListener("click", async () => {
    if (state.mode === "live") {
      await refreshLive(true);
      return;
    }
    simulateTick("manual");
  });

  document.getElementById("refreshLiveBtn")?.addEventListener("click", async () => {
    const ok = await refreshLive(true);
    if (els.dispatchStatus) els.dispatchStatus.textContent = ok ? "Live feed refreshed." : "Live feed unavailable. Using demo fallback.";
  });

  document.getElementById("dispatchForm")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const mission = (document.getElementById("missionTitle")?.value || "").trim();
    const priorityLevel = (document.getElementById("missionPriority")?.value || "P3").trim();
    const lane = (document.getElementById("missionLane")?.value || "builder").trim();
    const context = (document.getElementById("missionContext")?.value || "").trim();
    const token = (document.getElementById("controlToken")?.value || "").trim();
    const projectKey = (document.getElementById("projectKey")?.value || "").trim();
    const workdir = (document.getElementById("missionWorkdir")?.value || "").trim();
    const allowWrite = !!document.getElementById("missionAllowWrite")?.checked;
    const base = apiBase(document.getElementById("apiBaseUrl")?.value || "/");
    if (!mission) return;
    if (projectKey) localStorage.setItem(STORAGE.projectKey, projectKey);

    if (token) {
      try {
        const selectedModel = modelForLane(lane);
        const result = await postJson(new URL("/api/codex/bridge/run", base).toString(), {
          token,
          goal: context ? `${mission}\n\nContext: ${context}` : mission,
          project_key: projectKey || undefined,
          mode: "project",
          scope: "implement",
          workdir: workdir || undefined,
          allow_write: allowWrite,
          model: selectedModel,
        });
        addEvent(`live dispatch -> run ${result.run_id || "unknown"} (${result.outcome || result.status || "unknown"}) model=${selectedModel}`);
        if (els.dispatchStatus) els.dispatchStatus.textContent = `Dispatched to Codex bridge run ${result.run_id || "unknown"} using ${selectedModel}.`;
        await refreshLive(false);
        event.target.reset();
        document.getElementById("apiBaseUrl").value = base;
        return;
      } catch (err) {
        addEvent(`live dispatch failed: ${err.message}`);
        if (els.dispatchStatus) els.dispatchStatus.textContent = `Live dispatch failed: ${err.message}. Applied local assignment instead.`;
      }
    }

    const picks = state.nodes.filter((n) => n.role === lane);
    const target = picks.length ? random(picks) : random(state.nodes);
    target.task = mission;
    target.log = context ? context.slice(0, 84) : "Mission accepted";
    target.priority = priorityLevel;
    target.status = "running";
    target.runtimeSec += 1;
    renderAll();
    addEvent(`local dispatch -> ${target.title}: ${mission}`);
    if (els.dispatchStatus) els.dispatchStatus.textContent = "Local dispatch only. Add Control Token for live Codex API dispatch.";
    event.target.reset();
    document.getElementById("apiBaseUrl").value = base;
  });

  document.getElementById("policyForm")?.addEventListener("submit", (event) => {
    event.preventDefault();
    policy = {
      coordinator: (document.getElementById("modelCoordinator")?.value || DEFAULT_POLICY.coordinator).trim(),
      builderFast: (document.getElementById("modelBuilderFast")?.value || DEFAULT_POLICY.builderFast).trim(),
      builderStrong: (document.getElementById("modelBuilderStrong")?.value || DEFAULT_POLICY.builderStrong).trim(),
      builderStrongShare: clamp(document.getElementById("builderStrongShare")?.value, 0, 1, DEFAULT_POLICY.builderStrongShare),
      reviewer: (document.getElementById("modelReviewer")?.value || DEFAULT_POLICY.reviewer).trim(),
      scout: (document.getElementById("modelScout")?.value || DEFAULT_POLICY.scout).trim(),
    };
    savePolicy();
    renderAll();
    addEvent(`policy updated: coord=${policy.coordinator}, reviewer=${policy.reviewer}, scout=${policy.scout}`);
    if (els.policyStatus) {
      const strongPct = (policy.builderStrongShare * 100).toFixed(0);
      els.policyStatus.textContent = `Policy applied. Builders: ${strongPct}% ${policy.builderStrong}, ${100 - strongPct}% ${policy.builderFast}.`;
    }
  });
}

async function main() {
  hydrateInputs();
  bindUi();
  state.nodes = clone(DEMO_NODES);
  state.completed = 0;
  renderAll();
  mode("demo", "Offline sample. Choose Refresh Live to connect to your configured API.");
  addEvent("swarm board online");
  addEvent("demo mode initialized");
  if (new URLSearchParams(location.search).get("connect") === "1") await refreshLive(true);
  window.addEventListener("resize", drawLinks);
  setInterval(() => { state.nodes.forEach((n) => { n.runtimeSec += 1; }); renderAll(); }, 1000);
  setInterval(() => { if (state.mode === "demo") simulateTick("auto"); }, 4000);
  setInterval(async () => { if (livePollingEnabled) await refreshLive(false); }, 15000);
}

main();
