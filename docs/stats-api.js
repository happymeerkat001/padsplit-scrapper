/* Report values are supplied by the private API; missing data stays missing. */
(function(root){
  'use strict';
  function parseReportResponse(response){
    if(!response || response.id!=='latest' || typeof response.json!=='string' || response.json.length>2000000 ||
       !(response.observed_at===null || typeof response.observed_at==='string'))throw new Error('Invalid report response.');
    const data=JSON.parse(response.json);
    if(!data || typeof data!=='object' || Array.isArray(data) || !data.kpis || typeof data.kpis!=='object' || Array.isArray(data.kpis))throw new Error('Invalid report data.');
    return {data,metrics:data.kpis,observedAt:response.observed_at,original:response.json};
  }
  // Mirrors the legacy temporary calculator. It never writes scraped data.
  function calculateManualScore(values){
    const fields=['occupancy_pct','avg_tenure_days','rooms_over_14d','listed_count','rooms_over_21d','avg_flip_days','flip_rooms_over_5d','open_tickets','tickets_over_7d','tickets_over_14d','daily_calls','todo_completion','ownership','other_hustle'];
    const parsed={};
    for(const field of fields){
      const raw=values?.[field];
      if((typeof raw!=='number'&&typeof raw!=='string')||String(raw).trim()==='')throw new Error('All score inputs are required.');
      const n=Number(raw);
      if(!Number.isFinite(n)||n<0||n>1000000)throw new Error('Invalid score input.');
      if(field==='occupancy_pct'&&n>100)throw new Error('Invalid occupancy.');
      if(['daily_calls','todo_completion','ownership','other_hustle'].includes(field)&&(!Number.isInteger(n)||n>5))throw new Error('Invalid hustle input.');
      if(['rooms_over_14d','listed_count','rooms_over_21d','flip_rooms_over_5d','open_tickets','tickets_over_7d','tickets_over_14d'].includes(field)&&!Number.isInteger(n))throw new Error('Counts must be whole numbers.');
      parsed[field]=n;
    }
    const bonuses=[],penalties=[];
    const plus=(label,points)=>bonuses.push({label,points});
    const minus=(label,points,count=1)=>penalties.push({label,points:-points,count});
    const occupancy=parsed.occupancy_pct,tenure=parsed.avg_tenure_days;
    if(occupancy>=90)plus('occupancy >= 90%',20);else if(occupancy>=80)plus('occupancy 80-89%',10);else if(occupancy<70)minus('occupancy < 70%',20);else if(occupancy<75)minus('occupancy 70-74%',10);
    if(tenure>=180)plus('avg tenure >= 180d',20);else if(tenure>=160)plus('avg tenure 160-179d',10);else if(tenure<140)minus('avg tenure < 140d',20);else if(tenure<152)minus('avg tenure 140-151d',10);
    const hustle=parsed.daily_calls+parsed.todo_completion+parsed.ownership+parsed.other_hustle;
    if(hustle)plus('daily hustle (subjective)',hustle);
    if(parsed.rooms_over_21d)minus('rooms listed > 21d',parsed.rooms_over_21d,parsed.rooms_over_21d);
    if(parsed.flip_rooms_over_5d)minus('flip rooms > 5d',2*parsed.flip_rooms_over_5d,parsed.flip_rooms_over_5d);
    if(parsed.tickets_over_14d)minus('open tickets > 14d',2*parsed.tickets_over_14d,parsed.tickets_over_14d);
    const bonus=bonuses.reduce((sum,item)=>sum+item.points,0),penalty=Math.abs(penalties.reduce((sum,item)=>sum+item.points,0));
    const score=Math.max(0,Math.min(125,Math.round(80+bonus-penalty)));
    const payoutScore=Math.min(100,score);
    const payout=payoutScore<=80?payoutScore*1000:80000+(payoutScore-80)*11000;
    return {score,payout,bonus,penalty,bonuses,penalties};
  }
  function manualScoreDefaults(kpis){
    const result={},valid=n=>typeof n==='number'&&Number.isFinite(n)&&n>=0&&n<=1000000;
    for(const name of ['occupancy_pct','avg_tenure_days','rooms_over_14d','avg_flip_days','open_tickets','tickets_over_7d','tickets_over_14d'])if(valid(kpis?.[name]))result[name]=kpis[name];
    if(valid(kpis?.listed))result.listed_count=kpis.listed;
    for(const [key,field,limit,target] of [['vacancy_rooms','days_listed',21,'rooms_over_21d'],['flip_rooms','days_in_flip',5,'flip_rooms_over_5d']]){
      const rows=kpis?.[key];
      if(Array.isArray(rows)&&rows.length<=5000&&rows.every(row=>row&&valid(row[field])))result[target]=rows.filter(row=>row[field]>limit).length;
    }
    if(valid(kpis?.subjective_score)){
      const total=Math.min(20,Math.round(kpis.subjective_score));
      ['daily_calls','todo_completion','ownership','other_hustle'].forEach((name,index)=>{result[name]=Math.min(5,Math.max(0,total-index*5));});
    }
    return result;
  }
  function mountManualScore(document,{onApply=()=>{},onReset=()=>{}}={}){
    const form=document.getElementById('report-score-form'),result=document.getElementById('report-score-result');
    const names=['occupancy_pct','avg_tenure_days','rooms_over_14d','listed_count','rooms_over_21d','avg_flip_days','flip_rooms_over_5d','open_tickets','tickets_over_7d','tickets_over_14d','daily_calls','todo_completion','ownership','other_hustle'];
    let initialized=false,dirty=false,defaults={},active=null;
    const clear=()=>{for(const name of names)form.querySelector(`[name="${name}"]`).value='';result.textContent='';initialized=false;dirty=false;defaults={};active=null;};
    const reset=()=>{for(const name of names)form.querySelector(`[name="${name}"]`).value=defaults[name]===undefined?'':String(defaults[name]);result.textContent='';dirty=false;active=null;onReset();};
    const initialize=kpis=>{defaults=manualScoreDefaults(kpis);if(initialized)return;if(!dirty){for(const name of names)if(defaults[name]!==undefined)form.querySelector(`[name="${name}"]`).value=String(defaults[name]);}initialized=true;};
    form.addEventListener('input',()=>{dirty=true;});
    document.getElementById('report-score-reset').addEventListener('click',reset);
    form.addEventListener('submit',event=>{
      event.preventDefault();
      try{
        const values=Object.fromEntries(names.map(name=>[name,form.querySelector(`[name="${name}"]`).value]));
        const estimate=calculateManualScore(values);
        active={estimate,values};onApply(estimate,values);
        const detail=[...estimate.bonuses,...estimate.penalties].map(item=>`${item.label}${item.count?' ('+item.count+')':''}: ${item.points}`).join('; ');
        result.textContent=`Raw score ${estimate.score} / 125; payout score ${Math.min(100,estimate.score)} / 100; estimated payout ₦${estimate.payout.toLocaleString('en-US')}. Base 80 + ${estimate.bonus} bonus − ${estimate.penalty} penalties. Average flip ${Number(values.avg_flip_days)} days. ${detail||'No bonus or penalty triggers.'} Temporary calculation; not saved.`;
      }catch(_){result.textContent='Enter valid values for all required fields.';}
    });
    return {clear,initialize,reapply(){if(active)onApply(active.estimate,active.values);}};
  }
  function reportFreshness(report,now=new Date()){
    const raw=report.data.scraped_at, t=typeof raw==='string'?Date.parse(raw):NaN;
    const run=report.data.run_status;
    const degraded=run && typeof run==='object' && (run.state==='degraded' || run.fallback_used===true);
    const stale=!Number.isFinite(t) || t>now.getTime()+300000 || now.getTime()-t>48*3600000 || degraded;
    return {state:stale?'stale':'fresh',scrapedAt:Number.isFinite(t)?raw:null,degraded:!!degraded};
  }
  function renderScoreCard(document,metrics,manual=false){
    const avg=manual?metrics.score:metrics.avg_score_30d,current=metrics.score;
    const finite=n=>typeof n==='number'&&Number.isFinite(n)&&Math.abs(n)<=Number.MAX_SAFE_INTEGER;
    const payout=n=>{const normalized=Math.max(0,Math.min(100,n));return normalized<=80?normalized*1000:80000+(normalized-80)*11000;};
    const summary=document.getElementById('report-score-summary');
    summary.textContent=finite(avg)?`${manual?'Manual score':'Month average score'} ${Math.max(0,Math.min(100,avg)).toFixed(manual?0:1)} / 100; estimated payout ₦${payout(avg).toLocaleString('en-US')}.${manual?' Temporary calculation; not saved.':''}`:'Month average score and payout unavailable.';
    const formula=document.getElementById('report-score-formula');
    if(formula)formula.textContent=[current,metrics.base_score,metrics.bonus_points,metrics.penalty_points].every(finite)?`${manual?'Manual':'Current'}: ${Math.max(0,Math.min(100,current)).toFixed(0)} (₦${payout(current).toLocaleString('en-US')}) from ${metrics.base_score} base + ${metrics.bonus_points} bonus − ${Math.abs(metrics.penalty_points)} penalties`:'Current score formula unavailable.';
    const flip=document.getElementById('report-score-flip');if(flip)flip.textContent=finite(metrics.avg_flip_days)?`Average flip ${metrics.avg_flip_days.toFixed(1)} days`:'Average flip unavailable.';
    const breakdown=document.getElementById('report-score-breakdown');breakdown.replaceChildren();
    for(const [key,label] of [['bonuses','Bonuses'],['penalties','Penalties']]){
      const section=document.createElement('section'),heading=document.createElement('h3');heading.textContent=label;section.appendChild(heading);
      const entries=metrics[key];
      if(!Array.isArray(entries)){const p=document.createElement('p');p.textContent='Unavailable';section.appendChild(p);}
      else if(entries.length===0){const p=document.createElement('p');p.textContent='No triggers';section.appendChild(p);}
      else {const list=document.createElement('ul');for(const entry of entries.slice(0,5000)){const item=document.createElement('li');item.textContent=`${typeof entry?.label==='string'?entry.label:'Unavailable'}${Number.isInteger(entry?.count)&&entry.count>0?' ('+entry.count+')':''}: ${cellValue(entry?.points)}`;list.appendChild(item);}section.appendChild(list);}
      breakdown.appendChild(section);
    }
  }
  function renderReport(document,report){
    const fields=[['occupancy_pct','Listed occupancy','%'],['total_rooms','Total rooms',''],['occupied','Marked occupied',''],['flip','Flip rooms',''],['move_in','Move-in',''],['avg_listed_days','Average listed',' days'],['median_tenure_days','Median tenure',' days'],['max_flip_days','Maximum flip',' days'],['avg_tenure_days','Average tenure',' days'],
      ['rooms_over_14d','Listed over 14 days',''],['listed','Listed rooms',''],
      ['rooms_over_30d','Listed over 30 days',''],['avg_flip_days','Average flip',' days'],
      ['open_tickets','Open tickets',''],
      ['tickets_over_7d','Tickets over 7 days',''],['tickets_over_14d','Tickets over 14 days',''],
      ['monthly_net_revenue','Monthly net revenue',''],['revenue_per_room','Revenue per room','']];
    const container=document.getElementById('report-metrics');container.replaceChildren();
    for(const [key,label,suffix] of fields){
      const card=document.createElement('div'),title=document.createElement('dt'),value=document.createElement('dd');
      title.textContent=label;const n=report.metrics[key];
      value.textContent=typeof n==='number' && Number.isFinite(n) && Math.abs(n)<=Number.MAX_SAFE_INTEGER ? String(n)+suffix : 'Unavailable';
      card.append(title,value);container.appendChild(card);
    }
    renderScoreCard(document,report.metrics);
    const freshness=reportFreshness(report), label=document.getElementById('report-freshness');
    label.textContent=(freshness.state==='stale'?'Stale or incomplete':'Current') +
      (freshness.scrapedAt?' · source scraped '+freshness.scrapedAt:' · source scrape time unavailable')+
      (freshness.degraded?' · collector reported degraded data':'')+
      (report.observedAt?' · stored '+report.observedAt:' · stored time unavailable');
    document.getElementById('report-source').textContent=report.original;
  }
  function parseHistoryResponse(response){
    if(!response || response.id!=='monthly_history' || typeof response.json!=='string' || response.json.length>2000000 ||
       !(response.observed_at===null || typeof response.observed_at==='string'))throw new Error('Invalid history response.');
    const data=JSON.parse(response.json);
    if(!data || !Array.isArray(data.months) || data.months.length>3000 || data.months.some(x=>!x || typeof x!=='object' || Array.isArray(x)))throw new Error('Invalid history data.');
    return {months:data.months,updatedAt:typeof data.updated_at==='string'?data.updated_at:null,observedAt:response.observed_at};
  }
  function cellValue(v){
    if(typeof v==='string')return v;
    if(typeof v==='number' && Number.isFinite(v) && Math.abs(v)<=Number.MAX_SAFE_INTEGER)return String(v);
    if(typeof v==='boolean')return v?'Yes':'No';
    return 'Unavailable';
  }
  function table(document,container,title,rows,columns){
    const section=document.createElement('section'),heading=document.createElement('h3');heading.textContent=title;section.appendChild(heading);
    if(!Array.isArray(rows) || rows.length>5000){const p=document.createElement('p');p.textContent='Data unavailable.';section.appendChild(p);container.appendChild(section);return;}
    if(!rows.length){const p=document.createElement('p');p.textContent='No records in this report.';section.appendChild(p);container.appendChild(section);return;}
    const tbl=document.createElement('table'),head=document.createElement('thead'),headRow=document.createElement('tr'),body=document.createElement('tbody');
    for(const [,label] of columns){const th=document.createElement('th');th.textContent=label;th.scope='col';headRow.appendChild(th);}head.appendChild(headRow);tbl.appendChild(head);
    for(const row of rows){const tr=document.createElement('tr');for(const [key] of columns){const td=document.createElement('td');td.textContent=cellValue(row && typeof row==='object'?row[key]:null);tr.appendChild(td);}body.appendChild(tr);}
    tbl.appendChild(body);section.appendChild(tbl);container.appendChild(section);
  }
  function chart(document,container,title,rows,labelKey,valueKey){
    if(!Array.isArray(rows) || rows.length>5000)return;
    const valid=rows.filter(row=>row && typeof row[valueKey]==='number' && Number.isFinite(row[valueKey]) && Math.abs(row[valueKey])<=Number.MAX_SAFE_INTEGER);
    if(!valid.length)return;
    const section=document.createElement('section'),heading=document.createElement('h3'),description=document.createElement('p');
    heading.textContent=title;description.textContent='Bar length shows magnitude; negative values retain their sign in the label.';section.append(heading,description);
    const max=Math.max(...valid.map(row=>Math.abs(row[valueKey]))) || 1;
    for(const row of valid){const line=document.createElement('div'),label=document.createElement('span'),bar=document.createElement('span');
      label.textContent=cellValue(row[labelKey])+': '+cellValue(row[valueKey]);bar.className='report-bar';bar.style.width=(Math.abs(row[valueKey])/max*100)+'%';bar.setAttribute('aria-hidden','true');line.append(label,bar);section.appendChild(line);}
    container.appendChild(section);
  }
  function renderReportDetails(document,report){
    const container=document.getElementById('report-detail');container.replaceChildren();const k=report.metrics;
    chart(document,container,'Net revenue trend',k.monthly_revenue_series,'label','net_revenue');
    table(document,container,'Monthly net revenue',k.monthly_revenue_series,[['label','Month'],['net_revenue','Net revenue']]);
    table(document,container,'Listed rooms',k.vacancy_rooms,[['property','Property'],['room_number','Room'],['days_listed','Days listed'],['base_price','Base price'],['last_room_price','Last room price']]);
    table(document,container,'Rooms in flip',k.flip_rooms,[['property','Property'],['room_number','Room'],['days_in_flip','Days in flip']]);
    table(document,container,'Open tickets',k.open_ticket_items,[['property_address','Property'],['room_number','Room'],['category','Category'],['status','Status'],['age_days','Age in days']]);
    table(document,container,'Property breakdown',k.per_property,[['property','Property'],['rooms','Rooms'],['occupancy_pct','Occupancy %'],['listed','Listed'],['flip','Flip'],['mini_score','Mini score']]);
    const hist=k.retention_histogram;
    chart(document,container,'Retention distribution',hist && typeof hist==='object' && !Array.isArray(hist)?Object.entries(hist).map(([range,count])=>({range,count})):null,'range','count');
    table(document,container,'Retention histogram',hist && typeof hist==='object' && !Array.isArray(hist)?Object.entries(hist).map(([range,count])=>({range,count})):null,[['range','Days'],['count','Count']]);
  }
  function renderHistory(document,history){
    const container=document.getElementById('report-history');container.replaceChildren();
    const note=document.createElement('p');note.textContent='History updated '+(history.updatedAt || 'time unavailable')+' · stored '+(history.observedAt || 'time unavailable');container.appendChild(note);
    const rows=[...history.months].sort((a,b)=>String(b.month||'').localeCompare(String(a.month||''))).map(row=>{
      const valid=typeof row.score==='number'&&Number.isFinite(row.score)&&Math.abs(row.score)<=Number.MAX_SAFE_INTEGER;
      const score=valid?Math.max(0,Math.min(100,row.score)):null;
      const sum=items=>Array.isArray(items)&&items.every(x=>x&&typeof x.points==='number'&&Number.isFinite(x.points))?items.reduce((total,x)=>total+x.points,0):null;
      const details=items=>Array.isArray(items)&&items.every(x=>x&&typeof x.label==='string'&&typeof x.points==='number'&&Number.isFinite(x.points))?items.map(x=>`${x.label}${Number.isInteger(x.count)&&x.count>0?' ('+x.count+')':''}: ${x.points}`).join('; '):null;
      return {...row,normalized_score:score,bonuses_total:sum(row.bonuses),penalties_total:sum(row.penalties),bonus_details:details(row.bonuses),penalty_details:details(row.penalties),payout:score===null?null:score<=80?score*1000:80000+(score-80)*11000};
    });
    const latest=rows[0], occupancy=rows.map(row=>row.occupancy_pct).filter(n=>typeof n==='number'&&Number.isFinite(n)&&Math.abs(n)<=Number.MAX_SAFE_INTEGER);
    const summary=document.createElement('p');summary.textContent=latest?`Latest snapshot: ${cellValue(latest.month)} · Occupancy ${cellValue(latest.occupancy_pct)}% · Flip days ${cellValue(latest.avg_flip_days)} · Tenure ${cellValue(latest.avg_tenure_days)} · Score ${cellValue(latest.normalized_score)} · Payout ₦${cellValue(latest.payout)} · ${rows.length} months · Best occupancy ${occupancy.length?String(Math.max(...occupancy))+'%':'Unavailable'}`:'No history data available.';container.appendChild(summary);
    const chronological=[...rows].reverse();
    chart(document,container,'Occupancy by month',chronological,'month','occupancy_pct');
    chart(document,container,'Score by month',chronological,'month','normalized_score');
    chart(document,container,'Payout by month',chronological,'month','payout');
    chart(document,container,'Flip days by month',chronological,'month','avg_flip_days');
    chart(document,container,'Tenure by month',chronological,'month','avg_tenure_days');
    table(document,container,'Monthly history',rows,[['month','Month'],['occupancy_pct','Occupancy %'],['avg_flip_days','Average flip days'],['avg_tenure_days','Average tenure days'],['normalized_score','Score'],['payout','Estimated payout (₦)'],['bonuses_total','Bonuses'],['bonus_details','Bonus details'],['penalties_total','Penalties'],['penalty_details','Penalty details'],['partial','Partial']]);
  }
  function createHistoryCharts(document,history,ChartClass){
    if(typeof ChartClass!=='function')return {destroy(){}};
    const rows=[...history.months].sort((a,b)=>String(a.month||'').localeCompare(String(b.month||'')));
    const labels=rows.map(row=>typeof row.month==='string'?row.month:'Unavailable');
    const metric=key=>rows.map(row=>typeof row[key]==='number'&&Number.isFinite(row[key])&&Math.abs(row[key])<=Number.MAX_SAFE_INTEGER?row[key]:null);
    const scores=metric('score').map(n=>n===null?null:Math.max(0,Math.min(100,n)));
    const payouts=scores.map(n=>n===null?null:n<=80?n*1000:80000+(n-80)*11000);
    const line=(label,data,color)=>({label,data,borderColor:color,backgroundColor:color,tension:.25,pointRadius:3,spanGaps:false});
    const threshold=(label,value,color)=>({label,data:rows.map(()=>value),borderColor:color,borderDash:[5,5],pointRadius:0});
    const common={responsive:true,maintainAspectRatio:false,animation:{duration:400},plugins:{legend:{display:true}},scales:{y:{min:0}}};
    const specs=[
      ['history-chart-occupancy','line',[line('Occupancy %',metric('occupancy_pct'),'rgb(56,189,248)'),threshold('Target 87.5%',87.5,'rgba(34,197,94,0.7)'),threshold('Warn 75%',75,'rgba(251,191,36,0.7)')],{...common,scales:{y:{min:0,max:100,ticks:{callback:v=>`${v}%`}}}}],
      ['history-chart-payout','bar',[{label:'Estimated payout (₦)',data:payouts,backgroundColor:scores.map(n=>n===null?'rgba(148,163,184,0.2)':n>=90?'rgba(34,197,94,0.55)':n>=70?'rgba(251,191,36,0.55)':'rgba(248,113,113,0.55)'),borderWidth:1,borderRadius:4}],{...common,plugins:{legend:{display:false},tooltip:{callbacks:{label:ctx=>scores[ctx.dataIndex]===null?'Unavailable':`${scores[ctx.dataIndex].toFixed(0)} (₦${payouts[ctx.dataIndex].toLocaleString('en-US')})`}}},scales:{y:{min:0,max:300000,ticks:{callback:v=>`₦${Number(v).toLocaleString('en-US')}`}}}}],
      ['history-chart-flip','line',[line('Average flip days',metric('avg_flip_days'),'rgb(251,191,36)'),threshold('Warn >5d',5,'rgba(248,113,113,0.7)')],common],
      ['history-chart-tenure','line',[line('Average tenure days',metric('avg_tenure_days'),'rgb(34,197,94)'),threshold('Bonus >120d',120,'rgba(56,189,248,0.6)')],common],
    ];
    const charts=[];
    try{for(const [id,type,datasets,options] of specs)charts.push(new ChartClass(document.getElementById(id),{type,data:{labels,datasets},options}));}
    catch(error){for(const chart of charts)chart.destroy();throw error;}
    return {destroy(){for(const chart of charts)chart.destroy();}};
  }
  root.parseReportResponse=parseReportResponse;root.calculateManualScore=calculateManualScore;root.manualScoreDefaults=manualScoreDefaults;root.mountManualScore=mountManualScore;root.reportFreshness=reportFreshness;root.renderReport=renderReport;root.renderScoreCard=renderScoreCard;
  root.parseHistoryResponse=parseHistoryResponse;root.renderReportDetails=renderReportDetails;root.renderHistory=renderHistory;root.createHistoryCharts=createHistoryCharts;
})(typeof window!=='undefined'?window:globalThis);
