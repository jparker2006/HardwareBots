import {startPlanet} from './planet.js';
const $ = id => document.getElementById(id);
const planet = startPlanet($('ascii'));
const escape = text => String(text ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const text = (id, value) => { if ($(id).textContent !== String(value)) $(id).textContent = value; };
const markup = (id, value) => { if ($(id)._markup !== value) { $(id).innerHTML = value; $(id)._markup = value; } };
let state, streamOnline = false, lastSuccess = 0, frameBusy = false;
function linkStatus() {
  const fresh = Date.now() - lastSuccess < 16000;
  text('link', !fresh ? 'link · offline / retrying' : streamOnline ? 'link · live' : 'link · reconnecting');
  $('link').classList.toggle('offline', !fresh);
}
function render(s) {
  state = s;
  const proc = s.procedures?.[s.procedure];
  const steps = proc?.steps || [];
  const session = s.sessions?.[s.session_id];
  const done = new Set(session?.done_steps || []);
  const teaching = s.mode === 'teach';
  text('command', [s.mode, s.procedure].filter(Boolean).join(' '));
  text('headline', s.hero?.text || s.pending_question?.text || 'Waiting for speech.');
  const kind = s.hero?.kind || (s.pending_question ? 'asking' : 'instructing');
  $('headline').className = kind;
  text('kind', s.hero || s.pending_question ? kind : 'Awaiting speech');
  $('needs').hidden = !(kind === 'asking' || s.pending_question);
  text('voice', `voice · ${s.voice_state || '—'}`);
  text('planet-state', s.voice_state || '—');
  planet.setState(s.voice_state);
  text('session', session ? `session / ${session.id}` : '');
  text('procedure-name', s.procedure || '—');
  text('revision', proc ? `rev ${proc.rev}` : 'rev —');
  markup('progress-label', teaching && steps[s.teach_index] ? `<a href="#step-${escape(steps[s.teach_index].id)}" aria-label="Jump to current step">${done.size} / ${steps.length} done ↘</a>` : `${steps.length} steps`);
  $('progress').hidden = !teaching;
  $('progress').max = Math.max(1, steps.length);
  $('progress').value = done.size;
  markup('steps', steps.length ? steps.map((step, i) => {
    const status = teaching ? done.has(step.id) ? 'done' : i === s.teach_index ? 'current' : i < s.teach_index ? 'skipped' : 'upcoming' : '';
    const tags = [status ? `<span class="tag state-tag">${status}</span>` : '', !step.confirmed ? '<span class="tag">unconfirmed</span>' : ''].join('');
    const figure = step.frame_id ? `<figure class="keyframe"><a href="/api/frames/${encodeURIComponent(step.frame_id)}.jpg" aria-label="Open keyframe for step ${escape(step.n)}"><img src="/api/frames/${encodeURIComponent(step.frame_id)}.jpg" alt="Keyframe for ${escape(step.title || step.id)}"></a><figcaption>FIG. ${String(step.n).padStart(2,'0')} / ${escape(step.frame_id)}</figcaption></figure>` : '<div class="keyframe mono">Keyframe · not captured</div>';
    const whys = (step.whys || []).map(why => `<figure class="reason"><blockquote>${escape(why.why)}</blockquote><figcaption>${escape(why.source || '—')} · ${why.confirmed ? 'confirmed' : 'unconfirmed'}</figcaption></figure>${why.consequence ? `<p class="consequence"><b>If skipped</b>${escape(why.consequence)}</p>` : ''}`).join('');
    return `<section class="step ${status}" id="step-${escape(step.id)}" ${status === 'current' ? 'aria-current="step"' : ''}><header class="step-head"><span class="step-number">${status === 'done' ? '✓' : String(step.n).padStart(2,'0')}</span><h3>${escape(step.title || step.id)}</h3><div class="tags">${tags}</div></header><div class="step-body"><div>${step.action ? `<p class="action">${escape(step.action)}</p>` : ''}${step.cue ? `<p class="cue"><b>Look for</b>${escape(step.cue)}</p>` : ''}${whys}${(step.warnings || []).map(w => `<p class="warning"><span class="tag">common mistake</span>${escape(w)}</p>`).join('')}</div>${figure}</div></section>`;
  }).join('') : '<p class="empty">Waiting for steps.</p>');
  text('observed', `Observed: ${s.observed?.text || '—'}`);
  // Count only knowledge added during this session, not the whole procedure.
  text('learned-steps', session ? steps.filter(st => st.created >= session.started).length : 0);
  text('learned-reasons', session ? steps.flatMap(st => st.whys || []).filter(w => w.t >= session.started).length : 0);
  text('learned-mistakes', session?.mistakes?.length || 0);
  const questions = (s.open_questions || []).filter(q => !q.procedure || q.procedure === s.procedure);
  const pending = s.pending_question;
  text('question-count', `${questions.length + (pending && !questions.some(q => q.question_id === pending.question_id) ? 1 : 0)} open`);
  markup('question-list', (pending ? `<p class="question pending">${escape(pending.text)}<small>needs you · awaiting answer</small></p>` : '') + questions.filter(q => q.question_id !== pending?.question_id).map(q => `<p class="question">${escape(q.question || q.text)}<small>not captured · ${escape(q.status || 'open')}</small></p>`).join('') || '<p class="empty">No open questions.</p>');
  const latency = [...(s.notes || [])].reverse().find(n => /\bin\s+[\d.]+s\b/.test(n.text || ''));
  const seconds = latency?.text.match(/\bin\s+([\d.]+s)\b/)?.[1];
  text('latency', `latency · ${seconds ? seconds + ' / observation' : '—'}`);
  $('latency').title = latency?.text || 'No latency recorded';
}
let busy = false, queued = false, timer = 0, lastFetch = 0;
async function refresh() {
  if (busy) { queued = true; return; }
  busy = true; lastFetch = Date.now();
  try {
    const response = await fetch('/api/state', {cache:'no-store', signal:AbortSignal.timeout(7000)});
    if (!response.ok) throw new Error(response.status);
    const snapshot = await response.json();
    render(snapshot); lastSuccess = Date.now();
  } catch { /* Retain the last good document while the link recovers. */ }
  finally { busy = false; linkStatus(); if (queued) { queued = false; schedule(); } }
}
function schedule() {
  if (timer) return;
  timer = setTimeout(() => { timer = 0; refresh(); }, Math.max(0, 250 - (Date.now() - lastFetch)));
}
async function health() {
  try {
    const response = await fetch('/api/ops/status', {method:'POST', headers:{'Content-Type':'application/json'}, body:'{}',signal:AbortSignal.timeout(7000)});
    if (!response.ok) throw new Error(response.status);
    const {result} = await response.json();
    const camera = result.camera;
    text('camera-health', `camera · ${camera.source}${camera.latest_age_s == null ? '' : ' / '+Math.round(camera.latest_age_s)+'s ago'}`);
    text('frame-count', `${camera.frames_kept} frames`);
  } catch { text('camera-health', 'camera · status unavailable'); }
}
async function cameraFrame() {
  if (frameBusy || document.hidden) return;
  frameBusy = true;
  const image = $('live-camera');
  const probe = new Image();
  probe.onload = () => {
    image.src = probe.src; image.hidden = false; $('camera-empty').hidden = true; frameBusy = false;
  };
  probe.onerror = () => { frameBusy = false; image.hidden = true; $('camera-empty').hidden = false; text('camera-empty', 'Camera frame unavailable'); };
  probe.src = `/api/frame/latest.jpg?t=${Date.now()}`;
}
const events = new EventSource('/api/events');
events.onopen = () => { streamOnline = true; schedule(); linkStatus(); };
events.onmessage = schedule;
events.addEventListener('snapshot', schedule);
events.onerror = () => { streamOnline = false; linkStatus(); };
refresh(); health(); cameraFrame();
const poll = setInterval(() => { if (!document.hidden) { schedule(); health(); } }, 5000);
const frames = setInterval(cameraFrame, 1500);
document.addEventListener('visibilitychange', () => { if (!document.hidden) { schedule(); health(); cameraFrame(); } });
window.addEventListener('pagehide', () => { events.close(); clearInterval(poll); clearInterval(frames); planet.stop(); });
// Returning from a full-size keyframe must resume the live connection.
window.addEventListener('pageshow', event => { if (event.persisted) location.reload(); });
