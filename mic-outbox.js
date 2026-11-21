/* Private recordings only. This module never opens a microphone. */
(function(root){
'use strict';
function captureContext(read,{watch=true,clock=()=>new Date().toISOString()}={}){
 const snapshot=()=>{const s=read()||{};return {card:String(s.card||''),shown_sha256:String(s.shown_sha256||'')}};
 const started_at=clock(),start=snapshot(),transitions=[];let last=start,observer;
 const mark=()=>{const next=snapshot();if(next.card!==last.card||next.shown_sha256!==last.shown_sha256){transitions.push({at:clock(),...next});last=next}};
 if(watch&&typeof MutationObserver!=='undefined'){observer=new MutationObserver(mark);observer.observe(document.body,{subtree:true,childList:true,attributes:true,attributeFilter:['data-shown-sha256','data-id','data-card']});addEventListener('scroll',mark,true)}
 return {mark,finish(){mark();observer?.disconnect();if(watch&&typeof removeEventListener!=='undefined')removeEventListener('scroll',mark,true);return {started_at,ended_at:clock(),start,end:{...last},transitions:transitions.map(x=>({...x})),mixed:transitions.length>0}}};
}
// Safari can resolve a retained Blob read with zero bytes instead of rejecting.
// Validate before replacing any IDB row; FileReader can still read some such Blobs.
async function audioBytes(blob){
 let bytes;try{bytes=await blob.arrayBuffer()}catch(_){}
 if(blob.size>0&&bytes?.byteLength===blob.size)return bytes;
 if(blob.size>0&&typeof FileReader!=='undefined'){
  try{bytes=await new Promise((resolve,reject)=>{const r=new FileReader();r.onload=()=>resolve(r.result);r.onerror=r.onabort=()=>reject(r.error);r.readAsArrayBuffer(blob)});}catch(_){}
  if(bytes instanceof ArrayBuffer&&bytes.byteLength===blob.size)return bytes;
 }
 const error=Error(blob.size?'This browser cannot read the recording. Keep this record.':'This retained recording contains no audio bytes. Its record is kept; new recording is available.');
 error.code=blob.size?'browser_audio_unreadable':'empty_audio';throw error;
}
class IndexedStore {
  async db(){
    if(!this.opening)this.opening=new Promise((resolve,reject)=>{
      const r=indexedDB.open('private-panel-mic-outbox-v1',1);
      r.onupgradeneeded=()=>r.result.createObjectStore('chunks',{keyPath:'capture_id'});
      r.onsuccess=()=>resolve(r.result);r.onerror=()=>reject(r.error);
      r.onblocked=()=>reject(Error('Private recording storage is blocked'));
    }).catch(e=>{this.opening=null;throw e});
    return this.opening;
  }
  async run(mode,action){const db=await this.db();return new Promise((resolve,reject)=>{
    let tx;try{tx=db.transaction('chunks',mode,{durability:'strict'})}catch(e){tx=db.transaction('chunks',mode)}
    const req=action(tx.objectStore('chunks'));
    tx.oncomplete=()=>resolve(req.result);tx.onerror=tx.onabort=()=>reject(tx.error||req.error||Error('Private recording storage failed'));
  })}
  async all(){
    const rows=await this.run('readonly',s=>s.getAll());
    return rows.map(row=>{
      if(row.storage_schema===2&&row.audio_buffer instanceof ArrayBuffer){
        const {audio_buffer,audio_type,...meta}=row;
        return {...meta,blob:new Blob([audio_buffer],{type:audio_type||'audio/webm'})};
      }
      // Legacy Blob rows stay intact; an unreadable original requires explicit
      // hash-bound server readback, never deletion or automatic replacement.
      return row;
    });
  }
  async put(row){
    let audio_buffer;
    audio_buffer=await audioBytes(row.blob);
    const {blob,...meta}=row;
    return this.run('readwrite',s=>s.put({...meta,storage_schema:2,audio_buffer,audio_type:blob.type||'audio/webm'}));
  }
  remove(id){return this.run('readwrite',s=>s.delete(id))}
}
function create({store=new IndexedStore(),fetcher=(...a)=>fetch(...a),status=()=>{},stop=()=>{},schedule=(f,ms)=>setTimeout(f,ms),cancel=id=>clearTimeout(id),digest=async blob=>{
  const hash=await crypto.subtle.digest('SHA-256',await audioBytes(blob));return [...new Uint8Array(hash)].map(x=>x.toString(16).padStart(2,'0')).join('');
}}={}){
 const hasOriginalIdentity=row=>typeof row.audio_sha256==='string'&&/^[0-9a-f]{64}$/.test(row.audio_sha256)&&typeof row.capture_id==='string'&&/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(row.capture_id);
 const pending=new Map(),retained=new Map();let busy=false,timer=null,backoff=1000,restoreError='',enqueuing=0;
 const retentionKey=row=>JSON.stringify([row.capture_id,row.audio_sha256||'',row.card,row.target,row.shown_sha256,row.capture_started_at,row.capture_ended_at,row.mic_session,row.capture_context,row.text||'',row.raw_asr||'']);
 const checkpoint=row=>retained.set(row.capture_id,{blob:row.blob,key:retentionKey(row),originalKey:originalKey(row)});
 async function persist(row){await store.put(row);checkpoint(row)}
 const hasUnretainedChanges=()=>captureBlocked()||enqueuing>0||[...pending.values()].some(row=>{const saved=retained.get(row.capture_id);return !saved||saved.blob!==row.blob||saved.key!==retentionKey(row)});
 const originalKey=row=>JSON.stringify([row.capture_id,row.audio_sha256||'',row.card,row.target,row.shown_sha256,row.capture_started_at,row.capture_ended_at,row.mic_session,row.capture_context]);
 const originalSafe=row=>{const saved=retained.get(row.capture_id);return !!(row.audio_durable===true&&hasOriginalIdentity(row))||!!(saved&&saved.blob===row.blob&&saved.originalKey===originalKey(row)&&!row.browser_bytes_unreadable)};
 const noSpeechPaused=row=>row.transcription_pause==='no_speech'||row.error==='no speech heard'||row.audio_pause==='empty';
 const captureBlocked=()=>!!restoreError||[...pending.values()].some(row=>!originalSafe(row));
 const report=(message='',saved=null)=>status({pending:pending.size,message,saved,restoreError,capture_blocked:captureBlocked(),paused_transcripts:[...pending.values()].filter(noSpeechPaused).length,entries:[...pending.values()]});
 const ready=store.all().then(rows=>{for(const row of rows){pending.set(row.capture_id,row);checkpoint(row)}report();}).catch(e=>{restoreError='Cannot read retained recordings: '+e.message;stop();report(restoreError)});
 async function response(url,options){const r=await fetcher(url,options);let j;try{j=await r.json()}catch(e){throw Error('Save response was unreadable')}
  if(!r.ok){const error=Error(j.error||('HTTP '+r.status));error.code=(url==='/transcribe'&&r.status===422&&j.error==='no speech heard')?'transcription_no_speech':j.code;throw error}return j;
 }
 async function save(row){
  // A failed local write leaves the original Blob in memory and stops further capture.
  try{await persist(row)}catch(e){
   if(e.code==='empty_audio')throw e;
   if(!originalSafe(row))stop();const unreadable=e.code==='browser_audio_unreadable';
   if(unreadable)row.browser_bytes_unreadable=true;
   const failure=Error(unreadable?(hasOriginalIdentity(row)?'This browser cannot read the recording. Recovery can check the exact archived original.':'This browser cannot read the recording and no exact archive identity is available. Keep this record.'):'Recording retained in this tab only; keep it open. '+e.message);
   if(unreadable&&hasOriginalIdentity(row))failure.code='capture_recovery_required';throw failure;
  }
  const actualHash=await digest(row.blob);
  if(row.audio_sha256&&row.audio_sha256!==actualHash)throw Error('Retained audio hash differs from its original identity. Keep this record.');
  if(!row.audio_sha256){row.audio_sha256=actualHash;await persist(row)}

  if(row.blob.size>4*1024*1024)throw Error('Audio chunk exceeds server limit; download retained audio');
  const bytes=new Uint8Array(await audioBytes(row.blob));let binary='';
  for(let offset=0;offset<bytes.length;offset+=32768)binary+=String.fromCharCode(...bytes.subarray(offset,offset+32768));
  const payload={chunk_id:row.capture_id,audio_base64:btoa(binary),audio_type:row.blob.type||'audio/webm',card:row.card,target:row.target,shown_sha256:row.shown_sha256||'',segment_started_at:row.capture_started_at,segment_ended_at:row.capture_ended_at,mic_session_id:row.mic_session,capture_context:row.capture_context};
  const voice=data=>response('/voice',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({...payload,...data})});
  if(!row.audio_durable){
   const j=await voice({text:'',transcription_status:'pending'});
   if(j.chunk_id!==row.capture_id||j.audio_durable!==true||j.audio_sha256!==row.audio_sha256)throw Error('Original audio was not durably acknowledged');
   row.audio_durable=true;await persist(row);
  }
  await store.remove(row.capture_id);pending.delete(row.capture_id);retained.delete(row.capture_id);report('',{capture_id:row.capture_id,card:row.card,target:row.target,text:row.text,shown_sha256:row.shown_sha256,capture_ended_at:row.capture_ended_at,audio_sha256:row.audio_sha256});
 }
 async function retry({explicit=false,only=null}={}){
  await ready;if(busy)return;if(explicit&&timer){cancel(timer);timer=null}busy=true;let failed=false;
  try{if(restoreError){try{for(const row of await store.all())if(!pending.has(row.capture_id)){pending.set(row.capture_id,row);checkpoint(row)}restoreError=''}catch(e){stop();report(restoreError);return}}
   for(const row of [...pending.values()]){
    if(only&&row.capture_id!==only)continue;
    if(noSpeechPaused(row)&&!explicit)continue;
    if(explicit&&noSpeechPaused(row)){delete row.transcription_pause;delete row.audio_pause;delete row.error;}
    try{await save(row)}catch(e){failed=true;row.error=e.message;if(e.code==='empty_audio')row.audio_pause='empty';if(e.code==='transcription_no_speech')row.transcription_pause='no_speech';if(e.code==='capture_recovery_required')row.recovery_required=true;try{await persist(row)}catch(_){}if(!originalSafe(row))stop();report(e.message)}
   }}
  finally{busy=false;if([...pending.values()].some(row=>!noSpeechPaused(row))&&!timer){timer=schedule(()=>{timer=null;retry()},failed?backoff:0);if(failed)backoff=Math.min(backoff*2,60000)}else if(!pending.size||[...pending.values()].every(noSpeechPaused))backoff=1000;report()}
 }
 async function recover(id){
  await ready;const row=pending.get(id);if(!row||!row.recovery_required)return;
  if(!hasOriginalIdentity(row)){row.error='No exact archived recording identity is available. Keep this record.';if(!originalSafe(row))stop();report(row.error);return;}
  const originalId=row.capture_id,originalHash=row.audio_sha256;
  try{
   row.grant_request_id ||= crypto.randomUUID();
   try{await persist(row)}catch(e){if(!hasOriginalIdentity(row))throw e}
   // A durable server original permits explicit recovery even when legacy
   // browser Blob bytes cannot be reserialized. The existing IDB row remains.

   const j=await response('/voice/recover',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({chunk_id:originalId,audio_sha256:originalHash,grant_request_id:row.grant_request_id,explicit_human_action:true})});
   if(j.ok!==true||j.durable!==true||j.chunk_id!==originalId||j.audio_sha256!==originalHash||typeof j.recovery_token!=='string'||j.recovery_token.length<32)throw Error('Recovery was not durably acknowledged');
   if(typeof j.audio_base64!=='string'||j.audio_base64.length>5592408)throw Error('Archived original was not returned safely');
   const binary=atob(j.audio_base64),bytes=Uint8Array.from(binary,c=>c.charCodeAt(0));
   if(!bytes.length||bytes.length>4*1024*1024)throw Error('Archived original exceeds the allowed size');
   const restored=new Blob([bytes],{type:j.audio_type||row.blob.type||'audio/webm'});
   if(await digest(restored)!==originalHash)throw Error('Archived original hash did not match; retained record was not replaced');
   row.blob=restored;row.audio_durable=true;delete row.browser_bytes_unreadable;row.recovery_token=j.recovery_token;row.recovery_required=false;delete row.error;await persist(row);await retry({explicit:true,only:id});
  }catch(e){row.error=e.message;if(!originalSafe(row))stop();report(e.message)}
 }
 async function enqueue(blob,meta){
  if(!blob.size)throw Error('No audio was captured. Check the microphone permission and try again.');
  if(typeof document!=='undefined')rememberAudio(blob,meta);
  enqueuing++;let row;
  try{await ready;row={...structuredClone(meta),blob,capture_id:meta.capture_id||crypto.randomUUID(),target:meta.target||'always-on recording'};pending.set(row.capture_id,row);}finally{enqueuing--;}
  report('Saving recording…');
  try{await persist(row)}catch(e){row.error='Recording retained in this tab only; keep it open. '+e.message;stop();report(row.error)}
  await retry();return row.capture_id;
 }
 return {enqueue,retry,recover,ready,pending,get hasUnretainedChanges(){return hasUnretainedChanges()},get canRecord(){return !captureBlocked()}};
}
let latestAudio=null,latestAudioURL=null;
function rememberAudio(blob,meta){
 latestAudio={blob,card:meta.card||'',capture_id:meta.capture_id||'recording'};
 let button=document.getElementById('last-recording-download');
 if(!button){button=document.createElement('button');button.id='last-recording-download';button.type='button';button.style.cssText='position:fixed;right:12px;bottom:max(12px,env(safe-area-inset-bottom));z-index:1090;max-width:220px;padding:8px;background:white;color:#111;border:2px solid #111;font:14px system-ui';document.body.append(button);}
 button.textContent='Download last audio chunk';button.title='Latest captured chunk only; original chunks are also retained by the private audio outbox';
 button.onclick=()=>{if(!latestAudio)return;if(latestAudioURL)URL.revokeObjectURL(latestAudioURL);latestAudioURL=URL.createObjectURL(latestAudio.blob);const a=document.createElement('a');a.href=latestAudioURL;a.download=latestAudio.capture_id+(latestAudio.blob.type.includes('mp4')?'.m4a':'.webm');a.click();};
}
function browserStatus(state){
 let box=document.getElementById('mic-save-status');
 if(!box){box=document.createElement('div');box.id='mic-save-status';box.setAttribute('role','status');box.style.cssText='position:fixed;left:12px;right:12px;bottom:calc(60px + env(safe-area-inset-bottom));z-index:1100;max-height:88px;overflow:auto;overscroll-behavior:contain;padding:8px 12px;border:2px solid #a12b16;background:#fff;color:#111;font-size:14px;overflow-wrap:anywhere';document.body.prepend(box)}
 const unsafe=!!(state.capture_blocked||state.restoreError);box.dataset.saveState=unsafe?'error':'retained';box.style.borderColor=unsafe?'#a12b16':'#8a8f94';box.style.background=unsafe?'#fff':'#f5f6f7';
 const sig=(state.entries||[]).map(x=>x.capture_id).sort().join(',')+'|'+(state.restoreError||state.message||state.entries.find(x=>x.error)?.error||'');
 box.hidden=(!state.pending&&!state.restoreError)||localStorage.getItem('mic-status-hidden')===sig;
 if(box.hidden)return;
 box.replaceChildren();box.style.paddingRight='52px';const close=document.createElement('button');close.type='button';close.textContent='\u00d7';close.setAttribute('aria-label','Hide this notice');close.style.cssText='position:sticky;float:right;top:0;margin-right:-44px;width:40px;height:40px;font:700 24px system-ui;background:#111;color:#fff;border:0;cursor:pointer';close.onclick=()=>{localStorage.setItem('mic-status-hidden',sig);box.hidden=true};box.append(close);const text=document.createElement('p');text.textContent=state.restoreError||`${state.pending} recording${state.pending===1?'':'s'} awaiting audio save. Original audio retained. ${state.message||state.entries.find(x=>x.error)?.error||'Retrying audio save…'}`;box.append(text);
 if(state.paused_transcripts===state.pending&&!state.capture_blocked)text.textContent=`Nothing heard in ${state.pending} earlier recording${state.pending===1?'':'s'}; audio kept. New recording is available. Retry transcription only if you want to try again.`;
 if(state.capture_blocked&&!state.restoreError)text.textContent=`Recording data is not safely retained. Keep this tab open; retry storage before recording more. ${state.message||state.entries.find(x=>x.error)?.error||''}`;
 if(state.entries.some(x=>x.browser_bytes_unreadable))text.textContent=`${state.pending} recording${state.pending===1?'':'s'} awaiting audio save. This browser cannot read the retained recording; keep it for exact archived-original recovery. ${state.message||state.entries.find(x=>x.error)?.error||''}`;
 if(!unsafe&&state.entries.some(x=>x.audio_pause==='empty'))text.textContent='An earlier recording contains no audio bytes. Its retained record is kept. You can make a new recording; retry saving checks the earlier record again.';
 const retry=document.createElement('button');retry.type='button';retry.textContent=state.paused_transcripts===state.pending&&!unsafe&&!state.entries.some(x=>x.audio_pause==='empty')?'Retry transcription':'Retry saving';retry.onclick=()=>api.retry({explicit:true});box.append(retry);
 const recoveryGroups=new Map(),seenOriginals=new Set();
 for(const row of state.entries.filter(x=>x.recovery_required)){
  const identity=row.capture_id+':'+(row.audio_sha256||'');
  if(seenOriginals.has(identity))continue;seenOriginals.add(identity);
  const label=row.card||'the original target';if(!recoveryGroups.has(label))recoveryGroups.set(label,[]);recoveryGroups.get(label).push(row.capture_id);
 }
 for(const [label,ids] of recoveryGroups){
  const recover=document.createElement('button');recover.type='button';recover.textContent='Recover '+ids.length+' saved recording'+(ids.length===1?'':'s')+' from '+label;
  recover.onclick=async event=>{if(!event.isTrusted)return;recover.disabled=true;try{for(const id of ids)await api.recover(id);}finally{recover.disabled=false;}};box.append(recover);
  const explanation=document.createElement('p');explanation.textContent='Checks only these retained originals and transcribes any readable audio. The microphone stays stopped; failed transcriptions remain retained.';box.append(explanation);
 }
 const download=document.createElement('button');download.type='button';download.textContent='Download retained audio';download.onclick=()=>{for(const row of state.entries){const a=document.createElement('a'),u=URL.createObjectURL(row.blob);a.href=u;a.download=row.capture_id+(row.blob.type.includes('mp4')?'.m4a':'.webm');a.click();setTimeout(()=>URL.revokeObjectURL(u),60000)}};box.append(download);
 const hide=document.createElement('button');hide.type='button';hide.textContent='Hide';hide.title='Hides this notice. Audio stays kept. It comes back only if something new fails.';hide.onclick=()=>{localStorage.setItem('mic-status-hidden',sig);box.hidden=true};box.append(hide);
}
let api;
if(typeof document!=='undefined'){
 api=create({status:state=>{browserStatus(state);if(!state.capture_blocked)for(const row of state.entries||[])if(row.transcription_pause==='no_speech'||row.error==='no speech heard')dispatchEvent(new CustomEvent('micoutbox:retained',{detail:{capture_id:row.capture_id,card:row.card,shown_sha256:row.shown_sha256,audio_durable:row.audio_durable===true}}));if(state.saved)dispatchEvent(new CustomEvent('micoutbox:saved',{detail:state.saved}))},stop:()=>dispatchEvent(new Event('micoutbox:stop'))});
 api.ready.then(()=>api.retry());addEventListener('online',()=>api.retry());
 addEventListener('beforeunload',e=>{if(activeContext||api.hasUnretainedChanges||root.panelHasUnsavedReviewText?.()){e.preventDefault();e.returnValue='Unretained audio or review text remains in this tab.'}});
 let activeContext=null;
 api.captureContext=read=>{const tracker=captureContext(read),finish=tracker.finish;tracker.finish=()=>{if(activeContext===tracker)activeContext=null;return finish()};activeContext=tracker;return tracker};
 api.markCapture=()=>activeContext?.mark();root.MicOutbox=api;
}
if(typeof module!=='undefined')module.exports={create,IndexedStore,captureContext,audioBytes};
})(typeof window!=='undefined'?window:globalThis);
