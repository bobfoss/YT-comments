const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../../yt_comments/browser.js'), 'utf8');

class Element {
  constructor(tag) { this.tagName = tag; this.children = []; this.dataset = {}; this.events = {}; this.text = ''; }
  set textContent(value) { this.text = value; this.children = []; }
  get textContent() { return this.text + this.children.map(child => child.textContent).join(''); }
  append(...children) { this.children.push(...children); }
  addEventListener(name, listener) { this.events[name] = listener; }
  setAttribute(name, value) { this[name] = value; }
}

function setup() {
  let now = Date.parse('2026-10-02T07:51:54Z');
  let plugin;
  const cards = [];
  const intervals = [];
  const walk = node => [node, ...node.children.flatMap(walk)];
  const document = {
    hidden: false,
    events: {},
    createElement: tag => new Element(tag),
    createTextNode: text => Object.assign(new Element('text'), {textContent: text}),
    addEventListener(name, callback) { this.events[name] = callback; },
    querySelectorAll: () => cards.flatMap(walk).filter(node => node.dataset.estimatedPostedAt),
  };
  class Clock extends Date {
    constructor(...args) { super(...(args.length ? args : [now])); }
    static now() { return now; }
  }
  vm.runInNewContext(source, {
    window: {YTLibraryBrowserPlugins: {apiVersion: 2, register: value => { plugin = value; }}},
    document, Date: Clock, Intl,
    setInterval: (callback, delay) => intervals.push({callback, delay}),
  });
  const host = {ui: {formatTime: value => `Exact ${value}`, localVideoHref: id => `/videos/${id}`}};
  return {
    document, intervals, plugin,
    advance: value => { now = Date.parse(value); },
    render(overrides = {}) {
      const row = {comment_id: 'root', author_id: 'channel', author_name: '@alias', text: 'Comment',
        posted_at: null, published_label: '5 months ago', estimated_posted_at: '2026-05-02T07:51:54Z',
        last_seen: '2026-10-02T07:51:54Z', is_edited: false, like_count: 1, ...overrides};
      const card = plugin.search.renderResult({thread_id: 'root', video_id: 'video', root_comment: row,
        own_comments: [], status: 'complete'}, host);
      cards.push(card);
      return walk(card).find(node => node.className === 'ytc-time');
    },
  };
}

test('relative age advances without fetching or rerendering and retains edited marker', () => {
  const app = setup();
  const timestamp = app.render({is_edited: true, published_label: '5 months ago (edited)'});
  assert.equal(timestamp.textContent, '5 months ago (edited)');
  assert.match(timestamp.title, /YouTube label: 5 months ago \(edited\)/);
  assert.match(timestamp.title, /Approximate age/);
  assert.equal(app.intervals[0].delay, 60000);
  app.advance('2026-11-02T07:51:54Z');
  app.intervals[0].callback();
  assert.equal(timestamp.textContent, '6 months ago (edited)');
  app.advance('2027-05-02T07:51:54Z');
  app.document.events.visibilitychange();
  assert.equal(timestamp.textContent, '1 year ago (edited)');
});

test('comment search uses host sorting and cards contain no separate sort control', () => {
  const app = setup();
  assert.equal(app.plugin.search.serverResults, true);
  assert.equal(app.plugin.search.fetchEmptyQuery, undefined);
  assert.equal(app.plugin.search.fetch, undefined);
  assert.equal(app.plugin.search.sortOptions[0].value, 'most_liked');
  assert.equal(typeof app.plugin.search.prepareResults, 'function');
  assert.doesNotMatch(source, /Comment order|sortControl|showSort/);
});

test('collection requests its dedicated endpoint with blank queries and shared sort', async () => {
  const app = setup();
  const requests = [];
  await app.plugin.collection.fetch({query: '', limit: 50, offset: 100, sort: 'oldest'}, {
    requestJson: (endpoint, params) => { requests.push({endpoint, params}); return Promise.resolve({results: []}); },
  });
  assert.equal(requests[0].endpoint, 'collection');
  assert.equal(requests[0].params.q, '');
  assert.equal(requests[0].params.sort, 'oldest');
  assert.equal(requests[0].params.offset, 100);
  assert.deepEqual(Array.from(app.plugin.collection.sorts), ['newest', 'oldest', 'most_liked']);
});

test('exact dates retain edits and are not modified by the relative timer', () => {
  const app = setup();
  const timestamp = app.render({posted_at: '2026-04-08T18:58:06Z', estimated_posted_at: null,
    published_label: '5 months ago (edited)', is_edited: true});
  app.advance('2027-10-02T07:51:54Z');
  app.intervals[0].callback();
  assert.equal(timestamp.textContent, 'Exact 2026-04-08T18:58:06Z (edited)');
});

test('short months, day boundaries, and future clock skew keep meaningful relative labels', () => {
  const app = setup();
  app.advance('2026-03-01T07:51:54Z');
  assert.equal(app.render({estimated_posted_at: '2026-02-01T07:51:54Z'}).textContent, '1 month ago');
  assert.equal(app.render({estimated_posted_at: '2026-02-28T07:51:54Z'}).textContent, '1 day ago');
  app.advance('2026-10-02T07:51:54Z');
  assert.equal(app.render({estimated_posted_at: '2026-10-02T07:49:54Z'}).textContent, '2 minutes ago');
  assert.equal(app.render({estimated_posted_at: '2026-10-03T07:51:54Z'}).textContent, 'just now');
});

test('unsupported labels preserve their evidence and capture date without duplicating edits', () => {
  const app = setup();
  const timestamp = app.render({estimated_posted_at: null, published_label: 'recently (edited)', is_edited: true});
  assert.equal(timestamp.textContent, 'recently (as of Exact 2026-10-02T07:51:54Z) (edited)');
});
