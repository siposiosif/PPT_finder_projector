'use strict';
const accessCode = document.body.dataset.accessCode || '';
const textEl = document.getElementById('slide-text'), labelEl = document.getElementById('slide-label');
const statusEl = document.getElementById('projector-status'), container = document.getElementById('slide-container');
let currentData = {text:'', label:'', font_size:7}, lastRevision = -1, lastTimestamp = 0;
let streamGeneration = 0, polling = false, commanding = false, controlsTimer, fitFrame;
async function api(url, body) {
  const controller = new AbortController(), timer = setTimeout(() => controller.abort(), 8000);
  try {
    const options = {headers: {'X-PPT-Access-Code':accessCode}, signal:controller.signal, cache:'no-store'};
    if (body) { options.method = 'POST'; options.headers['Content-Type'] = 'application/json'; options.body = JSON.stringify(body); }
    const response = await fetch(url, options);
    const data = await response.json();
    if (!response.ok || data?.success === false) throw new Error(data?.error || `Eroare ${response.status}`);
    return data;
  } finally { clearTimeout(timer); }
}
function fitText() {
  cancelAnimationFrame(fitFrame);
  fitFrame = requestAnimationFrame(() => {
    const requested = Math.max(3, Math.min(15, Number(currentData.font_size) || 7)) * window.innerHeight / 100;
    let low = 8, high = requested, best = 8;
    for (let step = 0; step < 12; step++) {
      const size = (low + high) / 2; textEl.style.fontSize = size + 'px';
      if (textEl.scrollHeight <= container.clientHeight && textEl.scrollWidth <= container.clientWidth) { best = size; low = size; }
      else high = size;
    }
    textEl.style.fontSize = best + 'px';
  });
}
function renderSlide(data) {
  if (!data || typeof data !== 'object') return;
  if (data.revision !== undefined) {
    const revision = Number(data.revision); if (Number.isFinite(revision)) { if (revision < lastRevision) return; lastRevision = revision; }
  } else if (Number(data.timestamp) && Number(data.timestamp) < lastTimestamp) return;
  lastTimestamp = Number(data.timestamp) || lastTimestamp;
  const changed = currentData.text !== data.text || currentData.font_size !== data.font_size;
  currentData = data;
  document.body.classList.toggle('blackout', Boolean(data.blackout));
  document.body.classList.toggle('clear-text', Boolean(data.clear_text));
  textEl.textContent = String(data.text || ''); labelEl.textContent = String(data.label || '');
  if (changed) fitText();
}
async function refresh() {
  if (polling) return; polling = true; const generation = streamGeneration;
  try { const data = await api('/api/projection/current'); if (generation === streamGeneration || data.revision !== undefined) renderSlide(data); statusEl.textContent = 'Conectat'; }
  catch (_) { statusEl.textContent = 'Conexiune întreruptă · se păstrează ultimul slide'; }
  finally { polling = false; }
}
function connectSSE() {
  if (!window.EventSource) return;
  const stream = new EventSource('/api/events?code=' + encodeURIComponent(accessCode));
  stream.addEventListener('projection_state', event => {
    try { const payload = JSON.parse(event.data); streamGeneration++; renderSlide(payload.data || payload); statusEl.textContent = 'Conectat'; }
    catch (_) { statusEl.textContent = 'Actualizare invalidă · se păstrează ultimul slide'; }
  });
  stream.onopen = () => { statusEl.textContent = 'Conectat'; refresh(); };
  stream.onerror = () => { statusEl.textContent = 'Reconectare · se păstrează ultimul slide'; };
  window.addEventListener('pagehide', () => stream.close(), {once:true});
}
async function toggleFullScreen() {
  try {
    if (window.pywebview?.api?.toggle_fullscreen) {
      await window.pywebview.api.toggle_fullscreen();
      return;
    }
    if (!document.fullscreenElement) await document.documentElement.requestFullscreen();
    else await document.exitFullscreen();
  } catch (_) { statusEl.textContent = 'Folosiți F11 pentru ecran complet'; showControls(); }
}
async function control(action) {
  if (commanding) return; commanding = true;
  try { await api('/api/remote/control', {action}); await refresh(); }
  catch (error) { statusEl.textContent = error.message || 'Comanda nu a fost confirmată'; showControls(); }
  finally { commanding = false; }
}
function showControls() { clearTimeout(controlsTimer); document.body.classList.add('show-controls'); controlsTimer = setTimeout(() => document.body.classList.remove('show-controls'), 2000); }
document.addEventListener('mousemove', showControls);
document.getElementById('fullscreen-btn').addEventListener('click', toggleFullScreen);
document.getElementById('previous-projector-btn').addEventListener('click', () => control('back'));
document.getElementById('next-projector-btn').addEventListener('click', () => control('next'));
document.addEventListener('dblclick', event => { if (!event.target.closest('button')) toggleFullScreen(); });
window.addEventListener('resize', fitText); document.addEventListener('fullscreenchange', fitText);
document.addEventListener('keydown', event => {
  if (event.repeat || event.ctrlKey || event.altKey || event.metaKey || event.target.closest('button,input,textarea,select,[contenteditable]')) return;
  const key = event.key.toLowerCase();
  if (key === 'f') { event.preventDefault(); toggleFullScreen(); }
  else if (key === 'b') { event.preventDefault(); control('blackout_toggle'); }
  else if (key === 'c') { event.preventDefault(); control('clear_toggle'); }
  else if (['arrowright','pagedown',' '].includes(key)) { event.preventDefault(); control('next'); }
  else if (['arrowleft','pageup'].includes(key)) { event.preventDefault(); control('back'); }
});
refresh(); connectSSE(); setInterval(refresh, 5000);
