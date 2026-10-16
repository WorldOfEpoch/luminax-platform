import { readFile } from 'node:fs/promises';
import { test } from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import { checkWeb } from '../scripts/check-web.mjs';

const swarm = await readFile(new URL('../web/app.js', import.meta.url), 'utf8');
const commandCenter = await readFile(new URL('../payload/luminax-ui/index.html', import.meta.url), 'utf8');

test('all authored web scripts and inline JavaScript parse', async () => {
  assert.ok(await checkWeb() >= 7);
});

for (const search of ['', '?connect=1']) {
  test(`swarm startup connects only when selected: ${search || 'offline default'}`, async () => {
    let requests = 0;
    const context = vm.createContext({
      document: { getElementById: () => null },
      localStorage: { getItem: () => null },
      location: { search, origin: 'http://source.invalid' },
      URLSearchParams, URL,
      window: { addEventListener() {} },
      setInterval() {},
    });
    // Execute the actual startup owner with inert rendering/network substitutes.
    vm.runInContext(swarm.replace(/\bmain\(\);\s*$/, ''), context);
    context.countRequest = () => { requests++; };
    await vm.runInContext(`
      hydrateInputs = bindUi = renderAll = mode = addEvent = () => {};
      refreshLive = async () => { countRequest(); return true; };
      main();
    `, context);
    assert.equal(requests, search ? 1 : 0);
  });
}

const start = commandCenter.indexOf("if(new URLSearchParams(window.location.search).get('connect')==='1'){");
const end = commandCenter.indexOf('setTimeout(positionLinks,200)', start);
assert.ok(start > 0 && end > start, 'command center startup gate must be present');
const startup = commandCenter.slice(start, end);

for (const search of ['', '?connect=1']) {
  test(`command center starts no polling until connection: ${search || 'offline default'}`, () => {
    let calls = 0;
    let timers = 0;
    const context = {
      window: { location: { search } }, URLSearchParams,
      addMsg() {},
      setInterval() { timers++; },
    };
    for (const name of ['bootstrapControlToken', 'loadOlympusIdentity', 'loadReviewCycleDashboard', 'ensureViewData', 'refreshCommandDeck', 'syncCommandDeckOffset']) {
      context[name] = () => { calls++; return Promise.resolve(); };
    }
    vm.runInNewContext(startup, context);
    assert.equal(calls > 0, Boolean(search));
    assert.equal(timers > 0, Boolean(search));
  });
}

test('fresh microphone and playback settings require selection', () => {
  for (const id of ['auto-voice', 'voice-keep-alive', 'realtime-mic-keepalive', 'audioBoardAutoPlayToggle']) {
    const input = commandCenter.match(new RegExp(`<input\\b[^>]*id="${id}"[^>]*>`));
    assert.ok(input, id);
    assert.doesNotMatch(input[0], /\bchecked\b/, id);
  }
});

test('restoring a view stays offline before an explicit connection', async () => {
  const owner = commandCenter.match(/async function ensureViewData\([^\n]+/)[0];
  let calls = 0;
  const context = {
    window: { location: { search: '' } }, URLSearchParams,
    viewDataLoaded: {},
    loadProjectsDashboard() { calls++; return Promise.resolve(); },
  };
  await vm.runInNewContext(owner + '\nensureViewData("projectsView");', context);
  assert.equal(calls, 0);
});

test('a visibility change cannot restart saved microphone settings while offline', () => {
  const first = commandCenter.indexOf("document.addEventListener('visibilitychange'");
  const last = commandCenter.indexOf('hydrateUiPrefs();', first);
  assert.ok(first > 0 && last > first);
  let listener;
  let calls = 0;
  const context = {
    window: { location: { search: '' } }, URLSearchParams,
    document: {
      visibilityState: 'visible',
      addEventListener(_event, callback) { listener = callback; },
      getElementById: () => ({ checked: true }),
    },
    voiceContinuous: true, voiceListening: false, micHold: false, realtimeMicOn: false,
    canUseServerVoiceCapture: () => true,
    ensureWakeLock: async () => { calls++; },
    startServerVoiceCapture: async () => { calls++; },
    startRealtimeMic: async () => { calls++; },
  };
  vm.runInNewContext(commandCenter.slice(first, last), context);
  listener();
  assert.equal(calls, 0);
});
