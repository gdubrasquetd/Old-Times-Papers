// Onglet Corrections : toutes les propositions de correction, en une page.
'use strict';

const groupsEl = document.getElementById('groups');
const statusEl = document.getElementById('status');
const computeBtn = document.getElementById('btn-compute');
const editorFrame = document.getElementById('editor-frame');
const editorEmpty = document.getElementById('editor-empty');

const colorById = {};
for (const l of window.LABELS) colorById[l.id] = l.color;

// Ouvre une une dans l'éditeur de gauche (iframe), sans quitter l'onglet.
function openEditor(imageId, query = '') {
  editorEmpty.style.display = 'none';
  editorFrame.src = `/annotate/${imageId}${query}`;
}

// ── Calcul batch (toutes les unes 'done') ──
computeBtn.addEventListener('click', async () => {
  computeBtn.disabled = true;
  statusEl.textContent = 'Calcul en cours… (le modèle traite chaque une, ~1-2 min)';
  try {
    const r = await fetch('/api/review/compute-all', { method: 'POST' }).then(r => r.json());
    if (r.error) { statusEl.textContent = 'Erreur : ' + r.error; computeBtn.disabled = false; return; }
    await pollUntilDone();
    await loadProposals();
  } catch (e) {
    statusEl.textContent = 'Erreur : ' + e;
  } finally {
    computeBtn.disabled = false;
  }
});

async function pollUntilDone() {
  for (;;) {
    const s = await fetch('/api/review/status').then(r => r.json());
    if (!s.reviewing) { statusEl.textContent = s.message || 'terminé'; return; }
    statusEl.textContent = 'Calcul en cours… ' + (s.message || '');
    await new Promise(res => setTimeout(res, 2000));
  }
}

// ── Chargement + rendu ──
async function loadProposals() {
  const props = await fetch('/api/proposals/all').then(r => r.json());
  render(props);
}

function render(props) {
  groupsEl.innerHTML = '';
  if (!props.length) {
    groupsEl.innerHTML = '<div class="review-empty">Aucune proposition en attente. '
      + 'Lance « Calculer les corrections » (ou tout a déjà été traité 👍).</div>';
    statusEl.textContent = '0 proposition';
    return;
  }
  // regrouper par image
  const byImg = new Map();
  for (const p of props) {
    if (!byImg.has(p.image_id)) byImg.set(p.image_id, []);
    byImg.get(p.image_id).push(p);
  }
  statusEl.textContent = `${props.length} propositions sur ${byImg.size} unes`;
  for (const [imgId, list] of byImg) {
    const sec = document.createElement('section');
    sec.className = 'corr-group';
    sec.dataset.imgId = imgId;
    const g0 = list[0];
    sec.innerHTML = `<h3>${g0.journal} — ${g0.iso_date}
        <span class="corr-count">${list.length} proposition(s)</span>
        <span class="corr-open" role="button">ouvrir l'une ✎</span></h3>
      <div class="corr-cards"></div>`;
    sec.querySelector('.corr-open').addEventListener('click', () => openEditor(imgId));
    const cards = sec.querySelector('.corr-cards');
    for (const p of list) cards.appendChild(makeCard(p));
    groupsEl.appendChild(sec);
  }
}

function makeCard(p) {
  const pl = p.payload;
  const card = document.createElement('div');
  card.className = `review-card type-${p.ptype}`;
  card.dataset.pid = p.id;
  const badge = p.ptype === 'reclassify' ? 'reclasser' : 'titre';
  card.innerHTML = `
    <div class="rc-head">
      <span class="rc-badge">${badge}</span>
      <span class="rc-desc"></span>
      <span class="rc-conf">conf ${pl.conf ?? '—'}</span>
      <div class="rc-actions">
        <button class="ok act-accept">✅ Accepter</button>
        <button class="act-correct" style="background:#e9a23b;">✏️ Corriger</button>
        <button class="warn act-reject">❌ Refuser</button>
      </div>
    </div>
    <div class="rc-views">
      <div class="rc-view"><span>Actuel</span><canvas class="mini-before"></canvas></div>
      <div class="rc-view"><span>Proposé</span><canvas class="mini-after"></canvas></div>
    </div>`;
  card.querySelector('.rc-desc').textContent = p.descr;
  drawCrop(card.querySelector('.mini-before'), p.image_id, pl.region, pl.before, true);
  drawCrop(card.querySelector('.mini-after'), p.image_id, pl.region, pl.after, false);
  card.querySelector('.act-accept').addEventListener('click', () => act(p, 'accept', card));
  card.querySelector('.act-correct').addEventListener('click', () => act(p, 'correct', card));
  card.querySelector('.act-reject').addEventListener('click', () => act(p, 'reject', card));
  return card;
}

// Dessine un crop (servi par le serveur) + les boîtes avant/après.
function drawCrop(cv, imageId, region, boxes, dashed) {
  const [rx0, ry0, rx1, ry1] = region;
  const rw = Math.max(1, rx1 - rx0);
  const im = new Image();
  im.onload = () => {
    cv.width = im.width; cv.height = im.height;
    const c = cv.getContext('2d');
    c.drawImage(im, 0, 0);
    const scale = im.width / rw;
    for (const b of boxes) {
      const col = colorById[b.label_id] || '#fff';
      const x = (b.x0 - rx0) * scale, y = (b.y0 - ry0) * scale;
      const w = (b.x1 - b.x0) * scale, h = (b.y1 - b.y0) * scale;
      c.save();
      c.strokeStyle = col; c.lineWidth = 2.5;
      if (dashed) c.setLineDash([5, 3]);
      c.strokeRect(x, y, w, h);
      if (b.label_name) {
        c.setLineDash([]); c.font = '11px sans-serif';
        const tw = c.measureText(b.label_name).width;
        c.fillStyle = col; c.fillRect(x, Math.max(0, y - 13), tw + 6, 13);
        c.fillStyle = '#000'; c.fillText(b.label_name, x + 3, Math.max(9, y - 3));
      }
      c.restore();
    }
  };
  im.onerror = () => { cv.width = 120; cv.height = 60; };
  im.src = `/api/image/${imageId}/crop?box=${rx0},${ry0},${rx1},${ry1}&w=300`;
}

async function act(p, mode, card) {
  if (mode === 'reject') {
    await fetch(`/api/proposals/${p.id}/reject`, { method: 'POST' });
    removeCard(card);
    return;
  }
  const r = await fetch(`/api/proposals/${p.id}/apply`, { method: 'POST' }).then(r => r.json());
  if (r.error) { statusEl.textContent = r.error; return; }
  if (mode === 'correct') {
    // Ouvrir l'une DANS l'éditeur de gauche, zoomée sur la zone, boîte sélectionnée.
    const reg = p.payload.region.join(',');
    const sel = r.created.length ? `&sel=${r.created[0].id}` : '';
    openEditor(p.image_id, `?focus=${reg}${sel}`);
  }
  removeCard(card);
}

function removeCard(card) {
  const sec = card.closest('.corr-group');
  card.remove();
  const left = sec.querySelectorAll('.review-card').length;
  if (!left) { sec.remove(); }
  else { sec.querySelector('.corr-count').textContent = `${left} proposition(s)`; }
  const total = groupsEl.querySelectorAll('.review-card').length;
  statusEl.textContent = `${total} propositions restantes`;
  if (!total) render([]);
}

document.getElementById('btn-reject-all').addEventListener('click', async () => {
  if (!confirm('Refuser toutes les propositions en attente ?')) return;
  for (const card of [...groupsEl.querySelectorAll('.review-card')]) {
    await fetch(`/api/proposals/${card.dataset.pid}/reject`, { method: 'POST' });
    removeCard(card);
  }
});

// Au chargement : afficher ce qui est déjà en attente (sans recalculer).
loadProposals();
