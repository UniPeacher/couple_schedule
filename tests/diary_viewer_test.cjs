const assert = require('node:assert/strict');
const {test} = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '..', 'app.js'), 'utf8');
function section(start, end) {
  return source.slice(source.indexOf(start), source.indexOf(end, source.indexOf(start)));
}
function element() {
  const attrs = {};
  const classes = new Set();
  return {
    style: {}, innerHTML: '', textContent: '', complete: false, naturalWidth: 0,
    classList: {contains: x => classes.has(x), add: x => classes.add(x), remove: x => classes.delete(x)},
    setAttribute(k, v) { attrs[k] = v; },
    getAttribute(k) { return attrs[k] ?? null; },
    removeAttribute(k) { delete attrs[k]; if (k === 'src') this.src = ''; },
    load() {}, pause() {}, play() { return Promise.resolve(); },
  };
}
function fixture() {
  const elements = Object.fromEntries(['ovDiaryDay', 'ovDiaryDayBox', 'ovPhotoViewer', 'pvVideo',
    'pvImage', 'pvLiveBtn', 'pvIndicator', 'pvBtnPrev', 'pvBtnNext', 'pvSaveBtn'].map(x => ['#' + x, element()]));
  elements['#ovPhotoViewer'].style.display = 'flex';
  const pending = [];
  const context = vm.createContext({
    $: x => elements[x],
    document: {querySelectorAll: () => Object.values(elements).filter(x => x.classList.contains('show'))},
    history: {state: null, back() {}}, syncPageScrollLock() {},
    activeDiaryDate: '', DIARY_DAY_CACHE: {},
    fmtDateCN: x => x, esc: x => x, diaryDayHTML: (date, list) => date + ':' + JSON.stringify(list),
    api: url => new Promise((resolve, reject) => pending.push({url, resolve, reject})),
    pvPhotosList: [], pvCurrentIndex: 0, resetPvTransform() {},
  });
  vm.runInContext(section('function closeAllModals(', 'function renderTopbarHTML('), context);
  vm.runInContext(section('var pvPlayback =', 'function savePhotoUrlToDevice('), context);
  vm.runInContext(section('function photoItemSrc(', 'var curSummaryMonth ='), context);
  vm.runInContext(section('function paintDiaryDay(', 'var EVENT_DETAIL_CACHE ='), context);
  context.openModal = id => elements[id].classList.add('show');
  return {context, elements, pending};
}
const flush = () => new Promise(resolve => setImmediate(resolve));

test('first diary open shows loading immediately and renders its response', async () => {
  const {context: c, elements: e, pending: p} = fixture();
  c.openDayDiaries('2026-10-01');
  assert(e['#ovDiaryDay'].classList.contains('show'));
  assert.match(e['#ovDiaryDayBox'].innerHTML, /正在翻开/);
  p[0].resolve({diaries: [{title: 'sample'}]});
  await flush();
  assert.match(e['#ovDiaryDayBox'].innerHTML, /sample/);
  assert.equal(e['#ovDiaryDayBox'].getAttribute('aria-busy'), 'false');
});

test('out-of-order diary responses do not replace the active date', async () => {
  const {context: c, elements: e, pending: p} = fixture();
  c.openDayDiaries('2026-10-01');
  c.openDayDiaries('2026-10-02');
  p[1].resolve({diaries: [{title: 'new date'}]});
  await flush();
  p[0].resolve({diaries: [{title: 'old date'}]});
  await flush();
  assert.match(e['#ovDiaryDayBox'].innerHTML, /new date/);
  assert.doesNotMatch(e['#ovDiaryDayBox'].innerHTML, /old date/);
});

test('closing and reopening the same date invalidates its old response', async () => {
  const {context: c, elements: e, pending: p} = fixture();
  c.openDayDiaries('2026-10-01');
  c.closeAllModals(true);
  p[0].resolve({diaries: [{title: 'stale'}]});
  await flush();
  assert(!e['#ovDiaryDay'].classList.contains('show'));
  assert.equal(c.DIARY_DAY_CACHE['2026-10-01'], undefined);
  c.openDayDiaries('2026-10-01');
  p[1].resolve({diaries: [{title: 'fresh'}]});
  await flush();
  assert.match(e['#ovDiaryDayBox'].innerHTML, /fresh/);
});

test('cached diary renders synchronously and refreshes without reopening', async () => {
  const {context: c, elements: e, pending: p} = fixture();
  c.DIARY_DAY_CACHE['2026-10-01'] = [{title: 'cached'}];
  c.openDayDiaries('2026-10-01');
  assert.match(e['#ovDiaryDayBox'].innerHTML, /cached/);
  p[0].resolve({diaries: [{title: 'updated'}]});
  await flush();
  assert.match(e['#ovDiaryDayBox'].innerHTML, /updated/);
});

test('failed first request offers retry and retains a close button', async () => {
  const {context: c, elements: e, pending: p} = fixture();
  c.openDayDiaries('2026-10-01');
  p[0].reject(new Error('offline'));
  await flush();
  assert.match(e['#ovDiaryDayBox'].innerHTML, /重试/);
  assert.match(e['#ovDiaryDayBox'].innerHTML, /data-act="close"/);
});

test('viewer uses a derived image, starts video immediately, and saves the original', () => {
  const {context: c, elements: e} = fixture();
  let plays = 0;
  e['#pvVideo'].play = () => { plays++; return Promise.resolve(); };
  c.pvPhotosList = [{src: '/photos/original.jpg', view: '/photos/vw_original.jpg', live: '/photos/live.mp4'}];
  c.updatePhotoViewer();
  assert.equal(e['#pvImage'].src, '/photos/vw_original.jpg');
  assert.equal(c.photoItemSrc(c.pvPhotosList[0]), '/photos/original.jpg');
  assert.equal(plays, 1);
  assert.equal(e['#pvVideo'].src, '/photos/live.mp4');
});

test('live preview is played while export retains the source video', () => {
  const {context: c, elements: e} = fixture();
  c.pvPhotosList = [{src: 'a.jpg', live: 'original.mp4', preview: 'pv_original.mp4'}];
  c.updatePhotoViewer();
  assert.equal(e['#pvVideo'].src, 'pv_original.mp4');
  assert.equal(c.pvPhotosList[0].live, 'original.mp4');
});

test('old video callbacks cannot hide or reveal the next live photo', () => {
  const {context: c, elements: e} = fixture();
  c.pvPhotosList = [{src: 'a.jpg', live: 'a.mp4'}, {src: 'b.jpg', live: 'b.mp4'}];
  c.updatePhotoViewer();
  const oldEnded = e['#pvVideo'].onended;
  const oldPlaying = e['#pvVideo'].onplaying;
  c.pvCurrentIndex = 1;
  c.updatePhotoViewer();
  oldPlaying();
  assert.equal(e['#pvVideo'].style.opacity, '0');
  e['#pvVideo'].onplaying();
  oldEnded();
  assert.equal(e['#pvVideo'].style.opacity, '1');
  assert.equal(e['#pvVideo'].style.display, 'block');
});

test('rejected old play promise cannot stop a newer playback', async () => {
  const {context: c, elements: e} = fixture();
  let reject;
  e['#pvVideo'].play = () => new Promise((_, r) => { reject = r; });
  c.pvPhotosList = [{src: 'a.jpg', live: 'a.mp4'}, {src: 'b.jpg', live: 'b.mp4'}];
  c.updatePhotoViewer();
  const rejectOld = reject;
  c.pvCurrentIndex = 1;
  c.updatePhotoViewer();
  e['#pvVideo'].onplaying();
  rejectOld(new Error('aborted'));
  await flush();
  assert.equal(e['#pvVideo'].style.opacity, '1');
});

test('closing releases video and makes in-flight callbacks inert', async () => {
  const {context: c, elements: e} = fixture();
  c.pvPhotosList = [{src: 'a.jpg', live: 'a.mp4'}];
  c.updatePhotoViewer();
  const playing = e['#pvVideo'].onplaying;
  c.closePhotoViewer();
  playing();
  assert.equal(e['#pvVideo'].src, '');
  assert.equal(e['#pvVideo'].style.display, 'none');
  assert.equal(e['#ovPhotoViewer'].style.display, 'none');
});

test('image loading remains independent of manual video replay', () => {
  const {context: c, elements: e} = fixture();
  c.pvPhotosList = [{src: 'a.jpg', live: 'a.mp4'}];
  c.updatePhotoViewer();
  c.playPvLiveVideo();
  e['#pvImage'].onload();
  assert.equal(e['#pvImage'].style.opacity, '1');
});

test('service worker bypasses media and Range but retains static caching', () => {
  const listeners = {};
  const sw = fs.readFileSync(path.join(__dirname, '..', 'sw.js'), 'utf8');
  const sandbox = vm.createContext({URL, self: {location: {origin: 'https://example.test'},
    addEventListener: (name, fn) => { listeners[name] = fn; }},
    caches: {match: () => Promise.resolve({}), open: () => Promise.resolve({put() {}})},
    fetch: () => Promise.resolve({ok: false}),
  });
  vm.runInContext(sw, sandbox);
  for (const [url, range] of [['/photos/sample.mp4', true], ['/photos/sample.jpg', false], ['/app.js', true]]) {
    let intercepted = false;
    listeners.fetch({request: {method: 'GET', url: 'https://example.test' + url,
      headers: {has: () => range}}, respondWith() { intercepted = true; }});
    assert.equal(intercepted, false, url);
  }
  let intercepted = false;
  listeners.fetch({request: {method: 'GET', url: 'https://example.test/app.js', headers: {has: () => false}},
    respondWith() { intercepted = true; }});
  assert.equal(intercepted, true);
});
