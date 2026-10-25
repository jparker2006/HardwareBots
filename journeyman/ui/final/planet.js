// Echo's baked sphere, rings, glyph ramps and palette, with a voice-driven clock.
import {ASPECT, GLYPH_W, GLYPHS, PALETTE, createScene, renderFrame, STATIC_T} from './ascii.js';
export function startPlanet(canvas) {
  const ctx = canvas.getContext('2d');
  if (!ctx) return {setState(){},stop(){}};
  const atlas = document.createElement('canvas');
  const ink = atlas.getContext('2d');
  const reduce = matchMedia('(prefers-reduced-motion: reduce)');
  let scene, tileW, tileH, cellW, cellH, pad, raf = 0, last = 0, time = STATIC_T, state = 'listening', visible = true, stopped = false;
  function fit() {
    const width = canvas.clientWidth;
    const W = width < 250 ? 60 : 80, H = width < 250 ? 27 : 36;
    scene = createScene(W,H);
    const dpr = Math.min(devicePixelRatio || 1,2);
    cellW = width / W * dpr; cellH = cellW * ASPECT;
    canvas.width = Math.round(width*dpr); canvas.height = Math.round(cellH*H);
    pad = Math.ceil(cellW*.3); tileW = Math.ceil(cellW)+pad*2; tileH = Math.ceil(cellH)+pad*2;
    atlas.width = tileW*GLYPHS.length; atlas.height = tileH*PALETTE.length;
    ink.font = `${cellW/GLYPH_W}px "JetBrains Mono", monospace`;
    ink.textBaseline = 'alphabetic';
    for(let tone=1;tone<PALETTE.length;tone++) {
      ink.fillStyle=PALETTE[tone];
      for(let g=1;g<GLYPHS.length;g++) ink.fillText(GLYPHS[g],g*tileW+pad,tone*tileH+pad+cellH*.79);
    }
    draw();
  }
  function draw() {
    if (!scene) return;
    renderFrame(scene,reduce.matches ? STATIC_T : time);
    ctx.clearRect(0,0,canvas.width,canvas.height);
    for(let k=0;k<scene.chars.length;k++) {
      const g=scene.chars[k],tone=scene.tones[k];
      if (!g || !tone) continue;
      ctx.drawImage(atlas,g*tileW,tone*tileH,tileW,tileH,Math.round(k%scene.W*cellW)-pad,Math.round(Math.floor(k/scene.W)*cellH)-pad,tileW,tileH);
    }
    // No audio amplitude is invented: movement encodes the service voice state.
    const pulse = reduce.matches ? 1 : state === 'speaking' ? 1+.035*Math.sin(time*5) : state === 'hearing' ? 1+.02*Math.sin(time*7) : 1;
    canvas.style.transform = `scale(${pulse})`;
    canvas.style.filter = state === 'thinking' ? 'brightness(1.2)' : state === 'hearing' ? 'brightness(1.35)' : 'none';
  }
  function tick(now) {
    raf = requestAnimationFrame(tick);
    if(now-last<1000/30-3) return;
    const speed = {listening:.4,hearing:1.4,thinking:2.6,speaking:1.1}[state] || .4;
    time += Math.min((now-last)/1000,.1)*speed; last=now; draw();
  }
  function stopLoop() {cancelAnimationFrame(raf);raf=0;}
  function resume() {stopLoop(); if(!stopped && visible && !document.hidden && !reduce.matches) {last=performance.now();raf=requestAnimationFrame(tick);}else draw();}
  const resize = new ResizeObserver(fit); resize.observe(canvas);
  const observer = new IntersectionObserver(([entry]) => {visible=entry.isIntersecting;resume();}); observer.observe(canvas);
  document.fonts.ready.then(() => { if(!stopped) {fit();resume();} });
  document.addEventListener('visibilitychange',resume);reduce.addEventListener('change',resume);
  return {setState(next) { if(state!==next){state=next;draw();} },stop() {stopped=true;stopLoop();resize.disconnect();observer.disconnect();document.removeEventListener('visibilitychange',resume);reduce.removeEventListener('change',resume);}};
}
