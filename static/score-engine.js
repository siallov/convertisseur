/* Shared by the converter and Paheko reader. No pitch-based voice guessing. */
(function(root){
'use strict';
function makeSec(tempo){
 const map=new Map();for(const t of tempo||[])if(t.b>=0&&t.bpm>0&&!map.has(t.b))map.set(t.b,t.bpm);
 const t=[...map].map(([b,bpm])=>({b,bpm})).sort((a,b)=>a.b-b.b);
 if(!t.length||t[0].b>0)t.unshift({b:0,bpm:120});
 const acc=[0];for(let i=1;i<t.length;i++)acc[i]=acc[i-1]+(t[i].b-t[i-1].b)*60/t[i-1].bpm;
 return b=>{let i=t.length-1;while(i>0&&t[i].b>b)i--;return acc[i]+(b-t[i].b)*60/t[i].bpm};
}
function parseXml(text){
 const d=new DOMParser().parseFromString(text,'application/xml'),root=d.documentElement;
 if(d.querySelector('parsererror')||root.nodeName!=='score-partwise')throw Error('MusicXML partwise illisible.');
 const child=(el,n)=>[...el.children].find(c=>c.nodeName===n);
 const names={};d.querySelectorAll('part-list > score-part').forEach(p=>names[p.getAttribute('id')]=child(p,'part-name')?.textContent?.trim()||'');
 const grouped=new Set((d.querySelector('identification > miscellaneous > miscellaneous-field[name="chorale-grouped-parts"]')?.textContent||'').split(',').filter(Boolean));
 const steps={C:0,D:2,E:4,F:5,G:7,A:9,B:11},lengths=[],numbers=[],groups=[],tempo=[];
 [...root.children].filter(p=>p.nodeName==='part').forEach(part=>{
  let divisions=1;const tracks=new Map();
  [...part.children].filter(m=>m.nodeName==='measure').forEach((measure,mi)=>{
   let q=0,last=0,end=0;
   numbers[mi]??=measure.getAttribute('number')||String(mi+1);
   for(const el of measure.children){
    const duration=()=>Number(child(el,'duration')?.textContent||0)/divisions;
    if(el.nodeName==='attributes'){const v=Number(child(el,'divisions')?.textContent);if(v>0)divisions=v;}
    else if(el.nodeName==='backup')q-=duration();
    else if(el.nodeName==='forward'){q+=duration();end=Math.max(end,q);}
    else if(el.nodeName==='direction'||el.nodeName==='sound'){
     const sound=el.nodeName==='sound'?el:child(el,'sound'),bpm=Number(sound?.getAttribute('tempo'));
     if(bpm>0)tempo.push({mi,b:q+Number(child(el,'offset')?.textContent||0)/divisions,bpm});
    }else if(el.nodeName==='note'){
     if(child(el,'grace'))continue;
     const chord=!!child(el,'chord'),start=chord?last:q,durationValue=duration(),pitch=child(el,'pitch');
     if(pitch&&!child(el,'rest')){
      const staff=child(el,'staff')?.textContent||'1',voice=child(el,'voice')?.textContent||'1';
      const key=part.getAttribute('id')+'/'+staff+'/'+voice;
      if(!tracks.has(key))tracks.set(key,{id:key,part:part.getAttribute('id'),staff,voice,sourceName:names[part.getAttribute('id')],notes:[]});
      const midi=12*(Number(child(pitch,'octave')?.textContent)+1)+steps[child(pitch,'step')?.textContent]+Number(child(pitch,'alter')?.textContent||0);
      if(Number.isFinite(midi))tracks.get(key).notes.push({mi,b:start,d:durationValue,m:midi,v:.8,ties:[...el.children].filter(c=>c.nodeName==='tie').map(t=>t.getAttribute('type'))});
     }
     end=Math.max(end,start+durationValue);
     if(!chord){last=q;q+=durationValue;}
    }
   }
   lengths[mi]=Math.max(lengths[mi]||0,end);
  });
  const count=tracks.size;
  for(const track of tracks.values()){
   let label=track.sourceName||'';
   if(!label||/^(voice|voix|part(ie)?|piano|instrument)[ ._-]*\d*$/i.test(label))label='Pupitre '+(groups.length+1);
   else if(count>1)label+=' — ligne '+track.voice+' (portée '+track.staff+')';
   track.name=label;groups.push(track);
  }
 });
 const starts=[];let total=0;for(let i=0;i<lengths.length;i++){starts[i]=total;total+=lengths[i]||0;}
 for(const track of groups){
  const open=new Map(),notes=[];
  for(const n of track.notes){
   n.b+=starts[n.mi];const previous=open.get(n.m);
   if(n.ties.includes('stop')&&previous&&Math.abs(previous.b+previous.d-n.b)<.00001){previous.d+=n.d;if(!n.ties.includes('start'))open.delete(n.m);}
   else{notes.push(n);if(n.ties.includes('start'))open.set(n.m,n);else open.delete(n.m);}
  }
  track.notes=notes;
 }
 const playback=[],combined=new Map();
 for(const track of groups){
  if(!grouped.has(track.part)){playback.push(track);continue;}
  if(!combined.has(track.part)){const merged={...track,name:track.sourceName,notes:[]};combined.set(track.part,merged);playback.push(merged);}
  combined.get(track.part).notes.push(...track.notes);
 }
 return {tracks:playback,tempo:tempo.map(t=>({b:starts[t.mi]+t.b,bpm:t.bpm})),measures:starts.map((b,i)=>({b,end:b+lengths[i],number:numbers[i]})),totalBeats:total};
}
function buildSong(parsed){
 if(!parsed.tracks.length)throw Error('Aucune note trouvée dans ce fichier.');
 const seconds=makeSec(parsed.tempo),notes=[];
 parsed.tracks.forEach((t,i)=>t.notes.forEach(n=>notes.push({s:seconds(n.b),e:seconds(n.b+n.d),m:n.m,v:n.v,tr:i})));
 notes.sort((a,b)=>a.s-b.s);
 return {tracks:parsed.tracks.map(t=>t.name),notes,total:Math.max(seconds(parsed.totalBeats||0),notes.reduce((end,n)=>Math.max(end,n.e),0)),
  seconds,measures:(parsed.measures||[]).map(m=>({...m,s:seconds(m.b),e:seconds(m.end)}))};
}
function positionCursor(view,song,seconds,previousSeconds){
 const cursor=view.cursor,iterator=()=>cursor.Iterator||cursor.iterator;
 const stamp=()=>iterator().CurrentSourceTimestamp||iterator().currentTimeStamp||iterator().CurrentTimeStamp;
 if(!stamp())return null;
 const at=()=>song.seconds(Number(stamp().RealValue)*4);
 if(seconds<previousSeconds)cursor.reset();
 let count=0;
 while(!iterator().EndReached&&at()<seconds-.025&&count++<20000)cursor.next();
 if(!iterator().EndReached&&at()>seconds+.025)cursor.previous();
 cursor.show();return cursor.cursorElement;
}
root.ChoirScore={parseXml,buildSong,makeSec,positionCursor};
if(typeof module!=='undefined')module.exports=root.ChoirScore;
})(typeof globalThis!=='undefined'?globalThis:window);
