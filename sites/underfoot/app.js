const fallbackMaps = [
  {
    map_id: "rootreach",
    title: "Rootreach Wilds",
    biome: "forest floor",
    summary: "A mossy woodland route with root bridges, raid lanes, and fast burrow returns.",
    hazards: ["boots", "owls", "spider silk", "axe swings"],
    quest_hooks: ["rescue cages", "sap siphons", "mushroom clusters"],
    spawns: [
      { id: "root-arch", label: "Root Arch Burrow", type: "root tunnel" },
      { id: "stone-crack", label: "Stone Crack Exit", type: "wall crack" },
      { id: "stump-door", label: "Stump Door", type: "tree stump" },
    ],
  },
  {
    map_id: "glassgarden",
    title: "Glassgarden Pantry",
    biome: "kitchen greenhouse",
    summary: "Warm glass, herbs, boiling kettles, and roaming house threats make this the risky harvest route.",
    hazards: ["burners", "steam", "cats", "knives"],
    quest_hooks: ["herb theft", "cork caches", "lantern seeds"],
    spawns: [
      { id: "terracotta-hole", label: "Terracotta Pot Hole", type: "pot exit" },
      { id: "wall-vent", label: "Wall Vent Grate", type: "vent" },
      { id: "drain-rim", label: "Drain Rim Tunnel", type: "drain" },
    ],
  },
  {
    map_id: "hollowhearth",
    title: "Hollowhearth Warren",
    biome: "underground forge burrow",
    summary: "The safer underground social zone with rescue triage, staging space, and fast return holes.",
    hazards: ["ember bursts", "unstable carts", "shock roots"],
    quest_hooks: ["quest board", "rescue triage", "upgrade benches"],
    spawns: [
      { id: "lantern-pit", label: "Lantern Pit Hatch", type: "burrow" },
      { id: "forge-door", label: "Forge Door Gap", type: "wall crack" },
      { id: "vine-drop", label: "Vine Drop", type: "tree root" },
    ],
  },
];

const fallbackFrontend = {
  manifest: {
    startupScene: "Startup",
    titleScene: "Title",
    characterSelectScene: "CharacterSelect",
    gameListScene: "GameList",
    worldMapScene: "WorldMapZone",
    minigameScenes: [
      { key: "rootreach_trial", scene: "RootreachTrial", label: "Rootreach Trial", mapId: "rootreach", modeKey: "topside_raid" },
      { key: "glassgarden_run", scene: "GlassgardenRun", label: "Glassgarden Run", mapId: "glassgarden", modeKey: "resource_run" },
      { key: "hollowhearth_rescue", scene: "HollowhearthRescue", label: "Hollowhearth Rescue", mapId: "hollowhearth", modeKey: "topside_village_defense" },
    ],
  },
  voice_policy: {
    openMic: true,
    textChat: "disabled",
    teamVoice: "disabled",
  },
};

const state = {
  config: null,
  maps: [...fallbackMaps],
  session: null,
  ws: null,
  selectedMapId: "",
  frontend: fallbackFrontend,
};

function sameOriginPlayable() {
  return location.port === "8450" || location.port === "8460";
}

function setText(id, value) {
  const node = document.getElementById(id);
  if (node) node.textContent = value;
}

function appendLog(text) {
  const node = document.getElementById("sessionOut");
  if (!node) return;
  const previous = node.textContent || "";
  node.textContent = `${text}\n\n${previous}`.trim();
}

async function safeFetchJson(url, fallback) {
  try {
    const response = await fetch(url);
    if (!response.ok) throw new Error(`${url} returned ${response.status}`);
    return await response.json();
  } catch (err) {
    return fallback;
  }
}

function renderSummary(snapshot) {
  const summary = document.getElementById("sessionSummary");
  if (!summary) return;
  const session = snapshot?.session || state.session;
  if (!session) {
    summary.textContent = "No active guest session.";
    return;
  }
  summary.textContent = JSON.stringify(
    {
      player_name: session.player_name,
      map: session.map_title,
      spawn: session.spawn?.label,
      stats: session.stats || {},
      quest: session.quest_state || {},
    },
    null,
    2,
  );
}

function renderSceneFlow() {
  const grid = document.getElementById("sceneGrid");
  if (!grid) return;
  const manifest = state.frontend?.manifest || fallbackFrontend.manifest;
  const sceneCards = [
    { tag: "Boot", title: manifest.startupScene, body: "Startup scene warms the config, verifies services, and hands off cleanly into the title surface." },
    { tag: "Title", title: manifest.titleScene, body: "Title scene sets the fantasy and routes players toward character select or world-map preview." },
    { tag: "Select", title: manifest.characterSelectScene, body: "Choose Warble or Biggun, preview tint direction, and lock the role before joining a match." },
    { tag: "Browser", title: manifest.gameListScene, body: "Game list is the player-facing session browser for joining or starting Underfoot matches." },
    { tag: "World", title: manifest.worldMapScene, body: "World map zone acts as the shared routing layer for discovered holes, burrows, and match portals." },
  ];
  grid.innerHTML = sceneCards
    .map((card) => `<article class="scene-card"><span class="tag">${card.tag}</span><h4>${card.title}</h4><p>${card.body}</p></article>`)
    .join("");
}

function renderVoiceRules() {
  const grid = document.getElementById("voiceGrid");
  if (!grid) return;
  const cards = [
    {
      tag: "Warble -> Warble",
      title: "Plain speech",
      body: "Warbles hear each other normally so coordination, panic, and laughs all stay readable during fast objective play.",
    },
    {
      tag: "Warble -> Biggun",
      title: "Cute squeaky warbling",
      body: "Bigguns never receive intelligible Warble speech. They only hear stylized squeaks and warbles that still reveal direction and panic.",
    },
    {
      tag: "Biggun -> Biggun",
      title: "Normal voice with bass",
      body: "Bigguns sound mostly natural, with a light bass or boom treatment so they feel larger without becoming muddy.",
    },
    {
      tag: "World occlusion",
      title: "Arc Raiders-style muffling",
      body: "Distance, walls, room transitions, and tunnel depth all push the receive mix toward heavier muffling and lower intelligibility.",
    },
    {
      tag: "Comms policy",
      title: "Open mic only",
      body: "Microphones are always on in the current plan. There is no team-only voice channel and there is no text chat layer.",
    },
    {
      tag: "Readability",
      title: "Spatial first",
      body: "Voice should reveal presence, scale, and hiding spots without flattening everyone into the same clean radio sound.",
    },
  ];
  grid.innerHTML = cards
    .map((card) => `<article class="feature"><span class="tag">${card.tag}</span><h4>${card.title}</h4><p>${card.body}</p></article>`)
    .join("");
}

function renderMaps() {
  const grid = document.getElementById("mapGrid");
  const mapSelect = document.getElementById("mapSelect");
  const spawnSelect = document.getElementById("spawnSelect");
  if (!grid || !mapSelect || !spawnSelect) return;
  grid.innerHTML = "";
  mapSelect.innerHTML = "";
  const maps = Array.isArray(state.maps) && state.maps.length ? state.maps : fallbackMaps;
  maps.forEach((map, index) => {
    const option = document.createElement("option");
    option.value = map.map_id;
    option.textContent = `${map.title} // ${map.biome}`;
    mapSelect.appendChild(option);

    const card = document.createElement("article");
    card.className = "map-card";
    card.innerHTML = `<span class="tag">${map.biome}</span><h4>${map.title}</h4><p>${map.summary}</p><p><strong>Hazards:</strong> ${map.hazards.join(", ")}</p><p><strong>Hooks:</strong> ${map.quest_hooks.join(", ")}</p>`;
    card.onclick = () => {
      mapSelect.value = map.map_id;
      syncSpawnOptions();
    };
    grid.appendChild(card);

    if (index === 0 && !state.selectedMapId) {
      state.selectedMapId = map.map_id;
    }
  });
  mapSelect.value = state.selectedMapId || maps[0]?.map_id || "";
  syncSpawnOptions();
}

function renderMinigames() {
  const grid = document.getElementById("minigameGrid");
  if (!grid) return;
  const manifest = state.frontend?.manifest || fallbackFrontend.manifest;
  const minigames = manifest.minigameScenes || [];
  grid.innerHTML = minigames
    .map((scene) => {
      const linkedMap = state.maps.find((map) => map.map_id === scene.mapId) || fallbackMaps.find((map) => map.map_id === scene.mapId);
      return `<article class="scene-card"><span class="tag">${scene.modeKey.replaceAll("_", " ")}</span><h4>${scene.label}</h4><p><strong>Scene:</strong> ${scene.scene}</p><p><strong>Linked zone:</strong> ${linkedMap?.title || scene.mapId}</p></article>`;
    })
    .join("");
}

function syncSpawnOptions() {
  const mapSelect = document.getElementById("mapSelect");
  const spawnSelect = document.getElementById("spawnSelect");
  if (!mapSelect || !spawnSelect) return;
  const mapId = mapSelect.value;
  state.selectedMapId = mapId;
  const map = state.maps.find((entry) => entry.map_id === mapId) || fallbackMaps.find((entry) => entry.map_id === mapId) || fallbackMaps[0];
  spawnSelect.innerHTML = "";
  (map?.spawns || []).forEach((spawn, index) => {
    const option = document.createElement("option");
    option.value = spawn.id;
    option.textContent = `${spawn.label} // ${spawn.type}`;
    if (index === 0) option.selected = true;
    spawnSelect.appendChild(option);
  });
}

async function loadConfig() {
  if (!sameOriginPlayable()) {
    setText("configLine", "Unity 6 / C# / NGO / Meshy stack // preview mode");
    setText("joinStatus", "Preview mode only. Open the Underfoot world port to create live guest sessions.");
    return;
  }
  const data = await safeFetchJson("/api/config", { project: "underfoot", engine: { client: "Unity 6" }, world_host: "underfoot-world:8460" });
  state.config = data;
  const engineText = typeof data.engine === "object" ? `${data.engine.client || "Unity"}${data.engine.scripting ? ` / ${data.engine.scripting}` : ""}${data.engine.network ? ` / ${data.engine.network}` : ""}` : "Unity 6 / C# / NGO";
  setText("configLine", `${engineText} // ${data.world_host}`);
}

async function loadFrontend() {
  if (!sameOriginPlayable()) {
    state.frontend = fallbackFrontend;
    renderSceneFlow();
    renderVoiceRules();
    renderMinigames();
    return;
  }
  const data = await safeFetchJson("/api/frontend", fallbackFrontend);
  state.frontend = data;
  renderSceneFlow();
  renderVoiceRules();
  renderMinigames();
}

async function loadMaps() {
  if (!sameOriginPlayable()) {
    state.maps = [...fallbackMaps];
    renderMaps();
    renderMinigames();
    return;
  }
  const data = await safeFetchJson("/api/maps", { maps: fallbackMaps });
  state.maps = data.maps || fallbackMaps;
  renderMaps();
  renderMinigames();
  setText("joinStatus", "Maps ready. Choose a zone and a spawn preference.");
}

function connectWorldFeed(sessionId) {
  if (!sameOriginPlayable()) return;
  if (state.ws) {
    try { state.ws.close(); } catch (err) {}
  }
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws/world/${sessionId}`);
  state.ws = ws;
  ws.onopen = () => appendLog("Connected to the Underfoot world feed.");
  ws.onmessage = (event) => {
    try {
      const payload = JSON.parse(event.data);
      if (payload.snapshot) {
        renderSummary(payload.snapshot);
      } else if (payload.session) {
        renderSummary(payload);
      }
      appendLog(JSON.stringify(payload, null, 2));
    } catch (err) {
      appendLog(String(event.data));
    }
  };
  ws.onerror = () => appendLog("World feed hit an error.");
  ws.onclose = () => appendLog("World feed closed.");
}

async function joinWorld(event) {
  event.preventDefault();
  if (!sameOriginPlayable()) {
    setText("joinStatus", "This page is a published preview. Open the dedicated Underfoot port to enter the live test world.");
    return;
  }
  const name = (document.getElementById("playerName")?.value || "").trim();
  const map_id = (document.getElementById("mapSelect")?.value || "").trim();
  const spawn_preference = (document.getElementById("spawnSelect")?.value || "").trim();
  if (!name) {
    setText("joinStatus", "Enter a player name first.");
    return;
  }
  setText("joinStatus", "Opening a guest session...");
  const response = await fetch("/api/session/guest", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, map_id, spawn_preference }),
  });
  const data = await response.json();
  if (!response.ok) {
    setText("joinStatus", data.detail || "Failed to enter the world.");
    return;
  }
  state.session = data;
  renderSummary({ session: data });
  setText("joinStatus", `Welcome underfoot, ${data.player_name}. You spawned at ${data.spawn.label}.`);
  setText("actionStatus", "Prototype actions are now enabled.");
  setText("sessionOut", JSON.stringify(data, null, 2));
  connectWorldFeed(data.session_id);
}

async function runAction(action, value) {
  if (!sameOriginPlayable()) {
    setText("actionStatus", "Prototype actions only work on the dedicated Underfoot port.");
    return;
  }
  if (!state.session?.session_id) {
    setText("actionStatus", "Join the world first.");
    return;
  }
  setText("actionStatus", `Running action: ${action}`);
  const response = await fetch(`/api/session/${state.session.session_id}/action`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action, value }),
  });
  const data = await response.json();
  if (!response.ok) {
    setText("actionStatus", data.detail || "Action failed.");
    return;
  }
  if (data.snapshot?.session) {
    state.session = data.snapshot.session;
    renderSummary(data.snapshot);
  }
  setText("actionStatus", data.note || "Action complete.");
  appendLog(JSON.stringify(data, null, 2));
}

function bindUi() {
  document.getElementById("joinForm")?.addEventListener("submit", joinWorld);
  document.getElementById("refreshMapsBtn")?.addEventListener("click", () => loadMaps().catch((err) => setText("joinStatus", String(err))));
  document.getElementById("mapSelect")?.addEventListener("change", syncSpawnOptions);
  document.querySelectorAll(".action-btn").forEach((button) => {
    button.addEventListener("click", () => {
      const action = button.getAttribute("data-action") || "";
      const value = Number(button.getAttribute("data-value") || "0") || undefined;
      runAction(action, value).catch((err) => setText("actionStatus", String(err)));
    });
  });
}

async function main() {
  bindUi();
  renderSummary(null);
  renderSceneFlow();
  renderVoiceRules();
  renderMaps();
  renderMinigames();
  if (new URLSearchParams(location.search).get("connect") !== "1") return;
  try {
    await loadConfig();
    await loadFrontend();
    await loadMaps();
  } catch (err) {
    setText("joinStatus", `Underfoot world service error: ${err}`);
  }
}

main();
