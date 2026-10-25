import {startPlanet} from './planet.js';
const $ = id => document.getElementById(id);
const planet = startPlanet($('planet'));
let refreshing = false, refreshAgain = false;
const rows = new Map();
function render(s) {
  const teach = s.mode === 'teach';
  const session = s.sessions?.[s.session_id] || Object.values(s.sessions || {}).at(-1);
  const steps = s.procedures?.[s.procedure || session?.procedure]?.steps || [];
  const done = new Set(session?.done_steps || []);
  $('mode').textContent = ({capture:'WATCHING',teach:'TEACHING',idle:'READY'})[s.mode] || 'READY';
  // Never paraphrase, truncate or type out the spoken line: judges see it immediately.
  const spoken = s.hero?.text ?? s.said?.at(-1)?.text ?? '';
  $('hero').textContent = spoken;
  $('hero').className = `${s.hero?.kind || 'instructing'}${spoken.length > 120 ? ' long' : ''}`;
  const why = teach && steps[s.teach_index]?.whys?.at(-1)?.why;
  $('reason').hidden = !why;
  $('reason').textContent = why ? `— Jake: ${why}` : '';
  const voice = s.voice_state || 'listening';
  planet.setState(voice);
  $('voice-state').textContent = ['thinking','speaking'].includes(voice) ? voice : 'listening';
  // A moving six-step window keeps the current instruction visible in long procedures.
  const current = teach ? s.teach_index : steps.length - 1;
  const start = Math.max(0, Math.min(current - 2, steps.length - 6));
  const visible = steps.slice(start, start + 6);
  $('steps').classList.toggle('dense', visible.length > 4);
  const keep = new Set(visible.map(st => st.id));
  for (const [id, row] of rows) if (!keep.has(id)) { row.remove(); rows.delete(id); }
  for (const st of visible) {
    let row = rows.get(st.id);
    if (!row) {
      row = document.createElement('li');
      const number = document.createElement('span'); number.className = 'number';
      const title = document.createElement('span'); title.className = 'title';
      row.append(number, title); rows.set(st.id, row);
    }
    const completed = done.has(st.id);
    const active = (teach || s.mode === 'capture') && steps[current]?.id === st.id;
    row.className = `step${completed ? ' done' : active ? ' current' : ''}`;
    row.firstChild.textContent = completed ? '✓' : String(st.n).padStart(2, '0');
    row.firstChild.setAttribute('aria-label', completed ? `Step ${st.n}, done` : `Step ${st.n}`);
    row.lastChild.textContent = st.title || '';
    if (active && !completed) row.setAttribute('aria-current', 'step'); else row.removeAttribute('aria-current');
    // Retain existing nodes so voice updates don't restart the arrival animation.
    const index = visible.indexOf(st);
    if ($('steps').children[index] !== row) $('steps').insertBefore(row, $('steps').children[index] || null);
  }
}
async function refresh() {
  if (refreshing) { refreshAgain = true; return; }
  refreshing = true;
  try {
    const r = await fetch('/api/state', {signal:AbortSignal.timeout(5000)});
    if (r.ok) render(await r.json());
  } catch { /* Retain the last snapshot while the local service reconnects. */ }
  finally { refreshing = false; if (refreshAgain) { refreshAgain = false; refresh(); } }
}
const events = new EventSource('/api/events');
events.onopen = refresh;
events.addEventListener('snapshot', refresh);
events.onmessage = refresh;
let frameURL = '';
async function camera() {
  let next = '';
  try {
    if (!document.hidden) {
      const r = await fetch('/api/frame/latest.jpg', {cache:'no-store',signal:AbortSignal.timeout(5000)});
      if (!r.ok) throw Error(r.status);
      next = URL.createObjectURL(await r.blob());
      const image = new Image(); image.src = next; await image.decode();
      $('camera').src = next; $('camera').hidden = false;
      $('no-camera').hidden = true; $('live').hidden = false;
      if (frameURL) URL.revokeObjectURL(frameURL);
      frameURL = next;
    }
  } catch { if (next) URL.revokeObjectURL(next); $('live').hidden = true; }
  finally { setTimeout(camera, 700); }
}
document.addEventListener('keydown', e => {
  if (e.key === '/' && !e.ctrlKey && !e.metaKey && !e.altKey && !e.target.closest('input,textarea,[contenteditable]')) {
    e.preventDefault(); if (!$('fallback').open) $('fallback').showModal(); $('say-text').focus();
  }
});
$('say').addEventListener('submit', async e => {
  e.preventDefault();
  const text = $('say-text').value.trim(); if (!text) return;
  const button = $('say').querySelector('button'); button.disabled = true;
  $('say-state').textContent = '';
  try {
    const r = await fetch('/api/headset/utterance', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text}),signal:AbortSignal.timeout(5000)});
    if (!r.ok) throw Error(r.status);
    $('say-text').value = ''; $('fallback').close(); refresh();
  } catch { $('say-state').textContent = 'Could not send. Try again.'; }
  finally { button.disabled = false; }
});
refresh(); camera();
setInterval(() => { if (!document.hidden) refresh(); }, 5000);
document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });
