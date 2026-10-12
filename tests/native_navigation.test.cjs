const assert = require('assert');
const fs = require('fs');
const vm = require('vm');
const path = require('path');
function load(name) {
  const context = {module: {exports: {}}};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '..', name), 'utf8').replace(/^\.pragma library\s*/m, ''), context);
  return context.module.exports;
}
const Nav = load('Navigation.js');
const Model = load('EeroModel.js');
let activated = '';
function button(key, options = {}) {
  return Object.assign({objectName: 'nav:' + key, visible: true, enabled: true, children: [], clicked() { activated = key; }}, options);
}
const tree = {visible: true, enabled: true, children: [button('client:aa'), button('client:bb'), button('disabled', {enabled: false}), button('pending', {busy: true}), button('passive', {interactive: false}), button('hidden', {visible: false})]};
let targets = Nav.targets(tree);
assert.deepStrictEqual(Array.from(targets, t => t.key), ['client:aa', 'client:bb']);
let key = Nav.move(targets, '', 1);
assert.strictEqual(key, 'client:aa');
assert.strictEqual(Nav.move(targets, key, -1), 'client:bb');
// Rebuild/reorder: retained key activates the same identity, not the old index.
tree.children = [button('client:bb'), button('client:aa')];
assert.strictEqual(Nav.activate(Nav.current(Nav.targets(tree), key)), true);
assert.strictEqual(activated, 'client:aa');
tree.children = [button('client:bb')];
assert.strictEqual(Nav.activate(Nav.current(Nav.targets(tree), key)), false);
assert.strictEqual(activated, 'client:aa');
tree.children = [button('duplicate'), button('duplicate')];
assert.strictEqual(Nav.targets(tree).length, 0);
let toggles = 0;
const toggle = {visible: true, enabled: true, interactive: true, busy: false, toggled() { toggles++; }};
assert.strictEqual(Nav.activate({item: toggle}), true);
toggle.busy = true;
assert.strictEqual(Nav.activate({item: toggle}), false);
assert.strictEqual(toggles, 1);
const anonymous = {mac: null, name: 'Unknown Client', ip: '192.0.2.1', connected: true, wireless: true, paused: false, guest: false, nodeName: 'Node', profileName: null};
const equivalent = {...anonymous};
assert.strictEqual(Model.equivalentClient({clients: [equivalent]}, anonymous), equivalent);
for (const field of Object.keys(anonymous)) {
  assert.strictEqual(Model.equivalentClient({clients: [{...anonymous, [field]: 'changed'}]}, anonymous), null, field);
}
assert.strictEqual(Model.equivalentClient({clients: []}, anonymous), null);
assert.strictEqual(Model.equivalentClient({clients: [anonymous, equivalent]}, anonymous), null);
assert.strictEqual(Model.equivalentClient(null, anonymous), null);
console.log('native navigation/model behavior OK');
