const menuButton=document.querySelector(".menu-button");
const navigation=document.querySelector(".main-nav");
if(menuButton&&navigation){menuButton.addEventListener("click",()=>{const open=navigation.classList.toggle("open");menuButton.setAttribute("aria-expanded",String(open));});navigation.querySelectorAll("a").forEach(link=>link.addEventListener("click",()=>{navigation.classList.remove("open");menuButton.setAttribute("aria-expanded","false");}));}
const number=value=>new Intl.NumberFormat("en-US",{maximumFractionDigits:1}).format(value);
async function renderForecast(){
  const chart=document.querySelector("#forecast-chart");
  if(!chart)return;
  const loading=document.querySelector("#forecast-loading");
  const content=document.querySelector("#forecast-content");
  const errorPanel=document.querySelector("#forecast-error");
  try{
    const response=await fetch("static/data/load_forecast.json",{cache:"no-store"});
    if(!response.ok)throw new Error("Forecast data request failed");
    const data=await response.json();
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
    document.querySelector("#forecast-method").textContent=data.method;
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
