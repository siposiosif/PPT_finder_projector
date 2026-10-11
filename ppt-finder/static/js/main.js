(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const page = document.body.dataset;
  const settings = {
    accessCode: page.accessCode || '',
    remoteUrl: page.remoteUrl || '',
    projectorUrl: page.projectorUrl || '/projector',
    voiceUrl: page.voiceUrl || '',
    comEnabled: page.comEnabled === 'true'
  };
  let slides = [], activeSlide = 0, searchTimer, selectedResultIndex = -1;
  let searchGeneration = 0, searchController = null, projectionBusy = false;
  let currentMode = 'none', lastRevision = -1, streamGeneration = 0, statusPending = false, slidesSignature = '';
  const systemTheme = window.matchMedia('(prefers-color-scheme: dark)');
  function savedTheme() { try { return localStorage.getItem('ppt-finder-theme'); } catch (_) { return null; } }
  function applyTheme(theme, source = 'manual') {
    const dark = theme === 'dark'; document.body.classList.toggle('dark', dark);
    $('theme-toggle-icon').textContent = dark ? '☀' : '☾';
    $('theme-toggle-label').textContent = source === 'system' ? 'Temă sistem' : (dark ? 'Mod întunecat' : 'Mod luminos');
    $('theme-toggle').setAttribute('aria-label', dark ? 'Activează modul luminos' : 'Activează modul întunecat');
  }
  function initialiseTheme() {
    const saved = savedTheme(); applyTheme(saved || (systemTheme.matches ? 'dark' : 'light'), saved ? 'manual' : 'system');
    $('theme-toggle').addEventListener('click', () => {
      const next = document.body.classList.contains('dark') ? 'light' : 'dark';
      try { localStorage.setItem('ppt-finder-theme', next); } catch (_) { /* Storage may be disabled. */ }
      applyTheme(next);
    });
    const changed = event => { if (!savedTheme()) applyTheme(event.matches ? 'dark' : 'light', 'system'); };
    if (systemTheme.addEventListener) systemTheme.addEventListener('change', changed);
    else if (systemTheme.addListener) systemTheme.addListener(changed);
  }
  function notice(message, kind = 'info') {
    const box = $('operator-notice'); box.hidden = !message; box.dataset.kind = kind;
    $('operator-notice-text').textContent = message;
  }
  async function safely(label, task) {
    try { return await task(); }
    catch (error) { if (error.name !== 'AbortError') notice(`${label}: ${error.message}`, 'error'); }
  }
  async function request(url, options = {}) {
    const {timeout = 15000, signal: externalSignal, ...fetchOptions} = options;
    const controller = new AbortController(); let timedOut = false;
    const cancel = () => controller.abort();
    if (externalSignal?.aborted) controller.abort();
    else externalSignal?.addEventListener('abort', cancel, {once: true});
    const timer = setTimeout(() => { timedOut = true; controller.abort(); }, timeout);
    const headers = new Headers(fetchOptions.headers || {});
    if (settings.accessCode) headers.set('X-PPT-Access-Code', settings.accessCode);
    try {
      const response = await fetch(url, {...fetchOptions, headers, signal: controller.signal, cache: 'no-store'});
      let data;
      try { data = await response.json(); } catch (_) { throw new Error(`Răspuns invalid de la server (${response.status}).`); }
      if (!response.ok || data?.success === false) throw new Error(data?.error || data?.message || `Cerere nereușită (${response.status}).`);
      return data;
    } catch (error) {
      if (timedOut) throw new Error('Serverul nu a răspuns la timp. Verificați starea înainte de a repeta comanda.');
      if (error instanceof TypeError) throw new Error('Conexiunea cu serverul este întreruptă.');
      throw error;
    } finally { clearTimeout(timer); externalSignal?.removeEventListener('abort', cancel); }
  }
  const post = (url, body = {}) => request(url, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
  let currentVoiceVerse = null;
  const empty = (icon, title, detail) => {
    const box = document.createElement('div'); box.className = 'empty-state';
    const i = document.createElement('div'); i.className = 'empty-icon'; i.textContent = icon;
    const h = document.createElement('h4'); h.textContent = title;
    const p = document.createElement('p'); p.textContent = detail; box.append(i, h, p); return box;
  };
  function button(label, handler, className = 'btn') {
    const b = document.createElement('button'); b.type = 'button'; b.className = className; b.textContent = label;
    b.addEventListener('click', async () => {
      if (b.disabled) return; b.disabled = true;
      try { await safely(label || 'Schimbare slide', handler); } finally { b.disabled = false; }
    }); return b;
  }
  function updateSelectedLocalResult() {
    const cards = Array.from(document.querySelectorAll('#local-results-list .card'));
    cards.forEach((card, index) => card.classList.toggle('selected', index === selectedResultIndex));
    cards[selectedResultIndex]?.scrollIntoView({block: 'nearest'});
  }
  function renderSnippet(node, html) {
    const parsed = new DOMParser().parseFromString(String(html || ''), 'text/html');
    const append = (parent, child) => {
      if (child.nodeType === Node.TEXT_NODE) parent.append(document.createTextNode(child.textContent));
      else if (child.nodeType === Node.ELEMENT_NODE) {
        if (child.tagName === 'MARK' || child.tagName === 'B') {
          const mark = document.createElement('mark'); mark.textContent = child.textContent; parent.append(mark);
        } else child.childNodes.forEach(next => append(parent, next));
      }
    }; parsed.body.childNodes.forEach(child => append(node, child));
  }
  function resultArray(data) {
    const items = Array.isArray(data) ? data : data?.results;
    if (!Array.isArray(items)) throw new Error('Formatul rezultatelor nu este valid.'); return items;
  }
  function renderLocal(items) {
    const root = $('local-results-list'); root.replaceChildren(); $('local-count').textContent = `${items.length} rezultate`;
    selectedResultIndex = items.length ? 0 : -1;
    if (!items.length) return root.append(empty('💻', 'Niciun rezultat local', 'Încercați alt titlu sau vers.'));
    items.forEach(item => {
      const c = document.createElement('article'); c.className = 'card';
      const h = document.createElement('h3'); h.textContent = item.title || item.name || 'Prezentare';
      const p = document.createElement('p'); renderSnippet(p, item.snippet);
      const path = document.createElement('small'); path.className = 'result-path'; path.textContent = item.path || '';
      const a = document.createElement('div'); a.className = 'card-actions';
      const open = button('Deschide', () => openLocal(item)); open.classList.add('open-result');
      open.title = 'Deschide fișierul în aplicația implicită a computerului';
      a.append(open, button('Proiectează text', () => loadLocal(item)), button('Arată fișierul', () => request('/api/reveal?path=' + encodeURIComponent(item.path))));
      if (settings.comEnabled) a.append(button('PowerPoint COM', () => openLocal(item, 'powerpoint')));
      c.append(h, p, path, a); root.append(c);
    }); updateSelectedLocalResult();
  }
  function renderWeb(items) {
    const root = $('web-results-list'); root.replaceChildren(); $('web-count').textContent = `${items.length} rezultate`;
    if (!items.length) return root.append(empty('🌐', 'Niciun cântec online', 'Rezultatele descărcate rămân disponibile offline.'));
    items.forEach(item => {
      const c = document.createElement('article'); c.className = 'card';
      const h = document.createElement('h3'); h.textContent = item.title || 'Cântare';
      const p = document.createElement('p'); p.textContent = item.author || '';
      c.append(h, p, button('Proiectează text', () => loadWeb(item))); root.append(c);
    });
  }
  function renderBible(items) {
    const root = $('bible-results-list'); root.replaceChildren(); $('bible-count').textContent = `${items.length} versete`;
    if (!items.length) return root.append(empty('📖', 'Niciun verset', 'Introduceți o referință sau un cuvânt cheie.'));
    items.forEach(item => {
      const c = document.createElement('article'); c.className = 'card';
      const h = document.createElement('h3'); h.textContent = item.ref || item.label || 'Verset';
      const p = document.createElement('p'); p.textContent = item.text || '';
      c.append(h, p, button('Proiectează text', () => loadBible(item))); root.append(c);
    });
  }
  function cancelSearch() { clearTimeout(searchTimer); searchGeneration++; searchController?.abort(); searchController = null; }
  async function performSearch() {
    cancelSearch(); const query = $('search-input').value.trim();
    if (!query) { clearSearch(); return; }
    const generation = searchGeneration; searchController = new AbortController();
    const signal = searchController.signal, q = encodeURIComponent(query), version = encodeURIComponent($('bible-version-select').value);
    selectedResultIndex = -1;
    const sources = [['local', '/api/search?q=' + q, renderLocal, 12000], ['web', '/api/search/web?q=' + q, renderWeb, 18000], ['bible', '/api/bible/search?q=' + q + '&version=' + version, renderBible, 12000]];
    await Promise.allSettled(sources.map(async ([source, url, render, timeout]) => {
      const root = $(source + '-results-list'); root.replaceChildren(empty('⌕', 'Căutare în curs…', query));
      root.setAttribute('aria-busy', 'true'); $(source + '-count').textContent = 'Caută…';
      try { const data = await request(url, {signal, timeout}); if (generation === searchGeneration) render(resultArray(data)); }
      catch (error) {
        if (generation !== searchGeneration || error.name === 'AbortError') return;
        $(source + '-count').textContent = 'Indisponibil';
        root.replaceChildren(empty('!', 'Căutarea nu a reușit', error.message), button('Reîncearcă', performSearch));
      } finally { if (generation === searchGeneration) root.setAttribute('aria-busy', 'false'); }
    }));
  }
  function clearSearch() {
    cancelSearch(); $('search-input').value = ''; selectedResultIndex = -1;
    ['local', 'web', 'bible'].forEach((source, i) => {
      const root = $(source + '-results-list'); root.setAttribute('aria-busy', 'false');
      root.replaceChildren(empty(['💻', '🌐', '📖'][i], 'Gata pentru căutare', 'Introduceți un titlu, vers sau referință.'));
      $(source + '-count').textContent = 'Tastați';
    });
  }
  async function openLocal(item, mode = 'default') {
    await post('/api/open', {path: item.path, mode});
    notice(mode === 'powerpoint' ? 'Prezentarea a fost deschisă prin PowerPoint COM.' : 'Fișierul a fost trimis aplicației implicite.');
    if (mode === 'powerpoint') await refreshStatus();
  }
  async function withProjectionLock(task) {
    if (projectionBusy) { notice('Așteptați confirmarea comenzii curente.'); return; }
    projectionBusy = true; $('projection-drawer').setAttribute('aria-busy', 'true');
    try { return await task(); } finally { projectionBusy = false; $('projection-drawer').setAttribute('aria-busy', 'false'); }
  }
  function loadLocal(item) {
    return withProjectionLock(async () => {
      const data = await request('/api/slides/local?path=' + encodeURIComponent(item.path), {timeout: 30000});
      await startProjection(item.title || item.name, data);
    });
  }
  function loadWeb(item) {
    return withProjectionLock(async () => {
      const params = new URLSearchParams({id: item.id || '', slug: item.slug || 'cantece', title_slug: item.title_slug || ''});
      const data = await request('/api/lyrics/web?' + params, {timeout: 30000}); await startProjection(item.title, data);
    });
  }
  function loadBible(item) {
    return withProjectionLock(async () => {
      const params = new URLSearchParams({ref: item.ref || item.label || '', version: $('bible-version-select').value});
      const chapter = await request('/api/bible/chapter?' + params, {timeout: 12000});
      await startProjection(item.ref || item.label || 'Biblia', chapter.slides, Number(chapter.current_slide_index) || 0);
    });
  }
  function closeVersePopup() {
    $('verse-popup-modal')?.classList.remove('active');
    currentVoiceVerse = null;
  }
  function handleVersePopup(data) {
    if (!data || !data.reference) return;
    currentVoiceVerse = data;
    $('verse-popup-title').textContent = data.reference;
    $('verse-popup-text').textContent = data.text || 'Deschide pentru a vizualiza textul complet.';
    $('verse-popup-modal').classList.add('active');
    notice(`Verset detectat: ${data.reference}`);
  }
  async function projectVoiceVerse() {
    if (!currentVoiceVerse) return;
    const verse = currentVoiceVerse;
    closeVersePopup();
    await loadBible({ref: verse.reference, label: verse.reference, text: verse.text});
  }
  function escapeHtml(value) {
    return String(value || '').replace(/[&<>"']/g, character => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    })[character]);
  }
  function printVoiceVerse() {
    if (!currentVoiceVerse) return;
    const reference = escapeHtml(currentVoiceVerse.reference);
    const text = escapeHtml(currentVoiceVerse.text || 'Deschide pentru a vizualiza textul complet.');
    const printWindow = window.open('', '_blank', 'width=760,height=540');
    if (!printWindow) { window.print(); return; }
    printWindow.document.write(`<!doctype html>
<html lang="ro">
<head>
  <meta charset="utf-8">
  <title>${reference}</title>
  <style>
    body{font-family:"Segoe UI",Arial,sans-serif;margin:0;padding:42px;color:#111}
    h1{margin:0 0 16px;padding-bottom:10px;border-bottom:2px solid #333;font-size:28px}
    blockquote{margin:0;font-size:20px;line-height:1.65}
    footer{margin-top:32px;color:#666;font-size:12px}
  </style>
</head>
<body>
  <h1>${reference}</h1>
  <blockquote>${text}</blockquote>
  <footer>PPT Finder & Asistent Vocal</footer>
  <script>window.onload=function(){window.print();window.close();};<\/script>
</body>
</html>`);
    printWindow.document.close();
  }
  async function startProjection(title, data, currentSlideIndex = 0) {
    if (!Array.isArray(data) || !data.length) throw new Error('Nu există text de proiectat. Deschideți fișierul în aplicația implicită pentru aspectul original.');
    const prepared = data.map((slide, index) => typeof slide === 'string' ? {label: `Slide ${index + 1}`, text: slide} : {label: String(slide.label || slide.title || `Slide ${index + 1}`), text: String(slide.text || '')});
    if (!prepared.some(slide => slide.text.trim())) throw new Error('Prezentarea nu conține text extras. Folosiți Deschide pentru slide-urile originale.');
    await post('/api/projection/session', {title: title || 'Proiecție', slides: prepared, current_slide_index: currentSlideIndex}); await refreshSession();
    notice('Textul este pregătit pe ecranul de proiecție. Folosiți „Ecran proiecție” pentru a deschide fereastra.');
  }
  function acceptRevision(data) {
    const revision = Number(data.revision);
    if (data.revision !== undefined && Number.isFinite(revision)) { if (revision < lastRevision) return false; lastRevision = revision; }
    return true;
  }
  function applyProjectionState(data) {
    if (!data || !acceptRevision(data)) return;
    const blackout = Boolean(data.blackout), clear = Boolean(data.clear_text);
    $('drawer-blackout-btn').classList.toggle('is-active', blackout); $('drawer-blackout-btn').setAttribute('aria-pressed', String(blackout));
    $('drawer-blackout-btn').textContent = blackout ? 'Restabilește ecranul' : 'Ecran negru';
    $('drawer-clear-btn').classList.toggle('is-active', clear); $('drawer-clear-btn').setAttribute('aria-pressed', String(clear));
    $('drawer-clear-btn').textContent = clear ? 'Arată textul' : 'Ascunde textul';
    const font = Number(data.font_size); if (Number.isFinite(font) && font > 0) $('drawer-font-size-val').textContent = font.toFixed(1);
    const visibility = $('projection-visibility'); visibility.textContent = blackout ? 'ECRAN NEGRU' : clear ? 'TEXT ASCUNS' : 'PE ECRAN';
    visibility.classList.toggle('is-hidden-output', blackout || clear);
  }
  function applySession(session) {
    if (!session || !acceptRevision(session)) return;
    if (session.active) {
      currentMode = 'projection'; slides = Array.isArray(session.slides) ? session.slides : [];
      activeSlide = Math.max(0, Math.min(slides.length - 1, Number(session.current_slide_index) || 0));
      $('projector-song-title').textContent = session.title || 'Proiecție text'; showProjection();
    } else if (currentMode === 'projection') hideProjection();
  }
  function applyRemoteStatus(status) {
    if (!status || !acceptRevision(status)) return;
    applyProjectionState(status);
    if (status.running && (status.mode === 'projection' || status.mode === 'powerpoint')) {
      currentMode = status.mode; slides = Array.isArray(status.slides) ? status.slides : [];
      activeSlide = Math.max(0, Math.min(slides.length - 1, (Number(status.current_slide_index) || 1) - 1));
      $('projector-song-title').textContent = status.title || 'Prezentare';
      $('drawer-start-btn').hidden = status.mode !== 'powerpoint' || status.slideshow_active; showProjection();
    } else if (!status.running) hideProjection();
  }
  function showProjection() {
    $('projection-drawer').classList.add('active'); document.body.classList.add('has-projection');
    $('projection-mode').textContent = currentMode === 'powerpoint' ? 'PowerPoint COM · previzualizare text' : 'Proiecție web · text extras';
    const webMode = currentMode === 'projection'; $('drawer-font-controls').hidden = !webMode; $('drawer-clear-btn').hidden = !webMode;
    if (webMode) $('drawer-start-btn').hidden = true; renderSlides();
  }
  function hideProjection() {
    slides = []; activeSlide = 0; currentMode = 'none'; slidesSignature = '';
    $('projection-drawer').classList.remove('active'); document.body.classList.remove('has-projection');
  }
  function slideText(slide) { return slide ? String(slide.text || slide.title || 'Acest slide nu conține text extras.') : ''; }
  function renderSlides() {
    const root = $('projector-slides-grid'), signature = JSON.stringify(slides);
    if (signature !== slidesSignature) {
      slidesSignature = signature; root.replaceChildren();
      slides.forEach((slide, index) => {
        const card = button('', () => withProjectionLock(() => sendSlide(index)), 'slide-card');
        card.setAttribute('aria-label', `Proiectează slide ${index + 1}: ${slide.label || slide.title || ''}`);
        const title = document.createElement('strong'); title.textContent = `${index + 1}. ${slide.label || slide.title || 'Slide'}`;
        const text = document.createElement('p'); text.textContent = slideText(slide); card.append(title, text); root.append(card);
      });
    }
    Array.from(root.children).forEach((card, index) => {
      card.classList.toggle('active', index === activeSlide); card.classList.toggle('up-next', index === activeSlide + 1);
      card.setAttribute('aria-current', index === activeSlide ? 'true' : 'false');
    });
    const current = slides[activeSlide], next = slides[activeSlide + 1];
    $('current-slide-label').textContent = current ? (current.label || current.title || `Slide ${activeSlide + 1}`) : 'Niciun slide';
    $('current-slide-text').textContent = slideText(current);
    $('next-slide-label').textContent = next ? (next.label || next.title || `Slide ${activeSlide + 2}`) : 'Sfârșitul prezentării';
    $('next-slide-text').textContent = next ? slideText(next) : 'Ultimul slide este pe ecran.';
    $('slide-counter').textContent = slides.length ? `${activeSlide + 1} / ${slides.length}` : '0 / 0';
    $('previous-slide-btn').disabled = activeSlide <= 0; $('next-slide-btn').disabled = activeSlide >= slides.length - 1;
  }
  async function refreshSession() {
    const generation = streamGeneration, session = await request('/api/projection/session');
    if (generation === streamGeneration || session.revision !== undefined) applySession(session);
  }
  async function refreshStatus() {
    if (statusPending) return; statusPending = true; const generation = streamGeneration;
    try { const status = await request('/api/remote/status', {timeout: 8000}); if (generation === streamGeneration || status.revision !== undefined) applyRemoteStatus(status); }
    finally { statusPending = false; }
  }
  async function sendSlide(index) {
    if (index < 0 || index >= slides.length) return;
    if (currentMode === 'powerpoint') await post('/api/remote/control', {action: 'goto', slide_index: index + 1});
    else await post('/api/projection/session/current', {current_slide_index: index});
    await refreshStatus();
  }
  async function openProjectorWindow() {
    if (window.pywebview?.api?.open_projector) {
      try {
        const result = await window.pywebview.api.open_projector();
        if (result?.success === false) throw new Error(result.error || 'Fereastra nu s-a deschis.');
        notice('Ecranul de proiecție este deschis. Mutați fereastra pe monitorul publicului.');
      } catch (error) { notice('Ecran proiecție: ' + error.message, 'error'); }
      return;
    }
    const opened = window.open(settings.projectorUrl, 'ppt-projector', 'popup,width=1280,height=720');
    if (!opened) notice('Fereastra nu s-a deschis. Permiteți ferestrele pop-up și apăsați din nou Ecran proiecție.', 'error');
    else { opened.focus(); notice('Mutați ecranul de proiecție pe monitorul publicului; dublu clic pentru ecran complet.'); }
  }
  async function action(name, extra = {}) { const result = await post('/api/remote/control', {action: name, ...extra}); await refreshStatus(); return result; }
  async function closeProjectionDrawer() {
    if (currentMode === 'powerpoint') await action('stop');
    else { await post('/api/projection/session/clear'); await refreshSession(); }
  }
  async function loadFolders() {
    const paths = await request('/api/folders'); if (!Array.isArray(paths)) throw new Error('Lista folderelor nu este validă.');
    const root = $('folder-list'); root.replaceChildren();
    paths.forEach((path, index) => {
      const li = document.createElement('li'), s = document.createElement('span'); s.textContent = path;
      li.append(s, button('Elimină din index', async () => { await request('/api/folders?index=' + index, {method: 'DELETE'}); await loadFolders(); }, 'btn btn-danger')); root.append(li);
    });
  }
  async function toggleSettings() { $('settings-panel').classList.toggle('active'); if ($('settings-panel').classList.contains('active')) await loadFolders(); }
  async function addFolder() { const input = $('new-folder-input'); if (!input.value.trim()) return; await post('/api/folders', {path: input.value.trim()}); input.value = ''; await loadFolders(); }
  async function triggerReindex() { await post('/api/reindex'); notice('Indexarea a fost solicitată. Progresul apare deasupra căutării.'); }
  function toggleRemoteModal() {
    const modal = $('remote-modal'); modal.classList.toggle('active');
    if (modal.classList.contains('active')) {
      const url = settings.remoteUrl; $('remote-link').href = url; $('remote-link').textContent = url;
      if (window.QRious) new QRious({element: $('qr-canvas'), value: url, size: 180}); else $('qr-canvas').hidden = true;
    }
  }
  function updateIndex(state) {
    if (!state) return; $('footer-status').textContent = state.text || 'Index pregătit'; $('progress-status-text').textContent = state.text || '';
    const percent = Math.max(0, Math.min(100, Math.round((Number(state.progress) || 0) * 100)));
    $('progress-percent').textContent = percent + '%'; $('progress-fill').style.width = percent + '%';
    $('progress-banner').classList.toggle('active', state.status === 'indexing'); $('reindex-btn').disabled = state.status === 'indexing';
  }
  function connectionStatus(connected) {
    $('connection-status').textContent = connected ? '● Conectat' : '● Reconectare…'; $('connection-status').classList.toggle('disconnected', !connected);
  }
  function initSSE() {
    if (!window.EventSource) { connectionStatus(false); return; }
    const stream = new EventSource('/api/events?code=' + encodeURIComponent(settings.accessCode));
    const listen = (name, render) => stream.addEventListener(name, event => {
      try { const payload = JSON.parse(event.data); streamGeneration++; render(payload.data || payload); }
      catch (_) { notice('O actualizare de stare nu a putut fi citită. Se încearcă sincronizarea.', 'error'); }
    });
    listen('indexing_state', updateIndex); listen('projection_state', applyProjectionState); listen('projection_session', applySession); listen('remote_status', applyRemoteStatus); listen('verse_popup', handleVersePopup);
    stream.onopen = () => { connectionStatus(true); safely('Sincronizare', refreshStatus); };
    stream.onerror = () => connectionStatus(false);
    window.addEventListener('pagehide', () => stream.close(), {once: true});
  }
  function isTyping(target) { return target instanceof Element && Boolean(target.closest('input, textarea, select, [contenteditable="true"], [role="textbox"]')); }
  function keyboard(event) {
    if (event.defaultPrevented || event.ctrlKey || event.metaKey || event.altKey || event.isComposing) return;
    if (event.key === 'Escape') { $('remote-modal').classList.remove('active'); closeVersePopup(); $('settings-panel').classList.remove('active'); return; }
    if ($('remote-modal').classList.contains('active')) return;
    const inSearch = event.target === $('search-input'); if (isTyping(event.target) && !inSearch) return;
    if (inSearch || (currentMode === 'none' && !isTyping(event.target))) {
      const cards = document.querySelectorAll('#local-results-list .card');
      if (cards.length && (event.key === 'ArrowDown' || event.key === 'ArrowUp')) {
        event.preventDefault(); selectedResultIndex = (selectedResultIndex + (event.key === 'ArrowDown' ? 1 : -1) + cards.length) % cards.length; updateSelectedLocalResult();
      } else if (event.key === 'Enter' && inSearch) {
        event.preventDefault(); if (selectedResultIndex >= 0) cards[selectedResultIndex]?.querySelector('.open-result')?.click(); else safely('Căutare', performSearch);
      } return;
    }
    if (isTyping(event.target) || event.repeat || currentMode === 'none' || event.target?.closest('button, a')) return;
    const key = event.key.toLowerCase();
    if (key === 'b') { event.preventDefault(); window.toggleBlackout(); }
    else if (key === 'c' && currentMode === 'projection') { event.preventDefault(); window.toggleClearText(); }
    else if (['arrowright', 'pagedown', ' '].includes(key)) { event.preventDefault(); window.moveSlide(1); }
    else if (['arrowleft', 'pageup'].includes(key)) { event.preventDefault(); window.moveSlide(-1); }
  }
  window.performSearch = () => safely('Căutare', performSearch); window.clearSearch = clearSearch; window.openProjectorWindow = openProjectorWindow;
  window.closeProjectionDrawer = () => safely('Oprire', () => withProjectionLock(closeProjectionDrawer));
  window.toggleBlackout = () => safely('Ecran negru', () => withProjectionLock(() => action('blackout_toggle')));
  window.toggleClearText = () => safely('Vizibilitate text', () => withProjectionLock(() => action('clear_toggle')));
  window.adjustProjectorFontSize = delta => safely('Dimensiune text', () => withProjectionLock(() => action('font_adjust', {delta})));
  window.moveSlide = delta => safely('Schimbare slide', () => withProjectionLock(() => sendSlide(activeSlide + delta)));
  window.startNativeSlideshow = () => safely('Pornire PowerPoint', () => withProjectionLock(() => action('start')));
  window.toggleSettings = () => safely('Setări', toggleSettings); window.addFolder = () => safely('Adăugare folder', addFolder);
  window.triggerReindex = () => safely('Indexare', triggerReindex); window.toggleRemoteModal = toggleRemoteModal; window.dismissNotice = () => notice('');
  $('remote-toggle-btn').addEventListener('click', toggleRemoteModal);
  $('remote-close-btn').addEventListener('click', toggleRemoteModal);
  $('verse-popup-close-btn').addEventListener('click', closeVersePopup);
  $('verse-popup-dismiss-btn').addEventListener('click', closeVersePopup);
  $('verse-popup-project-btn').addEventListener('click', () => safely('Proiectare verset', projectVoiceVerse));
  $('verse-popup-print-btn').addEventListener('click', printVoiceVerse);
  $('projector-window-btn').addEventListener('click', () => safely('Ecran proiecție', openProjectorWindow));
  $('settings-btn').addEventListener('click', () => safely('Setări', toggleSettings));
  $('settings-close-btn').addEventListener('click', () => safely('Setări', toggleSettings));
  $('notice-close-btn').addEventListener('click', () => notice(''));
  $('add-folder-btn').addEventListener('click', () => safely('Adăugare folder', addFolder));
  $('reindex-btn').addEventListener('click', () => safely('Indexare', triggerReindex));
  $('search-clear-btn').addEventListener('click', clearSearch);
  $('bible-version-select').addEventListener('change', () => safely('Căutare', performSearch));
  $('drawer-start-btn').addEventListener('click', () => safely('Pornire PowerPoint', () => withProjectionLock(() => action('start'))));
  $('drawer-blackout-btn').addEventListener('click', () => safely('Ecran negru', () => withProjectionLock(() => action('blackout_toggle'))));
  $('drawer-clear-btn').addEventListener('click', () => safely('Vizibilitate text', () => withProjectionLock(() => action('clear_toggle'))));
  $('font-smaller-btn').addEventListener('click', () => safely('Dimensiune text', () => withProjectionLock(() => action('font_adjust', {delta: -0.5}))));
  $('font-larger-btn').addEventListener('click', () => safely('Dimensiune text', () => withProjectionLock(() => action('font_adjust', {delta: 0.5}))));
  $('stop-projection-btn').addEventListener('click', () => safely('Oprire', () => withProjectionLock(closeProjectionDrawer)));
  $('previous-slide-btn').addEventListener('click', () => safely('Schimbare slide', () => withProjectionLock(() => sendSlide(activeSlide - 1))));
  $('next-slide-btn').addEventListener('click', () => safely('Schimbare slide', () => withProjectionLock(() => sendSlide(activeSlide + 1))));
  initialiseTheme();
  $('search-input').addEventListener('input', () => {
    cancelSearch(); selectedResultIndex = -1;
    document.querySelectorAll('#local-results-list .card').forEach(card => card.classList.remove('selected'));
    if (!$('search-input').value.trim()) clearSearch(); else searchTimer = setTimeout(() => safely('Căutare', performSearch), 180);
  });
  document.addEventListener('keydown', keyboard);
  const voiceLink = $('voice-app-link'); if (settings.voiceUrl) { voiceLink.href = settings.voiceUrl; voiceLink.hidden = false; }
  safely('Versiuni Biblie', async () => {
    const items = await request('/api/bible/versions'); if (!Array.isArray(items)) return;
    const select = $('bible-version-select'), selected = select.value;
    select.replaceChildren(...items.map(item => { const option = document.createElement('option'); option.value = item.key; option.textContent = item.name; return option; }));
    if (items.some(item => item.key === selected)) select.value = selected;
  });
  safely('Stare index', async () => updateIndex(await request('/api/status'))); safely('Sincronizare proiecție', refreshStatus); initSSE();
  setInterval(() => { if (!document.hidden) safely('Sincronizare', refreshStatus); }, 5000);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) safely('Sincronizare', refreshStatus); });
})();
