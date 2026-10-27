const number=value=>new Intl.NumberFormat("en-US",{maximumFractionDigits:1}).format(value);
const displayDate=value=>{
  if(!value)return "Date unavailable";
  const parts=String(value).split("-").map(Number);
  if(parts.length!==3||parts.some(part=>!Number.isFinite(part)))return String(value);
  return new Intl.DateTimeFormat("en-US",{weekday:"long",month:"long",day:"numeric",year:"numeric"}).format(new Date(parts[0],parts[1]-1,parts[2]));
};

const NAV_GROUPS = [
  {label:'Campus Energy', links:[['energy-usage.html','Energy Usage'],['load-forecasting.html','Load Forecasting'],['what-ifs.html','What-Ifs'],['data.html','Data']]},
  {label:'Learn & Explore', links:[['power-basics.html','Power Basics'],['fun-facts.html','Fun Facts'],['dorm-facts.html','Dorm Facts'],['campus-map.html','Campus Map'],['sustainability-resources.html','Sustainability Resources']]},
  {label:'About AVATAR', links:[['why-avatar.html','Why AVATAR?'],['meet-the-team.html','Meet the Team'],['acknowledgements.html','Acknowledgements']]}
];
(function renderNav(){
 const nav=document.getElementById('main-navigation');
 if(!nav)return;
 const menu=document.querySelector('.menu-button');
 const current=location.pathname.split('/').pop()||'index.html';
 nav.innerHTML=NAV_GROUPS.map((group,i)=>`<div class="nav-group"><button type="button" class="nav-group-button${group.links.some(([href])=>href===current)?' current-group':''}" aria-expanded="false" aria-controls="nav-panel-${i}">${group.label}<span class="nav-chevron" aria-hidden="true"></span></button><div class="nav-panel" id="nav-panel-${i}" hidden>${group.links.map(([href,label])=>`<a href="${href}"${href===current?' class="active" aria-current="page"':''}>${label}</a>`).join('')}</div></div>`).join('');
 const groups=[...nav.querySelectorAll('.nav-group')];
 function show(target){groups.forEach(g=>{const open=g===target;g.querySelector('button').setAttribute('aria-expanded',String(open));g.querySelector('.nav-panel').hidden=!open;});}
 const desktop=matchMedia('(min-width:1081px)');
 groups.forEach(g=>{
  const button=g.querySelector('button');
  button.addEventListener('click',()=>show(button.getAttribute('aria-expanded')==='true'?null:g));
  g.addEventListener('pointerenter',e=>{if(desktop.matches&&e.pointerType==='mouse')show(g);});
  g.addEventListener('pointerleave',()=>{if(desktop.matches&&!g.contains(document.activeElement))show(null);});
  button.addEventListener('keydown',e=>{if(e.key==='ArrowDown'){e.preventDefault();show(g);g.querySelector('a').focus();}});
  g.addEventListener('focusout',()=>{setTimeout(()=>{if(!g.contains(document.activeElement)&&button.getAttribute('aria-expanded')==='true')show(null);},0);});
 });
 nav.addEventListener('keydown',e=>{if(e.key==='Escape'){const open=groups.find(g=>g.querySelector('button').getAttribute('aria-expanded')==='true');if(open){open.querySelector('button').focus();show(null);}else{nav.classList.remove('open');menu?.setAttribute('aria-expanded','false');menu?.focus();}}});
 menu?.addEventListener('click',()=>{const open=nav.classList.toggle('open');menu.setAttribute('aria-expanded',String(open));if(!open)show(null);});
 nav.addEventListener('click',e=>{if(e.target.closest('a')){show(null);nav.classList.remove('open');menu?.setAttribute('aria-expanded','false');}});
 document.addEventListener('click',e=>{if(!nav.contains(e.target)&&!menu?.contains(e.target)){show(null);nav.classList.remove('open');menu?.setAttribute('aria-expanded','false');}});
 desktop.addEventListener('change',()=>{show(null);nav.classList.remove('open');menu?.setAttribute('aria-expanded','false');});
})();

async function requestForecastData(){
  const apiBase=document.querySelector('meta[name="avatar-api-url"]')?.content?.replace(/\/$/,"");
  if(document.querySelector('meta[name="avatar-api-url"]')){
    try{
      const response=await fetch((apiBase||"")+"/api/forecast",{cache:"no-store",signal:AbortSignal.timeout(45000)});
      if(!response.ok)throw new Error("Live forecast request failed");
      const liveData=await response.json();
      if(liveData.status==="error")throw new Error(liveData.message||"Live forecast request failed");
      return liveData;
    }catch(error){
      console.warn("Using the static AVATAR forecast fallback.",error);
    }
  }
  const response=await fetch("static/data/load_forecast.json",{cache:"no-store"});
  if(!response.ok)throw new Error("Forecast data request failed");
  return response.json();
}

function renderForecastWeather(weather){
  const panel=document.querySelector("#forecast-weather");
  const unavailable=document.querySelector("#forecast-weather-unavailable");
  if(!panel||!unavailable)return;
  if(weather?.status!=="available"){
    panel.hidden=true;
    unavailable.hidden=false;
    return;
  }
  document.querySelector("#weather-provider").textContent=weather.provider||"weather service";
  document.querySelector("#weather-date").textContent=displayDate(weather.forecast_date);
  document.querySelector("#weather-temperature").textContent=number(weather.temperature_low_f)+"–"+number(weather.temperature_high_f)+"°F";
  document.querySelector("#weather-humidity").textContent=number(weather.average_humidity_pct)+"%";
  document.querySelector("#weather-precipitation").textContent=number(weather.precipitation_total_in)+" in";
  document.querySelector("#weather-note").textContent=weather.note||"Weather is displayed for context and is not yet applied to demand.";
  unavailable.hidden=true;
  panel.hidden=false;
}

async function renderForecast(){
  const chart=document.querySelector("#forecast-chart");
  if(!chart)return;
  const loading=document.querySelector("#forecast-loading");
  const content=document.querySelector("#forecast-content");
  const errorPanel=document.querySelector("#forecast-error");
  try{
    const data=await requestForecastData();
    if(!Array.isArray(data.hourly)||![23,24,25].includes(data.hourly.length))throw new Error("Forecast data is incomplete");
    chart.style.gridTemplateColumns=`repeat(${data.hourly.length},minmax(27px,1fr))`;
    const maximum=Math.max(...data.hourly.map(row=>Number(row.forecast_kw)),1);
    chart.innerHTML=data.hourly.map(row=>{
      const value=Number(row.forecast_kw);
      const height=Math.max(5,value/maximum*100);
      return '<div class="bar-wrap"><span class="bar-value">'+number(value)+'</span><div class="bar-track"><div class="bar" title="'+row.label+': '+number(value)+' kW" style="height:'+height+'%"></div></div><span class="bar-label">'+row.label+'</span></div>';
    }).join("");
    document.querySelector("#forecast-daily-energy").textContent=number(data.daily_energy_mwh)+" MWh";
    document.querySelector("#forecast-average-load").textContent=number(data.average_load_kw)+" kW";
    document.querySelector("#forecast-peak-load").textContent=number(data.peak_load_kw)+" kW";
    document.querySelector("#forecast-peak-time").textContent="Expected near "+data.peak_label+".";
    document.querySelector("#forecast-warning").textContent=data.warning;
    document.querySelector("#forecast-date").textContent=data.forecast_date?"Profile date: "+displayDate(data.forecast_date):"Static representative profile";
    document.querySelector("#forecast-method").textContent=data.method;
    renderForecastWeather(data.weather);
    loading.hidden=true;
    content.hidden=false;
  }catch(error){
    loading.hidden=true;
    errorPanel.hidden=false;
    console.error(error);
  }
}
const currency=value=>new Intl.NumberFormat("en-US",{style:"currency",currency:"USD",maximumFractionDigits:0}).format(value);
const integer=value=>new Intl.NumberFormat("en-US",{maximumFractionDigits:0}).format(value);

function scenarioValue(selector){return Number(document.querySelector(selector)?.value);}

function scenarioRequest(){
  return {
    displayed_season:document.querySelector("#displayed-season")?.value,
    selected_panels:scenarioValue("#selected-panels"),
    selected_bess_units:scenarioValue("#selected-bess"),
    energy_conservation_percent:scenarioValue("#conservation"),
    occupancy_load_change_percent:scenarioValue("#occupancy-change"),
    temperature_load_change_percent:scenarioValue("#temperature-change")
  };
}

function showScenarioState(state,message=""){
  const ids=["scenario-empty","scenario-loading","scenario-error","scenario-results"];
  ids.forEach(id=>{const element=document.querySelector("#"+id);if(element)element.hidden=id!==state;});
  if(state==="scenario-error")document.querySelector("#scenario-error-message").textContent=message;
  if(state!=="scenario-results"){
    document.querySelector("#comparison-card")?.setAttribute("hidden","");
    document.querySelector("#bess-card")?.setAttribute("hidden","");
    document.querySelector("#model-details")?.setAttribute("hidden","");
  }
}

function renderBessOperation(rows){
  const chart=document.querySelector("#bess-operation-chart");
  if(!chart||!Array.isArray(rows))return;
  const maximumPower=Math.max(...rows.flatMap(row=>[Number(row.Solar_To_Battery_kW),Number(row.Battery_Discharge_kW)]),1);
  const maximumSoc=Math.max(...rows.map(row=>Number(row.Battery_SOC_kWh)),1);
  chart.innerHTML=rows.map(row=>{
    const charge=Number(row.Solar_To_Battery_kW)||0;
    const discharge=Number(row.Battery_Discharge_kW)||0;
    const soc=Number(row.Battery_SOC_kWh)||0;
    const hour=Number(row.Hour);
    const label=String(hour).padStart(2,"0");
    return '<div class="bess-hour">'+
      '<span class="bess-soc-label">'+number(soc)+' kWh</span>'+
      '<div class="bess-bar charge" title="'+label+':00 charge: '+number(charge)+' kW" style="height:'+Math.max(charge?2:0,charge/maximumPower*100)+'%"></div>'+
      '<div class="bess-bar discharge" title="'+label+':00 discharge: '+number(discharge)+' kW" style="height:'+Math.max(discharge?2:0,discharge/maximumPower*100)+'%"></div>'+
      '<div class="bess-bar soc" title="'+label+':00 state of charge: '+number(soc)+' kWh" style="height:'+Math.max(soc?2:0,soc/maximumSoc*100)+'%"></div>'+
      '<span class="load-label">'+(hour%2===0?label:"")+'</span></div>';
  }).join("");
}

function renderLoadComparison(rows,seasonRows){
  const chart=document.querySelector("#scenario-load-chart");
  if(!chart||!Array.isArray(rows))return;
  const gridByHour=new Map((Array.isArray(seasonRows)?seasonRows:[]).map(row=>[Number(row.Hour),Number(row.Grid_Supply_kW)]));
  const maximum=Math.max(...rows.flatMap(row=>[Number(row.baseline_kw),Number(row.adjusted_kw),gridByHour.get(Number(row.hour))||0]),1);
  chart.innerHTML=rows.map(row=>{
    const baseline=Number(row.baseline_kw);
    const adjusted=Number(row.adjusted_kw);
    const grid=gridByHour.get(Number(row.hour))??0;
    const label=String(row.hour).padStart(2,"0");
    return '<div class="load-hour">'+
      '<div class="load-bar" title="'+label+':00 baseline: '+number(baseline)+' kW" style="height:'+Math.max(2,baseline/maximum*100)+'%"></div>'+
      '<div class="load-bar adjusted" title="'+label+':00 adjusted: '+number(adjusted)+' kW" style="height:'+Math.max(2,adjusted/maximum*100)+'%"></div>'+
      '<div class="load-bar grid" title="'+label+':00 grid after PV + BESS: '+number(grid)+' kW" style="height:'+Math.max(2,grid/maximum*100)+'%"></div>'+
      '<span class="load-label">'+(row.hour%2===0?label:"")+'</span></div>';
  }).join("");
}

function renderModelTable(targetId,rows){
  const target=document.querySelector("#"+targetId);
  if(!target||!Array.isArray(rows)||rows.length===0)return;
  const columns=Object.keys(rows[0]);
  const formatted=value=>typeof value==="number"?number(value):String(value);
  target.innerHTML='<table class="model-table"><thead><tr>'+columns.map(column=>'<th>'+column.replaceAll("_"," ")+'</th>').join("")+'</tr></thead><tbody>'+rows.map(row=>'<tr>'+columns.map(column=>'<td>'+formatted(row[column])+'</td>').join("")+'</tr>').join("")+'</tbody></table>';
}

function renderScenarioResults(data){
  const whatIf=data.what_if_tab;
  const summary=whatIf.summary;
  document.querySelector("#scenario-result-title").textContent="Your selected scenario";
  document.querySelector("#result-panels").textContent=integer(summary.panels_selected);
  document.querySelector("#result-bess").textContent=integer(summary.bess_units_selected);
  document.querySelector("#result-project-cost").textContent=currency(summary["project_cost_$"]);
  document.querySelector("#result-savings").textContent=currency(summary["annual_total_operating_savings_$"])+"/yr";
  document.querySelector("#result-grid-reduction").textContent=integer(summary.annual_grid_energy_reduction_kwh)+" kWh/yr";
  document.querySelector("#result-renewable-share").textContent=number(summary.renewable_share_of_load_percent)+"%";
  document.querySelector("#scenario-summary").textContent="Adjusted daily demand is "+number(summary.adjusted_daily_energy_kwh)+" kWh with an estimated peak of "+number(summary.adjusted_peak_kw)+" kW. The selected equipment has an estimated project cost of "+currency(summary["project_cost_$"])+".";
  renderLoadComparison(whatIf.load_comparison,whatIf.selected_season_hourly);
  renderBessOperation(whatIf.selected_season_hourly);
  renderModelTable("commitment-table",whatIf.unit_commitment);
  renderModelTable("dispatch-table",whatIf.economic_dispatch);
  showScenarioState("scenario-results");
  document.querySelector("#comparison-card")?.removeAttribute("hidden");
  document.querySelector("#bess-card")?.removeAttribute("hidden");
  document.querySelector("#model-details")?.removeAttribute("hidden");
}

async function runScenario(event){
  event.preventDefault();
  const form=event.currentTarget;
  if(!form.reportValidity())return;
  const button=document.querySelector("#run-optimization");
  const apiBase=document.querySelector('meta[name="avatar-api-url"]')?.content?.replace(/\/$/,"");
  if(!apiBase){showScenarioState("scenario-error","The optimization API URL has not been configured.");return;}
  const payload=scenarioRequest();
  showScenarioState("scenario-loading");
  if(button)button.disabled=true;
  try{
    const response=await fetch(apiBase+"/api/optimize",{
      method:"POST",
      headers:{"Content-Type":"application/json"},
      body:JSON.stringify(payload)
    });
    const data=await response.json().catch(()=>({}));
    if(!response.ok||data.status!=="optimal")throw new Error(data.message||"The optimization service returned an error.");
    renderScenarioResults(data);
  }catch(error){
    const message=error instanceof TypeError
      ?"The webpage could not reach the optimization service. Confirm that the Python API is running and that its URL is correct."
      :error.message;
    showScenarioState("scenario-error",message);
  }finally{
    if(button)button.disabled=false;
  }
}

document.querySelector("#scenario-form")?.addEventListener("submit",runScenario);

function updateStudentActions(){
  const population=8000;
  const participation=Number(document.querySelector("#student-participation")?.value||0);
  const participants=Math.round(population*participation/100);
  let perStudentKwh=0;
  document.querySelectorAll(".action-card").forEach(card=>{
    if(card.querySelector("[data-action-toggle]")?.checked){
      perStudentKwh+=Math.max(0,Number(card.querySelector("[data-action-kwh]")?.value)||0);
    }
  });
  const dailyKwh=participants*perStudentKwh;
  const set=(selector,value)=>{const target=document.querySelector(selector);if(target)target.textContent=value;};
  set("#student-participation-label",participation+"%");
  set("#action-participants",integer(participants));
  set("#action-daily-mwh",number(dailyKwh/1000)+" MWh");
  set("#action-baseline-percent",number(dailyKwh/104000*100)+"%");
  set("#action-average-kw",number(dailyKwh/24)+" kW");
}
document.querySelector("#student-action-list")?.addEventListener("input",updateStudentActions);
document.querySelector("#student-participation")?.addEventListener("input",updateStudentActions);
updateStudentActions();
renderForecast();

// Shared static-site chatbot. The provider key remains server-side on Render.
(()=>{
  if(document.querySelector("#avatar-chat-toggle"))return;
  const apiBase=document.querySelector('meta[name="avatar-api-url"]')?.content?.replace(/\/$/,"")||"https://avatar-api-5i2l.onrender.com";
  const style=document.createElement("style");
  style.textContent=`
    #avatar-chat-toggle{position:fixed;right:22px;bottom:22px;z-index:1100;display:flex;align-items:center;gap:11px;min-height:66px;padding:0 26px;border:1px solid rgba(255,255,255,.2);border-radius:999px;background:linear-gradient(135deg,#0b2d50,#124b59);color:#fff;font:800 17px/1 system-ui,sans-serif;box-shadow:0 18px 42px rgba(6,29,54,.32);cursor:pointer;animation:avatar-pulse 2.4s ease-in-out infinite}
@keyframes avatar-pulse{0%,100%{box-shadow:0 18px 42px rgba(6,29,54,.32)}50%{box-shadow:0 18px 42px rgba(6,29,54,.32),0 0 0 8px rgba(88,214,160,.16)}}
    #avatar-chat-toggle::before{content:"";width:10px;height:10px;border-radius:50%;background:#58d6a0;box-shadow:0 0 0 5px rgba(88,214,160,.14)}
    #avatar-chat-panel{position:fixed;right:22px;bottom:86px;z-index:1100;width:min(390px,calc(100vw - 28px));height:min(540px,calc(100vh - 120px));display:flex;flex-direction:column;overflow:hidden;border:1px solid #d5e1e7;border-radius:20px;background:#fff;color:#082b4c;font:14px/1.45 system-ui,sans-serif;box-shadow:0 26px 70px rgba(6,29,54,.28)}
    #avatar-chat-panel[hidden]{display:none}.avatar-chat-head{display:flex;align-items:center;justify-content:space-between;padding:17px 18px;background:linear-gradient(135deg,#082b4c,#124b59);color:#fff}.avatar-chat-head small{display:block;margin-top:3px;color:#aee6cb;font-size:10px;font-weight:700;letter-spacing:.08em;text-transform:uppercase}.avatar-chat-close{border:0;background:transparent;color:#fff;font-size:27px;line-height:1;cursor:pointer}
    #avatar-chat-log{flex:1;display:flex;flex-direction:column;gap:10px;overflow-y:auto;padding:17px;background:#f6f9f8}.avatar-chat-message{max-width:86%;padding:10px 12px;border-radius:14px;white-space:pre-wrap;overflow-wrap:anywhere}.avatar-chat-bot{align-self:flex-start;border:1px solid #dce7e3;background:#fff}.avatar-chat-user{align-self:flex-end;background:#0b2d50;color:#fff}
    #avatar-chat-form{display:flex;gap:8px;padding:12px;border-top:1px solid #dbe5e9;background:#fff}#avatar-chat-input{min-width:0;flex:1;padding:11px 12px;border:1px solid #bfcfd7;border-radius:11px;color:#082b4c;font:inherit}#avatar-chat-form button{padding:0 15px;border:0;border-radius:11px;background:#2e8b57;color:#fff;font-weight:800;cursor:pointer}#avatar-chat-form button:disabled,#avatar-chat-input:disabled{opacity:.65}
    .avatar-chat-head-actions{display:flex;align-items:center;gap:8px}
    .avatar-chat-newchat{background:transparent;border:1px solid rgba(255,255,255,.5);color:#fff;border-radius:7px;padding:5px 10px;font-size:12px;cursor:pointer}
    .avatar-chat-disclaimer{margin:0;padding:8px 14px;font-size:11px;color:#5c6b70;background:#eef3f2;border-bottom:1px solid #dbe5e9}
    @media(max-width:520px){#avatar-chat-toggle{right:14px;bottom:14px;min-height:58px;padding:0 20px}#avatar-chat-panel{right:14px;bottom:88px}}
  `;
  document.head.appendChild(style);
  const toggle=document.createElement("button");toggle.id="avatar-chat-toggle";toggle.type="button";toggle.setAttribute("aria-expanded","false");toggle.textContent="Ask AVATAR AI";
  const panel=document.createElement("section");panel.id="avatar-chat-panel";panel.hidden=true;panel.setAttribute("aria-label","AVATAR AI chat");
  panel.innerHTML=`<header class="avatar-chat-head"><div><strong>AVATAR AI</strong><small>Campus energy guide</small></div><div class="avatar-chat-head-actions"><button class="avatar-chat-newchat" type="button">New chat</button><button class="avatar-chat-close" type="button" aria-label="Close chat">&times;</button></div></header><p class="avatar-chat-disclaimer">AI-generated answers may be inaccurate. Verify anything important.</p><div id="avatar-chat-log" aria-live="polite"></div><form id="avatar-chat-form"><input id="avatar-chat-input" maxlength="500" placeholder="Ask about campus energy…" autocomplete="off" required><button type="submit">Send</button></form>`;
  document.body.append(toggle,panel);
  const closeButton=panel.querySelector(".avatar-chat-close"),newChatButton=panel.querySelector(".avatar-chat-newchat"),log=panel.querySelector("#avatar-chat-log"),form=panel.querySelector("#avatar-chat-form"),input=panel.querySelector("#avatar-chat-input"),send=form.querySelector("button");
  let history=[];try{history=JSON.parse(sessionStorage.getItem("avatarChat"))||[];}catch(error){history=[];}
  const save=()=>{try{sessionStorage.setItem("avatarChat",JSON.stringify(history.slice(-10)));}catch(error){}};
  const addMessage=(role,message)=>{const item=document.createElement("div");item.className=`avatar-chat-message avatar-chat-${role}`;item.textContent=message;log.appendChild(item);log.scrollTop=log.scrollHeight;return item;};
  const setOpen=open=>{panel.hidden=!open;toggle.setAttribute("aria-expanded",String(open));if(open){input.focus();log.scrollTop=log.scrollHeight;}};
  addMessage("bot","Hi! I’m AVATAR AI. Ask me about campus energy, forecasting, solar power, batteries, or sustainability.");history.forEach(message=>addMessage(message.role==="user"?"user":"bot",message.content));
  toggle.addEventListener("click",()=>setOpen(panel.hidden));closeButton.addEventListener("click",()=>setOpen(false));
  newChatButton.addEventListener("click",()=>{history=[];try{sessionStorage.removeItem("avatarChat");}catch(error){}log.innerHTML="";addMessage("bot","Hi! I’m AVATAR AI. Ask me about campus energy, forecasting, solar power, batteries, or sustainability.");input.focus();});
  form.addEventListener("submit",async event=>{
    event.preventDefault();const question=input.value.trim();if(!question)return;input.value="";addMessage("user",question);history.push({role:"user",content:question});const pending=addMessage("bot","Thinking…");input.disabled=true;send.disabled=true;
    try{const response=await fetch(`${apiBase}/api/chat`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({messages:history.slice(-10)})});const data=await response.json();if(response.status===429)throw new Error("You’re sending messages too quickly. Please wait a minute and try again.");if(!response.ok)throw new Error(data.reply||"AVATAR AI is unavailable right now.");pending.textContent=data.reply;history.push({role:"assistant",content:data.reply});save();}
    catch(error){pending.textContent=error.message||"Network error. Please try again.";history.pop();save();}
    finally{input.disabled=false;send.disabled=false;input.focus();log.scrollTop=log.scrollHeight;}
  });
})();



(function applyAvatarBranding(){
 const brand=document.querySelector('.site-header .brand');
 if(brand){
  brand.innerHTML='<span class="avatar-wordmark" aria-hidden="true">AVAT<span class="avatar-leaf-a">A<svg viewBox="0 0 32 28"><path d="M3 24C3 8 18 3 29 3c0 13-9 23-26 21Z" fill="#45a58c"/><path d="M3 25 25 7" stroke="white" stroke-width="2"/></svg></span>R</span><span class="avatar-brand-caption">Campus energy. Shared possibilities.</span>';
  brand.setAttribute('aria-label','AVATAR homepage');
 }
 const icons=[
 '<path d="M4 20V12h3v8m4 0V7h3v13m4 0V3h3v17"/>',
 '<path d="m3 17 6-6 4 3 8-10m-5 0h5v5"/>',
 '<path d="M3 6h18M3 12h18M3 18h18M7 3v6m10 0v6M9 15v6"/>',
 '<ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v14c0 4 16 4 16 0V5M4 12c0 4 16 4 16 0"/>',
 '<path d="M12 5C8 3 5 3 2 4v16c4-1 7-1 10 1 3-2 6-2 10-1V4c-3-1-6-1-10 1v16"/>',
 '<path d="M8 17c0-4-4-4-4-9a8 8 0 0 1 16 0c0 5-4 5-4 9ZM8 21h8"/>',
 '<path d="m2 8 10-5 10 5-10 5-10-5Zm4 3v6c4 3 8 3 12 0v-6M22 8v10"/>',
 '<path d="M20 10c0 6-8 12-8 12S4 16 4 10a8 8 0 0 1 16 0Z"/><circle cx="12" cy="10" r="3"/>',
 '<path d="M3 21C3 6 14 3 22 3c0 12-7 18-19 18Zm0 0L18 7"/>',
 '<circle cx="8" cy="7" r="3"/><circle cx="18" cy="8" r="2"/><path d="M2 21v-4a6 6 0 0 1 12 0v4m2-7a5 5 0 0 1 6 5v2"/>',
 '<circle cx="12" cy="7" r="4"/><path d="M4 22v-4a8 8 0 0 1 16 0v4"/>',
 '<path d="M6 2h9l5 5v15H6ZM14 2v6h6M9 12h8m-8 4h8"/>'
 ];
 document.querySelectorAll('.nav-panel a').forEach((link,i)=>{if(icons[i])link.insertAdjacentHTML('afterbegin','<svg class="nav-link-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'+icons[i]+'</svg>');});
 document.querySelectorAll('.site-footer').forEach(footer=>{
  footer.innerHTML='<strong>AVATAR</strong><button class="back-to-top" type="button">Back to top <span aria-hidden="true">↑</span></button>';
  footer.querySelector('button').addEventListener('click',()=>{
   const target=document.querySelector('.site-header .brand');
   target?.focus({preventScroll:true});
   window.scrollTo({top:0,behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'instant':'smooth'});
  });
 });
})();
