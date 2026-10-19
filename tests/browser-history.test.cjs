// Saved outputs: the source column and filter use only what the audit recorded.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const web = path.join(__dirname, '../src/mcdxkit/web');
function element(tag) {
  return {
    // A select starts on its first option, which is "All statuses" here.
    tag, hidden: false, value: tag === 'select' ? 'all' : '', textContent: '', className: '', dataset: {}, children: [], listeners: {},
    classList: {add() {}, remove() {}, toggle() {}},
    addEventListener(type, callback) { this.listeners[type] = callback; },
    setAttribute() {}, removeAttribute() {},
    append(...children) { this.children.push(...children); },
    replaceChildren(...children) { this.children = children; },
    querySelector() { return null; }, contains() { return false; }, closest() { return {}; }, close() {}, show() {}, focus() {},
  };
}
function text(node) { return [node.textContent, ...(node.children || []).map(text)].join(' '); }

async function openHistory(rows) {
  const html = fs.readFileSync(path.join(web, 'index.html'), 'utf8');
  const nodes = new Map([...html.matchAll(/id="([^"]+)"/g)].map(match => [match[1], element('div')]));
  const context = vm.createContext({
    document: {
      getElementById: id => nodes.get(id), querySelectorAll: () => [], addEventListener() {},
      createElement: element, createTextNode: value => ({textContent: value}), body: element('body'), activeElement: null,
    },
    window: {matchMedia: () => ({matches: false})},
    setTimeout, clearTimeout, URL, URLSearchParams,
    fetch: () => Promise.resolve({status: 401}),
  });
  vm.runInContext(fs.readFileSync(path.join(web, 'app.js'), 'utf8'), context);
  context.historyRows = rows;
  vm.runInContext('api = async () => historyRows; resultCard = () => document.createElement("article");', context);
  await context.history();
  const [controls, count, list] = nodes.get('inspector-content').children;
  const search = controls.children[0].children[1];
  return {count, list, filter(value) { search.value = value; search.listeners.input(); }};
}

const row = (source, name) => ({source, worksheet: {id: name, name}, created: 1, cases: [1], status: 'calculated'});

test('an unrecorded source is shown as not recorded, never as the worksheet name', async () => {
  const view = await openHistory([row(null, 'loads.mcdx'), row('pier.gp11t', 'pier.mcdx')]);
  const shown = view.list.children.map(text).join('\n');
  assert.match(shown, /Source not recorded/);
  assert.doesNotMatch(shown, /loads\.mcdx/);
});

test('the source filter matches recorded report names only', async () => {
  const view = await openHistory([row(null, 'loads.mcdx'), row('pier.gp11t', 'pier.mcdx')]);
  view.filter('  PIER ');
  assert.equal(view.count.textContent, '1 of 2 saved outputs');
  view.filter('loads');
  assert.equal(view.count.textContent, '0 of 2 saved outputs');
  view.filter('not recorded');
  assert.equal(view.count.textContent, '0 of 2 saved outputs');
  view.filter('');
  assert.equal(view.count.textContent, '2 of 2 saved outputs');
});

test('overrides an audit did not record are not shown as "No overrides"', async () => {
  const view = await openHistory([{...row('a.gp11t', 'a.mcdx'), overrides: null},
    {...row('b.gp11t', 'b.mcdx'), overrides: {}}, {...row('c.gp11t', 'c.mcdx'), overrides: {n_z: 9}}]);
  const [missing, none, some] = view.list.children.map(text);
  assert.match(missing, /Overrides not recorded/);
  assert.match(none, /No overrides/);
  assert.match(some, /Overrides n_z=9/);
});
