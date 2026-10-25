'use strict';
// Camera luminance → Echo's planet ramp. No model or external service in the browser.
const $ = id => document.getElementById(id);
const ramp = ' .:;+*#%@';
const palette = ['#09090d','#3a2675','#5c37c4','#844ff0','#a56bff','#a675ff','#cba8ff','#c6f35a'];
const reduce = matchMedia('(prefers-reduced-motion: reduce)');
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let state = null, connected = false, timer = 0, fetching = false, dirty = false;
let frameImage = null, frameURL = null, asciiStill = false, voiceState = 'listening';
let lastDocument = '', lastQuestions = '', cameraTimer = 0;
function link(ok) {
  connected = ok;
  $('link').textContent = ok ? '● live link' : '↻ reconnecting';
  $('link').classList.toggle('offline', !ok);
}
function render(s) {
  state = s;
  const steps = s.procedures?.[s.procedure]?.steps || [];
  const session = s.sessions?.[s.session_id];
  const teaching = s.mode === 'teach';
  $('command').textContent = [s.mode, s.procedure].filter(Boolean).join(' ');
  $('hero').textContent = s.hero?.text || 'Waiting for the session.';
  $('hero').className = ['asking','correcting'].includes(s.hero?.kind) ? s.hero.kind : '';
  $('speech-label').textContent = `voice / ${s.hero?.kind || 'waiting'}${s.hero?.kind === 'asking' ? ' · needs you' : ''}`;
  voiceState = s.voice_state || 'listening';
  $('voice-state').textContent = voiceState;
  $('observed').textContent = s.observed?.text || 'Not captured.';
  $('procedure-title').textContent = s.procedure || 'No procedure yet.';
  $('revision').textContent = s.procedures?.[s.procedure] ? `r${s.procedures[s.procedure].rev}` : '—';
  $('session-id').textContent = s.session_id || '';
  const since = session?.started ?? Infinity;
  const learned = steps.filter(st => st.created >= since).length;
  const reasons = steps.flatMap(st => st.whys || []).filter(w => w.t >= since).length;
  const mistakes = session?.mistakes?.length || 0;
  const done = new Set(session?.done_steps || []);
  $('counters').innerHTML = [[learned,'steps learned'],[reasons,'reasons saved'],[mistakes,'mistakes caught']].map(([n,label])=>`<div class="counter"><b>${String(n).padStart(2,'0')}</b><span>${label}</span></div>`).join('');
  $('progress-label').textContent = teaching ? `${done.size} / ${steps.length} done` : `${steps.length} steps`;
  $('progress').hidden = !teaching;
  $('progress').firstElementChild.style.width = `${steps.length ? done.size / steps.length * 100 : 0}%`;
  const documentKey = JSON.stringify([steps,teaching,s.teach_index,[...done]]);
  if (documentKey !== lastDocument) {
    lastDocument = documentKey;
    $('steps').innerHTML = steps.length ? steps.map((st,i) => {
      const status = teaching ? done.has(st.id) ? 'done' : i === s.teach_index ? 'current' : 'upcoming' : '';
      return `<li class="step ${status}" ${status === 'current' ? 'aria-current="step"' : ''}><span class="number">${String(st.n).padStart(2,'0')}</span><div><h3>${esc(st.title || st.id)}</h3>${st.action ? `<p class="action">${esc(st.action)}</p>` : ''}${!st.confirmed ? '<span class="tag">unconfirmed</span>' : ''}${st.cue ? `<p class="cue">Look for / ${esc(st.cue)}</p>` : ''}${(st.whys || []).map(w => `<blockquote>“${esc(w.why)}”</blockquote><p class="source">${esc(w.source || 'expert')}${!w.confirmed ? ' · unconfirmed' : ''}</p>${w.consequence ? `<p class="consequence"><span>If skipped</span>${esc(w.consequence)}</p>` : ''}`).join('')}${(st.warnings || []).map(w=>`<p class="warning"><span class="tag">common mistake</span> ${esc(w)}</p>`).join('')}</div><div class="step-media">${st.frame_id ? `<a href="/api/frames/${encodeURIComponent(st.frame_id)}.jpg" aria-label="Open keyframe for step ${st.n}"><img class="keyframe" src="/api/frames/${encodeURIComponent(st.frame_id)}.jpg" alt="Step ${st.n} keyframe"></a>` : ''}<span class="status">${status || 'captured'}</span></div></li>`;
    }).join('') : '<li class="empty">No steps captured.</li>';
  }
  const questions = (s.open_questions || []).filter(q => !q.procedure || q.procedure === s.procedure);
  const pending = s.pending_question;
  const questionKey = JSON.stringify([questions,pending]);
  if (questionKey !== lastQuestions) {
    lastQuestions = questionKey;
    $('question-count').textContent = questions.length + (pending && !questions.some(q=>q.question_id === pending.question_id) ? 1 : 0);
    $('questions').innerHTML = (pending ? `<p class="question pending">↳ ${esc(pending.question || pending.text || pending.action || '')}</p>` : '') + questions.filter(q=>q.question_id !== pending?.question_id).map(q=>`<p class="question">↳ ${esc(q.question)}</p>`).join('') || '<p>No open questions.</p>';
  }
  const latency = [...(s.notes || [])].reverse().find(n=> /\bin \d+(?:\.\d+)?s\b/.test(n.text || ''));
  $('latency').textContent = latency ? `latency / ${latency.text}` : 'latency / not reported';
  drawOrb(reduce.matches ? 0 : performance.now() / 1000);
}
async function fetchState() {
  if (fetching) { dirty = true; return; }
  fetching = true;
  try {
    const response = await fetch('/api/state', {cache:'no-store',signal:AbortSignal.timeout(5000)});
    if (!response.ok) throw Error(response.status);
    render(await response.json()); link(true);
  } catch { link(false); }
  finally { fetching = false; if (dirty) { dirty = false; scheduleState(); } }
}
function scheduleState() {
  if (!timer) timer = setTimeout(()=>{timer=0;fetchState();},200);
}
const events = new EventSource('/api/events');
events.onmessage = scheduleState;
events.addEventListener('snapshot',scheduleState);
events.onopen = scheduleState;
events.onerror = ()=>link(false);
fetchState();
setInterval(()=>{if(!document.hidden) scheduleState();},5000);
async function health() {
  if (document.hidden) return;
  try {
    const response = await fetch('/api/ops/status',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}',signal:AbortSignal.timeout(5000)});
    if (!response.ok) throw Error(response.status);
    const camera = (await response.json()).result.camera;
    const age = camera.latest_age_s;
    $('camera-health').textContent = `camera / ${camera.source}${age == null ? ' · no frame' : ` · ${age}s ago`}`;
  } catch { $('camera-health').textContent = 'camera / status unavailable'; }
}
health(); setInterval(health,5000);
// Fetch each JPEG once; both views use the exact same decoded image.
async function loadFrame() {
  if (document.hidden) { cameraTimer=setTimeout(loadFrame,1000); return; }
  let nextURL;
  try {
    const response = await fetch(`/api/frame/latest.jpg?t=${Date.now()}`,{cache:'no-store',signal:AbortSignal.timeout(5000)});
    if (!response.ok) throw Error(response.status);
    nextURL = URL.createObjectURL(await response.blob());
    const image = new Image(); image.src = nextURL; await image.decode();
    frameImage=image;
    $('camera').src=nextURL; $('camera').hidden=false;
    $('camera-empty').hidden=true; $('ascii-empty').hidden=true;
    if (!reduce.matches || !asciiStill) { drawAscii(); asciiStill=true; }
    if(frameURL) URL.revokeObjectURL(frameURL);
    frameURL=nextURL;
  } catch {
    if(nextURL) URL.revokeObjectURL(nextURL);
    // Retain the last good frame during a link interruption; health reports its age.
    if(!frameImage) { $('camera-empty').hidden=false; $('ascii-empty').hidden=false; }
  } finally { cameraTimer=setTimeout(loadFrame,500); }
}
const sample=document.createElement('canvas'), sx=sample.getContext('2d',{willReadFrequently:true});
const atlas=document.createElement('canvas'), ax=atlas.getContext('2d');
let atlasKey='', tileW=0,tileH=0;
function drawAscii() {
  if(!frameImage) return;
  const cv=$('ascii'), box=cv.getBoundingClientRect();
  if(!box.width || !box.height) return;
  const dpr=Math.min(devicePixelRatio || 1,2);
  const cols=box.width<250 ? 54 : 90;
  const rows=Math.round(cols*box.height/box.width/1.75);
  sample.width=cols; sample.height=rows;
  sx.fillStyle='#000000'; sx.fillRect(0,0,cols,rows);
  const scale=Math.min(cols/frameImage.width,rows*1.75/frameImage.height);
  const w=frameImage.width*scale,h=frameImage.height*scale/1.75;
  sx.drawImage(frameImage,(cols-w)/2,(rows-h)/2,w,h);
  const pixels=sx.getImageData(0,0,cols,rows).data;
  cv.width=Math.round(box.width*dpr); cv.height=Math.round(box.height*dpr);
  const ctx=cv.getContext('2d'), cw=cv.width/cols,ch=cv.height/rows;
  const key=`${cv.width}/${cv.height}/${cols}`;
  if(atlasKey!==key) {
    atlasKey=key;tileW=Math.ceil(cw)+2;tileH=Math.ceil(ch)+2;
    atlas.width=tileW*ramp.length;atlas.height=tileH*palette.length;
    ax.font=`${cw/0.6}px "JetBrains Mono", monospace`;ax.textBaseline='top';
    for(let p=1;p<palette.length;p++) {
      ax.fillStyle=palette[p];
      for(let g=1;g<ramp.length;g++) ax.fillText(ramp[g],g*tileW,p*tileH);
    }
  }
  ctx.fillStyle='#09090d';ctx.fillRect(0,0,cv.width,cv.height);
  for(let y=0;y<rows;y++) for(let x=0;x<cols;x++) {
    const k=(y*cols+x)*4;
    const luminance=(pixels[k]*.2126+pixels[k+1]*.7152+pixels[k+2]*.0722)/255;
    const value=Math.min(1,Math.pow(luminance,.65)*1.55);
    const g=Math.round(value*(ramp.length-1)),p=luminance>.82 ? 7 : Math.max(1,Math.round(value*6));
    if(g) ctx.drawImage(atlas,g*tileW,p*tileH,tileW,tileH,Math.round(x*cw),Math.round(y*ch),tileW,tileH);
  }
}
// A small ASCII planet: the listening ring opens, hearing ripples, thinking
// orbits, and speaking pulses. These react only to the service's voice state.
function drawOrb(t) {
  const cv=$('orb'),ctx=cv.getContext('2d');
  ctx.clearRect(0,0,cv.width,cv.height);ctx.font='9px "JetBrains Mono",monospace';ctx.textBaseline='top';
  const activity={listening:.08,hearing:.27,thinking:.16,speaking:.38}[voiceState] ?? .08;
  for(let r=0;r<13;r++) for(let c=0;c<39;c++) {
    const x=(c-19)/12,y=(r-6)/5;
    const dist=x*x+y*y;
    const ring=Math.abs((x*x/2.15+(y+x*.23)**2/0.14)-1)<.16;
    let b=0;
    if(dist<.7) b=(Math.sqrt(.7-dist)*.6+.2+activity*Math.sin(c*.5+r*.3-t*(voiceState==='thinking'?2:5)));
    if(ring) b=.45+activity*Math.sin(t*4+c*.2);
    if(b>0) {ctx.fillStyle=palette[Math.max(1,Math.min(6,Math.round(b*6)))];ctx.fillText(ramp[Math.max(1,Math.min(8,Math.round(b*8)))],c*6+8,r*8);}
  }
}
let lastTick=0, orbVisible=true;
new IntersectionObserver(([entry])=>{orbVisible=entry.isIntersecting;}).observe($('orb'));
function tick(now) {
  if(!document.hidden && !reduce.matches && orbVisible && now-lastTick>1000/30-2) {lastTick=now;drawOrb(now/1000);}
  requestAnimationFrame(tick);
}
requestAnimationFrame(tick);
new ResizeObserver(()=>{atlasKey='';drawAscii();}).observe($('ascii'));
reduce.addEventListener('change',()=>{asciiStill=false;drawAscii();drawOrb(0);});
document.fonts.ready.then(()=>{atlasKey='';drawAscii();drawOrb(0);});
document.addEventListener('visibilitychange',()=>{if(!document.hidden){scheduleState();health();}});
loadFrame();
