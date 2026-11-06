'use strict';
let correctionData=null,correctionDraft={};
const $=s=>document.querySelector(s);
const esc=s=>s.replace(/[<>&"]/g,'');
const groups={};
let currentJob,installation={},nameDirty=false,nameSaving=false,displayGeneration=0,notationView=null,cursorBeat=-1;
function makeSec(tempo){
 const t=[...tempo].sort((a,b)=>a.b-b.b).filter(x=>x.bpm>0);
 if(!t.length||t[0].b>0)t.unshift({b:0,bpm:t.length?t[0].bpm:120});
 const acc=[0];for(let i=1;i<t.length;i++)acc[i]=acc[i-1]+(t[i].b-t[i-1].b)*60/t[i-1].bpm;
 return b=>{let i=t.length-1;while(i>0&&t[i].b>b)i--;return acc[i]+(b-t[i].b)*60/t[i].bpm};
}

function parseMidi(buf){
 const u=new Uint8Array(buf),v=new DataView(buf);let p=0;
 const text=(a,l)=>new TextDecoder('utf-8').decode(u.subarray(a,a+l)).replace(/\0/g,'').trim();
 if(u.length<14||text(0,4)!=='MThd')throw new Error('Fichier MIDI invalide.');
 const count=v.getUint16(10),div=v.getUint16(12);
 if(div&0x8000)throw new Error('Ce fichier MIDI (temps SMPTE) n’est pas géré.');
 p=8+v.getUint32(4);
 const vlq=()=>{let x=0,b;do{b=u[p++];x=x*128+(b&127)}while((b&128)&&p<u.length);return x};
 const tracks=[],tempo=[];
 for(let k=0;k<count&&p+8<=u.length;k++){
  if(text(p,4)!=='MTrk'){p+=8+v.getUint32(p+4);continue}
  const end=Math.min(u.length,p+8+v.getUint32(p+4));p+=8;
  let ticks=0,running=0,name='';const open={},notes=[];
  const close=(key,at)=>{const o=open[key];if(!o)return;delete open[key];notes.push({b:o.b,d:Math.max(at-o.b,.02),m:o.m,v:o.v,ch:o.ch})};
  while(p<end){
   ticks+=vlq();let s=u[p];
   if(s<0x80){if(!running)break;s=running}else{p++;if(s<0xF0)running=s}
   if(s===0xFF){const t=u[p++],l=vlq();
    if(t===0x51&&l===3)tempo.push({b:ticks/div,bpm:60e6/((u[p]<<16)|(u[p+1]<<8)|u[p+2])});
    else if(t===0x03)name=text(p,l);
    p+=l;
   }else if(s===0xF0||s===0xF7){p+=vlq()}
   else{
    const kind=s>>4,ch=s&15;
    if(kind===8||kind===9){const m=u[p++],vel=u[p++],key=ch*128+m;
     if(kind===9&&vel>0){close(key,ticks/div);open[key]={b:ticks/div,m,v:vel/127,ch}}else close(key,ticks/div)}
    else if(kind===12||kind===13)p+=1;
    else p+=2;
   }
  }
  for(const key of Object.keys(open))close(key,ticks/div);
  p=end;
  const real=notes.filter(n=>n.ch!==9);
  if(real.length)tracks.push({name:name||'Piste '+(tracks.length+1),notes:real});
 }
 return {tracks,tempo};
}

function parseXml(text){return ChoirScore.parseXml(text)}
function buildSong(parsed){return ChoirScore.buildSong(parsed)}

let ctx=null,master=null,song=null,rate=1,vol=.8,pos=0,playing=false,t0=0,p0=0,idx=0,timer=0,loadToken=0;
let muted=new Set(),solo=-1,trackVolumes=[];
const live=new Set();
const curPos=()=>playing?Math.max(0,Math.min(song.total,p0+(ctx.currentTime-t0)*rate)):pos;
const fmt=s=>Math.floor(s/60)+':'+String(Math.floor(s%60)).padStart(2,'0');
const audible=tr=>solo>=0?tr===solo:!muted.has(tr);

function voice(m,at,dur,vel){
 const f=440*2**((m-69)/12),g=ctx.createGain(),g2=ctx.createGain(),o=ctx.createOscillator(),o2=ctx.createOscillator();
 o.type='triangle';o.frequency.value=f;o2.type='sine';o2.frequency.value=f*2;g2.gain.value=.25;
 o.connect(g);o2.connect(g2);g2.connect(g);g.connect(master);
 const peak=.2*vel,end=at+Math.max(dur,.08),dec=Math.max(at+.03,Math.min(end,at+1)),rel=Math.max(end,dec)+.12;
 g.gain.setValueAtTime(0.0001,at);g.gain.linearRampToValueAtTime(peak,at+.012);
 g.gain.exponentialRampToValueAtTime(peak*.45,dec);g.gain.linearRampToValueAtTime(0.0001,rel);
 o.start(at);o2.start(at);o.stop(rel+.02);o2.stop(rel+.02);
 live.add(o);o.onended=()=>{live.delete(o);try{g.disconnect()}catch(e){}};
}

function pump(){
 if(!playing)return;
 const now=curPos();
 while(idx<song.notes.length&&song.notes[idx].s<now+1.2*rate){
  const n=song.notes[idx++];
  if(!audible(n.tr)||(trackVolumes[n.tr]??1)<=0||n.e<=now)continue;
  const from=Math.max(n.s,now),at=Math.max(ctx.currentTime,t0+(from-p0)/rate);
  voice(n.m,at,(n.e-from)/rate,n.v*(trackVolumes[n.tr]??1));
 }
 updateUi();
 if(now>=song.total){pos=song.total;halt();updateUi()}
}

async function audio(){
 if(!ctx){const AC=window.AudioContext||window.webkitAudioContext;if(!AC)throw new Error('Audio non pris en charge dans ce navigateur.');ctx=new AC()}
 await ctx.resume();
}

async function play(){
 if(!song)return;
 await audio();
 if(playing)return;
 if(pos>=song.total-.05)pos=0;
 master=ctx.createGain();master.gain.value=vol;master.connect(ctx.destination);
 p0=pos;t0=ctx.currentTime+.08;idx=0;playing=true;
 timer=setInterval(pump,150);pump();updateUi();
}

function halt(){
 playing=false;clearInterval(timer);
 if(master){try{master.disconnect()}catch(e){}master=null}
 for(const o of live){try{o.stop()}catch(e){}}
 live.clear();
}
function pause(){if(!playing)return;pos=curPos();halt();updateUi()}
function stop(rewind){if(playing)pos=curPos();halt();if(rewind)pos=0;updateUi()}
async function restart(){if(!playing)return;pos=curPos();halt();await play()}

function updateUi(){
 const b=$('#play');if(!b||!song)return;
 b.textContent=playing?'⏸ Pause':'▶ Jouer';$('#play-top').textContent=b.textContent;
 const p=curPos();$('#seek').value=p;$('#time').textContent=fmt(p)+' / '+fmt(song.total);$('#time-top').textContent=$('#time').textContent;
 syncCursor(p);
}

function showScore(key){
 loadToken++;const token=loadToken;
 halt();pos=0;song=null;muted=new Set();solo=-1;trackVolumes=[];
 const g=groups[key];
 for(const b of document.querySelectorAll('#list button'))b.setAttribute('aria-pressed',b.dataset.key===key);
 const st=$('#stage');st.replaceChildren();
 const card=document.createElement('div');card.className='card';
 const h=document.createElement('h2');h.textContent=g.title;card.append(h);
 const grid=document.createElement('div');grid.className='grid';card.append(grid);
 const left=document.createElement('div'),right=document.createElement('div');grid.append(left,right);
 if(g.pdf){
  const o=document.createElement('object');o.data=g.pdf;o.type='application/pdf';
  const fb=document.createElement('p');fb.className='muted';fb.textContent='Votre navigateur n’affiche pas le PDF ici.';o.append(fb);
  left.append(o);
  const a=document.createElement('a');a.href=g.pdf;a.target='_blank';a.rel='noopener';a.textContent='Ouvrir le PDF en grand';
  const pp=document.createElement('p');pp.append(a);left.append(pp);
 }else{left.innerHTML='<p class="muted">Pas de PDF pour ce morceau.</p>'}
 st.append(card);
 if(!g.mid&&!g.musicxml){
  right.innerHTML='<p class="muted">Aucun son associé. Ajoutez « '+esc(key)+'.mid » ou « .musicxml » dans le dossier « '+esc(dirRaw)+' » pour pouvoir l’écouter.</p>';
  return;
 }
 right.innerHTML='<p class="muted" id="loading">Chargement du son…</p>';
 const useMidi=!g.musicxml&&!!g.mid;const url=g.musicxml||g.mid;
 fetch(url).then(r=>{if(!r.ok)throw new Error('Fichier introuvable ('+r.status+').');return useMidi?r.arrayBuffer():r.text()})
  .then(d=>buildSong(useMidi?parseMidi(d):parseXml(d)))
  .then(s=>{if(token!==loadToken)return;song=s;buildPlayer(right)})
  .catch(e=>{if(token!==loadToken)return;right.replaceChildren();const m=document.createElement('div');m.className='msg';m.textContent=e.message;right.append(m)});
}

function buildPlayer(box){
 box.replaceChildren();
 const html=`<div class="controls"><button class="primary" id="play">▶ Jouer</button><button id="stopb">⏹ Début</button><span id="time"></span></div>
<input type="range" id="seek" min="0" max="${song.total}" step="0.1" value="0" aria-label="Position">
<div class="row"><label>Vitesse <select id="rate"><option value="0.5">50 %</option><option value="0.75">75 %</option><option value="0.9">90 %</option><option value="1" selected>100 %</option><option value="1.15">115 %</option></select></label>
<label>Volume <input type="range" id="vol" min="0" max="1" step="0.05" value="${vol}" style="width:110px"></label></div>
<p class="muted">Pupitres à entendre (« seul » pour travailler le vôtre) :</p><div class="tracks" id="tracks"></div>
<p class="muted">Son synthétique. Les reprises et les nuances de la partition ne sont pas jouées.</p>`;
 box.innerHTML=html;
 $('#play').onclick=()=>{(playing?Promise.resolve(pause()):play()).catch(e=>alert(e.message))};
 $('#stopb').onclick=()=>stop(true);
 $('#seek').oninput=e=>{pos=+e.target.value;if(playing){halt();play().catch(err=>alert(err.message))}else updateUi()};
 $('#rate').onchange=e=>{const cur=curPos();rate=+e.target.value;if(playing){pos=cur;halt();play()}else updateUi()};
 $('#vol').oninput=e=>{vol=+e.target.value;if(master)master.gain.value=vol};
 const tb=$('#tracks');
 song.tracks.forEach((name,i)=>{
  const row=document.createElement('div');row.className='track';
  const lab=document.createElement('label');
  const cb=document.createElement('input');cb.type='checkbox';cb.checked=true;cb.dataset.i=i;
  const sp=document.createElement('span');sp.textContent=name;lab.append(cb,sp);
  const sb=document.createElement('button');sb.textContent='seul';sb.setAttribute('aria-pressed','false');sb.dataset.i=i;
  cb.onchange=()=>{cb.checked?muted.delete(i):muted.add(i);restart()};
  sb.onclick=()=>{solo=solo===i?-1:i;for(const b of tb.querySelectorAll('button'))b.setAttribute('aria-pressed',String(+b.dataset.i===solo));restart()};
  const volume=document.createElement('input');volume.type='range';volume.min='0';volume.max='1';volume.step='0.05';volume.value='1';volume.style.width='90px';volume.setAttribute('aria-label','Volume du pupitre '+name);volume.oninput=()=>{trackVolumes[i]=Number(volume.value);restart()};row.append(lab,volume,sb);tb.append(row);
 });
 updateUi();
}


$('#play-top').onclick=()=>{if(!song){$('#state').textContent='Attendez le chargement de la partition.';return;}(playing?Promise.resolve(pause()):play()).catch(e=>$('#state').textContent=e.message)};
$('#stop-top').onclick=()=>stop(true);
const id=new URLSearchParams(location.search).get('job');
async function api(url,options){const r=await fetch(url,options);const d=await r.json();if(!r.ok)throw Error(d.error||'Service indisponible.');return d;}
async function start(){
 if(!/^[a-f0-9]{32}$/.test(id||''))throw Error('Conversion invalide.');
 installation=await api('/api/config');
 const job=await api('/api/jobs/'+id);currentJob=job;
 $('#title').textContent=job.title||job.name;
 $('#state').textContent=job.message;
 const files=(job.files||[]).filter(f=>/^partition-[0-9]+\.musicxml$/.test(f));
 if(!files.length)throw Error('La reconnaissance n’est pas terminée. Revenez à la liste des conversions.');
 for(const f of files){const o=document.createElement('option');o.value=f;o.textContent=files.length===1?'Partition reconnue':f;$('#movement').append(o);}
 $('#movement').onchange=()=>display($('#movement').value);
 $('#confirm').onchange=()=>{$('#publish').disabled=!$('#confirm').checked||!canPublish();};
 $('#publish').onclick=async()=>{if(!$('#confirm').checked)return;$('#publish').disabled=true;try{await api('/api/jobs/'+id+'/publish',{method:'POST'});$('#state').textContent='Dépôt en cours…';await poll();}catch(e){$('#state').textContent=e.message;$('#publish').disabled=false;}};
 await display(files[0]);
 $('#publish').disabled=true;
 if(!canPublish()){$('#confirmation').hidden=true;}
 await poll();
}
function canPublish(){return !nameDirty&&!nameSaving&&(currentJob.status==='review'||(currentJob.status==='done'&&currentJob.upload==='failed'));}
async function display(file){
 const generation=++displayGeneration;nameDirty=false;nameSaving=false;notationView=null;cursorBeat=-1;
 $('#confirm').checked=false;$('#publish').disabled=true;
 halt();$('#notation').replaceChildren();$('#render-state').textContent='Affichage de la partition reconnue…';
 const key='partition';groups[key]={title:currentJob.title||currentJob.name,pdf:'/api/jobs/'+id+'/source.pdf',musicxml:'/api/jobs/'+id+'/'+file+'?v='+Date.now()};
 showScore(key);
 loadPupitres(file,generation);
 try{const r=await fetch(groups[key].musicxml,{cache:'no-store'});if(!r.ok)throw Error();const xml=await r.text();if(generation!==displayGeneration)return;const osmd=new opensheetmusicdisplay.OpenSheetMusicDisplay('notation',{autoResize:true,backend:'svg',drawTitle:true,followCursor:false});await osmd.load(xml.trimStart().startsWith('<?xml')?xml:'<?xml version="1.0" encoding="utf-8"?>\n'+xml);if(generation!==displayGeneration)return;osmd.render();notationView=osmd;osmd.cursor.show();$('#render-state').textContent='Partition issue de la reconnaissance. Le curseur suit le son synthétique ; reprises et nuances ne sont pas jouées.';syncCursor(0);}
 catch(e){console.warn('Affichage de partition :',e);if(generation===displayGeneration)$('#render-state').textContent='L’affichage de cette partition a échoué. Téléchargez le MusicXML et vérifiez-le dans votre éditeur avant tout dépôt.';}
}
async function loadPupitres(file,generation){
 const fields=$('#pupitres-fields');fields.replaceChildren();$('#pupitres-form').hidden=true;
 $('#pupitres-state').textContent='Recherche des lignes reconnues…';
 try{
  const result=await api('/api/jobs/'+id+'/pupitres?file='+encodeURIComponent(file));
  if(generation!==displayGeneration)return;
  $('#pupitres-state').textContent=result.tracks.length+' ligne(s) reconnue(s). Donnez le même nom aux lignes à regrouper avant de déposer les fichiers.';
  for(const track of result.tracks){
   const label=document.createElement('label');label.className='pupitre-field';
   const text=document.createElement('span');text.textContent=(track.source_name||'Partie reconnue')+' · portée '+track.staff+', ligne '+track.voice;
   const small=document.createElement('small');small.textContent=track.notes+' notes reconnues';text.append(small);
   const input=document.createElement('input');input.type='text';input.required=true;input.maxLength=80;input.value=track.label;input.dataset.track=track.id;input.setAttribute('list','pupitres-choices');input.setAttribute('aria-label','Nom du pupitre — portée '+track.staff+', ligne '+track.voice+' de '+(track.source_name||track.part));
   input.addEventListener('input',()=>{renderCorrections();nameDirty=true;$('#confirm').checked=false;$('#publish').disabled=true;$('#pupitres-state').textContent='Noms modifiés : enregistrez-les avant de valider le dépôt.';});
   label.append(text,input);fields.append(label);
  }
  correctionData=result;correctionDraft=structuredClone(result.corrections||{});
  const measures=$('#correction-measure');measures.replaceChildren();const seen=new Set();for(const note of result.notes||[]){const key=note.id.slice(0,note.id.lastIndexOf('/'));if(seen.has(key))continue;seen.add(key);const option=document.createElement('option');option.value=key;option.textContent=note.part+' · mesure '+note.measure;measures.append(option);}
  measures.onchange=renderCorrections;renderCorrections();
  $('#pupitres-form').hidden=!canPublish()||!result.tracks.length;
 }catch(e){if(generation===displayGeneration)$('#pupitres-state').textContent='Les noms ne peuvent pas être modifiés ici : '+e.message;}
}
function correctionChanged(){nameDirty=true;$('#confirm').checked=false;$('#publish').disabled=true;$('#pupitres-state').textContent='Affectations modifiées : enregistrez puis vérifiez la partition et l’écoute.';}
function renderCorrections(){
 const box=$('#correction-notes');box.replaceChildren();if(!correctionData)return;
 const labels=new Map([...$('#pupitres-fields').querySelectorAll('input[data-track]')].map(input=>[input.dataset.track,input.value.trim()]));
 for(const note of correctionData.notes||[]){
  if(note.id.slice(0,note.id.lastIndexOf('/'))!==$('#correction-measure').value)continue;
  const row=document.createElement('div');row.className='card';const title=document.createElement('p');title.textContent='Note '+note.index+' : '+note.note+' · '+({whole:'ronde',half:'blanche',quarter:'noire / soupir',eighth:'croche', '16th':'double croche'}[note.type]||note.type);row.append(title);
  const options=[],seen=new Set();for(const track of correctionData.tracks){if(track.part!==note.part||track.staff!==note.staff)continue;const name=labels.get(track.id)||track.label,key=name.toLocaleLowerCase('fr');if(seen.has(key))continue;seen.add(key);options.push({id:track.id,name,key});}
  const selected=correctionDraft[note.id]||[note.track];const selectedNames=new Set(selected.map(id=>(labels.get(id)||'').toLocaleLowerCase('fr')));
  const checks=[];for(const option of options){const label=document.createElement('label'),input=document.createElement('input');input.type='checkbox';input.checked=selectedNames.has(option.key);checks.push({input,id:option.id});input.onchange=()=>{const targets=checks.filter(c=>c.input.checked).map(c=>c.id);if(!targets.length){input.checked=true;return;}correctionDraft[note.id]=targets;correctionChanged();};label.append(input,document.createTextNode(' '+option.name+' '));row.append(label);}
  const reset=document.createElement('button');reset.type='button';reset.textContent='Affectation d’origine';reset.onclick=()=>{delete correctionDraft[note.id];correctionChanged();renderCorrections();};row.append(reset);box.append(row);
 }
}
$('#suggest-groups').addEventListener('click',()=>{
 let changed=0;for(const input of $('#pupitres-fields').querySelectorAll('input[data-track]')){const name=input.value.replace(/\s+(bis|ter)\s*$/i,'').trim();if(name!==input.value){input.value=name;input.dispatchEvent(new Event('input',{bubbles:true}));changed++;}}
 $('#pupitres-state').textContent=changed?'Regroupement proposé : vérifiez les noms puis enregistrez. Aucune note n’a encore été modifiée.':'Aucun nom terminé par bis ou ter. Saisissez le même nom pour les lignes à réunir.';
});
$('#pupitres-form').addEventListener('submit',async event=>{
 event.preventDefault();if(nameSaving)return;
 const file=$('#movement').value,generation=displayGeneration,labels={};
 for(const input of $('#pupitres-fields').querySelectorAll('input[data-track]'))labels[input.dataset.track]=input.value.trim();
 nameSaving=true;$('#publish').disabled=true;$('#pupitres-state').textContent='Enregistrement et regroupement des lignes reconnues…';
 try{
  await api('/api/jobs/'+id+'/pupitres',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({file,labels,corrections:correctionDraft})});
  if(generation!==displayGeneration)return;
  nameDirty=false;nameSaving=false;await display(file);$('#pupitres-state').textContent='Pupitres enregistrés dans le MusicXML et le MXL. Vérifiez à nouveau l’écoute avant le dépôt.';
 }catch(e){if(generation===displayGeneration){nameSaving=false;$('#pupitres-state').textContent=e.message;}}
});
function syncCursor(seconds){
 if(!notationView||!song)return;
 const cursor=notationView.cursor;if(!$('#follow-score').checked){cursor.hide();return;}
 const element=ChoirScore.positionCursor(notationView,song,seconds,cursorBeat);cursorBeat=seconds;
 if(playing&&element){const rect=element.getBoundingClientRect();if(rect.top<80||rect.bottom>innerHeight-40)element.scrollIntoView({block:'center',behavior:'auto'});}
}
$('#follow-score').addEventListener('change',()=>{cursorBeat=-1;syncCursor(song?curPos():0);});
async function poll(){try{currentJob=await api('/api/jobs/'+id);$('#state').textContent=currentJob.message;$('#result-links').replaceChildren();for(const item of currentJob.reader_links||[]){const u=new URL(item.url);if(u.protocol==='https:'&&u.origin===installation.paheko_origin&&u.pathname===installation.module_path){const a=document.createElement('a');a.href=u.href;a.textContent='Ouvrir dans le lecteur Paheko →';$('#result-links').append(a);}}if(currentJob.status==='publishing')setTimeout(poll,2000);else if(currentJob.status==='done'&&currentJob.upload==='sent')$('#confirmation').hidden=true;else $('#publish').disabled=!$('#confirm').checked||!canPublish();}catch(e){$('#state').textContent=e.message;}}
start().catch(e=>$('#state').textContent=e.message);
window.addEventListener('pagehide',halt);
