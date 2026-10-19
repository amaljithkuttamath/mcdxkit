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

async function browser(action, count, setup = '', storage = undefined) {
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
    setTimeout, clearTimeout, URL, URLSearchParams, ...(storage ? {localStorage: storage} : {}),
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
    ${setup}
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

// Review decisions are advisory: only a decision the engineer made reaches the request.
const flag = (rule, c) => `{rule:'${rule}', case:${c}, component:'P', message:'m', impact:{envelope:true, selection:true, magnitude:0.2, components:['P']}}`;
const decided = `
    const item = items[0]; item.sha = 'abc'; item.inspection.load_source = 'effects';
    const [kept, copied, later] = [${flag('service_axial_above_strength', 3)}, ${flag('duplicate_case', 4)}, ${flag('ratio_outlier', 3)}];
    item.inspection.review_checks = {flags:[kept, copied]};
    item.keepNote = '  checked the factors  ';
    decide(item, {case:3, flags:[kept]}, 'kept_service');
    // A card decision is never overwritten. A flag raised later for the same case was never shown,
    // so it reopens the card with only that flag instead of being counted as reviewed.
    globalThis.overwrote = decide(item, {case:3, flags:[kept]}, 'will_fix_in_group');
    globalThis.laterGroup = reviewGroups(item, {flags:[kept, later]}).find(g => g.case === 3);
    // An "added" decision for a case that is no longer selected does not stand.
    decide(item, {case:4, flags:[copied]}, 'included_case', 'r1');
`;
test('review decisions are sent only after the engineer decides, without local state', async () => {
  const app = await browser('preview', 1, decided);
  assert.equal(vm.runInContext('overwrote', app.context), false);
  assert.equal(vm.runInContext('laterGroup.record', app.context), null);
  assert.equal(vm.runInContext(`laterGroup.flags.map(f => f.rule).join()`, app.context), 'ratio_outlier');
  const body = JSON.parse(app.requests[0].options.body);
  assert.equal(body.review_decisions.length, 1);
  const [sent] = body.review_decisions;
  assert.deepEqual(Object.keys(sent).sort(), ['case', 'component', 'decided_at', 'decision', 'note', 'rule']);
  assert.equal(sent.decision, 'kept_service');
  assert.equal(sent.note, 'checked the factors');
  assert.match(sent.decided_at, /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/);
  await app.respond(0, true);
  await app.finished;
  // A decision is keyed by load source: on another basis it is not reused.
  vm.runInContext(`items[0].inspection.load_source = 'reactions'`, app.context);
  assert.equal(vm.runInContext(`reviewDecisions(items[0], items[0].inspection.review_checks.flags).length`, app.context), 0);
  // Checks that could not run stay visible on Changes and Outputs.
  assert.match(vm.runInContext(`reviewChip({flags:[], errors:[{id:'my_check', message:'boom'}]}).children[0].textContent`, app.context),
    /could not run; check the GROUP input yourself.*my_check: boom/);
  assert.match(vm.runInContext(`items[0].preview = {review_checks:{flags:[], errors:[{id:null, message:'view failed'}]}}; pendingView(items[0]).children[0].textContent`, app.context),
    /could not run/);
});

test('a stale review decision never blocks the preview, and outputs repeat the preview decisions', async () => {
  const app = await browser('preview', 1, decided);
  assert.ok(JSON.parse(app.requests[0].options.body).review_decisions);
  await app.respond(0, false, "Review decision does not match a raised flag: ('service_axial_above_strength', 3, 'P')");
  assert.equal(JSON.parse(app.requests[1].options.body).review_decisions, undefined);
  await app.respond(1, true);
  await app.finished;
  assert.equal(app.nodes.get('step-3').hidden, false);
  const convert = await browser('convert', 1, decided + `item.preview = {worksheet:{id:'preview'}, diff:{changes:[]}}; item.previewDecisions = reviewDecisions(item, item.inspection.review_checks.flags);`);
  assert.equal(JSON.parse(convert.requests[0].options.body).review_decisions[0].decision, 'kept_service');
  await convert.respond(0, true);
  await convert.finished;
});

// Synthetic flags shaped like the built-in checks: a unit slip only affects the envelope once its case is selected.
const svc = `{rule:'service_axial_above_strength', case:3, component:'P', message:'svc', value:500, unit:'kip', impact:{envelope:true, selection:true, magnitude:0.2, components:['P']}}`;
const slip = selected => `{rule:'gross_magnitude', case:3, component:'P', message:'slip', impact:{envelope:${selected}, selection:false, magnitude:${selected ? 9 : 0}, components:${selected ? "['P']" : '[]'}}}`;
test('adding a case never marks a flag it was not shown for as reviewed', async () => {
  const app = await browser('preview', 1, `
    const item = items[0]; item.sha = 's'; item.inspection.load_source = 'effects';
    item.inspection.review_checks = {flags:[${svc}, ${slip(false)}]};
    globalThis.shown = reviewGroups(item)[0].flags.map(f => f.rule);
    decide(item, reviewGroups(item)[0], 'included_case', item.id);
    item.selected = [1, 3]; item.inspection.review_checks = {flags:[${svc}, ${slip(true)}]};
    globalThis.after = reviewGroups(item);
    globalThis.sent = reviewDecisions(item, item.inspection.review_checks.flags);
    globalThis.chip = reviewChip({flags:item.inspection.review_checks.flags, decisions:sent}).children[0].textContent;
  `);
  await app.respond(0, true);
  await app.finished;
  const run = code => vm.runInContext(code, app.context);
  run(`items[0].preview.review_checks = {flags:items[0].inspection.review_checks.flags, decisions:sent}`);
  assert.equal(run('items[0].previewDecisions.map(d => d.rule).join()'), 'service_axial_above_strength');
  assert.equal(run('shown.join()'), 'service_axial_above_strength');
  assert.equal(run('after.length'), 1);
  assert.equal(run('after[0].record'), null);
  assert.equal(run('after[0].flags.map(f => f.rule).join()'), 'gross_magnitude');
  assert.equal(run('chip'), 'Input checks: 1 not reviewed');
  assert.match(run('pendingView(items[0]).children[0].textContent'), /^Not reviewed yet: case 3 may use different units/);
  // Deciding the new flag closes the card; Undo then clears both decisions for the case.
  run(`decide(items[0], after[0], 'kept_service')`);
  assert.equal(run('Boolean(reviewGroups(items[0])[0].record)'), true);
  assert.equal(run('reviewDecisions(items[0], items[0].inspection.review_checks.flags).length'), 2);
});

test('a decision or Undo on one copy of a report drops previews of every copy', async () => {
  const app = await browser('preview', 2, `
    for (const item of items) { item.sha = 's'; item.inspection.load_source = 'effects'; item.inspection.review_checks = {flags:[${svc}]}; }
  `);
  await app.respond(0, true);
  await app.respond(1, true);
  await app.finished;
  const run = code => vm.runInContext(code, app.context);
  // Deciding on the first copy drops the second copy's preview, which was made without the decision.
  run(`decide(items[0], reviewGroups(items[0])[0], 'kept_service')`);
  assert.equal(run('items[1].preview'), null);
  // Undo likewise: otherwise Generate outputs would record, for the other copy, a decision the engineer undid.
  run(`items[1].preview = {worksheet:{id:'p'}, diff:{changes:[]}}; items[1].previewDecisions = reviewDecisions(items[1], items[1].inspection.review_checks.flags)`);
  assert.equal(run('items[1].previewDecisions.length'), 1);
  await run(`undoDecision(items[0], reviewGroups(items[0])[0])`);
  assert.equal(run('items[1].preview'), null);
});

test('Changes lists a decision dropped by the stale-decision retry as not reviewed', async () => {
  const app = await browser('preview', 1, decided);
  await app.respond(0, false, "Review decision does not match a raised flag: ('service_axial_above_strength', 3, 'P')");
  await app.respond(1, true);
  await app.finished;
  // The retried preview recorded no decision, although the browser still remembers one for case 3.
  const run = code => vm.runInContext(code, app.context);
  run(`items[0].preview.review_checks = {flags:items[0].inspection.review_checks.flags}`);
  assert.equal(run('Boolean(reviewGroups(items[0]).find(g => g.case === 3).record)'), true);
  assert.equal(run('items[0].previewDecisions.length'), 0);
  assert.match(run('pendingView(items[0]).children[0].textContent'), /^Not reviewed yet: adding case 3/);
});

test('remembered decisions in browser storage are checked before use', async () => {
  const good = {scope:'s|effects', rule:'service_axial_above_strength', case:3, component:'P', decision:'kept_service', note:null, decided_at:'2026-10-06T09:30:00.000Z'};
  const stored = {good, empty:null, text:'x', approve:{...good, decision:'approve'}, noScope:{...good, scope:undefined}, badCase:{...good, case:'3'}};
  const app = await browser('preview', 1, `items[0].sha = 's'; items[0].inspection.load_source = 'effects';`,
    {getItem: () => JSON.stringify(stored), setItem() {}});
  // A malformed or older row would otherwise break every render of the Inputs step.
  assert.equal(vm.runInContext('[...reviewMemory.keys()].join()', app.context), 'good');
  await app.respond(0, true);
  await app.finished;
});
