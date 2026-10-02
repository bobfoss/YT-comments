(() => {
  'use strict';
  const api = window.YTLibraryBrowserPlugins;
  if (!api || api.apiVersion !== 2) return;
  let preferenceWrites = Promise.resolve();
  let preferenceRevision = 0;
  const element = (tag, className, text = '') => {
    const node = document.createElement(tag);
    node.className = className;
    node.textContent = text;
    return node;
  };
  const sourceUrl = (video, comment) => `https://www.youtube.com/watch?v=${encodeURIComponent(video)}&lc=${encodeURIComponent(comment)}`;

  function highlighted(node, text, query) {
    const terms = String(query || '').trim().split(/\s+/).filter(Boolean);
    if (!terms.length) { node.textContent = text; return; }
    const pattern = new RegExp(`(${terms.map(t => t.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|')})`, 'gi');
    for (const part of String(text || '').split(pattern)) {
      node.append(terms.some(t => t.toLowerCase() === part.toLowerCase())
        ? element('mark', '', part) : document.createTextNode(part));
    }
  }

  async function profiles(rows, host) {
    const ids = [...new Set(rows.map(row => row.author_id).filter(Boolean))];
    return host.libraryChannels(ids).catch(() => new Map());
  }

  function commentRow(row, videoId, known, query, host) {
    const block = element('div', 'ytc-comment');
    block.dataset.commentId = row.comment_id;
    const profile = known.get(row.author_id);
    const name = row.author_name || row.author_id;
    const avatar = element('span', 'ytc-avatar', (name.replace(/^@/, '')[0] || '?').toUpperCase());
    if (profile?.thumbnail_path) {
      const image = document.createElement('img');
      image.src = `/${profile.thumbnail_path.replace(/^\/+/, '')}`;
      image.alt = '';
      image.loading = 'lazy';
      avatar.replaceChildren(image);
    }
    const content = element('div', 'ytc-content');
    const header = element('div', 'ytc-author-line');
    const author = element('a', 'ytc-author', name);
    author.href = `https://www.youtube.com/channel/${encodeURIComponent(row.author_id)}`;
    author.target = '_blank'; author.rel = 'noreferrer';
    const timestamp = element('a', 'ytc-time', row.posted_at
      ? host.ui.formatTime(row.posted_at) : row.published_label || 'Date unknown');
    timestamp.href = sourceUrl(videoId, row.comment_id);
    timestamp.target = '_blank'; timestamp.rel = 'noreferrer';
    timestamp.title = `Open comment on YouTube · Last captured ${host.ui.formatTime(row.last_seen)}`;
    header.append(author, timestamp);
    const text = element('div', 'ytc-text');
    highlighted(text, row.text, query);
    const likes = row.like_count === null ? row.like_label || 'Unknown' : Number(row.like_count).toLocaleString();
    content.append(header, text, element('div', 'details ytc-likes', `👍 ${likes}`));
    block.append(avatar, content);
    return block;
  }

  function sortControl(sort, host, refresh) {
    const label = element('label', 'ytc-sort details', 'Comment order ');
    const select = document.createElement('select');
    select.setAttribute('aria-label', 'Comment order');
    for (const [value, text] of [['newest', 'Newest participation'], ['oldest', 'Oldest participation'], ['likes', 'Most liked participation']]) {
      const option = element('option', '', text); option.value = value; select.append(option);
    }
    select.value = sort;
    const status = element('span', 'ytc-save-status');
    select.addEventListener('change', () => {
      const value = select.value;
      const revision = ++preferenceRevision;
      preferenceWrites = preferenceWrites.catch(() => {}).then(() => host.postJson('preferences', { sort: value }));
      preferenceWrites.then(async () => {
        if (revision === preferenceRevision) await refresh();
      }).catch(error => { if (revision === preferenceRevision) status.textContent = error.message; });
    });
    label.append(select, status);
    return label;
  }

  function threadCard(item, host, known = new Map()) {
    const card = element('article', 'card ytc-card');
    card.dataset.threadId = item.thread_id;
    const body = element('div', 'body');
    body.append(element('div', 'details', 'Comments'));
    if (item.showSort) body.append(sortControl(item.sort, host, () => host.refreshSearch()));
    const title = element('a', 'video-title', item.title || item.video_id);
    title.href = host.ui.localVideoHref(item.video_id);
    body.append(title);
    if (item.root_comment) body.append(commentRow(item.root_comment, item.video_id, known, item.query, host));
    const ownReplies = element('div', 'ytc-participant-preview');
    for (const row of item.own_comments || []) {
      if (row.comment_id !== item.thread_id) ownReplies.append(commentRow(row, item.video_id, known, item.query, host));
    }
    body.append(ownReplies);
    if (item.match && !item.match.is_current_user && item.match.text !== item.root_comment?.text) {
      const match = element('div', 'ytc-match');
      match.append(element('div', 'details', `Matching reply · ${item.match.author_name}`));
      const text = element('div', 'ytc-text'); highlighted(text, item.match.text, item.query); match.append(text); body.append(match);
    }
    const replies = element('details', 'ytc-replies');
    const captured = item.captured_replies || 0;
    const reported = item.reply_count;
    const count = reported !== null && reported !== undefined && captured < reported ? `${captured} of ${reported}` : String(captured);
    replies.append(element('summary', '', `${count} ${captured === 1 && String(count) === '1' ? 'reply' : 'replies'}`));
    const content = element('div', 'ytc-reply-list'); replies.append(content);
    let loaded = false; let loading = false;
    replies.addEventListener('toggle', async () => {
      ownReplies.hidden = replies.open;
      if (!replies.open || loaded || loading) return;
      loading = true;
      content.textContent = 'Loading replies…';
      try {
        const full = await host.requestJson(`threads/${encodeURIComponent(item.thread_id)}`);
        const authors = await profiles(full.comments, host);
        content.replaceChildren(...full.comments.filter(row => row.comment_id !== item.thread_id)
          .map(row => commentRow(row, item.video_id, authors, item.query, host)));
        loaded = true;
      } catch (error) { content.textContent = error.message; }
      finally { loading = false; }
    });
    if (captured || reported) body.append(replies);
    if (item.status !== 'complete') body.append(element('div', 'details ytc-capture-state',
      item.status === 'partial' ? 'Partially captured conversation' : 'Latest check unavailable · saved comments retained'));
    card.append(body);
    return card;
  }

  async function fetchResults({ query, limit, offset }, host, videoId = '') {
    const result = await host.requestJson('search', { q: query, limit, offset, video_id: videoId });
    const rows = result.results.flatMap(item => [...item.own_comments, ...(item.root_comment ? [item.root_comment] : [])]);
    const known = await profiles(rows, host);
    for (const [index, item] of result.results.entries()) {
      item.known = known; item.sort = result.sort; item.showSort = index === 0;
    }
    return result;
  }

  async function videoPanel(videoId, host) {
    const panel = element('section', 'ytc-panel');
    panel.append(element('h3', '', 'Comments'));
    const input = document.createElement('input'); input.type = 'search'; input.placeholder = 'Search comments'; input.setAttribute('aria-label', 'Search comments');
    const controls = element('div', 'ytc-controls'); controls.append(input); panel.append(controls);
    const rows = element('div', 'ytc-thread-list'); panel.append(rows);
    const message = element('div', 'details'); panel.append(message);
    const more = element('button', '', 'Load more threads'); more.type = 'button'; more.hidden = true; panel.append(more);
    let generation = 0; let offset = 0; let total = 0; let busy = false; let timer;
    async function load(reset = true) {
      const revision = ++generation;
      if (reset) offset = 0;
      busy = true;
      try {
        const result = await fetchResults({query: input.value, limit: 20, offset}, host, videoId);
        if (revision !== generation) return;
        if (reset) rows.replaceChildren();
        for (const item of result.results) { item.showSort = false; rows.append(threadCard(item, host, item.known)); }
        offset += result.results.length; total = result.total;
        message.textContent = total ? `${total} participating ${total === 1 ? 'thread' : 'threads'}` : 'No captured participating threads.';
        more.hidden = offset >= total;
      } catch (error) { if (revision === generation) message.textContent = error.message; }
      finally { if (revision === generation) busy = false; }
    }
    const pref = await host.requestJson('preferences');
    controls.append(sortControl(pref.sort, host, () => load()));
    input.addEventListener('input', () => { ++generation; clearTimeout(timer); timer = setTimeout(() => load(), 180); });
    more.addEventListener('click', () => { if (!busy) load(false); });
    await load();
    return panel;
  }

  api.register({
    id: 'comments',
    search: {
      capability: 'comment_search', label: 'Comments', preferenceKey: 'plugins.comments.search', fetchEmptyQuery: true,
      searchField: {key: 'comments', label: 'Comments', defaultEnabled: true, appliesToKinds: ['comments']},
      catalogCount: status => Number(status?.pluginStatus?.threads || 0),
      fetch: fetchResults,
      renderResult: (item, host) => threadCard(item, host, item.known),
    },
    videoDetail: {capability: 'comment_threads', render: videoPanel},
    entityCards: {
      capability: 'comment_presence', kinds: ['video'],
      prepare: async (entities, host) => (await host.requestJson('videos', {id: entities.map(entity => entity.id)})).videos,
      render: (entity, counts) => {
        if (!counts?.[entity.id]) return null;
        const badge = element('span', 'ytc-decorator');
        badge.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><path d="M4 4h16v12H9l-5 4V4Z"/><path d="M8 8h8M8 12h6"/></svg>';
        badge.append(document.createTextNode(`Comments · ${counts[entity.id]} ${counts[entity.id] === 1 ? 'thread' : 'threads'}`));
        return {primaryMetadata: [badge]};
      },
    },
  });
})();
