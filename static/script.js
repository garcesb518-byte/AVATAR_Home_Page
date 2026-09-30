const menuButton=document.querySelector(".menu-button");
const navigation=document.querySelector(".main-nav");
if(menuButton&&navigation){menuButton.addEventListener("click",()=>{const open=navigation.classList.toggle("open");menuButton.setAttribute("aria-expanded",String(open));});navigation.querySelectorAll("a").forEach(link=>link.addEventListener("click",()=>{navigation.classList.remove("open");menuButton.setAttribute("aria-expanded","false");}));}
const number=value=>new Intl.NumberFormat("en-US",{maximumFractionDigits:1}).format(value);
const displayDate=value=>{
  if(!value)return "Date unavailable";
  const parts=String(value).split("-").map(Number);
  if(parts.length!==3||parts.some(part=>!Number.isFinite(part)))return String(value);
  return new Intl.DateTimeFormat("en-US",{weekday:"long",month:"long",day:"numeric",year:"numeric"}).format(new Date(parts[0],parts[1]-1,parts[2]));
};

async function requestForecastData(){
  const apiBase=document.querySelector('meta[name="avatar-api-url"]')?.content?.replace(/\/$/,"");
  if(apiBase){
    try{
      const response=await fetch(apiBase+"/api/forecast",{cache:"no-store"});
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
    if(!Array.isArray(data.hourly)||data.hourly.length!==24)throw new Error("Forecast data is incomplete");
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

function toggleScenarioMode(){
  const mode=document.querySelector("#system-mode")?.value;
  const optimizeFields=document.querySelector("#optimize-equipment");
  const scenarioFields=document.querySelector("#scenario-equipment");
  if(!optimizeFields||!scenarioFields)return;
  optimizeFields.hidden=mode!=="optimize";
  scenarioFields.hidden=mode!=="scenario";
}

function scenarioRequest(){
  return {
    mode:document.querySelector("#system-mode")?.value,
    displayed_season:document.querySelector("#displayed-season")?.value,
    project_budget:scenarioValue("#project-budget"),
    max_solar_panels:scenarioValue("#max-panels"),
    max_bess_units:scenarioValue("#max-bess"),
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
    document.querySelector("#model-details")?.setAttribute("hidden","");
  }
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

function renderScenarioResults(data,mode){
  const whatIf=data.what_if_tab;
  const summary=whatIf.summary;
  document.querySelector("#scenario-result-title").textContent=mode==="optimize"?"Recommended AVATAR system":"Selected-system result";
  document.querySelector("#result-panels").textContent=integer(summary.panels_selected);
  document.querySelector("#result-bess").textContent=integer(summary.bess_units_selected);
  document.querySelector("#result-project-cost").textContent=currency(summary["project_cost_$"]);
  document.querySelector("#result-savings").textContent=currency(summary["annual_total_operating_savings_$"])+"/yr";
  document.querySelector("#result-grid-reduction").textContent=integer(summary.annual_grid_energy_reduction_kwh)+" kWh/yr";
  document.querySelector("#result-renewable-share").textContent=number(summary.renewable_share_of_load_percent)+"%";
  document.querySelector("#scenario-summary").textContent="Adjusted daily demand is "+number(summary.adjusted_daily_energy_kwh)+" kWh with an estimated peak of "+number(summary.adjusted_peak_kw)+" kW. Remaining project budget: "+currency(summary["budget_remaining_$"])+".";
  renderLoadComparison(whatIf.load_comparison,whatIf.selected_season_hourly);
  renderModelTable("commitment-table",whatIf.unit_commitment);
  renderModelTable("dispatch-table",whatIf.economic_dispatch);
  showScenarioState("scenario-results");
  document.querySelector("#comparison-card")?.removeAttribute("hidden");
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
    renderScenarioResults(data,payload.mode);
  }catch(error){
    const message=error instanceof TypeError
      ?"The webpage could not reach the optimization service. Confirm that the Python API is running and that its URL is correct."
      :error.message;
    showScenarioState("scenario-error",message);
  }finally{
    if(button)button.disabled=false;
  }
}

document.querySelector("#system-mode")?.addEventListener("change",toggleScenarioMode);
document.querySelector("#scenario-form")?.addEventListener("submit",runScenario);
toggleScenarioMode();
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
