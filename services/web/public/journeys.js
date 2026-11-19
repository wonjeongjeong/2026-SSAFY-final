export function createJourneyUI(state,{esc,fmtTime,fmtDate,lineBadge,lineName,stationName,renderRoute,fitStations}){
  const $=s=>document.querySelector(s),$$=s=>[...document.querySelectorAll(s)];
  const labels={fastest:'지금 출발 · 빠른 도착',latest:'목표에 맞춘 늦은 출발',recommended:'여유 있는 출발',fewest:'환승 적은 경로'};
  let category='fastest',selectedKey=null,busy=false,refreshTimer;

  function invalidate(){
    state.request++;state.result=null;state.selectedTrip=null;selectedKey=null;
    $('#search-button').disabled=false;$('#search-button').innerHTML='실제 열차로 경로 찾기 <span>↗</span>';
    $('#form-error').hidden=true;$('#result-meta').textContent='출발·도착역과 목표 도착시각을 설정하세요';
    $('#results').innerHTML='<div class="empty-state"><span>↗</span><div><h3>언제까지 도착할까요?</h3><p>실제 시간표와 현재 도착예정 정보를 이용해 여러 경로를 비교합니다.</p></div></div>';
    renderRoute();
  }

  function routes(){
    const r=state.result;if(!r)return [];
    if(category==='fewest')return [...(r.routes.fastest||[])].concat(r.fewer_transfers||[]).filter((v,i,a)=>a.findIndex(t=>t.route_key===v.route_key)===i).sort((a,b)=>a.transfers-b.transfers||a.arrive_ts-b.arrive_ts);
    return r.routes[category]||[];
  }

  function choose(key){
    selectedKey=key;state.selectedTrip=routes().find(r=>r.route_key===key)||null;
    $$('.journey-option').forEach(b=>{const active=b.dataset.key===key;b.classList.toggle('selected',active);b.setAttribute('aria-pressed',String(active));});
    renderRoute();renderDetail();
  }

  function card(trip,index){
    const late=category==='latest'||category==='recommended';
    return `<button type="button" class="journey-option" data-key="${esc(trip.route_key)}" aria-pressed="false"><div class="option-top"><b>${index===0?labels[category]:`대안 ${index+1}`}</b><span class="evidence ${trip.live_legs?'is-live':''}">${trip.live_legs?`실시간 보정 ${trip.live_legs}/${trip.legs.length}구간`:'실제 시간표 기준'}</span></div><div class="option-times"><div><small>${late?'승강장 출발':'예상 도착'}</small><strong>${fmtTime(late?trip.board_at:trip.arrive_at)}</strong></div><span class="option-arrow">${late?'→':'←'}</span><div><small>${late?'예상 도착':'승강장 출발'}</small><b>${fmtTime(late?trip.arrive_at:trip.board_at)}</b></div><span class="option-duration">${trip.duration_minutes}분<small>환승 ${trip.transfers}회</small></span></div><div class="option-lines">${trip.legs.map((l,i)=>`${i?'<span>→</span>':''}${lineBadge(l.line_id)}${l.express?'<em>급행</em>':''}`).join('')}<span class="deadline-status ${trip.slack_minutes<0?'is-late':''}">${trip.slack_minutes>=0?`${trip.slack_minutes}분 일찍 도착`:`목표보다 ${-trip.slack_minutes}분 늦음`}</span></div></button>`;
  }

  function renderDetail(){
    const trip=state.selectedTrip;if(!trip||!$('#journey-detail'))return;
    const names={official_shortest:'공식 최단 이동시간',official_reverse_assumption:'공식 최단거리의 역방향 적용 · 추정',user:'직접 설정한 환승시간',unverified_assumption:'미확보 구간 · 환승시간 가정'};
    $('#journey-detail').innerHTML=`<div class="itinerary"><div class="itinerary-heading"><h3>${esc(stationName(state.origin))}<span>→</span>${esc(stationName(state.destination))}</h3><span>${fmtDate(trip.board_at)} · ${trip.duration_minutes}분 · 승강장 기준</span></div>${trip.legs.map((l,i)=>{
      const transfer=trip.transfer_details[i-1];
      return `${transfer?`<div class="transfer-event"><span>↳</span><div><b>${esc(stationName(transfer.station))} 환승 · 총 ${Math.ceil(transfer.total_seconds/60)}분 확보</b><p>이동 ${Math.ceil(transfer.walk_seconds/60)}분 + 추가 여유 ${transfer.extra_seconds/60}분 + 승차 여유 ${transfer.boarding_seconds}초 · 이후 대기 ${Math.max(0,Math.floor(transfer.wait_seconds/60))}분</p><small>${esc(names[transfer.basis]||transfer.basis)}${transfer.source?' · <a href="'+esc(transfer.source)+'" target="_blank" rel="noopener">출처 ↗</a>':''}</small></div></div>`:''}<article class="ride-event" style="--ride-color:${state.network.lines.find(v=>v.id===l.line_id).color}"><div class="ride-time">${fmtTime(l.board_at)}<span>${fmtTime(l.arrive_at)}</span></div><div class="ride-content"><div>${lineBadge(l.line_id)}<strong>${esc(lineName(l.line_id))}</strong><span class="train-type">${l.express?'급행':'일반'} · ${esc(l.train_no)}</span></div><h4>${esc(stationName(l.origin))} → ${esc(stationName(l.destination))}</h4><p>${esc(l.direction_label)} · ${l.stop_stations.length-1}개 정차 구간</p><small class="ride-source">${l.basis==='timetable'?'실제 운행 시간표':`실시간 ETA에서 후속 구간 추정 · 시간표 대비 ${l.delay_seconds>=0?'+':''}${Math.round(l.delay_seconds/60*10)/10}분`}</small><details><summary>시간표·예측 근거</summary><p>계획 ${fmtTime(l.scheduled_board_at)} 출발 / ${fmtTime(l.scheduled_arrive_at)} 도착${l.observed_at?`<br>원천 정보 생성 ${fmtTime(l.observed_at)}`:''}<br><a href="${esc(l.source)}" target="_blank" rel="noopener">공식 시간표 출처 ↗</a></p></details></div></article>`;
    }).join('')}<p class="itinerary-foot">${trip.skip_trains?`각 탑승에서 앞의 ${trip.skip_trains}대를 보내는 가정을 적용했습니다. `:''}열차 출도착은 예측이며 운행 상황에 따라 달라집니다. 실제 탑승 여부는 현장에서 확인해 주세요.</p></div>`;
  }

  function renderResults(previous){
    const r=state.result;
    $('#result-meta').textContent=`${fmtTime(r.as_of)} 계산 · 30초마다 자동 갱신`;
    $('#live-status').innerHTML=`<i class="${r.live.used_anchors?'':'is-muted'}"></i><span>${r.live.used_anchors?'실시간 도착정보 반영':'실제 시간표 기준'}${r.live.collected_at?` · ${fmtTime(r.live.collected_at)} 수신`:''}</span><button type="button" id="refresh-now">지금 갱신 ↻</button>`;
    $('#refresh-now').onclick=()=>search();
    const warning=r.warnings.filter(w=>!w.startsWith('시간표 미확보')).map(w=>`<p>${esc(w)}</p>`).join('');
    const available=routes();
    $('#results').innerHTML=`${warning?`<div class="calculation-notice">${warning}</div>`:''}${r.status==='unreachable'?'<p class="form-error">현재 계산에서 목표 시각을 맞추는 열차 조합을 찾지 못했습니다. 지금 출발 기준의 빠른 도착 대안을 확인하세요.</p>':''}<div class="journey-tabs" role="group" aria-label="경로 정렬">${Object.entries(labels).map(([key,label])=>`<button type="button" data-category="${key}" aria-pressed="${category===key}">${label}</button>`).join('')}</div>${available.length?`<div class="journey-options">${available.map(card).join('')}</div><div id="journey-detail"></div>`:`<div class="empty-state"><span>◷</span><div><h3>${r.status==='no_data'?'계산 가능한 실제 운행이 없습니다.':'이 조건으로 이용 가능한 경로가 없습니다.'}</h3><p>목표 시각·환승 조건을 바꾸거나 다른 정렬을 확인해 주세요.</p></div></div>`}<details class="data-coverage"><summary>계산 범위와 추정 기준</summary><p>시간표 ${r.coverage.line_ids.length}개 노선 · 현재 운행 ${r.live.matched_trains}편에 실시간 ETA 연결</p>${r.warnings.filter(w=>w.startsWith('시간표 미확보')).map(w=>`<p>${esc(w)}</p>`).join('')}${r.coverage.partial_lines?.length?`<p>일부 운행·정차역 시간표만 확보: ${r.coverage.partial_lines.map(v=>esc(v.name)).join(', ')}</p>`:''}${r.assumptions.map(v=>`<p>· ${esc(v)}</p>`).join('')}</details>`;
    $$('[data-category]').forEach(b=>b.onclick=()=>{category=b.dataset.category;selectedKey=null;renderResults();});
    $$('.journey-option').forEach(b=>b.onclick=()=>choose(b.dataset.key));
    if(available.length)choose(available.some(v=>v.route_key===selectedKey)?selectedKey:available[0].route_key);
    else{state.selectedTrip=null;renderRoute();}
    if(previous?.earliest&&r.earliest){
      const change=Math.round((r.earliest.arrive_ts-previous.earliest.arrive_ts)/60);
      $('#update-note').textContent=change?`이전 계산보다 가장 빠른 도착이 ${Math.abs(change)}분 ${change>0?'늦어졌습니다':'빨라졌습니다'}.`:'최신 열차 정보를 반영했습니다.';
    }
  }

  async function search({scroll=false}={}){
    if(busy)return;
    if(!state.origin||!state.destination){$('#form-error').textContent='출발역과 도착역을 목록에서 선택해 주세요.';$('#form-error').hidden=false;return;}
    busy=true;const request=++state.request;state.hasSearched=true;
    $('#form-error').hidden=true;$('#search-button').disabled=true;$('#search-button').textContent='실제 열차 조합을 계산하고 있어요…';
    $('#results').setAttribute('aria-busy','true');
    if(!state.result)$('#results').innerHTML='<div class="search-progress"><span class="spinner"></span><div><b>시간표와 현재 운행을 연결하고 있습니다.</b><p>일반·급행·행선지·환승시간을 확인해 여러 경로를 비교합니다.</p></div></div>';
    try{
      const response=await fetch('/api/recommendations',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({origin:state.origin,destination:state.destination,deadline:$('#deadline').value,allow_transfer:$('#transfer').checked,safety_minutes:Number($('#safety').value),transfer_extra_minutes:Number($('#transfer-extra').value),boarding_seconds:30,skip_trains:Number($('#skip-trains').value),max_transfers:Number($('#max-transfers').value),transfer_overrides:JSON.parse($('#transfer-overrides').dataset.value||'{}')})});
      const result=await response.json();if(request!==state.request)return;
      if(!response.ok)throw Error(result.error||'계산에 실패했습니다.');
      const previous=state.result;state.result=result;renderResults(previous);
      try{localStorage.setItem('yeoyu-route',JSON.stringify({origin:state.origin,destination:state.destination}));}catch{}
      if(scroll&&innerWidth<781)$('.results-section').scrollIntoView({behavior:'smooth',block:'start'});
    }catch(error){
      if(request!==state.request)return;
      $('#form-error').textContent=error.message;$('#form-error').hidden=false;
      if(!state.result)$('#results').innerHTML=`<div class="empty-state error"><span>!</span><div><h3>경로를 계산하지 못했습니다.</h3><p>${esc(error.message)}</p></div></div>`;
      else $('#update-note').textContent='갱신 실패 · 이전 계산 결과를 표시합니다.';
    }finally{
      busy=false;if(request===state.request){$('#search-button').disabled=false;$('#search-button').innerHTML='실제 열차로 경로 찾기 <span>↗</span>';$('#results').removeAttribute('aria-busy');}
    }
  }

  function startRefresh(){clearInterval(refreshTimer);refreshTimer=setInterval(()=>{if(state.hasSearched&&document.visibilityState==='visible'&&new Date($('#deadline').value)>new Date())search();},30000);}
  return {search,invalidate,startRefresh};
}
