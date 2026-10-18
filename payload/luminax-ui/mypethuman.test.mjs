import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const html = readFileSync(new URL('./mypethuman.html', import.meta.url), 'utf8');
const match = html.match(/<script id="mypethuman-app">([\s\S]*?)<\/script>/);
assert.ok(match, 'The self-contained app script must exist.');
const script = new vm.Script(match[1], { filename: 'mypethuman.html' });

function app(fetch = async () => { throw new Error('Unexpected network access in offline checks.'); }) {
  const context = vm.createContext({ URL, URLSearchParams, AbortController, setTimeout, clearTimeout, fetch });
  script.runInContext(context);
  return context.MyPetHumanTest;
}

function response(data, status = 200, headers = {}) {
  return { ok: status >= 200 && status < 300, status, headers: new Headers(headers), json: async () => data };
}

test('repository choices cannot turn into arbitrary hosts, paths, or credentials', () => {
  const { normalizeRepo, safeGithubUrl } = app();
  const credentialUrl = new URL('https://github.com/team/repository');
  credentialUrl.username = 'synthetic-user';
  credentialUrl.password = 'synthetic-password';
  const userInfoIssue = new URL('https://github.com/team/repository/issues/8');
  userInfoIssue.username = 'synthetic-user';
  assert.equal(normalizeRepo('team/repository'), 'team/repository');
  assert.equal(normalizeRepo('https://github.com/team/repository/'), 'team/repository');
  for (const value of ['https://github.com.evil.example/team/repository', credentialUrl.href, 'https://github.com/team/repository?token=secret', 'team/repository/issues', 'team/..', '../repository', 'team%2Frepo/other', '//example.com/path', 'https://github.com/team/repository#section']) assert.equal(normalizeRepo(value), null, value);
  assert.equal(safeGithubUrl('https://github.com/TEAM/repository/issues/8', 'team/repository', 8), 'https://github.com/TEAM/repository/issues/8');
  for (const value of ['javascript:alert(1)', 'https://github.com.evil.example/team/repository/issues/8', 'https://github.com/team/other/issues/8', 'https://github.com/team/repository/pull/8', 'https://github.com/team/repository/issues/8?return=elsewhere', userInfoIssue.href]) assert.equal(safeGithubUrl(value, 'team/repository', 8), null, value);
});

test('saved browser configuration retains choices only and bounds imported repositories', () => {
  const { normalizeConfig } = app();
  const config = normalizeConfig({ token: 'must-not-survive', tasks: [{ title: 'private record' }], connectLocal: 'true', projectKey: 'work', repositories: [{ repository: 'team/repo', token: 'must-not-survive', purpose: 'Track builds' }, { repository: 'TEAM/REPO' }, ...Array.from({ length: 20 }, (_, n) => ({ repository: 'team/project' + n }))] });
  assert.equal(config.repositories.length, 12);
  assert.equal(config.connectLocal, false);
  assert.equal(config.projectKey, 'work');
  assert.equal(JSON.stringify(config).includes('must-not-survive'), false);
  assert.equal(JSON.stringify(config).includes('private record'), false);
  assert.equal(config.repositories[0].purpose, 'Track builds');
});

test('public and server issue responses exclude pull requests and unsafe evidence links', () => {
  const { normalizeIssues } = app();
  const publicRows = [
    { number: 1, title: '<img src=x onerror=alert(1)>', state: 'open', html_url: 'https://github.com/team/repo/issues/1', labels: [{ name: 'bug' }] },
    { number: 2, title: 'Pull request', state: 'open', html_url: 'https://github.com/team/repo/issues/2', pull_request: null },
    { number: 3, title: 'Wrong repository', state: 'open', html_url: 'https://github.com/other/repo/issues/3' },
    { number: 4, title: 'Closed', state: 'closed', html_url: 'https://github.com/team/repo/issues/4' },
  ];
  const issues = normalizeIssues(publicRows, 'team/repo');
  assert.equal(issues.length, 1);
  assert.equal(issues[0].title, '<img src=x onerror=alert(1)>');
  assert.equal(issues[0].labels[0], 'bug');
  assert.equal(normalizeIssues([{ number: 5, title: 'Server issue', state: 'open', url: 'https://github.com/team/repo/issues/5', labels: ['help'] }], 'team/repo', true)[0].number, 5);
  assert.throws(() => normalizeIssues({ error: 'not a list' }, 'team/repo'));
  assert.doesNotMatch(match[1], /\binnerHTML\b|\binsertAdjacentHTML\b|document\.write\s*\(|\beval\s*\(/, 'Remote titles and purposes must be rendered as text.');
});

test('an unavailable task store cannot masquerade as an empty successful task list', () => {
  const { normalizeTasks, activeTasks } = app();
  assert.throws(() => normalizeTasks({ rows: [] }, { summary: { db_error: 'database unavailable' } }));
  assert.throws(() => normalizeTasks({ rows: [] }, null));
  assert.equal(normalizeTasks({ rows: [] }, { summary: {} }).length, 0);
  const tasks = normalizeTasks({ rows: [
    { task_uid: 'done', goal: 'Done task', status: 'completed', created_at: 1 },
    { task_uid: 'queued', goal: 'Queued task', status: 'queued', created_at: 2 },
    { task_uid: 'progress', goal: 'Current work', status: 'in_progress', created_at: 3 },
    { title: 'A made-up fallback title is insufficient' },
  ] }, { summary: {} });
  assert.equal(tasks.length, 3);
  assert.equal(activeTasks(tasks).map(task => task.uid).join(','), 'progress,queued');
});

test('writes require the exact server-reported trusted operator session', () => {
  const { trustedSession } = app();
  const ping = { status: 'ok', service: 'assistant-core', control_granted: true, web_control_auto_granted: true, trusted_web_control_user: 'operator' };
  assert.equal(trustedSession(ping), true);
  for (const change of [{ control_granted: false }, { web_control_auto_granted: false }, { trusted_web_control_user: '' }, { service: 'another-service' }, { control_granted: 'true' }, { status: 'error' }]) assert.equal(trustedSession({ ...ping, ...change }), false);
  assert.equal(trustedSession({ control_granted: true }), false);
});

test('late repository responses are discarded after disconnect, replacement, or transport change', () => {
  const { requestIsCurrent } = app();
  const privateRequest = { transport: 'server', generation: 4 };
  assert.equal(requestIsCurrent(privateRequest, privateRequest, 4, true), true);
  assert.equal(requestIsCurrent(undefined, privateRequest, 5, false), false, 'Disconnect cleared this request.');
  assert.equal(requestIsCurrent(privateRequest, privateRequest, 5, true), false, 'A newer home base session owns the view.');
  assert.equal(requestIsCurrent(privateRequest, privateRequest, 4, false), false, 'The home base is no longer connected.');
  const publicReplacement = { transport: 'public', generation: 4 };
  assert.equal(requestIsCurrent(publicReplacement, privateRequest, 4, true), false, 'Public and private results cannot share pending identity.');
  assert.equal(requestIsCurrent(undefined, publicReplacement, 4, true), false, 'Removing a repository invalidates its pending response.');
  assert.equal(requestIsCurrent(publicReplacement, publicReplacement, 5, false), true, 'An independent public read may complete without the home base.');
});

test('public reads omit credentials; native writes use only the existing same-origin session', async () => {
  const calls = [];
  const { requestJSON } = app(async (url, options) => { calls.push({ url, options }); return response({ status: 'ok' }); });
  await requestJSON('https://api.github.com/repos/team/repo', { external: true });
  await requestJSON('/api/projects/tasks/quick_add', { body: { token: '', project_key: 'work', command: 'Track this task', source: 'mypethuman' } });
  assert.equal(calls[0].options.method, 'GET');
  assert.equal(calls[0].options.credentials, 'omit');
  assert.equal(calls[0].options.mode, 'cors');
  assert.equal(calls[0].options.referrerPolicy, 'no-referrer');
  assert.equal(calls[0].options.cache, 'no-store');
  assert.equal(calls[1].options.method, 'POST');
  assert.equal(calls[1].options.credentials, 'same-origin');
  assert.equal(calls[1].options.mode, 'same-origin');
  assert.equal(JSON.parse(calls[1].options.body).token, '');
  for (const { options } of calls) {
    assert.equal(options.redirect, 'error');
    assert.equal(Object.keys(options.headers).some(key => /authorization|x-web-auth|x-control-token/i.test(key)), false);
  }
});

test('one failed non-idempotent write is never retried', async () => {
  let requests = 0;
  const { requestJSON } = app(async () => { requests++; throw new Error('Lost response after a possible server write'); });
  await assert.rejects(requestJSON('/api/projects/tasks/quick_add', { body: { token: '', command: 'One task' } }), error => error.code === 'network');
  assert.equal(requests, 1);
});

test('a timed-out request is aborted and not retried', async () => {
  let requests = 0, aborted = false;
  const { requestJSON } = app((url, options) => { requests++; return new Promise((resolve, reject) => { options.signal.addEventListener('abort', () => { aborted = true; const error = new Error('aborted'); error.name = 'AbortError'; reject(error); }); }); });
  await assert.rejects(requestJSON('/api/ping', { timeoutMs: 5 }), error => error.code === 'timeout');
  assert.equal(aborted, true);
  assert.equal(requests, 1);
});

test('a GitHub rate limit blocks additional public repository requests until reset', async () => {
  let requests = 0;
  const { requestJSON } = app(async () => { requests++; return response({}, 403, { 'retry-after': '120' }); });
  const before = Date.now();
  await assert.rejects(requestJSON('https://api.github.com/repos/team/one', { external: true }), error => error.code === 'rate_limit' && error.retryAt >= before + 120000);
  await assert.rejects(requestJSON('https://api.github.com/repos/team/two', { external: true }), error => error.code === 'rate_limit');
  assert.equal(requests, 1);
});

test('server cooldown, authentication, malformed data, and bounded pagination remain distinct', async () => {
  let { requestJSON } = app(async () => response({ detail: { code: 'github_rate_limited', retry_after_seconds: 180 } }, 503, { 'Retry-After': '120' }));
  const before = Date.now();
  await assert.rejects(requestJSON('/api/mypethuman/projects/team/repo?limit=20'), error => error.code === 'github_rate_limited' && error.retryAt >= before + 180000);
  ({ requestJSON } = app(async () => response({}, 403)));
  await assert.rejects(requestJSON('/api/mypethuman/status'), error => error.code === 'auth');
  ({ requestJSON } = app(async () => ({ ...response(null), json: async () => { throw new Error('sign-in HTML'); } })));
  await assert.rejects(requestJSON('/api/ping'), error => error.code === 'invalid_response');
  ({ requestJSON } = app(async () => response([], 200, { link: '<https://api.github.com/repos/team/repo/issues?page=2>; rel="next"' })));
  assert.equal((await requestJSON('https://api.github.com/repos/team/repo/issues?page=1', { external: true })).hasNext, true);
});

test('unsupported external sources are rejected before any network access', async () => {
  let requests = 0;
  const { requestJSON } = app(async () => { requests++; return response({}); });
  await assert.rejects(requestJSON('https://example.com/repos/team/repo', { external: true }), error => error.code === 'invalid_source');
  await assert.rejects(requestJSON('//example.com/api/ping'), error => error.code === 'invalid_source');
  assert.equal(requests, 0);
});

function serviceControlPayload() {
  return {
    status: 'ok', runtime_enabled: true, host_execution_enabled: true,
    operator_authenticated: true, authentication_mode: 'trusted_ingress',
    control_granted: true, control_state: 'granted', actions_enabled: true,
    maintenance: { mode: 'normal', service_actions_allowed: true },
    allowed_services: [{ name: 'worker', kind: 'docker', allowed_actions: ['start', 'stop', 'restart'] }],
    profiles: [], profiles_status: 'ok', checked_at: 1800000000,
  };
}

const operatorPing = { status: 'ok', service: 'assistant-core', control_granted: true, web_control_auto_granted: true, trusted_web_control_user: 'operator' };

test('only the five exact admin routes with their intended methods can be requested', async () => {
  const calls = [];
  const { requestJSON } = app(async (url, options) => { calls.push({ url, options }); return response({}); });
  for (const path of ['/admin/services/control', '/admin/services/status']) await requestJSON(path);
  for (const action of ['start', 'stop', 'restart']) await requestJSON('/admin/services/' + action, { body: { token: '', name: 'worker' } });
  assert.equal(calls.length, 5);
  assert.equal(calls.map(call => call.options.method).join(','), 'GET,GET,POST,POST,POST');
  for (const { options } of calls) {
    assert.equal(options.credentials, 'same-origin');
    assert.equal(Object.keys(options.headers).some(key => /authorization|x-web-auth|x-control-token/i.test(key)), false);
  }
  for (const path of ['/admin/services/start', '/admin/services/stop', '/admin/services/restart', '/admin/logs/tail?name=worker', '/admin/services/status?token=value', '/admin/services/start?name=worker', '/admin/services/restart/', '/admin/services/../../execute']) await assert.rejects(requestJSON(path), error => error.code === 'invalid_source');
  for (const path of ['/admin/services/control', '/admin/services/status', '/admin/maintenance/mode']) await assert.rejects(requestJSON(path, { body: { token: '' } }), error => error.code === 'invalid_source');
  await assert.rejects(requestJSON('https://api.github.com/repos/team/repo/issues', { external: true, body: { title: 'A write is outside this connection' } }), error => error.code === 'invalid_source');
  assert.equal(calls.length, 5, 'Rejected routes and methods never reach fetch.');
});

test('service command permission requires current trusted access, feature flags, maintenance, health identity, and exact allowlist', () => {
  const { normalizeServiceControl, serviceControlAllowed } = app();
  const service = { name: 'worker', kind: 'docker', status: 'ok' };
  const ready = normalizeServiceControl(serviceControlPayload());
  for (const action of ['start', 'stop', 'restart']) assert.equal(serviceControlAllowed(ready, operatorPing, service, action), true);
  const changes = [
    { runtime_enabled: false }, { host_execution_enabled: false }, { operator_authenticated: false },
    { authentication_mode: 'operator_token' }, { control_granted: false }, { actions_enabled: false },
    { maintenance: { mode: 'freeze', service_actions_allowed: false } },
    { allowed_services: [] }, { allowed_services: [{ name: 'other-worker', kind: 'docker', allowed_actions: ['restart'] }] },
    { allowed_services: [{ name: 'worker', kind: 'docker', allowed_actions: [] }] },
    { host_execution_enabled: 'true' },
  ];
  for (const change of changes) assert.equal(serviceControlAllowed(normalizeServiceControl({ ...serviceControlPayload(), ...change }), operatorPing, service, 'restart'), false);
  assert.equal(serviceControlAllowed(ready, { ...operatorPing, web_control_auto_granted: false }, service, 'restart'), false);
  assert.equal(serviceControlAllowed(ready, operatorPing, { ...service, kind: 'edge' }, 'restart'), false);
  assert.equal(serviceControlAllowed(ready, operatorPing, undefined, 'restart'), false, 'A configured service missing from current observations cannot receive a command.');
  assert.equal(serviceControlAllowed(ready, operatorPing, service, 'execute'), false);
  assert.equal(serviceControlAllowed({ ...ready, maintenance: null }, operatorPing, service, 'restart'), false);
});

test('service observations and declared profiles never become verified availability or raw logs', () => {
  const { normalizeServices, normalizeServiceControl } = app();
  const services = normalizeServices({ services: [
    { name: 'worker', kind: 'docker', status: 'idle', display_name: 'Worker', info: 'raw-detail-must-not-survive', stdout: 'raw-output-must-not-survive' },
    { name: 'worker;execute', kind: 'docker', status: 'ok' },
  ] });
  assert.equal(services.length, 1);
  assert.equal(services[0].status, 'idle');
  assert.equal(JSON.stringify(services).includes('must-not-survive'), false);
  assert.throws(() => normalizeServices({ status: 'feature_disabled' }));
  const control = normalizeServiceControl({ ...serviceControlPayload(), provider_token: 'must-not-survive', profiles: [{ key: 'voice-service', display_name: 'Voice service', availability: 'available', capabilities: ['speech'], routes: [{ owner: 'voice-service', path: '/api/voice/status' }, { owner: 'foreign', path: 'https://example.com/run' }, { owner: 'foreign', path: '//example.com/run' }] }] });
  assert.equal(control.profiles[0].availability, 'declared');
  assert.equal(control.profiles[0].routes.length, 1);
  assert.equal(control.profiles[0].routes[0].path, '/api/voice/status');
  assert.equal(JSON.stringify(control).includes('must-not-survive'), false);
  assert.throws(() => normalizeServiceControl({ status: 'ok' }));
});

test('maintenance and disabled service features produce fixed messages without exposing handler output', async () => {
  let requests = 0;
  let { requestJSON } = app(async () => { requests++; return response({ detail: 'raw-policy-detail-must-not-survive' }, 409); });
  await assert.rejects(requestJSON('/admin/services/restart', { body: { token: '', name: 'worker' } }), error => error.code === 'maintenance_blocked' && !error.message.includes('must-not-survive'));
  assert.equal(requests, 1, 'A rejected service command is not retried.');
  for (const payload of [{ status: 'feature_disabled', service: 'admin-api' }, { detail: 'feature_disabled' }]) {
    ({ requestJSON } = app(async () => response(payload, 503)));
    await assert.rejects(requestJSON('/admin/services/status'), error => error.code === 'feature_disabled');
  }
  ({ requestJSON } = app(async () => response({ status: 'runtime_disabled', service: 'admin-api' }, 503)));
  await assert.rejects(requestJSON('/admin/services/control'), error => error.code === 'runtime_disabled');
});
