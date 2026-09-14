const menuButton=document.querySelector(".menu-button");
const navigation=document.querySelector(".main-nav");
if(menuButton&&navigation){menuButton.addEventListener("click",()=>{const open=navigation.classList.toggle("open");menuButton.setAttribute("aria-expanded",String(open));});navigation.querySelectorAll("a").forEach(link=>link.addEventListener("click",()=>{navigation.classList.remove("open");menuButton.setAttribute("aria-expanded","false");}));}
const number=value=>new Intl.NumberFormat("en-US",{maximumFractionDigits:1}).format(value);
function renderForecast(){
  const load=Number(document.querySelector("#current-load")?.value);
  const growth=Number(document.querySelector("#growth-rate")?.value);
  const years=Number(document.querySelector("#forecast-years")?.value);
  const chart=document.querySelector("#forecast-chart");
  if(!chart||!Number.isFinite(load)||!Number.isFinite(growth)||!Number.isFinite(years)||load<0||growth<-100||growth>100||years<1||years>20)return;
  const values=Array.from({length:Math.floor(years)},(_,i)=>load*Math.pow(1+growth/100,i+1));
  const max=Math.max(...values,1);
  chart.innerHTML=values.map((value,i)=>'<div class="bar-wrap"><div class="bar" title="'+number(value)+' MWh" style="height:'+Math.max(5,value/max*100)+'%"></div><span class="bar-label">Y'+(i+1)+'</span></div>').join("");
}
document.querySelector("#forecast-form")?.addEventListener("submit",event=>{event.preventDefault();renderForecast();});
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