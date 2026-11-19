const $=s=>document.querySelector(s);
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const time=s=>s?new Intl.DateTimeFormat('ko-KR',{timeZone:'Asia/Seoul',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false}).format(new Date(s)):'—';
const compact=s=>String(s??'').replace(/[\s·]/g,'').replace(/\(.*?\)/g,'');
const type=t=>({'0':'일반','1':'급행','7':'특급'}[t.express_code]||'유형 미확인');

export function renderObservedTrains(state){
  const slots=new Map();let plotted=0,total=0;
  $('#train-layer').innerHTML=(state.observations||[]).flatMap(entry=>{
    const line=state.network.lines.find(l=>l.name===entry.line);if(!line)return [];
    const stale=Date.now()-new Date(entry.collected_at).getTime()>120000;
    return entry.trains.flatMap(train=>{
      total++;
      const station=state.network.stations.find(s=>s.lines.includes(line.id)&&[s.name,...s.aliases].some(n=>compact(n)===compact(train.station_name)));
      if(!station)return [];
      plotted++;
      if(state.focusLine&&state.focusLine!==line.id)return [];
      const key=station.id,slot=slots.get(key)||0;slots.set(key,slot+1);
      const x=station.x+(slot%4)*11-15,y=station.y-17-Math.floor(slot/4)*11;
      const express=['1','7'].includes(train.express_code);
      return [`<g class="observed-train ${stale?'stale':''}" transform="translate(${x} ${y})"><title>${esc(entry.line)} ${esc(train.train_no)} · ${esc(type(train))} · ${esc(train.station_name)} · 관측 ${esc(train.observed_at)} / 수집 ${time(entry.collected_at)}</title><rect width="13" height="10" rx="3" fill="${line.color}" stroke="${express?'#d32e32':'white'}" stroke-width="${express?2.5:1.5}"/></g>`];
    });
  }).join('');
  $('#observed-summary').textContent=total?`실제 관측 ${total}건 · 지도 매칭 ${plotted}건 · 급행은 빨간 테두리`:'실제 열차 위치를 불러오는 중';
}

export function initCollector(state){
  async function refresh(){
    try{
      const response=await fetch('/api/collector');if(!response.ok)throw Error();
      const data=await response.json();if(!data.available)throw Error(data.error);
      state.observations=data.lines;
      $('#collector-status').textContent=`EC2 ${data.service==='active'?'가동 중':'상태 확인 필요'} · ${data.active_window?'수집 시간대':'수집 대기'} · ${time(data.fetched_at)} 확인`;
      const sessions=data.sessions||[];
      const sessionCount=(index,name)=>sessions[index]?.lines.filter(l=>l.line===name).reduce((sum,l)=>sum+l.received_rows,0)||0;
      $('#collector-lines').innerHTML=`<div class="collection-table"><table><thead><tr><th>노선</th><th>어제 퇴근<br><small>17:30–20:30</small></th><th>오늘 출근<br><small>06:30–09:30</small></th><th>최근 수집 / 열차</th></tr></thead><tbody>${state.network.lines.map(line=>{
        const entry=data.lines.find(l=>l.line===line.name);
        const age=entry?Date.now()-new Date(entry.collected_at).getTime():Infinity;
        const trains=entry?.trains||[];
        const express=trains.filter(t=>['1','7'].includes(t.express_code)).length;
        return `<tr><th><span style="color:${line.color}">●</span> ${esc(line.name)}</th><td>${sessionCount(0,line.name).toLocaleString()}</td><td>${sessionCount(1,line.name).toLocaleString()}</td><td><details><summary>${time(entry?.collected_at)} · ${trains.length}건${age>120000?' · 과거 관측':''}${express?` · 급행/특급 ${express}`:''}</summary><div class="train-list">${trains.map(t=>`<span><b>${esc(t.train_no)} ${esc(type(t))}</b> ${esc(t.station_name)} · ${esc(t.destination)} 방면<br><small>API 관측 ${esc(t.observed_at||'시각 없음')}</small></span>`).join('')||'최근 수집 응답 없음'}</div></details></td></tr>`;
      }).join('')}</tbody></table></div>`;
      $('#collection-dates').textContent=`${sessions[0]?.date||''} 퇴근 / ${sessions[1]?.date||''} 출근 · 숫자는 API 수신 위치 관측 건수이며 같은 열차의 반복 관측을 포함합니다.`;
      $('#observed-summary').dataset.connected='true';renderObservedTrains(state);
    }catch(error){
      $('#collector-status').textContent=error.message||'EC2 연결 실패';
      $('#observed-summary').textContent=state.observations?.length?'EC2 갱신 실패 · 마지막 수신 위치를 표시합니다.':'EC2 연결 실패 · 열차 위치를 표시할 수 없습니다.';
      if(!state.observations?.length)$('#collector-lines').textContent='수집 내역을 가져오지 못했습니다. 잠시 후 자동으로 다시 연결합니다.';
    }
  }
  refresh();setInterval(refresh,30000);
}
