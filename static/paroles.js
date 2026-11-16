'use strict';
const $ = selector => document.querySelector(selector);

const state = {
  id: null, info: null, page: 0, zoom: 1.5,
  selection: { styles: [], ignore_margin: true, overrides: {}, voices: null, by_verse: true, strip_numbers: true },
  lyrics: new Set(), generated: '', edited: false, previewURL: null,
};

async function request(url, options = {}) {
  const response = await fetch(url, options);
  if (response.status === 401) throw Error('Votre connexion a expiré. Rechargez la page et reconnectez-vous.');
  if (!response.ok) {
    let message = 'Erreur du serveur.';
    try { message = (await response.json()).error || message; } catch { /* not JSON */ }
    throw Error(message);
  }
  return response;
}
const post = (url, body) => request(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
const notice = text => { $('#notice').textContent = text; };
const stem = () => (state.info?.name || 'partition').replace(/\.pdf$/i, '') || 'partition';

function download(blob, name) {
  const link = document.createElement('a');
  link.href = URL.createObjectURL(blob);
  link.download = name;
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(link.href), 10000);
}

async function checkAvailability() {
  try {
    const status = await (await request('/api/paroles/statut')).json();
    if (!status.available) {
      $('#lyrics-status').textContent = '○ Extracteur non installé';
      notice('L’extracteur de paroles n’est pas installé sur cet ordinateur (bibliothèques PyMuPDF, numpy, SciPy, Pillow). Contactez l’administrateur.');
      $('#drop').classList.add('disabled');
      return false;
    }
    $('#lyrics-status').textContent = status.french ? '● Extracteur disponible' : '● Disponible (scans : texte non lu)';
    return true;
  } catch (error) {
    $('#lyrics-status').textContent = '○ Service injoignable';
    notice(error.message);
    return false;
  }
}

async function choose(file) {
  if (!file) return;
  if (!file.name.toLowerCase().endsWith('.pdf') || file.size > 80 * 1024 * 1024) {
    notice('Choisissez un PDF de moins de 80 Mo.');
    return;
  }
  if (!await checkAvailability()) return;
  $('#workspace').hidden = true;
  notice(`Envoi de « ${file.name} »…`);
  try {
    const response = await request('/api/paroles', { method: 'POST', headers: { 'Content-Type': 'application/pdf', 'X-Filename': encodeURIComponent(file.name) }, body: file });
    state.id = (await response.json()).id;
    await waitForAnalysis();
  } catch (error) {
    notice(error.message);
  }
}

async function waitForAnalysis() {
  for (;;) {
    const info = await (await request(`/api/paroles/${state.id}`)).json();
    notice(info.message);
    if (info.state === 'error') return;
    if (info.state === 'ready') return start(info);
    await new Promise(resolve => setTimeout(resolve, 1000));
  }
}

function start(info) {
  Object.assign(state, { info, page: 0, edited: false, generated: '' });
  state.selection = { styles: info.styles.filter(s => s.selected).map(s => s.id), ignore_margin: true, overrides: {}, voices: null, by_verse: true, strip_numbers: true };
  $('#ignore-margin').checked = $('#by-verse').checked = $('#strip-numbers').checked = true;
  $('#show-result').checked = false;
  $('#text').value = '';
  $('#edited').hidden = true;
  $('#save-notice').textContent = '';
  renderStyles();
  $('#workspace').hidden = false;
  if (!state.selection.styles.length) notice('Le style des paroles n’a pas été deviné : cochez-le dans « Styles de texte ».');
  recompute();
}

function renderStyles() {
  const list = $('#styles');
  list.replaceChildren();
  for (const style of state.info.styles) {
    const label = document.createElement('label');
    label.className = 'check style-choice';
    const box = document.createElement('input');
    box.type = 'checkbox';
    box.checked = state.selection.styles.includes(style.id);
    box.onchange = () => {
      const chosen = new Set(state.selection.styles);
      box.checked ? chosen.add(style.id) : chosen.delete(style.id);
      state.selection.styles = [...chosen];
      recompute();
    };
    const text = document.createElement('span');
    const kind = state.info.scanned ? 'Lignes de texte' : `${style.font} · ${style.size} pt`;
    text.innerHTML = '<strong></strong> <small></small><br><em></em>';
    text.querySelector('strong').textContent = kind;
    text.querySelector('small').textContent = `${style.count} élément${style.count > 1 ? 's' : ''}`;
    text.querySelector('em').textContent = style.samples.join(' · ');
    label.append(box, text);
    list.append(label);
  }
}

function renderVoices(voices) {
  const list = $('#voices');
  list.replaceChildren();
  if (!voices.length) {
    list.textContent = 'Aucune parole sélectionnée.';
    return;
  }
  for (const voice of voices) {
    const label = document.createElement('label');
    label.className = 'check';
    const box = document.createElement('input');
    box.type = 'checkbox';
    box.checked = state.selection.voices === null || state.selection.voices.includes(voice.voice);
    box.onchange = () => {
      state.selection.voices = [...list.querySelectorAll('input')].map((input, i) => input.checked ? voices[i].voice : null).filter(v => v !== null);
      recompute(true);
    };
    const sample = voice.sample.length > 40 ? voice.sample.slice(0, 38) + '…' : voice.sample;
    label.append(box, ` Portée ${voice.voice + 1} — ${voice.lines} ligne${voice.lines > 1 ? 's' : ''} : « ${sample} »`);
    list.append(label);
  }
}

let pending = null;
async function recompute(replaceEditedText = false) {
  const ticket = pending = Symbol('recompute');
  try {
    const result = await (await post(`/api/paroles/${state.id}/calcul`, state.selection)).json();
    if (ticket !== pending) return;
    state.lyrics = new Set(result.lyrics);
    renderVoices(result.voices);
    if (!state.edited || replaceEditedText) {
      if (state.edited && !confirm('Remplacer le texte modifié à la main ?')) {
        renderPage();
        return;
      }
      $('#text').value = state.generated = result.text;
      state.edited = false;
      $('#edited').hidden = true;
    } else {
      $('#edited').hidden = false;
    }
    notice(`${state.lyrics.size} élément${state.lyrics.size > 1 ? 's' : ''} pris comme paroles.`);
    renderPage();
  } catch (error) {
    notice(error.message);
  }
}

async function renderPage() {
  const info = state.info;
  const [width, height] = info.pages[state.page];
  $('#page-label').textContent = `Page ${state.page + 1} sur ${info.pages.length}`;
  $('#prev').disabled = state.page === 0;
  $('#next').disabled = state.page === info.pages.length - 1;
  const view = $('#page');
  view.style.width = `${Math.round(width * state.zoom)}px`;
  view.style.aspectRatio = `${width} / ${height}`;
  const showResult = $('#show-result').checked;
  const image = $('#page-image');
  if (state.previewURL) URL.revokeObjectURL(state.previewURL);
  state.previewURL = null;
  if (showResult) {
    image.src = '';
    view.classList.add('loading');
    try {
      const blob = await (await post(`/api/paroles/${state.id}/apercu/${state.page}?zoom=${state.zoom}`, state.selection)).blob();
      image.src = state.previewURL = URL.createObjectURL(blob);
    } catch (error) {
      notice(error.message);
    } finally {
      view.classList.remove('loading');
    }
  } else {
    image.src = `/api/paroles/${state.id}/page/${state.page}?zoom=${state.zoom}`;
  }
  const boxes = $('#boxes');
  boxes.replaceChildren();
  if (showResult) return;
  for (const span of info.spans) {
    if (span.page !== state.page) continue;
    const [x0, y0, x1, y1] = span.box;
    const box = document.createElement('button');
    box.type = 'button';
    const lyric = state.lyrics.has(span.id);
    box.className = lyric ? 'span lyric' : 'span other';
    box.style.left = `${(x0 / width) * 100}%`;
    box.style.top = `${(y0 / height) * 100}%`;
    box.style.width = `${((x1 - x0) / width) * 100}%`;
    box.style.height = `${((y1 - y0) / height) * 100}%`;
    box.title = span.text;
    box.setAttribute('aria-pressed', String(lyric));
    box.setAttribute('aria-label', `${span.text} : ${lyric ? 'paroles' : 'autre texte'}`);
    box.onclick = () => {
      state.selection.overrides[span.id] = !lyric;
      notice(`« ${span.text} » ${lyric ? 'retiré des' : 'ajouté aux'} paroles.`);
      recompute();
    };
    boxes.append(box);
  }
}

$('#file').onchange = event => choose(event.target.files[0]);
$('#drop').onkeydown = event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); $('#file').click(); } };
for (const name of ['dragenter', 'dragover']) $('#drop').addEventListener(name, event => { event.preventDefault(); $('#drop').classList.add('drag'); });
for (const name of ['dragleave', 'drop']) $('#drop').addEventListener(name, event => {
  event.preventDefault();
  $('#drop').classList.remove('drag');
  if (name === 'drop') choose(event.dataTransfer.files[0]);
});
$('#ignore-margin').onchange = event => { state.selection.ignore_margin = event.target.checked; recompute(); };
$('#by-verse').onchange = event => { state.selection.by_verse = event.target.checked; recompute(true); };
$('#strip-numbers').onchange = event => { state.selection.strip_numbers = event.target.checked; recompute(true); };
$('#show-result').onchange = renderPage;
$('#prev').onclick = () => { state.page--; renderPage(); };
$('#next').onclick = () => { state.page++; renderPage(); };
$('#zoom-in').onclick = () => { state.zoom = Math.min(3, state.zoom * 1.25); renderPage(); };
$('#zoom-out').onclick = () => { state.zoom = Math.max(0.5, state.zoom / 1.25); renderPage(); };
$('#text').oninput = () => {
  state.edited = $('#text').value.trim() !== state.generated.trim();
  $('#edited').hidden = !state.edited;
};
$('#regenerate').onclick = () => recompute(true);
$('#save-text').onclick = () => {
  download(new Blob([$('#text').value.trimEnd() + '\n'], { type: 'text/plain;charset=utf-8' }), `${stem()}_paroles.txt`);
  $('#save-notice').textContent = 'Texte téléchargé.';
};
$('#save-pdf').onclick = async () => {
  const button = $('#save-pdf');
  button.disabled = true;
  $('#save-notice').textContent = 'Création du PDF sans paroles…';
  try {
    const response = await post(`/api/paroles/${state.id}/pdf`, state.selection);
    download(await response.blob(), `${stem()}_sans_paroles.pdf`);
    const left = Number(response.headers.get('X-Remaining') || 0);
    $('#save-notice').textContent = left ? `PDF téléchargé. Attention : ${left} syllabe${left > 1 ? 's' : ''} n’ont pas pu être retirées.` : 'PDF sans paroles téléchargé.';
  } catch (error) {
    $('#save-notice').textContent = error.message;
  } finally {
    button.disabled = false;
  }
};
checkAvailability();
