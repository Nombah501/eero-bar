const assert = require('assert');
const fs = require('fs');
const vm = require('vm');
const path = require('path');

const sourcePath = path.join(__dirname, '..', 'EeroModel.js');
const source = fs.readFileSync(sourcePath, 'utf8').replace(/^\.pragma library\s*/m, '');
const context = { module: { exports: {} }, exports: {} };
vm.runInNewContext(source, context, { filename: sourcePath });
const Model = context.module.exports;

// -- parseHelperOutput -------------------------------------------------------

let result = Model.parseHelperOutput(
  '{"ok":true,"type":"snapshot","fetchedAt":"2026-09-22T00:00:00Z","network":{"name":"Home"}}', 0);
assert.strictEqual(result.ok, true);
assert.strictEqual(result.type, 'snapshot');
assert.strictEqual(result.code, null);
assert.strictEqual(result.data.network.name, 'Home');

result = Model.parseHelperOutput(
  '{"ok":false,"type":"error","code":"auth_required","message":"Not logged in."}', 1);
assert.strictEqual(result.ok, false);
assert.strictEqual(result.type, 'error');
assert.strictEqual(result.code, 'auth_required');
assert.strictEqual(result.message, 'Not logged in.');
assert.strictEqual(result.data, null);

result = Model.parseHelperOutput('not json at all', 1);
assert.strictEqual(result.ok, false);
assert.strictEqual(result.code, 'api');
assert.strictEqual(result.message, 'Helper returned invalid output');

result = Model.parseHelperOutput('', 1);
assert.strictEqual(result.ok, false);
assert.strictEqual(result.code, 'api', 'empty stdout is invalid output');

result = Model.parseHelperOutput('   \n\n  \n', 1);
assert.strictEqual(result.ok, false, 'whitespace-only stdout is invalid output');

// multi-line output: diagnostics on earlier lines, the real payload last,
// with trailing blank lines after it.
result = Model.parseHelperOutput(
  'some diagnostic noise\nmore noise\n{"ok":true,"type":"logged_out"}\n\n\n', 0);
assert.strictEqual(result.ok, true);
assert.strictEqual(result.type, 'logged_out');

// exit code/payload mismatch: an `ok` payload with a nonzero exit violates
// the protocol and must not be trusted as success.
result = Model.parseHelperOutput('{"ok":true,"type":"snapshot"}', 1);
assert.strictEqual(result.ok, false);
assert.strictEqual(result.code, 'api');

// error object missing code/message falls back to defaults.
result = Model.parseHelperOutput('{"ok":false}', 1);
assert.strictEqual(result.code, 'api');
assert.strictEqual(result.message, 'Helper returned invalid output');

// non-object JSON must not throw.
assert.strictEqual(Model.parseHelperOutput('42', 0).ok, false);
assert.strictEqual(Model.parseHelperOutput('null', 0).ok, false);
assert.strictEqual(Model.parseHelperOutput('[1,2,3]', 1).ok, false);
assert.strictEqual(Model.parseHelperOutput(undefined, 1).ok, false);

// an explicit ok:false payload with exit 0 is still an error (exit code
// alone must never be trusted over the payload's own `ok`).
result = Model.parseHelperOutput('{"ok":false,"type":"error","code":"usage","message":"bad"}', 0);
assert.strictEqual(result.ok, false);
assert.strictEqual(result.code, 'usage');
assert.strictEqual(result.message, 'bad');

// -- summarize ----------------------------------------------------------

// vm.runInNewContext gives Model objects a different realm's Object/Array
// prototypes than this file's literals, so assert.deepStrictEqual (which
// checks prototype identity) cannot be used against its return values;
// compare fields individually instead.
function assertSummary(actual, expected, msg) {
  assert.strictEqual(actual.level, expected.level, msg);
  assert.strictEqual(actual.onlineNodes, expected.onlineNodes, msg);
  assert.strictEqual(actual.totalNodes, expected.totalNodes, msg);
  assert.strictEqual(actual.connectedClients, expected.connectedClients, msg);
  assert.strictEqual(actual.networkName, expected.networkName, msg);
}

assertSummary(Model.summarize(null),
  { level: 'unknown', onlineNodes: 0, totalNodes: 0, connectedClients: 0, networkName: null });

let snapshot = {
  network: { name: 'Home', status: 'online', internetOnline: true },
  nodes: [{ online: true }, { online: true }],
  clients: [{ connected: true }, { connected: true }, { connected: false }],
};
let summary = Model.summarize(snapshot);
assert.strictEqual(summary.level, 'ok');
assert.strictEqual(summary.onlineNodes, 2);
assert.strictEqual(summary.totalNodes, 2);
assert.strictEqual(summary.connectedClients, 2);
assert.strictEqual(summary.networkName, 'Home');

// Counts keep unknown connectivity distinct from disconnected devices.
const partial = Model.summarize({ clients: [{ connected: true }, { connected: false }, { connected: null }] });
assert.strictEqual(partial.totalClients, 3);
assert.strictEqual(partial.unknownClients, 1);
assert.strictEqual(Model.tooltipText('error', null, { code: 'network' }).includes('offline'), false,
  'a cloud transport failure is not evidence the Network is offline');
assert.strictEqual(Model.hasNewSpeedResult({ date: '2026-10-10T11:00:00Z', down: 312.4, up: 41 }, '2026-10-10T10:00:00Z'), true);
assert.strictEqual(Model.hasNewSpeedResult({ date: '2026-10-10T11:00:00Z', down: 0, up: null }, '2026-10-10T10:00:00Z'), true,
  'a measured zero is valid even when the other direction is unknown');
assert.strictEqual(Model.hasNewSpeedResult({ date: '2026-10-10T11:00:00Z', down: null, up: null }, '2026-10-10T10:00:00Z'), false,
  'a newer timestamp alone is not a speed measurement');
assert.strictEqual(Model.hasNewSpeedResult({ date: '2026-10-10T11:00:00Z', down: Infinity, up: -1 }, '2026-10-10T10:00:00Z'), false,
  'invalid speeds cannot confirm a result');
assert.strictEqual(Model.hasNewSpeedResult({ date: '2026-10-10T10:00:00Z', down: 312.4 }, '2026-10-10T10:00:00Z'), false);
assert.strictEqual(Model.hasNewSpeedResult({ date: null, down: 312.4 }, null), false);
assert.strictEqual(Model.hasNewSpeedResult({ date: '2026-10-10T10:00:00Z', down: 312.4 }, null,
  Date.parse('2026-10-10T11:00:00Z')), false, 'an old measurement is not confirmation when baseline was unknown');

snapshot.network.status = 'degraded';
assert.strictEqual(Model.summarize(snapshot).level, 'warn', 'degraded network status is warn');

snapshot.network.status = 'offline';
assert.strictEqual(Model.summarize(snapshot).level, 'bad', 'offline network status is bad');

snapshot.network.status = 'online';
snapshot.network.internetOnline = false;
assert.strictEqual(Model.summarize(snapshot).level, 'bad', 'internetOnline:false is bad even if status says online');

snapshot.network.internetOnline = true;
snapshot.nodes = [{ online: true }, { online: false }];
assert.strictEqual(Model.summarize(snapshot).level, 'warn', 'any offline node is warn');

// A node whose online-ness is unknown (null) is excluded from the
// onlineNodes count, but must NOT by itself downgrade an online network's
// level -- only a confirmed-offline node (online === false) does that.
snapshot.nodes = [{ online: true }, { online: null }];
summary = Model.summarize(snapshot);
assert.strictEqual(summary.level, 'ok', 'an unknown-online node does not block ok when the network is online');
assert.strictEqual(summary.onlineNodes, 1, 'an unknown-online node is not counted as online');
assert.strictEqual(summary.totalNodes, 2);

// All-null nodes: still ok if the network itself reports online.
snapshot.nodes = [{ online: null }, { online: null }];
summary = Model.summarize(snapshot);
assert.strictEqual(summary.level, 'ok');
assert.strictEqual(summary.onlineNodes, 0);
assert.strictEqual(summary.totalNodes, 2);

// A null entry in the nodes array itself (not just a null `online` field)
// must not throw.
snapshot.nodes = [{ online: true }, null];
summary = Model.summarize(snapshot);
assert.strictEqual(summary.level, 'ok');
assert.strictEqual(summary.onlineNodes, 1);
assert.strictEqual(summary.totalNodes, 2);

// Missing fields entirely.
assert.strictEqual(Model.summarize({}).level, 'unknown');
assert.strictEqual(Model.summarize({ network: {} }).level, 'unknown');
assert.strictEqual(Model.summarize({ nodes: 'not-an-array', clients: null }).totalNodes, 0);

// -- barLevel -------------------------------------------------------------

assert.strictEqual(Model.barLevel('signedOut', null, null), 'signedOut');
assert.strictEqual(Model.barLevel('codeSent', snapshot, null), 'signedOut');

let okSnapshot = { network: { status: 'online', internetOnline: true }, nodes: [{ online: true }], clients: [] };
assert.strictEqual(Model.barLevel('ready', okSnapshot, null), 'ok');
assert.strictEqual(Model.barLevel('ready', okSnapshot, { code: 'network', message: 'x' }), 'warn',
  'a network error bumps an ok stale snapshot up to warn');
assert.strictEqual(Model.barLevel('ready', okSnapshot, { code: 'rate_limited', message: 'x' }), 'warn');
assert.strictEqual(Model.barLevel('ready', okSnapshot, { code: 'api', message: 'x' }), 'ok',
  'a non-connectivity error does not touch the level');

let badSnapshot = { network: { status: 'offline', internetOnline: false }, nodes: [], clients: [] };
assert.strictEqual(Model.barLevel('error', badSnapshot, { code: 'network', message: 'x' }), 'bad',
  'a network error must not downgrade an already-bad level to warn');

assert.strictEqual(Model.barLevel('error', null, { code: 'network', message: 'x' }), 'warn',
  'unknown level bumps to warn on a connectivity error');

assert.strictEqual(Model.barLevel('loading', null, null), 'unknown',
  'loading with no snapshot yet and no error is unknown, not signedOut');
assert.strictEqual(Model.barLevel('loading', okSnapshot, null), 'ok',
  'loading does not itself mask a snapshot carried over from before a reload');


// -- visibleClients -----------------------------------------------------

assert.strictEqual(Model.visibleClients(null, false).length, 0);
assert.strictEqual(Model.visibleClients({}, false).length, 0);

let clientsSnapshot = { clients: [{ mac: 'a', connected: true }, { mac: 'b', connected: false }, { mac: 'c' }] };
assert.strictEqual(Array.from(Model.visibleClients(clientsSnapshot, false)).map(c => c.mac).join(','), 'a');
assert.strictEqual(Array.from(Model.visibleClients(clientsSnapshot, true)).map(c => c.mac).join(','), 'a,b,c');

// -- matchClients ----------------------------------------------------------

assert.strictEqual(Model.matchClients(null, 'x').length, 0);
assert.strictEqual(Model.matchClients({}, 'x').length, 0);

let searchSnapshot = {
  clients: [
    { mac: 'aa:bb:cc:dd:ee:ff', name: 'Pixel', ip: '192.168.4.30', connected: true },
    { mac: 'bb:cc:dd:ee:ff:00', name: 'MacBook', ip: '192.168.4.31', connected: false },
    { mac: 'cc:dd:ee:ff:00:11', name: 'TV', ip: '192.168.4.32' },
    null
  ]
};

function clientNames(list) { return Array.from(list).map(c => c && c.name).join(','); }

assert.strictEqual(clientNames(Model.matchClients(searchSnapshot, '')), 'Pixel,MacBook,TV',
  'empty query returns every client');
assert.strictEqual(clientNames(Model.matchClients(searchSnapshot, '   ')), 'Pixel,MacBook,TV',
  'whitespace-only query matches everything');
assert.strictEqual(clientNames(Model.matchClients(searchSnapshot, 'pix')), 'Pixel',
  'case-insensitive name match');
assert.strictEqual(clientNames(Model.matchClients(searchSnapshot, 'MAC')), 'MacBook',
  'uppercase query matches');
assert.strictEqual(clientNames(Model.matchClients(searchSnapshot, '4.32')), 'TV',
  'ip substring match');
assert.strictEqual(clientNames(Model.matchClients(searchSnapshot, '00:11')), 'TV',
  'mac substring match');
assert.strictEqual(clientNames(Model.matchClients(searchSnapshot, 'zzz')), '',
  'no match is an empty list');
// Search must not care about connected state: an offline client is a hit too.
assert.ok(Array.from(Model.matchClients(searchSnapshot, 'macbook'))[0].connected === false);

// -- formatSpeed ----------------------------------------------------------

assert.strictEqual(Model.formatSpeed({ down: 312.4, up: 41.0, units: 'Mbps' }), '↓312 ↑41 Mbps');
assert.strictEqual(Model.formatSpeed(null), '—');
assert.strictEqual(Model.formatSpeed({}), '—');
assert.strictEqual(Model.formatSpeed({ down: null, up: null }), '—');
assert.strictEqual(Model.formatSpeed({ down: 10 }), '↓10 units unknown', 'missing units are not invented');
assert.strictEqual(Model.formatSpeed({ up: 5, units: 'Mbps' }), '↑5 Mbps');
assert.strictEqual(Model.formatSpeed({ down: 1, up: 2, units: '' }), '↓1 ↑2 units unknown');

// -- relativeTime ---------------------------------------------------------

const base = Date.parse('2026-09-22T12:00:00Z');
assert.strictEqual(Model.relativeTime('2026-09-22T12:00:00Z', base), 'just now');
assert.strictEqual(Model.relativeTime('2026-09-22T11:59:31Z', base), 'just now');
assert.strictEqual(Model.relativeTime('2026-09-22T11:55:00Z', base), '5 min ago');
assert.strictEqual(Model.relativeTime('2026-09-22T09:00:00Z', base), '3 h ago');
assert.strictEqual(Model.relativeTime('2026-09-20T12:00:00Z', base), '2 d ago');
assert.strictEqual(Model.relativeTime('not-a-date', base), '');
assert.strictEqual(Model.relativeTime('', base), '');
assert.strictEqual(Model.relativeTime(null, base), '');
assert.strictEqual(Model.relativeTime('2026-09-22T12:00:05Z', base), 'just now', 'a future timestamp clamps to just now');

// -- errorText --------------------------------------------------------------

// auth_required/network always show the fixed copy, even over a helper
// message -- these two must never say something other than "signed out" /
// "offline".
assert.strictEqual(Model.errorText('auth_required', 'ignored'), 'Sign in to eero to continue.');
assert.strictEqual(Model.errorText('network', 'ignored'), 'Could not reach eero. Check your connection.');
// Every other known code prefers a non-empty helper message over its
// generic line.
assert.strictEqual(Model.errorText('login_rejected', 'Wrong code.'), 'Wrong code.');
assert.strictEqual(Model.errorText('rate_limited', 'Slow down.'), 'Slow down.');
assert.strictEqual(Model.errorText('api', 'Network changed; refresh and try again.'), 'Network changed; refresh and try again.');
assert.strictEqual(Model.errorText('usage', 'Invalid node.'), 'Invalid node.');
assert.strictEqual(Model.errorText('state', 'unsafe session file'), 'unsafe session file');
// ...and falls back to the generic per-code line when the message is
// empty/missing.
assert.strictEqual(Model.errorText('login_rejected', ''), 'That identifier or code was not accepted.');
assert.strictEqual(Model.errorText('rate_limited', undefined), 'Too many requests to eero. Try again shortly.');
assert.strictEqual(Model.errorText('api', ''), 'eero returned an unexpected response.');
assert.strictEqual(Model.errorText('usage', undefined), 'That request was invalid.');
assert.strictEqual(Model.errorText('state', ''), "eero's saved session is unsafe to use. Check file permissions.");
assert.strictEqual(Model.errorText('something_new', 'literal message'), 'literal message');
assert.strictEqual(Model.errorText('something_new', ''), 'Something went wrong.');
assert.strictEqual(Model.errorText('something_new', undefined), 'Something went wrong.');

// -- validators -------------------------------------------------------------

assert.strictEqual(Model.isNodeUrl('/2.2/eeros/456'), true);
assert.strictEqual(Model.isNodeUrl('/2.2/eeros/'), false);
assert.strictEqual(Model.isNodeUrl('/2.2/eeros/abc'), false);
assert.strictEqual(Model.isNodeUrl('/2.2/eeros/456\n'), false, 'a trailing newline must not slip through');
assert.strictEqual(Model.isNodeUrl(null), false);
assert.strictEqual(Model.isNodeUrl(42), false);
// [0-9] must reject Unicode "digits" that are not ASCII 0-9 (JS \d would
// also reject these since it is ASCII-only, but the helper's own regexes
// are deliberately [0-9] rather than \d -- mirror that exactly).
assert.strictEqual(Model.isNodeUrl('/2.2/eeros/٥٦٧'), false,
  'Arabic-Indic digits are not [0-9]');
assert.strictEqual(Model.isMac('a０:bb:cc:dd:ee:ff'), false, 'fullwidth digit is not [0-9a-f]');

assert.strictEqual(Model.isProfileUrl('/2.2/networks/123/profiles/789'), true);
assert.strictEqual(Model.isProfileUrl('/2.2/networks/123/profiles/'), false);
assert.strictEqual(Model.isProfileUrl('/2.2/networks/123'), false);
assert.strictEqual(Model.isProfileUrl('/2.2/networks/123/profiles/789\n'), false,
  'a trailing newline must not slip through');
assert.strictEqual(Model.isProfileUrl('/2.2/networks/۱۲۳/profiles/789'), false,
  'Extended Arabic-Indic digits are not [0-9]');

assert.strictEqual(Model.isMac('aa:bb:cc:dd:ee:ff'), true);
assert.strictEqual(Model.isMac('AA:BB:CC:DD:EE:FF'), true, 'mixed case is accepted, matching the helper');
assert.strictEqual(Model.isMac('aa:bb:cc:dd:ee'), false);
assert.strictEqual(Model.isMac('aabbccddeeff'), false);
assert.strictEqual(Model.isMac(null), false);

// -- actionKey ----------------------------------------------------------

assert.strictEqual(Model.actionKey('led', '/2.2/eeros/5'), 'led:/2.2/eeros/5');
assert.strictEqual(Model.actionKey('guest', ''), 'guest:');
assert.strictEqual(Model.actionKey('reboot-network', undefined), 'reboot-network:');
assert.strictEqual(Model.actionKey('reboot-network', null), 'reboot-network:');

// -- glyphs -----------------------------------------------------------------

const glyphs = [Model.GLYPH_ROUTER, Model.GLYPH_WIFI, Model.GLYPH_ETHERNET, Model.GLYPH_PAUSE,
  Model.GLYPH_REBOOT, Model.GLYPH_LED, Model.GLYPH_LOGIN, Model.GLYPH_GUEST, Model.GLYPH_WARNING];
for (const glyph of glyphs) {
  assert.strictEqual(typeof glyph, 'string');
  assert.strictEqual(Array.from(glyph).length, 1, 'each glyph is exactly one code point: ' + glyph);
}
assert.strictEqual(new Set(glyphs).size, glyphs.length, 'glyphs must be distinct');


// Node identity is the current Snapshot target, never a name/id fallback.
const nodesSnapshot = { nodes: [
  { url: '/2.2/eeros/2', name: 'Leaf', gateway: false, ledOn: null },
  { url: '/2.2/eeros/1', name: 'Z'.repeat(300), gateway: true, online: null },
  { url: null, name: 'Unknown', gateway: null }
] };
assert.strictEqual(Model.nodeForUrl(nodesSnapshot, '/2.2/eeros/1'), nodesSnapshot.nodes[1]);
assert.strictEqual(Model.nodeForUrl(nodesSnapshot, '/2.2/eeros/999'), null);
assert.strictEqual(Model.nodeForUrl(nodesSnapshot, null), null);
assert.strictEqual(Model.nodeForUrl(nodesSnapshot, 'https://example.invalid/2.2/eeros/1'), null);
assert.strictEqual(Model.orderedNodes(nodesSnapshot)[0], nodesSnapshot.nodes[1]);
assert.strictEqual(Model.orderedNodes(nodesSnapshot)[0].name.length, 300);
assert.strictEqual(nodesSnapshot.nodes[0].gateway, false, 'ordering never mutates Snapshot');
assert.strictEqual(Model.orderedNodes(nodesSnapshot)[0].online, null);
assert.strictEqual(Model.isNetworkUrl('/2.2/networks/1'), true);
assert.strictEqual(Model.isNetworkUrl('/2.2/networks/1/reboot'), false);
assert.strictEqual(Model.isNetworkUrl(null), false);
// Clients page public model: explicit scope applies even to search.
const clientJourney = {clients: [
  {name: 'Zulu', mac: 'aa:bb:cc:dd:ee:01', ip: '192.0.2.10', connected: true, paused: true, guest: true, nodeName: 'Study', profileName: 'Kids'},
  {name: 'alpha', mac: 'aa:bb:cc:dd:ee:02', ip: '192.0.2.11', connected: true, paused: false, guest: false, nodeName: 'Study', profileName: 'Adults'},
  {name: 'Offline Tablet', mac: 'aa:bb:cc:dd:ee:03', ip: '192.0.2.12', connected: false, paused: true, guest: true, nodeName: 'Study', profileName: 'Kids'},
  {name: null, mac: null, ip: null, connected: null, paused: null, guest: null, nodeName: null, profileName: null},
  null
], profiles: [{url: '/2.2/networks/1/profiles/2', paused: true}]};
const namesOf = values => Array.from(values, client => client.name);
assert.deepStrictEqual(namesOf(Model.filterClients(clientJourney, '', {connection: 'connected'})), ['alpha', 'Zulu']);
assert.strictEqual(Model.filterClients(clientJourney, 'TABLET', {connection: 'connected'}).length, 0);
assert.deepStrictEqual(namesOf(Model.filterClients(clientJourney, 'TABLET', {connection: 'all'})), ['Offline Tablet']);
assert.deepStrictEqual(namesOf(Model.filterClients(clientJourney, '192.0.2.12', {connection: 'offline'})), ['Offline Tablet']);
assert.deepStrictEqual(namesOf(Model.filterClients(clientJourney, 'AA:BB:CC:DD:EE:01', {connection: 'connected', paused: true, guest: true, node: 'Study', profile: 'Kids'})), ['Zulu']);
assert.strictEqual(Model.filterClients(clientJourney, '', {connection: 'all', paused: true, guest: true, node: 'Other', profile: 'Kids'}).length, 0);
const journeyResults = Model.filterClients(clientJourney, '', {connection: 'all', paused: true, guest: true, node: 'Study', profile: 'Kids'});
assert.strictEqual(Model.summarize({clients: journeyResults}).connectedClients, 1);
assert.strictEqual(Model.summarize({clients: journeyResults}).totalClients, 2);
assert.deepStrictEqual(namesOf(Model.filterClients(clientJourney, '', {connection: 'connected', profile: 'Adults', node: 'Study'})), ['alpha']);
assert.strictEqual(Model.filterClients(clientJourney, '', {connection: 'connected', profile: 'Adults', paused: true}).length, 0);
assert.strictEqual(Model.filterClients(clientJourney, '', {connection: 'all'}).length, 4);
assert.strictEqual(Model.filterClients(null, '', {connection: 'all'}).length, 0);
const pausedConnectedDetail = Model.clientByMac(clientJourney, 'AA:BB:CC:DD:EE:01');
assert.strictEqual(pausedConnectedDetail.name, 'Zulu');
assert.strictEqual(pausedConnectedDetail.ip, '192.0.2.10');
assert.strictEqual(pausedConnectedDetail.nodeName, 'Study');
assert.strictEqual(pausedConnectedDetail.profileName, 'Kids');
assert.strictEqual(pausedConnectedDetail.connected, true);
assert.strictEqual(pausedConnectedDetail.paused, true, 'Pause must not imply disconnected');
assert.strictEqual(Model.clientByMac(clientJourney, null), null);
assert.strictEqual(Model.clientByMac(clientJourney, 'aa:bb:cc:dd:ee:99'), null);
for (const value of [null, undefined, '', '   ', 42]) assert.strictEqual(Model.displayValue(value), 'Unknown');
const longClientName = 'Synthetic Client '.repeat(40);
assert.strictEqual(Model.displayValue(longClientName), longClientName);
assert.strictEqual(Model.booleanLabel(null, 'Connected', 'Offline'), 'Unknown');
assert.strictEqual(Model.booleanLabel(false, 'Connected', 'Offline'), 'Offline');
assert.strictEqual(Model.booleanLabel(true, 'Connected', 'Offline'), 'Connected');
const hundredClients = {clients: Array.from({length: 120}, (_, index) => ({name: 'Client ' + index, connected: index < 75}))};
assert.strictEqual(Model.filterClients(hundredClients, '', {connection: 'connected'}).length, 75);
assert.strictEqual(Model.filterClients(hundredClients, '', {connection: 'all'}).length, 120);

console.log('eero_model.test.cjs: all checks passed');
