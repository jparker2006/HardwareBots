import {startPlanet} from './planet.js';
const $ = id => document.getElementById(id);
const planet = startPlanet($('planet'));
let refreshing = false, refreshAgain = false;
const rows = new Map();
// Buttons say the same words as the voice commands, so a click and a spoken command take one path.
const ACTIONS = {
  idle:    [['● Start capture', 'Journeyman, start capture', 'say “Journeyman, start capture”'], ['Teach me ▶', 'Journeyman, teach me']],
  capture: [['■ I’m done', "Journeyman, I'm done", 'say “I’m done” to save it']],
  teach:   [['Next step →', 'next', 'say “next” · “I’m done” to finish'], ['■ End', "Journeyman, I'm done"]],
};
let skew = 0, clockFrom = null, waiting = null, lastMode = 'idle';
const clock = secs => `${Math.floor(secs / 60)}:${String(Math.floor(secs % 60)).padStart(2, '0')}`;
function tickTimer() { $('timer').textContent = clockFrom ? clock(Math.max(0, Date.now() / 1000 + skew - clockFrom)) : ''; }
setInterval(tickTimer, 1000);
function render(s) {
  const teach = s.mode === 'teach', capture = s.mode === 'capture', idle = !teach && !capture;
  lastMode = s.mode || 'idle';
  if (s.now) skew = s.now - Date.now() / 1000;
  const all = Object.values(s.sessions || {});
  const session = s.sessions?.[s.session_id] || all.at(-1);
  const steps = s.procedures?.[s.procedure || session?.procedure]?.steps || [];
  const done = new Set(session?.done_steps || []);
  const name = s.procedure || session?.procedure || '';
  const plural = (n, w) => `${n} ${w}${n === 1 ? '' : 's'}`;
  $('mode').textContent = capture ? '● RECORDING' : teach ? 'TEACHING' : 'READY';
  $('mode').className = s.mode || 'idle';
  // Session panel: what is happening, for how long, and how to stop it.
  $('badge').className = s.mode || 'idle';
  $('session-label').textContent = capture ? 'Recording' : teach ? 'Teaching' : session?.ended ? 'Session saved' : 'No session';
  clockFrom = idle ? null : session?.started;
  if (idle) $('timer').textContent = session?.ended ? clock(session.ended - session.started) : '';
  else tickTimer();
  $('session-sub').textContent = capture ? `${name} · ${s.expert || 'expert'} · ${plural(steps.length, 'step')} saved`
    : teach ? `${name} · step ${Math.min(s.teach_index + 1, steps.length)} of ${steps.length}`
    : session ? `${session.kind} · ${name} · ${plural(steps.length, 'step')}` : `${all.length ? '' : 'Show me a job once.'}`;
  $('empty').hidden = steps.length > 0 || idle;
  $('empty').textContent = 'Talk through each step. They land here.';
  const [primary, secondary] = ACTIONS[idle ? 'idle' : s.mode];
  for (const [id, action] of [['primary', primary], ['secondary', secondary]]) {
    const b = $(id);
    b.hidden = !action;
    if (!action) continue;
    b.textContent = action[0]; b.dataset.say = action[1];
    b.disabled = id === 'secondary' && idle && !steps.length;
  }
  $('primary').className = capture ? 'stop' : '';
  // Ending a capture reviews the last frames first; hold the button until the mode actually changes.
  if (waiting && waiting.mode === (s.mode || 'idle') && Date.now() < waiting.until) {
    $('primary').disabled = $('secondary').disabled = true;
    $('primary').textContent = capture ? 'Saving…' : teach ? 'One moment…' : 'Starting…';
  } else waiting = null;
  $('hint').textContent = `or ${primary[2]}`;
  // Never paraphrase, truncate or type out the spoken line: judges see it immediately.
  const spoken = s.hero?.text ?? s.said?.at(-1)?.text ?? '';
  $('hero').textContent = spoken || 'Say “Journeyman, start capture.”';
  $('hero').className = spoken ? `${s.hero?.kind || 'instructing'}${spoken.length > 120 ? ' long' : ''}` : 'prompt';
  const why = teach && steps[s.teach_index]?.whys?.at(-1)?.why;
  $('reason').hidden = !why;
  $('reason').textContent = why ? `— ${s.expert || 'expert'}: ${why}` : '';
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
    const active = (teach || capture) && steps[current]?.id === st.id;
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
async function send(text) {
  const r = await fetch('/api/headset/utterance', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text}),signal:AbortSignal.timeout(5000)});
  if (!r.ok) throw Error(r.status);
}
for (const id of ['primary', 'secondary']) $(id).addEventListener('click', async e => {
  const b = e.currentTarget, say = b.dataset.say;
  waiting = say === 'next' ? null : {mode: lastMode, until: Date.now() + 30000};
  b.disabled = true;
  try { await send(say); } catch { waiting = null; $('hint').textContent = 'Could not reach Journeyman. Try again.'; }
  finally { setTimeout(() => { b.disabled = false; refresh(); }, 600); }
});
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
    await send(text);
    $('say-text').value = ''; $('fallback').close(); refresh();
  } catch { $('say-state').textContent = 'Could not send. Try again.'; }
  finally { button.disabled = false; }
});
refresh(); camera();
setInterval(() => { if (!document.hidden) refresh(); }, 5000);
document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });
