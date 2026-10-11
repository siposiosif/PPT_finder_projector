const accessCode = document.body.dataset.accessCode || '';
const protectedHeaders = {'Content-Type': 'application/json', 'X-PPT-Access-Code': accessCode};
let currentStatus = {
  running: false,
  slideshow_active: false,
  title: "",
  current_slide_index: 1,
  total_slides: 0,
  blackout: false,
  clear_text: false,
  slides: []
};
let actionBusy = false;
let reconnectTimer = null;

async function readResponse(response) {
  let data;
  try { data = await response.json(); }
  catch (_) { throw new Error(`Răspuns invalid de la server (${response.status}).`); }
  if (!response.ok || data?.success === false) {
    throw new Error(data?.error || data?.message || `Comanda nu a reușit (${response.status}).`);
  }
  return data;
}

async function sendAction(action, slideIndex = null) {
  if (actionBusy) return;
  actionBusy = true;
  try {
    const response = await fetch('/api/remote/control', {
      method: 'POST',
      headers: protectedHeaders,
      body: JSON.stringify({ action: action, slide_index: slideIndex }),
      cache: 'no-store'
    });
    await readResponse(response);
    await updateStatus();
  } catch (err) {
    const badge = document.getElementById('status-badge');
    badge.className = 'status-badge inactive';
    badge.textContent = err.message || 'Comanda nu a fost confirmată';
  } finally { actionBusy = false; }
}

function renderStatus(status) {
  currentStatus = status;
  const badge = document.getElementById('status-badge');
  const titleEl = document.getElementById('pres-title');
  const slideNumEl = document.getElementById('pres-slide-num');
  const modeEl = document.getElementById('pres-mode');
  const previewEl = document.getElementById('current-slide-preview');
  const blackoutBtn = document.getElementById('btn-blackout');
  const clearBtn = document.getElementById('btn-clear');
  const startBtn = document.getElementById('btn-start');
  clearBtn.hidden = status.mode === 'powerpoint';

  if (status.running && status.slideshow_active) {
    badge.className = 'status-badge active';
    badge.innerText = '● Activ';
    titleEl.innerText = status.title || 'Prezentare';
    slideNumEl.innerText = `Slide ${status.current_slide_index} din ${status.total_slides}`;
    modeEl.innerText = `Mod: ${status.mode === 'powerpoint' ? 'PowerPoint COM' : 'Proiecție Web'}`;

    // Update active slide text
    if (status.slides && status.slides.length > 0) {
      const idx = status.current_slide_index - 1;
      if (idx >= 0 && idx < status.slides.length) {
        previewEl.innerText = status.slides[idx].text || status.slides[idx].title || `Slide ${idx+1}`;
      }
    }
  } else if (status.running && status.mode === 'powerpoint') {
    badge.className = 'status-badge inactive';
    badge.innerText = '● Gata de pornire';
    titleEl.innerText = status.title || 'Prezentare PowerPoint';
    slideNumEl.innerText = `${status.total_slides || 0} slide-uri pregătite`;
    modeEl.innerText = 'Apăsați Pornește pentru slideshow (F5)';
    previewEl.innerText = 'Prezentarea este deschisă. Apăsați butonul „Pornește (F5)”.';
  } else {
    badge.className = 'status-badge inactive';
    badge.innerText = 'Inactiv';
    titleEl.innerText = 'Nicio proiecție activă';
    slideNumEl.innerText = 'Slide - / -';
    modeEl.innerText = 'Mod: Deconectat';
    previewEl.innerText = 'Deschideți o prezentare pe computer pentru a începe.';
  }

  // F5 is useful only while PowerPoint has an open presentation and its
  // slideshow is not running. Keep it out of the way in every other mode.
  startBtn.classList.toggle('visible', Boolean(status.running && status.mode === 'powerpoint' && !status.slideshow_active));

  // Quick buttons state
  if (status.blackout) {
    blackoutBtn.classList.add('active');
  } else {
    blackoutBtn.classList.remove('active');
  }

  if (status.clear_text) {
    clearBtn.classList.add('active');
  } else {
    clearBtn.classList.remove('active');
  }

  // Render slides list
  const listEl = document.getElementById('slides-list');
  listEl.innerHTML = '';
  if (status.slides && status.slides.length > 0) {
    status.slides.forEach((s, index) => {
      const item = document.createElement('div');
      const slideNum = index + 1;
      item.className = 'slide-item' + (slideNum === status.current_slide_index ? ' active' : '');
      item.addEventListener('click', () => sendAction('goto', slideNum));

      const number = document.createElement('span');
      number.className = 'slide-item-idx';
      number.textContent = String(slideNum);
      const text = document.createElement('span');
      text.className = 'slide-item-text';
      text.textContent = (s.text || s.title || `Slide ${slideNum}`).replace(/\n/g, ' ');
      item.append(number, text);
      listEl.appendChild(item);
    });
  }
}

async function updateStatus() {
  try {
    const response = await fetch('/api/remote/status?code=' + encodeURIComponent(accessCode), {cache: 'no-store'});
    renderStatus(await readResponse(response));
  } catch (err) {
    const badge = document.getElementById('status-badge');
    badge.className = 'status-badge inactive';
    badge.textContent = 'Reconectare…';
  }
}

// Connect to SSE for instant updates
function connectSSE() {
  clearTimeout(reconnectTimer);
  const evtSource = new EventSource('/api/events?code=' + encodeURIComponent(accessCode));
  evtSource.addEventListener('remote_status', (e) => {
    try {
      const payload = JSON.parse(e.data);
      renderStatus(payload.data || payload);
    } catch (err) {
      console.error("SSE parse error:", err);
    }
  });
  evtSource.onerror = () => {
    evtSource.close();
    reconnectTimer = setTimeout(connectSSE, 3000);
  };
  window.addEventListener('pagehide', () => evtSource.close(), {once: true});
}

// Touch Swipe Gestures
let startX = 0;
let endX = 0;
const swipeArea = document.getElementById('swipe-area');

swipeArea.addEventListener('touchstart', e => {
  startX = e.changedTouches[0].screenX;
}, false);

swipeArea.addEventListener('touchend', e => {
  endX = e.changedTouches[0].screenX;
  if (endX < startX - 45) {
    sendAction('next');
  } else if (endX > startX + 45) {
    sendAction('back');
  }
}, false);

updateStatus();
connectSSE();
document.getElementById('btn-start').addEventListener('click', () => sendAction('start'));
document.getElementById('btn-blackout').addEventListener('click', () => sendAction('blackout_toggle'));
document.getElementById('btn-clear').addEventListener('click', () => sendAction('clear_toggle'));
document.getElementById('btn-stop').addEventListener('click', () => sendAction('stop'));
document.getElementById('btn-prev').addEventListener('click', () => sendAction('back'));
document.getElementById('btn-next').addEventListener('click', () => sendAction('next'));
