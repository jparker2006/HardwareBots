'use strict';
const $ = id => document.getElementById(id);
const esc = value => String(value ?? '').replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
const reduced = matchMedia('(prefers-reduced-motion: reduce)');
let snapshot = null, cameraStatus = null, linked = false, feedOK = false;
let lastHero = '', typing = 0, lastLog = '', lastSteps = '', activeStep = '', refreshing = false, refreshAgain = false, refreshTimer = 0;
function setHTML(id, html) { if ($(id).innerHTML !== html) $(id).innerHTML = html; }
function hero(text, kind) {
  $('hero').setAttribute('aria-label', text);
  $('hero').className = `${kind || ''} ${text.length > 110 ? 'long' : ''}`;
  $('kind').className = kind || '';
  $('kind').textContent = kind === 'asking' ? 'needs you' : kind || 'waiting';
  if (text === lastHero) return;
  const initial = !lastHero;
  lastHero = text;
  clearInterval(typing);
  if (initial || reduced.matches || !text) { $('spoken').textContent = text; return; }
  const chars = Array.from(text); let n = Math.min(3, chars.length);
  $('spoken').textContent = chars.slice(0, n).join('');
  typing = setInterval(() => { n += Math.max(1, Math.ceil(chars.length / 60)); $('spoken').textContent = chars.slice(0, n).join(''); if (n >= chars.length) clearInterval(typing); }, 18);
}
function render(s) {
  snapshot = s;
  const sessions = Object.values(s.sessions || {});
  const sess = s.sessions?.[s.session_id] || sessions.at(-1);
  const procName = s.procedure || sess?.procedure;
  const proc = s.procedures?.[procName];
  const steps = proc?.steps || [];
  const teach = s.mode === 'teach';
  document.querySelector('.document-scroll').classList.toggle('teaching', teach);
  const done = new Set(teach ? sess?.done_steps || [] : []);
  $('command').textContent = [s.mode, procName].filter(Boolean).join(' ');
  $('session').textContent = sess ? `session / ${sess.id}` : 'session / —';
  $('doc-mode').textContent = s.mode;
  $('procedure-name').textContent = procName || 'No procedure';
  $('revision').textContent = proc ? teach ? `${done.size}/${steps.length} done · r${proc.rev}` : `${steps.length} steps · r${proc.rev}` : '—';
  $('voice-state').textContent = s.voice_state || 'unknown';
  hero(s.hero?.text || s.pending_question?.text || 'Waiting for spoken output', s.hero?.kind || (s.pending_question ? 'asking' : ''));
  $('observed').textContent = s.observed?.text || 'not captured';
  $('progress').hidden = !teach;
  $('progress-fill').style.width = `${steps.length ? done.size / steps.length * 100 : 0}%`;
  $('progress-label').textContent = `${done.size} / ${steps.length} done`;
  const stepHTML = steps.length ? steps.map((st, i) => {
    const status = done.has(st.id) ? 'done' : teach && i === s.teach_index ? 'current' : teach ? 'upcoming' : '';
    return `<article class="step ${status}" data-step="${esc(st.id)}"><div class="step-number">${String(st.n).padStart(2,'0')}${status === 'done' ? '✓' : ''}</div><div>${status ? `<div class="step-status">${status === 'current' ? '→ current' : status}</div>` : ''}<div class="step-head"><div><h4>${esc(st.title || st.id)}</h4>${!st.confirmed ? '<span class="tag">unconfirmed</span>' : ''}</div>${st.frame_id ? `<a href="/api/frames/${encodeURIComponent(st.frame_id)}.jpg" aria-label="Open keyframe for step ${st.n}"><img class="step-image" src="/api/frames/${encodeURIComponent(st.frame_id)}.jpg" alt="Step ${st.n} keyframe" loading="lazy"></a>` : '<span class="tag">no keyframe</span>'}</div>${st.action ? `<p>${esc(st.action)}</p>` : ''}${st.cue ? `<p class="cue"><b>look for</b>${esc(st.cue)}</p>` : ''}${(st.whys || []).map(w => `<blockquote>“${esc(w.why)}”</blockquote><div class="source">${esc(w.source || '')}${!w.confirmed ? ' · unconfirmed' : ''}</div>${w.consequence ? `<p class="consequence"><b>if skipped</b>${esc(w.consequence)}</p>` : ''}`).join('')}${(st.warnings || []).map(w => `<p class="warning"><span class="tag">common mistake</span> ${esc(w)}</p>`).join('')}</div></article>`;
  }).join('') : '<p class="empty">[ no steps captured ]</p>';
  if (lastSteps !== stepHTML) { $('steps').innerHTML = lastSteps = stepHTML; }
  // Scroll only the document pane; never move the phone's viewport on a live update.
  const cur = teach ? steps[s.teach_index]?.id : '';
  if (cur && cur !== activeStep && innerWidth > 1000) {
    const row = Array.from($('steps').children).find(el => el.dataset.step === cur);
    if (row) { const pane = document.querySelector('.document-scroll'); pane.scrollTop += row.getBoundingClientRect().top - pane.getBoundingClientRect().top; }
  }
  activeStep = cur;
  const questions = (s.open_questions || []).filter(q => !procName || !q.procedure || q.procedure === procName);
  $('question-count').textContent = String(questions.length).padStart(2,'0');
  setHTML('questions', questions.map(q => `<p>? ${esc(q.question)}</p>`).join('') || '<p class="empty">[ none ]</p>');
  const since = sess?.started ?? Infinity;
  const learned = steps.filter(st => st.created >= since).length;
  const reasons = steps.reduce((n, st) => n + (st.whys || []).filter(w => w.t >= since).length, 0);
  const open = (s.open_questions || []).filter(q => q.t >= since).length;
  $('learned-label').textContent = s.session_id ? 'learned this session' : sess ? 'learned last session' : 'learned this session';
  const counters = [[learned,'steps'],[reasons,'reasons'],[open,'open questions'],[sess?.mistakes?.length || 0,'slips caught']];
  setHTML('counts', counters.map(([n,label]) => `<div class="count"><b>${n}</b><span>${label}</span></div>`).join(''));
  const heard = (sess?.transcript || []).map(u => ({t:u.t1,text:u.text,jm:false}));
  const said = (s.said || []).filter(u => !sess || u.t >= sess.started).map(u => ({...u,jm:true}));
  const lines = [...heard,...said].sort((a,b) => a.t-b.t).slice(-80);
  const logHTML = lines.map(l => `<div class="log-line ${l.jm ? 'jm':'heard'}"><time>${esc(new Date(l.t * 1000).toLocaleTimeString([], {hour12:false,hour:'2-digit',minute:'2-digit',second:'2-digit'}))}</time><div><span class="speaker">${l.jm ? '&lt; journeyman' : '&gt; heard'}</span>${esc(l.text)}</div></div>`).join('') || '<p class="empty">[ no transcript ]</p>';
  if (lastLog !== logHTML) {
    const log = $('log'), follow = log.scrollHeight - log.scrollTop - log.clientHeight < 50 || !lastLog;
    log.innerHTML = lastLog = logHTML;
    if (follow) log.scrollTop = log.scrollHeight;
  }
  health();
}
function health() {
  $('link').textContent = linked ? 'link: live / SSE' : 'link: reconnecting';
  const parts = [];
  if (cameraStatus) {
    const age = cameraStatus.latest_age_s;
    parts.push(`camera: ${cameraStatus.source}${age == null ? '' : ` · ${age.toFixed(1)}s old`}`);
    $('feed-state').textContent = !feedOK ? 'no feed' : age == null ? 'frame received' : age >= 5 ? `stale / ${Math.round(age)}s` : 'live';
    $('feed-state').className = age >= 5 ? 'stale' : 'dim';
  } else { parts.push('camera: status unavailable'); $('feed-state').textContent = feedOK ? 'status unavailable' : 'no feed'; }
  const notes = snapshot?.notes || [];
  for (const [regex,label] of [[/^heard in ([\d.]+)s/,'ears'],[/^observed \d+ frames in ([\d.]+)s/,'vision']]) {
    const match = [...notes].reverse().map(n => regex.exec(n.text || '')).find(Boolean);
    if (match) parts.push(`${label}: ${match[1]}s`);
  }
  const warning = [...notes].reverse().find(n => ['warn','error'].includes(n.level) && Date.now()/1000 - n.t < 120);
  if (warning) parts.push(warning.text);
  $('health').textContent = parts.join(' · ');
}
async function refresh() {
  if (refreshing) { refreshAgain = true; return; }
  refreshing = true;
  try { const res = await fetch('/api/state', {signal:AbortSignal.timeout(5000)}); if (!res.ok) throw Error(res.status); const data = await res.json(); linked = events.readyState === EventSource.OPEN; render(data); }
  catch { linked = false; health(); }
  finally { refreshing = false; if (refreshAgain) { refreshAgain = false; schedule(); } }
}
function schedule() { if (!refreshTimer) refreshTimer = setTimeout(() => { refreshTimer = 0; refresh(); }, 180); }
const events = new EventSource('/api/events');
events.onopen = () => { linked = true; health(); schedule(); };
events.addEventListener('snapshot', schedule);
events.onmessage = schedule;
events.onerror = () => { linked = false; health(); };
async function pollCamera() {
  if (document.hidden) return;
  const image = new Image();
  image.onload = () => { $('camera').src = image.src; $('camera').hidden = false; $('no-camera').hidden = true; feedOK = true; health(); };
  image.onerror = () => { $('camera').hidden = true; $('no-camera').hidden = false; feedOK = false; health(); };
  image.src = `/api/frame/latest.jpg?t=${Date.now()}`;
}
async function pollStatus() {
  if (document.hidden) return;
  try { const r = await fetch('/api/ops/status', {method:'POST',headers:{'Content-Type':'application/json'},body:'{}',signal:AbortSignal.timeout(5000)}); if (!r.ok) throw Error(r.status); cameraStatus = (await r.json()).result.camera; }
  catch { cameraStatus = null; }
  health();
}
// Echo's shaded glyph vocabulary, on a small local canvas. No images or GPU.
const canvas = $('orb'), ctx = canvas.getContext('2d');
const ramp = ' .:;+*#%@', palette = ['#3a2675','#5c37c4','#844ff0','#a56bff','#cba8ff'];
let phase = 0, lastFrame = 0, orbVisible = true;
new IntersectionObserver(entries => { orbVisible = entries[0].isIntersecting; }).observe(canvas);
function drawOrb(t) {
  ctx.clearRect(0,0,448,224);
  ctx.font = '10px "JetBrains Mono",monospace';
  const state = snapshot?.voice_state || 'listening';
  const speed = {listening:.18,hearing:1.7,thinking:.8,speaking:2.3}[state] || .18;
  const pulse = state === 'hearing' || state === 'speaking' ? Math.sin(t*speed*3)*.07 : 0;
  for (let row=0;row<23;row++) for(let col=0;col<56;col++) {
    const x=(col-27.5)/15, y=(row-11)/7.8, r=1+pulse;
    const ring=Math.abs(Math.sqrt(x*x+Math.pow((y+x*.16)*3.7,2))-1.55)<.07;
    let glyph=' ', tone=0;
    if(x*x+y*y<r*r) {
      const z=Math.sqrt(r*r-x*x-y*y);
      const light=Math.max(0,(-x*.5-y*.5+z*.7)/r);
      const texture=.62+.22*Math.sin(y*12 + t*speed + Math.sin(x*7+t*speed)*1.4);
      const b=Math.min(.99,.12+light*texture);
      glyph=ramp[Math.max(1,Math.floor(b*(ramp.length-1)))];tone=Math.min(4,Math.floor(b*5));
    }
    if(ring && (y > -x*.16 || glyph === ' ')) { glyph=state==='thinking' ? '.-='[(col+Math.floor(t*5))%3]:'=';tone=3; }
    if(glyph!==' ') { ctx.fillStyle=palette[tone];ctx.fillText(glyph,col*8,row*9+12); }
  }
}
function tick(now) {
  requestAnimationFrame(tick);
  if (document.hidden || !orbVisible || now-lastFrame<33 || reduced.matches) return;
  phase+=Math.min((now-lastFrame)/1000,.1);lastFrame=now;drawOrb(phase);
}
reduced.addEventListener('change', () => drawOrb(0));
document.fonts.ready.then(() => drawOrb(0));
requestAnimationFrame(tick);
refresh(); pollCamera(); pollStatus();
setInterval(pollCamera,1000); setInterval(pollStatus,3000);
setInterval(() => { if (!document.hidden) refresh(); },10000);
document.addEventListener('visibilitychange', () => { if (!document.hidden) { refresh();pollCamera();pollStatus();drawOrb(phase); } });
