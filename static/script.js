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
function renderScenario(){
  const use=Number(document.querySelector("#annual-use")?.value);
  const reduction=Number(document.querySelector("#reduction")?.value);
  if(!Number.isFinite(use)||!Number.isFinite(reduction)||use<0||reduction<0||reduction>100)return;
  const saved=use*reduction/100;
  document.querySelector("#saved-energy").textContent=number(saved)+" MWh";
  document.querySelector("#remaining-energy").textContent="Remaining annual use: "+number(use-saved)+" MWh";
}
document.querySelector("#scenario-form")?.addEventListener("submit",event=>{event.preventDefault();renderScenario();});
renderForecast();renderScenario();
