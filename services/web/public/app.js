import {initCollector,renderObservedTrains} from './live.js';
import {createJourneyUI} from './journeys.js?v=live-v2';
const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const esc = (value) => String(value).replace(/[&<>"']/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmtTime = (value) => new Intl.DateTimeFormat('ko-KR',{timeZone:'Asia/Seoul',hour:'2-digit',minute:'2-digit',hour12:false}).format(new Date(value));
const fmtDate = (value) => new Intl.DateTimeFormat('ko-KR',{timeZone:'Asia/Seoul',month:'short',day:'numeric'}).format(new Date(value));
const localInput = (date) => new Intl.DateTimeFormat('sv-SE',{timeZone:'Asia/Seoul',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}).format(date).replace(' ','T');
const state = {network:null, stations:new Map(), edges:new Map(), origin:null, destination:null, selection:'origin', result:null, selectedTrip:null, selectedKind:'recommended', focusLine:null, zoom:1, center:{x:1550,y:1050}, request:0, hasSearched:false};
let toastTimer, popupTimer, recalcTimer;
function lineName(id){return state.network.lines.find(l=>l.id===id)?.name||id;}
function lineLabel(id){return id.match(/^\d+$/)?id:lineName(id);}

function toast(message){ $('#toast').textContent=message; $('#toast').hidden=false; clearTimeout(toastTimer); toastTimer=setTimeout(()=>$('#toast').hidden=true,2600); }
function setSelection(which){state.selection=which; ['origin','destination'].forEach(key=>{const b=$(`#choose-${key}`);b.classList.toggle('active',key===which);b.setAttribute('aria-pressed',String(key===which));});}
function stationName(id){return state.stations.get(id)?.name || '';}
function lineBadge(id){const color=state.network?.lines.find(l=>l.id===id)?.color||'#66776a';return `<b class="line-dot line-${id}" style="background:${color}">${esc(lineLabel(id))}</b>`;}

function pickStation(which,id,fromMap=false){
  state[which]=id; $(`#${which}`).value=stationName(id); closeSuggestions(which); invalidate(); renderStations();
  if(fromMap){
    const text=`${stationName(id)} · ${which==='origin'?'출발역':'도착역'}으로 선택했어요`;
    $('#station-popup').textContent=text; $('#station-popup').hidden=false;clearTimeout(popupTimer);popupTimer=setTimeout(()=>$('#station-popup').hidden=true,2400);
    if(which==='origin')setSelection('destination');
  }else{
    const s=state.stations.get(id);state.center={x:s.x,y:s.y};state.zoom=Math.max(state.zoom,3.5);updateView();
  }
}

function closeSuggestions(which){$(`#${which}-options`).hidden=true;$(`#${which}`).setAttribute('aria-expanded','false');$(`#${which}`).removeAttribute('aria-activedescendant');}

function initAutocomplete(which){
  const input=$(`#${which}`),box=$(`#${which}-options`);let highlighted=-1;
  function show(){
    const query=input.value.trim();
    const matches=state.network.stations.filter(s=>!query||s.name.includes(query)||(s.aliases||[]).some(a=>a.includes(query))).sort((a,b)=>Number(b.name===query)-Number(a.name===query)).slice(0,10);
    box.innerHTML=matches.length?matches.map((s,i)=>`<button type="button" class="suggestion" id="${which}-option-${i}" role="option" aria-selected="false" data-id="${s.id}" tabindex="-1">${esc(s.name)}<small>${s.lines.map(lineBadge).join('')}</small></button>`).join(''):'<div class="no-suggestions">지원하는 역을 찾을 수 없어요.</div>';
    highlighted=-1;box.hidden=false;input.setAttribute('aria-expanded','true');
  }
  input.addEventListener('focus',()=>{setSelection(which);show();});
  input.addEventListener('input',()=>{state[which]=null;invalidate();renderStations();show();});
  box.addEventListener('mousedown',e=>e.preventDefault());
  box.addEventListener('click',e=>{const option=e.target.closest('[data-id]');if(option){pickStation(which,option.dataset.id);input.focus();closeSuggestions(which);}});
  input.addEventListener('keydown',e=>{
    const options=[...box.querySelectorAll('.suggestion')];
    if(e.key==='Escape'){closeSuggestions(which);return;}
    if(['ArrowDown','ArrowUp'].includes(e.key)){
      e.preventDefault();if(box.hidden)show();
      const current=[...box.querySelectorAll('.suggestion')];if(!current.length)return;
      highlighted=(highlighted+(e.key==='ArrowDown'?1:-1)+current.length)%current.length;
      current.forEach((node,i)=>{node.classList.toggle('highlighted',i===highlighted);node.setAttribute('aria-selected',String(i===highlighted));});
      input.setAttribute('aria-activedescendant',current[highlighted].id);current[highlighted].scrollIntoView({block:'nearest'});
    }else if(e.key==='Enter'&&!box.hidden&&options[highlighted]){e.preventDefault();pickStation(which,options[highlighted].dataset.id);}
  });
  input.addEventListener('blur',()=>{closeSuggestions(which);const exact=state.network.stations.find(s=>s.name===input.value.trim()||(s.aliases||[]).includes(input.value.trim()));if(exact&&!state[which])pickStation(which,exact.id);});
}

function points(ids){return ids.map(id=>{const s=state.stations.get(id);return `${s.x},${s.y}`;}).join(' ');}
function geometryPoints(leg){
  const coordinates=[];
  for(let i=0;i<leg.stations.length-1;i++){
    const a=leg.stations[i],b=leg.stations[i+1];
    const edge=state.edges.get(`${leg.line_id}:${a}:${b}`);
    coordinates.push(...(edge||[state.stations.get(a),state.stations.get(b)].map(s=>[s.x,s.y])));
  }
  return coordinates.map(p=>p.join(',')).join(' ');
}

function fitStations(ids){
  const rows=ids.map(id=>state.stations.get(id));
  const xs=rows.map(s=>s.x),ys=rows.map(s=>s.y);
  const minX=Math.min(...xs),maxX=Math.max(...xs),minY=Math.min(...ys),maxY=Math.max(...ys);
  state.center={x:(minX+maxX)/2,y:(minY+maxY)/2};
  state.zoom=Math.min(7,Math.max(.8,Math.min(state.network.map.width/(maxX-minX+300),state.network.map.height/(maxY-minY+260))));
  updateView();
}

function focusLine(id){
  state.focusLine=id;
  $$('#line-filters button').forEach(b=>b.setAttribute('aria-pressed',String((b.dataset.line||null)===id)));
  if(id){fitStations(state.network.lines.find(l=>l.id===id).stations);}
  else{state.zoom=1;state.center={x:state.network.map.width/2,y:state.network.map.height/2};updateView();}
  $$('.base-line').forEach(p=>p.classList.toggle('is-muted',Boolean(id)&&p.dataset.line!==id));
  renderStations();renderObservedTrains(state);
}

function renderMap(){
  const svg=$('#subway-map');
  svg.innerHTML=`<title id="svg-title">수도권 지하철 19개 노선 전체 노선도</title><desc id="svg-desc">노선 번호를 누르면 해당 노선 전체를 확대합니다. 역을 선택하면 출발역이나 도착역으로 지정됩니다.</desc><g id="base-lines">${state.network.lines.map(line=>line.paths.map(path=>`<polyline class="base-line" data-line="${line.id}" stroke="${line.color}" points="${path.map(p=>p.join(',')).join(' ')}"/>`).join('')).join('')}</g><g id="route-lines"></g><g id="station-layer"></g><g id="train-layer"></g>`;
  $('#line-filters').innerHTML='<button type="button" data-line="" aria-pressed="true" class="all-lines">전체</button>'+state.network.lines.map(line=>`<button type="button" data-line="${line.id}" aria-pressed="false" aria-label="${line.name} 전체 보기" title="${line.name} · ${line.station_count}개 역" style="--line-color:${line.color}">${esc(lineLabel(line.id))}</button>`).join('');
  $$('#line-filters button').forEach(button=>button.onclick=()=>focusLine(button.dataset.line||null));
  $('#network-summary').textContent=`${state.network.lines.length}개 노선 · ${state.network.stations.length}개 역 · 지선 포함`;
  renderStations();
  svg.addEventListener('click',e=>{const station=e.target.closest('[data-station]');if(station&&!svg.classList.contains('just-dragged'))pickStation(state.selection,station.dataset.station,true);});
  svg.addEventListener('keydown',e=>{const station=e.target.closest('[data-station]');if(station&&['Enter',' '].includes(e.key)){e.preventDefault();pickStation(state.selection,station.dataset.station,true);}});
  initPanZoom();updateView();animateTrains();
}

function labelPosition(s){
  const p=s.label_position||'E';
  let x=p.includes('W')?-15:p.includes('E')?15:0;
  let y=p.includes('N')?-15:p.includes('S')?25:5;
  let anchor=x<0?'end':x>0?'start':'middle',rotate='';
  return {x,y,anchor,rotate};
}

function renderStations(){
  if(!state.network)return;
  const routeSet=new Set(state.selectedTrip?.legs.flatMap(l=>l.stations)||[]);
  $('#station-layer').innerHTML=state.network.stations.map(s=>{
    const selected=s.id===state.origin?'출발':s.id===state.destination?'도착':null;
    const label=labelPosition(s);const transfer=s.lines.length>1;const color=transfer?'#66776a':state.network.lines.find(l=>l.id===s.lines[0]).color;
    const nameLabel=s.name.length>7?`<tspan x="${label.x}">${esc(s.name.slice(0,5))}</tspan><tspan x="${label.x}" dy="16">${esc(s.name.slice(5))}</tspan>`:esc(s.name);
    const muted=state.focusLine&&!s.lines.includes(state.focusLine);
    return `<g class="station ${transfer?'is-transfer':''} ${selected?'is-selected':''} ${routeSet.has(s.id)?'on-route':''} ${muted?'line-muted':''}" data-station="${s.id}" transform="translate(${s.x} ${s.y})" role="button" tabindex="0" aria-label="${esc(s.name)}, ${s.lines.map(lineName).join('·')}${selected?`, ${selected}역`:''}"><circle class="station-hit" r="16"/><circle class="station-circle" r="${transfer?8:5}" stroke="${color}"/>${transfer?'<circle class="inner-circle" r="4"/>':''}<text x="${label.x}" y="${label.y}" text-anchor="${label.anchor}" transform="${label.rotate}">${nameLabel}</text>${selected?`<g class="station-badge" transform="translate(-29 -65)"><rect width="58" height="30" rx="8"/><path d="M24 30l5 7 5-7" fill="#19313b"/><text x="29" y="21" text-anchor="middle">${selected}</text></g>`:''}</g>`;
  }).join('');
}

function renderRoute(){
  if(!state.network)return;
  const trip=state.selectedTrip;
  $('#subway-map').classList.toggle('has-route',Boolean(trip));
  $('#route-lines').innerHTML=trip?trip.legs.map(leg=>`<polyline class="route-line" stroke="${state.network.lines.find(l=>l.id===leg.line_id).color}" points="${geometryPoints(leg)}"/>`).join(''):'';
  renderStations();
}

function animateTrains(){renderObservedTrains(state);}

function updateView(){
  const w=state.network.map.width/state.zoom,h=state.network.map.height/state.zoom;
  $('#subway-map').setAttribute('viewBox',`${state.center.x-w/2} ${state.center.y-h/2} ${w} ${h}`);
  $('#zoom-label').textContent=`${Math.round(state.zoom*100)}%`;
}

function initPanZoom(){
  const svg=$('#subway-map');let start=null;
  function zoom(factor){state.zoom=Math.max(.8,Math.min(10,state.zoom*factor));updateView();}
  $('#zoom-in').onclick=()=>zoom(1.2);$('#zoom-out').onclick=()=>zoom(1/1.2);
  $('#zoom-reset').onclick=()=>focusLine(null);
  svg.addEventListener('wheel',e=>{e.preventDefault();zoom(e.deltaY<0?1.08:1/1.08);},{passive:false});
  svg.addEventListener('pointerdown',e=>{if(e.target.closest('[data-station]'))return;start={x:e.clientX,y:e.clientY,center:{...state.center},moved:false};svg.setPointerCapture(e.pointerId);});
  svg.addEventListener('pointermove',e=>{if(!start)return;const scale=state.network.map.width/state.zoom/svg.getBoundingClientRect().width;const dx=e.clientX-start.x,dy=e.clientY-start.y;if(Math.abs(dx)+Math.abs(dy)>5){start.moved=true;svg.classList.add('dragging');}state.center={x:start.center.x-dx*scale,y:start.center.y-dy*scale};updateView();});
  svg.addEventListener('pointerup',()=>{if(start?.moved){svg.classList.add('just-dragged');setTimeout(()=>svg.classList.remove('just-dragged'),30);}start=null;svg.classList.remove('dragging');});
  svg.addEventListener('pointercancel',()=>{start=null;svg.classList.remove('dragging');});
}

function setDeadline(minutes){$('#deadline').value=localInput(new Date(Date.now()+minutes*60000));$$('.time-shortcuts [data-offset]').forEach(b=>b.classList.toggle('active',Number(b.dataset.offset)===minutes));}

const {search,invalidate,startRefresh}=createJourneyUI(state,{esc,fmtTime,fmtDate,lineBadge,lineName,stationName,renderRoute,fitStations});

async function init(){
  function clock(){const now=new Date();$('#clock').textContent=new Intl.DateTimeFormat('ko-KR',{timeZone:'Asia/Seoul',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false}).format(now);$('#deadline').min=localInput(now);$('#deadline').max=localInput(new Date(Date.now()+48*3600000));}clock();setInterval(clock,1000);
  try{
    const response=await fetch('/api/network');if(!response.ok)throw Error('노선도를 불러오지 못했습니다.');state.network=await response.json();
    state.stations=new Map(state.network.stations.map(s=>[s.id,s]));
    state.center={x:state.network.map.width/2,y:state.network.map.height/2};
    state.network.edges.forEach(e=>{state.edges.set(`${e.line_id}:${e.origin}:${e.destination}`,e.points);state.edges.set(`${e.line_id}:${e.destination}:${e.origin}`,[...e.points].reverse());});
    state.origin=state.network.stations.find(s=>s.name==='대곡').id;state.destination=state.network.stations.find(s=>s.name==='역삼').id;
    try{const saved=JSON.parse(localStorage.getItem('yeoyu-route')||'null');if(saved&&state.stations.has(saved.origin)&&state.stations.has(saved.destination)){state.origin=saved.origin;state.destination=saved.destination;}}catch{}
    $('#origin').value=stationName(state.origin);$('#destination').value=stationName(state.destination);
    initAutocomplete('origin');initAutocomplete('destination');renderMap();initCollector(state);setDeadline(120);
    $('#planner-form').onsubmit=e=>{e.preventDefault();search({scroll:true});};
    $('#swap').onclick=()=>{[state.origin,state.destination]=[state.destination,state.origin];$('#origin').value=stationName(state.origin);$('#destination').value=stationName(state.destination);invalidate();renderStations();};
    $('#choose-origin').onclick=()=>setSelection('origin');$('#choose-destination').onclick=()=>setSelection('destination');
    $('#reset-selection').onclick=()=>{state.origin=null;state.destination=null;$('#origin').value='';$('#destination').value='';invalidate();renderStations();};
    $$('.time-shortcuts [data-offset]').forEach(b=>b.onclick=()=>{setDeadline(Number(b.dataset.offset));invalidate();});
    ['deadline','transfer','safety','transfer-extra','skip-trains','max-transfers'].forEach(id=>$('#'+id).addEventListener('change',()=>{invalidate();}));
    $$('.quick-routes button').forEach(b=>b.onclick=()=>{state.origin=state.network.stations.find(s=>s.name===b.dataset.origin).id;state.destination=state.network.stations.find(s=>s.name===b.dataset.destination).id;$('#origin').value=stationName(state.origin);$('#destination').value=stationName(state.destination);$('#transfer').checked=true;setDeadline(Number(b.dataset.offset)||120);invalidate();renderStations();fitStations([state.origin,state.destination]);search({scroll:false});});
    $('#apply-transfer').onclick=()=>{
      const station=state.network.stations.find(s=>s.name===$('#override-station').value.trim());
      const from=state.network.lines.find(l=>l.name===$('#override-from').value.trim());
      const to=state.network.lines.find(l=>l.name===$('#override-to').value.trim());
      const minutes=Number($('#override-minutes').value);
      if(!station||!from||!to||!station.lines.includes(from.id)||!station.lines.includes(to.id)||!Number.isFinite(minutes)||minutes<0||minutes>60){toast('역·노선·시간을 확인해 주세요.');return;}
      const values=JSON.parse($('#transfer-overrides').dataset.value||'{}');values[`${station.id}:${from.id}:${to.id}`]=Math.round(minutes*60);$('#transfer-overrides').dataset.value=JSON.stringify(values);
      $('#override-summary').textContent=Object.entries(values).map(([key,seconds])=>{const [sid,a,b]=key.split(':');return `${stationName(sid)} ${lineName(a)}→${lineName(b)} ${seconds/60}분`;}).join(' / ');invalidate();toast('해당 방향의 환승 이동시간을 적용했어요.');
    };
    $('#clear-transfers').onclick=()=>{$('#transfer-overrides').dataset.value='{}';$('#override-summary').textContent='직접 설정 없음';invalidate();};
    $('#station-names').innerHTML=state.network.stations.map(s=>`<option value="${esc(s.name)}">`).join('');
    $('#line-names').innerHTML=state.network.lines.map(l=>`<option value="${esc(l.name)}">`).join('');
    const about=$('#about-dialog');$('#about-button').onclick=()=>about.showModal();$('#close-about').onclick=()=>about.close();$('#understood').onclick=()=>about.close();
    startRefresh();await search();
  }catch(error){$('#form-error').textContent=error.message;$('#form-error').hidden=false;console.error(error);}
}
init();
