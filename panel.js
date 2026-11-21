/* Forked from the working phone panel. The one-card media and note interactions are retained. */
'use strict';
const $=s=>document.querySelector(s);
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const LETTERS='ABCDEFGHIJKLMNOPQRSTUVWXYZ', VIDS=['.mp4','.mov','.webm','.m4v'], PICS=['.png','.jpg','.jpeg','.webp','.gif'];
const ext=(m,list)=>list.some(x=>String(m).toLowerCase().split('?')[0].endsWith(x));
const kindOf=m=>ext(m,VIDS)?'video':ext(m,PICS)?'image':'link';
const srcOf=m=>'/media?p='+encodeURIComponent(m);
const safeDecode=t=>{try{return decodeURIComponent(t)}catch(e){return t}};
const nameOf=m=>safeDecode(String(m).split('/').pop()).replace(/\.[a-z0-9]+$/i,'').replace(/_+/g,' ');
const cleanTitle=t=>PanelPresentation.plain(t);
const NOTE={},NSENT={},NMSG={},NPENDING={},SLIDE_AT={},STEP={};
const DONE=new Set();let Q=null,CUR=null,PASS=[],SHOWN={id:null,at:Date.now()},ERR='',NOTE_TIMER,DECIDING=false,DOCK_OBSERVER,SLIDE_OBSERVER;
const PINNED_ID=null,isReadOnlyReport=()=>false;
const displayMedia=s=>s.media||[],comparisonContextHTML=s=>PanelPresentation.context(s),historyHTML=()=>'';
const PARENTS={};
function steps(){for(const s of Q?.items||[]){if(!(s.id in NOTE)&&s.saved_note){NOTE[s.id]=s.saved_note;NSENT[s.id]=s.saved_note;}if(!PASS.includes(s.id))PASS.push(s.id);if(!STEP[s.id]||s.id!==CUR)STEP[s.id]=s;}return PASS.filter(id=>STEP[id]&&!DONE.has(id)&&(Q.items.some(s=>s.id===id)||id===CUR)).map(id=>STEP[id]);}
function current(list){let i=list.findIndex(s=>s.id===CUR);if(i<0)i=0;CUR=list[i]?.id||null;return i;}
function topHTML(list,k){return `<div class="where"><div class="line"><b>${esc(list[k]?.project||'Review complete')}</b><span>${list.length} left</span></div></div>`;}
function leadSplit(what){
  const paras = String(what || '').trim().split(/\n\s*\n/), para = (paras[0] || '').replace(/\s+/g, ' ').trim();
  const m = para.match(/^[\s\S]*?[.!?](?=\s|$)(?:[\s\S]*?[.!?](?=\s|$))?/);
  const lead = (m ? m[0] : para).trim();
  return {lead, rest: [para.slice(lead.length).trim(), ...paras.slice(1)].filter(Boolean).join('\n\n')};
}
function recOption(it){
  const r = String(it.recommendation || '').toUpperCase();
  return (it.options || []).map(String).filter(o => r.startsWith(o.toUpperCase())).sort((a, b) => b.length - a.length)[0];
}

function slideHTML(m, k, n, label, eager){
  const kind = kindOf(m), src = srcOf(m), name = nameOf(m);
  const tag = `<div class="tag"><b>${esc(label || (n > 1 ? `${k + 1}/${n}` : ''))}</b>${esc(name)}</div>`;
  if (kind === 'video') return `${tag}<button type="button" class="fsbtn" data-fs aria-label="Play ${esc(name)} full screen">Full screen</button><a class="orig" href="${esc(src)}" target="_blank" rel="noopener">Original</a><video controls playsinline webkit-playsinline preload="none" fetchpriority="high" data-src="${esc(src)}" data-path="${esc(m)}"></video>`;
  if (kind === 'image') return `${tag}<button type="button" class="fsbtn" data-fs aria-label="Open ${esc(name)} full screen">Full screen</button><img alt="${esc(name)}" ${eager ? 'src' : 'data-src'}="${esc(src)}" fetchpriority="low" decoding="async" data-path="${esc(m)}" data-zoom="${esc(src)}">`;
  return `${tag}<div class="sheet"><a class="open" href="${esc(src)}" target="_blank" rel="noopener">Open ${esc(name || m)}</a></div>`;
}
function releaseVideos(){ for (const v of document.querySelectorAll('#app video')){ try { v.pause(); v.removeAttribute('src'); v.removeAttribute('poster'); v.load(); } catch(e){} } }
function releaseImages(){ for (const img of document.querySelectorAll('#app img')) if (img.getAttribute('src') && !img.complete){ img.removeAttribute('data-path'); img.src = ''; } }
let DOCABORT = new AbortController();

function optsHTML(s){
  const rec = recOption(s);
  return s.options.map(String).map((o, i) => {
    const own = /^[A-Z](?:$|[.): -])/.test(o);
    return `<button class="opt${o === rec ? ' rec' : ''}" data-pick="${esc(o)}">${own ? '' : `<span class="k">${LETTERS[i]}</span>`}<span class="t">${esc(o)}</span>${o === rec ? '<span class="r">Recommended</span>' : ''}</button>`;
  }).join('');
}


const RATE = {};
function parseBody(rest){
  const lines = String(rest || '').split('\n'), items = [], prose = [];
  for (const l of lines){ const m = l.match(/^\s*(\d{1,3})[.)]\s+(.+)/); if (m) items.push([m[1], m[2]]); else if (l.trim()) prose.push(l.trim()); }
  return items.length >= 3 ? {items, prose} : {items:[], prose:lines.filter(l => l.trim())};
}
function itemsHTML(s, items){
  const r = RATE[s.id] || {};
  return `<ol class="items">${items.map(([n, t]) => `<li><span class="n">${esc(n)}</span><span class="it">${esc(t)}</span>
      <span class="kc"><button data-rate="keep" data-n="${esc(n)}" class="${r[n] === 'keep' ? 'on' : ''}">Keep</button><button data-rate="cut" data-n="${esc(n)}" class="${r[n] === 'cut' ? 'on cut' : ''}">Cut</button></span></li>`).join('')}</ol>`;
}

function bodyHTML(s, rest, listOnStage){
  const {items, prose} = parseBody(rest);
  const text = prose.map(l => `<p>${esc(l)}</p>`).join('') + (items.length && !listOnStage ? itemsHTML(s, items) : '');
  return text ? `<div class="body">${text}</div>` : '';
}
function rateLine(id){
  const r = RATE[id] || {}, pick = v => Object.keys(r).filter(n => r[n] === v).sort((a, b) => a - b).join(' ');
  return [pick('keep') && 'keep ' + pick('keep'), pick('cut') && 'cut ' + pick('cut')].filter(Boolean).join(', ');
}
const noteText = id => [String(NOTE[id] || '').trim(), rateLine(id)].filter(Boolean).join(' | ');
function noteHTML(s){
  return `<div class="note"><textarea data-note="${esc(s.id)}" rows="2" placeholder="A thought, or what to change. It saves on its own and goes with your answer.">${esc(NOTE[s.id]||'')}</textarea>
    <div class="bar"><button data-mic>Speak a note</button><button data-savenote>Save note</button></div></div><div class="nmsg" data-nmsg="${esc(s.id)}"></div>`;
}
function moreHTML(s, rest){
  const ev = (s.evidence || []).map(e => `<li>${esc(e)}</li>`).join('');
  return `<details class="more"><summary>Recommendation and context</summary>${rest ? `<p>${esc(rest).replace(/\n/g, '<br>')}</p>` : ''}
    ${s.recommendation ? `<p><b>Recommended:</b> ${esc(s.recommendation)}</p>` : ''}
    <p class="mono">${esc(s.clipParent || s.id)}</p></details>`;
}
const docsFirst = list => list;


function reelHTML(s){

  const {lead, rest} = leadSplit(s.what), {items} = s.clipCount ? {items:[]} : parseBody(rest);
  s.abMode=s.media.length===2&&s.media.every(m=>kindOf(m)==="video");
  let media = s.clipCount ? (s.media || []).map(String) : docsFirst((s.media || []).map(String));

  const n = media.length + (items.length ? 1 : 0);
  const labels = s.clipCount === 2 ? media.map((_, i) => LETTERS[i]) : media.map((_, i) => n > 1 ? `${i + 1 + (items.length ? 1 : 0)}/${n}` : '');
  const listSlide = items.length ? `<div class="slide" data-i="0"><div class="tag"><b>1/${n}</b>The list to rate</div><div class="sheet listsheet" data-list>${itemsHTML(s, items)}</div></div>` : '';
  const off = items.length ? 1 : 0;
  const slides = n
    ? listSlide + media.map((m, i) => `<div class="slide" data-i="${i + off}">${slideHTML(m, i, n, labels[i])}</div>`).join('')
    : `<div class="slide"><p class="empty">No picture or video on this card. Everything is in the words.</p></div>`;
  const dots = n > 1 ? `<div class="dots">${Array.from({length:n}, (_, i) => `<i data-dot="${i}"></i>`).join('')}</div>` : '';
  const arrows = n > 1 ? `<button class="snav prev" data-snav="-1" aria-label="Previous picture">‹</button><button class="snav next" data-snav="1" aria-label="Next picture">›</button>` : '';
  const mediaNames = (items.length ? ['The list to rate'] : []).concat(media.map(nameOf));
  const btnLabels = (items.length ? ['List'] : []).concat(media.map((_, i) => (s.clipCount === 2 || s.abMode) ? 'Clip ' + LETTERS[i] : String(i + 1 + off)));
  const picker = n > 1 ? `<div class="media-picker" role="group" aria-label="Choose media">${mediaNames.map((name,i) => `<button type="button" data-media-btn="${i}" class="${i===0?'on':''}" aria-label="${esc(name)}">${esc(btnLabels[i])}</button>`).join('')}</div>` : '';
  return `<div class="reel" data-card="${esc(s.id)}" data-shown-sha256="${esc(s.card_sha256||'')}">
    ${PanelPresentation.concepts?.(s)||''}${comparisonContextHTML(s)}
    <section class="stage"><div class="slides" id="slides">${slides}</div>${dots}${arrows}${picker}</section>
    <section class="rail">
      <p class="kicker">${s.priority===1?'<span class="p1badge">P1 today</span> ':''}${esc(s.project)}</p>
      <h2>${esc(cleanTitle(s.title))}</h2>


      ${historyHTML(s,s.clipIndex||1)}
      <div class="review-controls"><p class="q">${esc(s.question)}</p>
      <div class="opts">${optsHTML(s)}</div>
      ${noteHTML(s)}
      <div class="minor"><button data-pick="UNCLEAR">Unclear, rewrite it</button></div>
      ${s.clipParent ? `<div class="minor"><button data-gridtoggle="${esc(s.clipParent)}" data-to="grid">See all ${s.clipCount} clips at once</button></div>` : ''}
      <div class="nav"><button data-go="-1">Back</button><button data-go="1">Skip for now</button></div>
      </div>${PanelPresentation.what(s)}${s.draft_text?`<section class="full-draft-text" aria-label="Full edition"><h3>Read the full edition</h3><div style="white-space:pre-wrap;line-height:1.6;overflow-wrap:anywhere">${esc(PanelPresentation.plain(s.draft_text))}</div></section>`:''}${PanelPresentation.mockups(s)}${PanelPresentation.sources(s)}${moreHTML(s, '')}
    </section></div>`;
}

function armVideo(v){ if (v.dataset.src && !v.getAttribute('src')){ v.preload = 'auto'; v.src = v.dataset.src; } }
function armSlide(slide){ if (!slide) return; for (const img of slide.querySelectorAll('img[data-src]')){ img.src = img.dataset.src; img.removeAttribute('data-src'); } }
function wireSlides(s){
  const box = $('#slides'); if (!box) return;
  const dots = [...document.querySelectorAll('[data-dot]')];
  const slideKey=JSON.stringify([s.id,s.card_sha256||'']);
  let width=box.clientWidth, activeIndex=Math.max(0,Math.min(box.querySelectorAll('.slide').length-1,SLIDE_AT[slideKey] ?? s.focusSlide ?? 0));
  const align=()=>{const nextWidth=box.clientWidth;if(nextWidth!==width){width=nextWidth;box.scrollLeft=activeIndex*Math.max(1,width);}};


  const at = i => box.querySelector(`.slide[data-i="${i}"]`);
  const mark = () => { align();const i = Math.round(box.scrollLeft / Math.max(1, box.clientWidth));activeIndex=i;SLIDE_AT[slideKey]=i; dots.forEach((d, j) => d.classList.toggle('on', j === i)); box.closest('.stage').querySelectorAll('[data-media-btn]').forEach(b=>b.classList.toggle('on', b.dataset.mediaBtn===String(i)));
    for (const v of box.querySelectorAll('video')){ if (v.closest('.slide').dataset.i == i) armVideo(v); else v.pause(); }
    const cur = at(i); if (!cur) return; armSlide(cur); fitStage(cur);

    const v = cur.querySelector('video');
    if (!v || v.readyState >= 2) armSlide(at(i + 1)); else v.addEventListener('loadeddata', () => armSlide(at(i + 1)), {once:true}); };

  box.selectSlide = value => {
    const i=Math.max(0,Math.min(box.children.length-1,Number(value)||0));

    activeIndex=i;
    width=box.clientWidth;
    box.style.scrollSnapType='none';
    box.style.scrollBehavior='auto';
    box.scrollLeft=i*width;
    mark();
    requestAnimationFrame(()=>{box.style.scrollSnapType='';mark();box.closest('.stage').scrollIntoView({block:'start',behavior:'auto'});});
  };
  box.addEventListener('scroll', () => requestAnimationFrame(mark), {passive:true});
  box.scrollLeft = activeIndex * box.clientWidth;
  mark();
  if(SLIDE_OBSERVER)SLIDE_OBSERVER.disconnect();
  SLIDE_OBSERVER=new ResizeObserver(()=>{align();mark();});SLIDE_OBSERVER.observe(box);
  const dock = document.querySelector('.review-controls'), reel=box.closest('.reel');
  if(dock && reel){ const size=()=>reel.style.setProperty('--dock-height', `${dock.getBoundingClientRect().height + 20}px`); if(DOCK_OBSERVER)DOCK_OBSERVER.disconnect(); DOCK_OBSERVER=new ResizeObserver(size); DOCK_OBSERVER.observe(dock); size(); }
}


function render(){
 const list=Q?steps():[],k=current(list),s=list[k];$('#top').innerHTML=topHTML(list,k);$('#err').textContent=ERR;
 if(!Q)return;
 releaseVideos();releaseImages();
 $('#app').innerHTML=s?reelHTML(s):`<div class="done"><h2>Nothing left.</h2><p>Your answers are saved.</p><a href="/history">Review history</a></div>`;
 if(s){if(SHOWN.id!==s.id)SHOWN={id:s.id,at:Date.now()};wireSlides(s);tallCached($('#app'));showMsg(s.id);}
}
const noteTarget = s => s.clipParent || s.id;
function showMsg(id){ const el = document.querySelector('[data-nmsg="'+CSS.escape(id)+'"]'); if (!el) return; const m = NMSG[id] || ''; el.textContent = m; el.classList.toggle('err', m.startsWith('Not saved')); }
async function saveNote(id){
  if (NPENDING[id]) { await NPENDING[id]; if(DECIDING)return true; return saveNote(id); }
  const s = STEP[id], text = noteText(id); if (!s || NSENT[id] === text || (!text && !NSENT[id])) return true;
  if(isReadOnlyReport(s))return true;
  NMSG[id] = 'Saving…'; showMsg(id);
  NPENDING[id] = (async () => {
    try {
      const payload={id:noteTarget(s),verdict:'NOTE',note:text};
      const singleVideo=(s.media||[]).length===1 && kindOf(s.media[0])==='video';
      if((s.clipParent && s.clipIndex)||singleVideo){
        const path=s.clipParent ? PARENTS[s.clipParent]?.clips[s.clipIndex-1] : s.media[0];
        const video=[...document.querySelectorAll('#app video')].find(v=>v.dataset.path===path);
        payload.clip_index=s.clipIndex||1;payload.t_seconds=Number(video?.currentTime)||0;
      }
      payload.shown_sha256=STEP[id]?.card_sha256;
      const r = await fetch('/verdict', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)});
      if (!r.ok){ const j = await r.json().catch(() => ({})); throw new Error(j.error || r.status); }
      NSENT[id] = text; NMSG[id] = `Saved ${new Date().toTimeString().slice(0, 5)}. The card stays open.`;
      return true;
    } catch(e){ NMSG[id] = 'Not saved: ' + e.message; return false; }
    finally { showMsg(id); }
  })();
  const saved = await NPENDING[id]; delete NPENDING[id]; return saved;
}
async function decide(id, verdict, btn){
  if(isReadOnlyReport(STEP[id]))return;
  const s = STEP[id]; if (!s || DECIDING || (PINNED_ID && id!==PINNED_ID)) return;
  DECIDING = true; clearTimeout(NOTE_TIMER);
  document.querySelectorAll('.review-controls button').forEach(b=>b.disabled=true);
  if(btn)btn.classList.add('saving');

  if(NPENDING[id])await NPENDING[id];
  NMSG[id]='Saving answer…'; showMsg(id);
  const elapsed = Math.max(0, Date.now() - (SHOWN.id === id ? SHOWN.at : Date.now()));
  const list = PINNED_ID ? [] : steps(), k = list.findIndex(x => x.id === id);
  const rest = list.slice(k + 1).concat(list.slice(0, Math.max(0, k)));
  const payload = {id:PINNED_ID ? id : noteTarget(s), verdict, note:noteText(id), elapsed_ms:elapsed};
  if (!PINNED_ID && s.clipParent) payload.clip_index = s.clipIndex;
  try {
    payload.shown_sha256=STEP[id]?.card_sha256;
      const r = await fetch('/verdict', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)});
    if (!r.ok){ const j = await r.json().catch(() => ({})); throw new Error(j.error || r.status); }
    ERR='';
    {
      NSENT[id]=noteText(id);DONE.add(id);
      if (CUR === id) CUR = rest.length ? rest[0].id : null;
      render(true); scrollTo(0,0);
    }
  } catch(e){
    CUR=id; ERR='Not saved: '+e.message; NMSG[id]=ERR;
    if(PINNED_ID){$('#err').textContent=ERR;showMsg(id);}
    else render(true);
  } finally {
    DECIDING=false;
    document.querySelectorAll('.review-controls button').forEach(b=>{b.disabled=false;b.classList.remove('saving');});
  }
}

async function go(d){if(DECIDING||MIC.busy)return;clearTimeout(NOTE_TIMER);if(CUR&&!(await saveNote(CUR)))return;const list=steps();if(!list.length)return;const i=list.findIndex(s=>s.id===CUR);CUR=list[(i+d+list.length)%list.length].id;render();scrollTo(0,0);}
const MIC = {rec:null, stream:null, busy:false};
async function mic(btn){
  const ta = document.querySelector('#app textarea[data-note]'); if (!ta) return;
  const id = ta.dataset.note;
  if (MIC.rec && MIC.rec.state === 'recording'){ MIC.rec.stop(); return; }
  if (!navigator.mediaDevices || !window.MediaRecorder){ NMSG[id] = 'Not saved: this browser cannot record. Type the note instead.'; return showMsg(id); }
  try {
    if(!window.MicOutbox)throw Error('Private audio retention is unavailable');
    await MicOutbox.ready;if(!MicOutbox.canRecord)throw Error('Keep retained recordings before starting another');
    NMSG[id]='Requesting microphone…';showMsg(id);
    const stream = await navigator.mediaDevices.getUserMedia({audio:true});
    const mime = ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4'].find(x => MediaRecorder.isTypeSupported(x)) || '';
    const rec = new MediaRecorder(stream, mime ? {mimeType:mime} : {}), parts = [];
    Object.assign(MIC, {rec, stream, busy:true});
    const started=new Date().toISOString(),capture=crypto.randomUUID(),shown=ta.closest('[data-shown-sha256]')?.dataset.shownSha256||'',context=MicOutbox.captureContext(()=>({card:id,shown_sha256:shown}));
    btn.classList.add('live'); btn.textContent = 'Stop and add';
    NMSG[id] = 'Listening…'; showMsg(id);
    rec.ondataavailable = e => { if (e.data.size) parts.push(e.data); };
    rec.onstop = async () => {
      for (const t of stream.getTracks()) t.stop();
      btn.classList.remove('live'); btn.textContent = 'Saving audio…'; btn.disabled = true;
      try {
        const blob = new Blob(parts, {type:rec.mimeType || mime || 'audio/webm'});
        NMSG[id]='Saving audio…';showMsg(id);
        await MicOutbox.enqueue(blob,{card:id,target:'one-shot note',capture_id:capture,capture_started_at:started,capture_ended_at:new Date().toISOString(),mic_session:capture,shown_sha256:shown,capture_context:context.finish()});
      } catch(e){ NMSG[id] = 'Not saved: ' + e.message; showMsg(id); }
      btn.disabled = false; btn.textContent = 'Speak a note'; MIC.rec = null; MIC.busy=false;
    };
    rec.start();
  } catch(e){ NMSG[id] = 'Not saved: microphone refused. ' + e.message; showMsg(id); }
}

const PHONE=700,BIG=w=>w>=1200,READ=0.55;
const PHONE_FIT = () => matchMedia('(max-width:800px)').matches;
let LB_Y = 0;
function openZoom(img){
  LB_Y = scrollY;
  $('#lb').innerHTML = `<button type="button" class="close">Close</button><img alt="${esc(img.alt||'')}" src="${esc(img.dataset.zoom)}">`;
  $('#lb').classList.add('on');
}
function closeZoom(){ const lb = $('#lb'); if (!lb.classList.contains('on')) return; lb.classList.remove('on'); lb.innerHTML = ''; scrollTo(0, LB_Y); }

function openFull(btn){
  const host = btn.closest('.slide, .cell'), img = host?.querySelector('img'), v = host?.querySelector('video');
  if (img){ img.dataset.zoom = img.dataset.zoom || img.currentSrc || img.src || img.dataset.src || ''; return openZoom(img); }
  if (!v) return; armVideo(v);
  if (v.requestFullscreen) v.requestFullscreen().catch(() => v.webkitEnterFullscreen?.());
  else v.webkitEnterFullscreen?.();
  v.play?.().catch(() => {});
}

function fitStage(slide){
  const stage = slide?.closest('.stage'); if (!stage) return;
  if (!PHONE_FIT()){ stage.style.removeProperty('--stage-h'); return; }
  const m = slide.querySelector('video, img'); if (!m || slide.querySelector('.tallpeek')){ stage.style.removeProperty('--stage-h'); return; }
  const w = m.videoWidth || m.naturalWidth, h = m.videoHeight || m.naturalHeight;
  if (!w || !h){ stage.style.removeProperty('--stage-h'); return; }
  const pick = stage.querySelector('.media-picker') ? 66 : 0, want = stage.clientWidth * h / w + pick;
  stage.style.setProperty('--stage-h', Math.round(Math.min(Math.max(want, 200), innerHeight * 0.56 + pick)) + 'px');
}

function fitCell(m){ const cell = m.closest?.('.cmp .cell'); if (!cell) return; const w = m.videoWidth || m.naturalWidth, h = m.videoHeight || m.naturalHeight; if (w && h) cell.style.setProperty('--ar', `${w}/${h}`); }
document.addEventListener('loadedmetadata', e => { const m = e.target; if (!m.matches?.('#app video')) return; fitCell(m); const sl = m.closest('.slide'); if (sl && sl.closest('.stage') && +sl.dataset.i === Math.round(sl.parentNode.scrollLeft / Math.max(1, sl.parentNode.clientWidth))) fitStage(sl); }, true);
document.addEventListener('load', e => { const m = e.target; if (!m.matches?.('#app img')) return; fitCell(m); const sl = m.closest('.slide'); if (sl && sl.closest('.stage') && +sl.dataset.i === Math.round(sl.parentNode.scrollLeft / Math.max(1, sl.parentNode.clientWidth))) fitStage(sl); }, true);
function markWide(img){

  if (PHONE_FIT()) return;
  const host = img.closest('.slide'); if (!host || innerWidth > PHONE || host.querySelector('.widebox, .tallbox')) return;
  const w = img.naturalWidth, h = img.naturalHeight; if (!w || !BIG(w) || h / w >= TALL) return;
  const scale = Math.max(READ, Math.min(1, host.clientHeight / h));
  const box = document.createElement('div'); box.className = 'widebox';
  img.style.setProperty('--ww', Math.round(w * scale) + 'px');
  img.replaceWith(box); box.appendChild(img);
  const hint = document.createElement('div'); hint.className = 'zoomhint'; hint.textContent = 'Drag to pan · tap for the whole picture'; host.appendChild(hint);
  const stage = host.closest('.stage'); if (stage) stage.classList.add('haspan');
}

const TALL = 2.4;
function makeTall(img){
  const host = img.closest('.slide, .cell'); if (!host || host.querySelector('.tallbox')) return;
  const w = img.naturalWidth, h = img.naturalHeight; if (!w || h / w < TALL) return;

  if (PHONE_FIT()){ if (!img.classList.contains('tallpeek')){ img.classList.add('tallpeek'); const n = document.createElement('div'); n.className = 'tallnote'; n.textContent = 'Tall page · tap to read it full screen'; host.appendChild(n); } return; }
  const box = document.createElement('div'); box.className = 'tallbox';
  img.style.setProperty('--w', w <= 800 ? Math.max(w, 420) + 'px' : '100%');
  img.removeAttribute('data-zoom');
  img.replaceWith(box); box.appendChild(img);
  const note = document.createElement('div'); note.className = 'tallnote'; host.appendChild(note);
  const count = () => { const n = Math.max(1, Math.ceil(box.scrollHeight / box.clientHeight)), i = Math.min(n, Math.floor(box.scrollTop / box.clientHeight) + 1); note.textContent = `Tall page · screen ${i} of ${n} · scroll`; };
  box.addEventListener('scroll', () => requestAnimationFrame(count), {passive:true}); count();
}
document.addEventListener('load', e => { const el = e.target; if (el.matches && el.matches('.slide img, .cell img')){ makeTall(el); markWide(el); } }, true);
function tallCached(root){ for (const img of root.querySelectorAll('.slide img, .cell img')) if (img.complete){ makeTall(img); markWide(img); } }

document.addEventListener('play',e=>{if(e.target.tagName==='VIDEO')for(const v of document.querySelectorAll('video'))if(v!==e.target)v.pause();},true);
document.addEventListener('visibilitychange',()=>{if(document.hidden)for(const v of document.querySelectorAll('video'))v.pause();});
document.addEventListener('click',e=>{
 const t=e.target,p=t.closest('[data-pick]');if(p)return decide(CUR,p.dataset.pick,p);
 const g=t.closest('[data-go]');if(g)return go(Number(g.dataset.go));
 const b=t.closest('[data-media-btn]');if(b)return $('#slides')?.selectSlide(b.dataset.mediaBtn);
 const sn=t.closest('[data-snav]');if(sn)return $('#slides').selectSlide(Math.round($('#slides').scrollLeft/$('#slides').clientWidth)+Number(sn.dataset.snav));
 if(t.closest('[data-savenote]'))return saveNote(CUR);
 const m=t.closest('[data-mic]');if(m)return mic(m);
 const f=t.closest('[data-fs]');if(f)return openFull(f);
 const z=t.closest('[data-zoom]');if(z)return openZoom(z);
 if(t.closest('#lb'))closeZoom();
});
document.addEventListener('input',e=>{const ta=e.target.closest('textarea[data-note]');if(ta){NOTE[ta.dataset.note]=ta.value;clearTimeout(NOTE_TIMER);NOTE_TIMER=setTimeout(()=>saveNote(ta.dataset.note),650);}});
window.addEventListener('micoutbox:saved',e=>{const {card,text}=e.detail;if(text){NOTE[card]=[NOTE[card],text].filter(Boolean).join(' ');const ta=document.querySelector('textarea[data-note]');if(ta?.dataset.note===card)ta.value=NOTE[card];}NMSG[card]='Voice note saved.';showMsg(card);});
window.addEventListener('voiceguard:stop',()=>{if(MIC.rec?.state==='recording')MIC.rec.stop();});
window.panelHasUnsavedReviewText=()=>Object.keys(NOTE).some(id=>NOTE[id]&&NOTE[id]!==NSENT[id]&&!DONE.has(id));
document.addEventListener('keydown',e=>{if(e.key==='Escape')closeZoom();if(e.target.matches('textarea,input'))return;if(e.key==='ArrowRight')go(1);if(e.key==='ArrowLeft')go(-1);});
async function load(){try{const r=await fetch('/review.json',{cache:'no-store'});if(r.status===401){location.href='/login';return;}if(!r.ok)throw Error('Queue unavailable');const q=await r.json();ERR=(q.skipped||[]).length?'Skipped '+q.skipped.map(x=>x.id+'.json ('+x.error+')').join('; '):'';if(!Q){Q=q;render();}else{Q=q;steps();$('#err').textContent=ERR;}}catch(e){ERR=e.message;$('#err').textContent=ERR;}}
load();setInterval(load,4000);
