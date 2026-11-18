'use strict';
const $ = selector => document.querySelector(selector);
const state = { blob: null, name: '', url: null, musicxml: false };
const XML_NAME = /\.(musicxml|mxl|xml)$/i;
const notice = text => { $('#notice').textContent = text; };

async function checkAvailability() {
  try {
    const response = await fetch('/api/liseuse/statut');
    if (response.status === 401) throw Error('Votre connexion a expiré. Rechargez la page et reconnectez-vous.');
    const status = await response.json();
    if (!status.available) {
      $('#reader-status').textContent = '○ Non installé';
      notice('La mise en page pour liseuse a besoin de PyMuPDF et numpy (requirements-paroles.txt). Contactez l’administrateur.');
      $('#drop').classList.add('disabled');
      return false;
    }
    state.musicxml = status.musicxml;
    $('#reader-status').textContent = !status.systems ? '● Disponible (systèmes non reconnus : SciPy absent)'
      : status.musicxml ? '● Disponible' : '● Disponible (PDF seulement : verovio absent)';
    return true;
  } catch (error) {
    $('#reader-status').textContent = '○ Service injoignable';
    notice(error.message);
    return false;
  }
}

async function choose(file) {
  if (!file) return;
  const xml = XML_NAME.test(file.name);
  if ((!xml && !file.name.toLowerCase().endsWith('.pdf')) || file.size > 80 * 1024 * 1024) {
    notice('Choisissez un PDF ou un MusicXML de moins de 80 Mo.');
    return;
  }
  if (!await checkAvailability()) return;
  if (xml && !state.musicxml) {
    notice('La gravure des MusicXML (bibliothèque verovio) n’est pas installée sur cet ordinateur. Contactez l’administrateur.');
    return;
  }
  state.file = file;
  $('#save').disabled = true;
  $('#summary').textContent = '';
  notice(xml ? `Gravure de « ${file.name} » au format de l’écran…`
    : `Mise en page de « ${file.name} »… Un scan peut prendre quelques secondes par page.`);
  try {
    const response = await fetch('/api/liseuse', {
      method: 'POST', body: file,
      headers: { 'Content-Type': xml ? 'application/vnd.recordare.musicxml' : 'application/pdf',
        'X-Filename': encodeURIComponent(file.name), 'X-Note-Size': $('#size').value },
    });
    if (!response.ok) {
      let message = 'Erreur du serveur.';
      try { message = (await response.json()).error || message; } catch { /* not JSON */ }
      throw Error(message);
    }
    state.blob = await response.blob();
    state.name = file.name.replace(/\.(pdf|musicxml|mxl|xml)$/i, '') + '_liseuse.pdf';
    if (state.url) URL.revokeObjectURL(state.url);
    state.url = URL.createObjectURL(state.blob);
    $('#pdf').src = state.url;
    $('#pdf').hidden = false;
    $('#placeholder').hidden = true;
    const pages = response.headers.get('X-Pages');
    const systems = response.headers.get('X-Systems');
    const plural = (n, word) => `${n} ${word}${Number(n) > 1 ? 's' : ''}`;
    $('#summary').textContent = `${plural(pages, 'page')} · ${plural(systems, 'système')}`;
    notice(response.headers.get('X-Engraved') === '1'
      ? 'Partition gravée depuis le MusicXML. Changez la taille des notes pour en mettre plus ou moins par page.'
      : systems === '0'
        ? 'Aucun système reconnu : la page a été découpée sur les espaces blancs. Vérifiez l’aperçu.'
        : 'Vérifiez l’aperçu, puis copiez le PDF sur la liseuse.');
    $('#save').disabled = false;
  } catch (error) {
    notice(error.message);
  }
}

$('#save').addEventListener('click', () => {
  if (!state.blob) return;
  const link = document.createElement('a');
  link.href = state.url;
  link.download = state.name;
  document.body.append(link);
  link.click();
  link.remove();
});
$('#file').addEventListener('change', event => choose(event.target.files[0]));
// A new note size re-engraves the MusicXML already chosen.
$('#size').addEventListener('change', () => { if (state.file && XML_NAME.test(state.file.name)) choose(state.file); });
const drop = $('#drop');
drop.addEventListener('keydown', event => { if (event.key === 'Enter' || event.key === ' ') $('#file').click(); });
drop.addEventListener('dragover', event => { event.preventDefault(); drop.classList.add('drag'); });
drop.addEventListener('dragleave', () => drop.classList.remove('drag'));
drop.addEventListener('drop', event => {
  event.preventDefault();
  drop.classList.remove('drag');
  choose(event.dataTransfer.files[0]);
});
checkAvailability();
