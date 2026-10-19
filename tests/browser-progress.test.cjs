// Exercise the real browser orchestration with controlled HTTP responses.
// DOM stubs isolate timing and errors; actual rendering is checked in a browser.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const web = path.join(__dirname, '../src/mcdxkit/web');
function element() {
  return {
    hidden: false, value: '', textContent: '', dataset: {}, children: [], attributes: {}, listeners: {},
    classList: {add() {}, remove() {}, toggle() {}},
    addEventListener(type, callback) { this.listeners[type] = callback; },
    setAttribute(key, value) { this.attributes[key] = String(value); },
    removeAttribute(key) { delete this.attributes[key]; if (key === 'value') delete this.value; },
    append(...children) { this.children.push(...children); },
    replaceChildren(...children) { this.children = children; },
    querySelector() { return null; }, contains() { return false; }, closest() { return {}; }, close() {}, show() {}, focus() {},
  };
}

async function browser(action, count) {
  const html = fs.readFileSync(path.join(web, 'index.html'), 'utf8');
  const nodes = new Map([...html.matchAll(/id="([^"]+)"/g)].map(match => [match[1], element()]));
  nodes.get('basis').value = 'effects';
  const requests = [];
  const context = vm.createContext({
    document: {
      getElementById: id => nodes.get(id), querySelectorAll: () => [], addEventListener() {},
      createElement: element, createTextNode: text => ({textContent: text}), body: element(),
    },
    window: {matchMedia: () => ({matches:false})},
    setTimeout, clearTimeout, URL, URLSearchParams,
    fetch(url, options) {
      if (url === '/api/session') return Promise.resolve({status: 401});
      return new Promise(resolve => requests.push({url, options, resolve}));
    },
  });
  vm.runInContext(fs.readFileSync(path.join(web, 'app.js'), 'utf8'), context);
  await Promise.resolve();
  vm.runInContext(`
    session = {token:'synthetic'}; hasTemplate = true;
    inspect = async () => {}; inspectDiff = async () => {};
    for (let n = 1; n <= ${count}; n++) items.push({
      id:'r'+n, name:'report-'+n+'.gp11t', selected:[1],
      inspection:{cases:[{id:1,name:'STR test'}], envelope:{P:10},template_validation:{status:'ready'}},
      preview:${action === 'convert' ? '{worksheet:{id:"preview"},diff:{changes:[]}}' : 'null'}
    });
  `, context);
  const finished = nodes.get(action === 'convert' ? 'convert-all' : 'compare-next').listeners.click();
  async function respond(index, ok, message = 'Synthetic calculation failure') {
    assert.ok(requests[index], 'Expected the next request');
    requests[index].resolve({ok, json: async () => ok
      ? {worksheet:{id:'out'+index}, diff:{changes:[]}, calculation:{calculated:true}}
      : {error:message}});
    await new Promise(resolve => setImmediate(resolve));
  }
  return {nodes, requests, respond, finished, context};
}

test('summary-only cases open for selection without claiming automatic STR selection', async () => {
  const app = await browser('preview', 0);
  await app.finished;
  vm.runInContext(`
    items.push({id:'summary', name:'summary.txt', selected:[], kind:'report',
      inspection:{summary_only:true, cases:[{id:1,name:null},{id:7,name:null}],
        selection_required:'Case names absent', envelope:null}});
    render();
  `, app.context);
  const card = app.nodes.get('queue').children[0];
  const details = card.children.find(child => child.children?.some(c => c.textContent === '0 of 2 load cases selected'));
  assert.ok(details, 'The parsed cases must be available for review');
  assert.equal(details.open, true);
  assert.match(details.children[1].textContent, /summary.only/i);
  assert.doesNotMatch(details.children[1].textContent, /STR cases selected/);
  assert.equal(app.nodes.get('compare-next').disabled, true);
  const checkboxes = details.children[2].children.map(label => label.children[0]);
  assert.ok(checkboxes.every(input => input.checked === false));
  assert.equal(app.requests.length, 0, 'No automatic inspection or conversion with guessed cases');
});

test('template mapping changes invalidate previews and switching templates clears old mappings', async () => {
  const app = await browser('preview', 0);
  await app.finished;
  let loading = vm.runInContext('loadTemplateInputs()', app.context);
  app.requests[0].resolve({ok:true,json:async()=>({name:'custom.mcdx',mapping_required:true,input_map:{},
    inputs:[{variable:'AxialCustom',value:10,unit:'kip',status:'retained'}]})});
  await loading;
  vm.runInContext(`items.push({id:'a',selected:[1],name:'a.txt',inspection:{cases:[{id:1}],envelope:{P:120}},preview:{},result:{}})`,app.context);
  const select=app.nodes.get('template-input-fields').children[0].children[2];
  select.value='P';const refreshed=select.listeners.change();
  assert.equal(app.nodes.get('compare-next').disabled,true);
  app.requests[1].resolve({ok:true,json:async()=>({cases:[{id:1}],envelope:{P:120},template_validation:{status:'ready'}})});
  await refreshed;
  assert.equal(app.nodes.get('template-status').textContent,'1 input mapped');
  assert.equal(vm.runInContext('items[0].preview',app.context),null);
  assert.equal(vm.runInContext('items[0].result',app.context),null);
  assert.equal(vm.runInContext('JSON.stringify(options(items[0]).input_map)',app.context),'\{"AxialCustom":"P"\}');
  loading=vm.runInContext('loadTemplateInputs()',app.context);
  app.requests[2].resolve({ok:true,json:async()=>({name:'other.mcdx',mapping_required:true,input_map:{},
    inputs:[{variable:'OtherLoad',value:5,unit:'kip',status:'retained'}]})});
  await loading;
  assert.equal(vm.runInContext('JSON.stringify(inputMap)',app.context),'{}');
  assert.equal(app.nodes.get('template-input-fields').children.length,1);
  assert.equal(app.nodes.get('template-input-fields').children[0].children[0].textContent,'OtherLoad');
});

test('check inputs validates the template/report pair and blocks incompatible files before preview', async () => {
  const app=await browser('preview',0);await app.finished;
  vm.runInContext(`items.push({id:'raw',name:'raw.txt',selected:[1],inspection:{cases:[{id:1}],envelope:{P:120}}});render();`,app.context);
  assert.equal(app.nodes.get('compare-next').disabled,true);
  const checking=app.nodes.get('review-next').listeners.click();
  assert.equal(app.requests[0].url,'/api/inspect');
  assert.equal(JSON.parse(app.requests[0].options.body).template_id,null);
  app.requests[0].resolve({ok:true,json:async()=>({cases:[{id:1}],envelope:{P:120},template_validation:{status:'blocked',errors:['Pile 3 exceeds template capacity 1']}})});
  await checking;
  assert.equal(app.nodes.get('compare-next').disabled,true);
  assert.equal(app.requests.length,1,'No preview or conversion for an incompatible pair');
});

for (const action of ['preview', 'convert']) {
  test(`${action}: single-file progress stays indeterminate until the response`, async () => {
    const app = await browser(action, 1);
    const panel = app.nodes.get('operation-progress');
    assert.ok(panel, 'A browser progress region must exist');
    assert.equal(panel.hidden, false);
    assert.equal(panel.dataset.running, 'true');
    assert.equal(app.nodes.get('operation-bar').attributes.value, undefined);
    assert.match(app.nodes.get('operation-current').textContent, /report-1/);
    assert.match(app.nodes.get('operation-count').textContent, /0 of 1/);
    assert.equal(app.requests.length, 1);
    assert.deepEqual(Object.keys(JSON.parse(app.requests[0].options.body)).sort(),
      ['cases','id','load_source','overrides','template_id']);
    await app.respond(0, true);
    await app.finished;
    assert.equal(panel.dataset.running, 'false');
    assert.equal(app.nodes.get('operation-bar').attributes.value, '1');
    assert.match(app.nodes.get('operation-current').textContent, /1 ready/);
  });
}

test('batch progress counts finished files and reports partial failures', async () => {
  const app = await browser('convert', 2);
  assert.ok(app.nodes.get('operation-progress'), 'A browser progress region must exist');
  assert.equal(app.nodes.get('operation-bar').attributes.value, '0');
  await app.respond(0, false);
  assert.equal(app.nodes.get('operation-bar').attributes.value, '1');
  assert.match(app.nodes.get('operation-count').textContent, /1 of 2/);
  assert.match(app.nodes.get('operation-current').textContent, /report-2/);
  await app.respond(1, true);
  await app.finished;
  assert.equal(app.nodes.get('operation-bar').attributes.value, '2');
  assert.equal(app.nodes.get('operation-progress').dataset.running, 'false');
  assert.match(app.nodes.get('operation-current').textContent, /1 ready.*1 failed/);
});

test('retry resets progress and clears a previous file failure', async () => {
  const app = await browser('convert', 1);
  await app.respond(0, false);
  await app.finished;
  assert.ok(app.nodes.get('operation-progress'), 'A browser progress region must exist');
  assert.match(app.nodes.get('operation-current').textContent, /0 ready.*1 failed/);
  const retry = app.nodes.get('convert-all').listeners.click();
  assert.equal(app.nodes.get('operation-progress').dataset.running, 'true');
  assert.equal(app.nodes.get('operation-bar').attributes.value, undefined);
  assert.match(app.nodes.get('operation-count').textContent, /0 of 1/);
  await app.respond(1, true);
  await retry;
  assert.match(app.nodes.get('operation-current').textContent, /^1 ready$/);
  assert.doesNotMatch(app.nodes.get('notice').textContent, /failed/);
});


test('rejected template overrides return to Inputs and stop the activity indicator', async () => {
  const app = await browser('preview', 1);
  await app.respond(0, false, 'Unsupported override: synthetic');
  await app.finished;
  assert.equal(app.nodes.get('operation-progress').dataset.running, 'false');
  assert.equal(app.nodes.get('step-2').hidden, false);
  assert.equal(app.nodes.get('overrides').attributes['aria-invalid'], 'true');
  assert.match(app.nodes.get('notice').textContent, /Fix the template input overrides/);
});
