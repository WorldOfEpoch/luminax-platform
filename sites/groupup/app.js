const page = document.body.dataset.page || '';
const params = new URLSearchParams(location.search);
const roomFromQuery = () => (params.get('room') || '').trim();
const hostTokenFromQuery = () => (params.get('host_token') || '').trim();
const participantTokenFromQuery = () => (params.get('participant_token') || '').trim();
const sessionKey = (room) => `groupup.session.${room}`;
const $ = (id) => document.getElementById(id);
const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (m) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]));

function loadSession(room) {
  if (!room) return {};
  try { return JSON.parse(localStorage.getItem(sessionKey(room)) || '{}'); } catch { return {}; }
}
function saveSession(room, data) {
  if (!room) return;
  const current = loadSession(room);
  localStorage.setItem(sessionKey(room), JSON.stringify({ ...current, ...data }));
}
function setStatus(id, text) { const el = $(id); if (el) el.textContent = text; }
function abs(url) { return new URL(url, location.origin).toString(); }
async function getJson(url) {
  const res = await fetch(url, { cache: 'no-store' });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || `Request failed (${res.status})`);
  return data;
}
async function postJson(url, payload) {
  const res = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || `Request failed (${res.status})`);
  return data;
}
async function uploadFile(url, formData) {
  const res = await fetch(url, { method: 'POST', body: formData });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || `Upload failed (${res.status})`);
  return data;
}

function renderRoomCards(listId, rows) {
  const list = $(listId);
  if (!list) return;
  list.innerHTML = '';
  if (!rows.length) {
    list.innerHTML = '<div class="mini-card">No live rooms yet. Create one for tonight.</div>';
    return;
  }
  rows.forEach((row) => {
    const card = document.createElement('div');
    card.className = 'room-card';
    card.innerHTML = `
      <strong>${esc(row.title || row.room_key)}</strong>
      <div class="meta">Room: ${esc(row.room_key)} | Participants: ${row.participant_count || 0} | Active: ${row.active_count || 0}</div>
      <div class="links">
        <a class="secondary-btn" href="index.html?room=${encodeURIComponent(row.room_key)}#join">Join</a>
        <a class="secondary-btn" href="overlay.html?room=${encodeURIComponent(row.room_key)}" target="_blank" rel="noreferrer">Overlay</a>
      </div>`;
    list.appendChild(card);
  });
}

async function loadOverviewCards() {
  const data = await getJson('/api/groupup/overview');
  renderRoomCards('liveRoomsList', data.rows || []);
  renderRoomCards('projectRoomsList', data.rows || []);
}

async function createRoom() {
  const payload = {
    room_key: ($('createRoomKey')?.value || '').trim(),
    title: ($('createRoomTitle')?.value || '').trim(),
    host_name: ($('createHostName')?.value || '').trim(),
    theme: ($('createTheme')?.value || 'confetti_arcade').trim(),
    headline: ($('createHeadline')?.value || '').trim(),
    subheadline: ($('createSubheadline')?.value || '').trim(),
    project_key: 'groupup',
  };
  setStatus('landingCreateStatus', 'Creating GroupUp room...');
  try {
    const data = await postJson('/api/groupup/rooms/create', payload);
    saveSession(data.room.room_key, {
      room_key: data.room.room_key,
      host_token: data.host_token,
      participant_token: data.host_participant_token,
      host_url: data.host_url,
      room_url: data.room_url,
      overlay_url: data.overlay_url,
    });
    setStatus('landingCreateStatus', `Room created: ${data.room.room_key}\nHost: ${data.host_url}\nOverlay: ${data.overlay_url}`);
    location.href = data.host_url;
  } catch (err) {
    setStatus('landingCreateStatus', `Create failed: ${err.message}`);
  }
}

async function joinRoom() {
  const payload = {
    room_key: ($('joinRoomKey')?.value || roomFromQuery() || '').trim(),
    display_name: ($('joinName')?.value || '').trim(),
    color: ($('joinColor')?.value || '').trim(),
    note: ($('joinNote')?.value || '').trim(),
  };
  setStatus('landingJoinStatus', 'Joining room...');
  try {
    const data = await postJson('/api/groupup/rooms/join', payload);
    saveSession(payload.room_key, {
      room_key: payload.room_key,
      participant_token: data.participant_token,
      room_url: data.room_url,
      overlay_url: data.overlay_url,
    });
    setStatus('landingJoinStatus', `Joined ${payload.room_key}. Redirecting to room...`);
    location.href = data.room_url;
  } catch (err) {
    setStatus('landingJoinStatus', `Join failed: ${err.message}`);
  }
}

function participantCardHtml(row, frameAsset = '') {
  const active = (row.status || '').toLowerCase() !== 'left';
  const initials = (row.display_name || '?').trim().slice(0, 1).toUpperCase() || '?';
  return `
    <div class="roster-tile ${(row.status || '').toLowerCase() === 'away' ? 'away' : ''}" style="border-color:${esc(row.color || '#6ae4ff')}33;background:linear-gradient(180deg, ${esc(row.color || '#6ae4ff')}22, rgba(255,255,255,0.03));">
      ${row.avatar_asset ? `<img src="${esc(row.avatar_asset)}" alt="${esc(row.display_name)}" />` : ''}
      <div class="shade"></div>
      <div class="content">
        <div class="tile-pill">${active ? 'Active' : 'Offline'} â€¢ ${esc(row.status || 'online')}</div>
        <div class="tile-name">${esc(row.display_name || 'Guest')}</div>
        <div class="tile-note">${esc(row.note || '')}</div>
      </div>
    </div>`;
}

function renderRoster(listId, participants) {
  const list = $(listId);
  if (!list) return;
  list.innerHTML = '';
  if (!participants.length) {
    list.innerHTML = '<div class="mini-card">Nobody is here yet.</div>';
    return;
  }
  participants.forEach((row) => {
    const wrap = document.createElement('div');
    wrap.innerHTML = participantCardHtml(row);
    list.appendChild(wrap.firstElementChild);
  });
}

async function loadRoomData(viewer = 'self') {
  const room = roomFromQuery();
  const session = loadSession(room);
  const participantToken = participantTokenFromQuery() || session.participant_token || '';
  const hostToken = hostTokenFromQuery() || session.host_token || '';
  const qs = new URLSearchParams();
  if (viewer === 'host' && hostToken) qs.set('host_token', hostToken);
  if (viewer !== 'host' && participantToken) qs.set('participant_token', participantToken);
  return getJson(`/api/groupup/rooms/${encodeURIComponent(room)}?${qs.toString()}`);
}

async function sendHeartbeat() {
  const room = roomFromQuery();
  const session = loadSession(room);
  const participantToken = participantTokenFromQuery() || session.participant_token || '';
  if (!room || !participantToken) return;
  try {
    await postJson(`/api/groupup/rooms/${encodeURIComponent(room)}/heartbeat`, { participant_token: participantToken });
  } catch {}
}

async function initRoomPage() {
  const room = roomFromQuery();
  if (!room) return;
  const session = loadSession(room);
  const participantToken = participantTokenFromQuery() || session.participant_token || '';
  if (!participantToken) {
    setStatus('roomStatusBox', 'Missing participant token. Join the room first.');
    return;
  }
  const load = async () => {
    try {
      const data = await loadRoomData('self');
      const roomData = data.room || {};
      $('roomTitle').textContent = roomData.title || room;
      $('roomSubtitle').textContent = roomData.overlay?.subheadline || 'Participant room';
      $('openOverlayLink').href = `overlay.html?room=${encodeURIComponent(room)}`;
      const hostToken = loadSession(room).host_token || '';
      $('openHostLink').href = hostToken ? `host.html?room=${encodeURIComponent(room)}&host_token=${encodeURIComponent(hostToken)}` : `host.html?room=${encodeURIComponent(room)}`;
      const me = (roomData.participants || []).find((row) => row.participant_token === participantToken) || (roomData.participants || []).find((row) => row.participant_id === (session.participant_id || '')) || null;
      if (me) {
        $('participantName').value = me.display_name || '';
        $('participantColor').value = me.color || '';
        $('participantStatus').value = me.status || 'online';
        $('participantNote').value = me.note || '';
        saveSession(room, { participant_id: me.participant_id, participant_token: participantToken });
      }
      renderRoster('roomRoster', (roomData.participants || []).filter((row) => (row.status || '').toLowerCase() !== 'left'));
      setStatus('roomStatusBox', `Room ready. Overlay URL: ${abs(roomData.overlay_url || `/sites/groupup/overlay.html?room=${encodeURIComponent(room)}`)}`);
    } catch (err) {
      setStatus('roomStatusBox', `Room load failed: ${err.message}`);
    }
  };
  $('saveParticipantBtn')?.addEventListener('click', async () => {
    try {
      const data = await postJson(`/api/groupup/rooms/${encodeURIComponent(room)}/participant/update`, {
        participant_token: participantToken,
        display_name: ($('participantName')?.value || '').trim(),
        color: ($('participantColor')?.value || '').trim(),
        status: ($('participantStatus')?.value || '').trim(),
        note: ($('participantNote')?.value || '').trim(),
      });
      setStatus('roomStatusBox', 'Profile updated.');
      renderRoster('roomRoster', (data.room?.participants || []).filter((row) => (row.status || '').toLowerCase() !== 'left'));
    } catch (err) {
      setStatus('roomStatusBox', `Save failed: ${err.message}`);
    }
  });
  $('uploadAvatarBtn')?.addEventListener('click', async () => {
    const file = $('participantAvatarUpload')?.files?.[0];
    if (!file) return setStatus('roomStatusBox', 'Choose an avatar image first.');
    const form = new FormData();
    form.set('kind', 'avatar');
    form.set('participant_token', participantToken);
    form.set('file', file);
    try {
      await uploadFile(`/api/groupup/rooms/${encodeURIComponent(room)}/assets/upload`, form);
      setStatus('roomStatusBox', 'Avatar uploaded.');
      await load();
    } catch (err) {
      setStatus('roomStatusBox', `Avatar upload failed: ${err.message}`);
    }
  });
  $('leaveRoomBtn')?.addEventListener('click', async () => {
    try {
      await postJson(`/api/groupup/rooms/${encodeURIComponent(room)}/participant/leave`, { participant_token: participantToken });
      setStatus('roomStatusBox', 'You left the room.');
    } catch (err) {
      setStatus('roomStatusBox', `Leave failed: ${err.message}`);
    }
  });
  await load();
  await sendHeartbeat();
  setInterval(sendHeartbeat, 25000);
  setInterval(load, 5000);
}

async function initHostPage() {
  const room = roomFromQuery();
  if (!room) return;
  const session = loadSession(room);
  const hostToken = hostTokenFromQuery() || session.host_token || '';
  if (!hostToken) {
    setStatus('hostStatusBox', 'Missing host token. Create the room first or open the host URL from the room create response.');
    return;
  }
  const load = async () => {
    try {
      const data = await getJson(`/api/groupup/rooms/${encodeURIComponent(room)}?host_token=${encodeURIComponent(hostToken)}`);
      const roomData = data.room || {};
      const overlay = roomData.overlay || {};
      $('hostRoomTitle').textContent = roomData.title || room;
      $('hostRoomSubtitle').textContent = overlay.subheadline || 'Host console';
      $('layoutPreset').value = overlay.preset || 'auto';
      $('layoutHeadline').value = overlay.headline || '';
      $('layoutSubheadline').value = overlay.subheadline || '';
      $('layoutTicker').value = overlay.ticker || '';
      $('layoutPrimary').value = overlay.accent_primary || '';
      $('layoutSecondary').value = overlay.accent_secondary || '';
      $('layoutShowNames').checked = overlay.show_names !== false;
      $('overlayPreviewBtn').href = `overlay.html?room=${encodeURIComponent(room)}`;
      $('openRoomFromHost').href = `room.html?room=${encodeURIComponent(room)}&participant_token=${encodeURIComponent((session.participant_token || roomData.host?.participant_token || ''))}`;
      const overlayUrl = abs(roomData.overlay_url || `/sites/groupup/overlay.html?room=${encodeURIComponent(room)}`);
      $('copyOverlayUrlBtn').onclick = async (event) => {
        event.preventDefault();
        try { await navigator.clipboard.writeText(overlayUrl); setStatus('hostStatusBox', `Overlay URL copied. Use this in OBS Browser Source:\n${overlayUrl}`); }
        catch { setStatus('hostStatusBox', overlayUrl); }
      };
      const focus = $('focusParticipant');
      focus.innerHTML = '<option value="">None</option>';
      (roomData.participants || []).filter((row) => (row.status || '').toLowerCase() !== 'left').forEach((row) => {
        const opt = document.createElement('option');
        opt.value = row.participant_id;
        opt.textContent = row.display_name;
        focus.appendChild(opt);
      });
      focus.value = overlay.focus_participant_id || '';
      renderRoster('hostRoster', (roomData.participants || []).filter((row) => (row.status || '').toLowerCase() !== 'left'));
      const assets = $('roomAssetPreview');
      assets.innerHTML = '';
      [['Logo', overlay.logo_asset], ['Background', overlay.background_asset], ['Frame', overlay.frame_asset]].forEach(([label, url]) => {
        const div = document.createElement('div');
        div.className = 'asset-card';
        div.innerHTML = `<strong>${label}</strong><div class="meta">${url ? `<a href="${esc(url)}" target="_blank" rel="noreferrer">Open asset</a>` : 'Not set yet'}</div>`;
        assets.appendChild(div);
      });
      setStatus('hostStatusBox', `Host ready. Join URL: ${abs(roomData.join_url || `/sites/groupup/?room=${encodeURIComponent(room)}`)}\nOverlay URL: ${overlayUrl}`);
    } catch (err) {
      setStatus('hostStatusBox', `Host load failed: ${err.message}`);
    }
  };
  $('saveLayoutBtn')?.addEventListener('click', async () => {
    try {
      await postJson(`/api/groupup/rooms/${encodeURIComponent(room)}/layout`, {
        host_token: hostToken,
        preset: $('layoutPreset')?.value || 'auto',
        headline: $('layoutHeadline')?.value || '',
        subheadline: $('layoutSubheadline')?.value || '',
        ticker: $('layoutTicker')?.value || '',
        focus_participant_id: $('focusParticipant')?.value || '',
        show_names: $('layoutShowNames')?.checked !== false,
        accent_primary: $('layoutPrimary')?.value || '',
        accent_secondary: $('layoutSecondary')?.value || '',
      });
      setStatus('hostStatusBox', 'Layout updated.');
      await load();
    } catch (err) {
      setStatus('hostStatusBox', `Layout save failed: ${err.message}`);
    }
  });
  $('refreshHostBtn')?.addEventListener('click', load);
  const bindUpload = (buttonId, inputId, kind) => {
    $(buttonId)?.addEventListener('click', async () => {
      const file = $(inputId)?.files?.[0];
      if (!file) return setStatus('hostStatusBox', `Choose a ${kind} image first.`);
      const form = new FormData();
      form.set('kind', kind);
      form.set('host_token', hostToken);
      form.set('file', file);
      try {
        await uploadFile(`/api/groupup/rooms/${encodeURIComponent(room)}/assets/upload`, form);
        setStatus('hostStatusBox', `${kind} uploaded.`);
        await load();
      } catch (err) {
        setStatus('hostStatusBox', `${kind} upload failed: ${err.message}`);
      }
    });
  };
  bindUpload('uploadLogoBtn', 'roomLogoUpload', 'logo');
  bindUpload('uploadBackgroundBtn', 'roomBackgroundUpload', 'background');
  bindUpload('uploadFrameBtn', 'roomFrameUpload', 'frame');
  await load();
  setInterval(load, 4000);
}

function overlayGridClass(count, preset) {
  if (preset === 'focus') return 'overlay-grid focus';
  if (count <= 0) return 'overlay-grid count-1';
  if (count <= 6) return `overlay-grid count-${count}`;
  if (count <= 9) return `overlay-grid count-${count}`;
  return 'overlay-grid count-many';
}

function renderOverlay(roomData) {
  const overlay = roomData.overlay || {};
  const participants = (roomData.participants || []).filter((row) => (row.status || '').toLowerCase() !== 'left');
  const grid = $('overlayGrid');
  if (!grid) return;
  document.documentElement.style.setProperty('--primary', overlay.accent_primary || '#6ae4ff');
  document.documentElement.style.setProperty('--secondary', overlay.accent_secondary || '#ff8a4d');
  $('overlayHeadline').textContent = overlay.headline || roomData.title || 'GroupUp';
  $('overlaySubheadline').textContent = overlay.subheadline || 'Waiting for participants';
  $('overlayTicker').textContent = overlay.ticker || 'Join the room and your tile appears automatically.';
  const bg = $('overlayBackground');
  bg.style.backgroundImage = overlay.background_asset ? `linear-gradient(180deg, rgba(6,11,22,0.45), rgba(6,11,22,0.72)), url('${overlay.background_asset}')` : '';
  const logo = $('overlayLogo');
  if (overlay.logo_asset) {
    logo.src = overlay.logo_asset;
    logo.style.display = 'block';
  } else {
    logo.removeAttribute('src');
    logo.style.display = 'none';
  }
  grid.className = overlayGridClass(participants.length || 1, overlay.preset || 'auto');
  grid.innerHTML = '';
  if (!participants.length) {
    grid.innerHTML = '<div class="overlay-tile featured"><div class="tile-gradient"></div><div class="overlay-content"><div class="overlay-status">Awaiting Guests</div><div class="overlay-name">No one has joined yet</div><div class="overlay-note">Share the join link and the room will populate here.</div></div></div>';
    return;
  }
  let rows = [...participants];
  if ((overlay.preset || '').toLowerCase() === 'focus' && overlay.focus_participant_id) {
    rows.sort((a, b) => (a.participant_id === overlay.focus_participant_id ? -1 : b.participant_id === overlay.focus_participant_id ? 1 : 0));
  }
  rows.forEach((row, index) => {
    const tile = document.createElement('div');
    const featured = (overlay.preset || '').toLowerCase() === 'focus' && index === 0;
    const dim = (row.status || '').toLowerCase() === 'away';
    tile.className = `overlay-tile ${featured ? 'featured' : ''} ${dim ? 'dim' : ''}`;
    const initials = (row.display_name || '?').trim().slice(0, 1).toUpperCase() || '?';
    tile.innerHTML = `
      <div class="tile-bg" style="background-image:${row.avatar_asset ? `url('${row.avatar_asset}')` : 'none'}; background-color:${esc(row.color || '#6ae4ff')}22"></div>
      <div class="tile-gradient"></div>
      ${overlay.frame_asset ? `<div class="overlay-frame" style="background-image:url('${esc(overlay.frame_asset)}')"></div>` : ''}
      ${row.avatar_asset ? '' : `<div class="overlay-avatar-fallback" style="background:${esc(row.color || '#6ae4ff')}">${esc(initials)}</div>`}
      <div class="overlay-content">
        <div class="overlay-status">${esc(row.status || 'online')}</div>
        <div class="overlay-name">${esc(row.display_name || 'Guest')}</div>
        ${overlay.show_names === false ? '' : `<div class="overlay-note">${esc(row.note || '')}</div>`}
      </div>`;
    grid.appendChild(tile);
  });
}

async function initOverlayPage() {
  const room = roomFromQuery();
  if (!room) return;
  const load = async () => {
    try {
      const data = await getJson(`/api/groupup/rooms/${encodeURIComponent(room)}`);
      renderOverlay(data.room || {});
    } catch (err) {
      $('overlayHeadline').textContent = 'GroupUp';
      $('overlaySubheadline').textContent = `Overlay error: ${err.message}`;
    }
  };
  await load();
  setInterval(load, 2000);
}

async function initProjectPage() {
  await loadOverviewCards().catch(() => {});
}

async function initLandingPage() {
  const room = roomFromQuery();
  if (room && $('joinRoomKey')) $('joinRoomKey').value = room;
  $('createRoomBtn')?.addEventListener('click', createRoom);
  $('joinRoomBtn')?.addEventListener('click', joinRoom);
  $('openOverlayFromLanding').href = room ? `overlay.html?room=${encodeURIComponent(room)}` : 'overlay.html';
  await loadOverviewCards().catch((err) => setStatus('landingCreateStatus', `Overview load failed: ${err.message}`));
}

(async function boot() {
  if (new URLSearchParams(location.search).get("connect") !== "1") return;
  if (page === 'landing') await initLandingPage();
  if (page === 'room') await initRoomPage();
  if (page === 'host') await initHostPage();
  if (page === 'overlay') await initOverlayPage();
  if (page === 'project') await initProjectPage();
})();
