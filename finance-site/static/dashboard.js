const $ = s => document.querySelector(s);
let selected = null, chart = null;
const usd = n => n == null ? "—" : new Intl.NumberFormat("en-US", {
  style: "currency", currency: "USD", maximumFractionDigits: n < 1 ? 4 : 2}).format(n);
const pct = n => n == null ? "" : `${n >= 0 ? "▲" : "▼"} ${Math.abs(n).toFixed(2)}% (24h)`;
const cls = n => n >= 0 ? "up" : "down";

async function load() {
  const r = await fetch("/api/prices");
  if (r.status === 401) return (location = "/login");
  const d = await r.json();
  if (!selected || !d.coins.some(c => c.coin === selected)) selected = d.coins[0]?.coin || null;
  renderList(d.coins);
  renderAlerts(d.alerts);
  renderHero(d.coins.find(c => c.coin === selected));
  drawChart();
}

function renderList(coins) {
  $("#list").innerHTML = coins.length ? coins.map(c => `
    <li class="row ${c.coin === selected ? "on" : ""}" data-coin="${c.coin}" tabindex="0">
      <b>${c.coin}</b><span class="px">${usd(c.usd)}</span>
      <small>${c.target ? "Alert at " + usd(c.target) : "No alert set"}</small>
      <small class="${cls(c.change24)}" style="text-align:right">${c.usd == null ? "Waiting for price" : pct(c.change24)}</small>
      <button data-remove="${c.coin}">Remove</button>
    </li>`).join("") : `<li class="empty">Add a coin ID like bitcoin to start tracking.</li>`;
}

function renderHero(c) {
  $("#h-name").textContent = c ? c.coin : "Select a coin";
  $("#h-price").textContent = c ? usd(c.usd) : "—";
  const el = $("#h-change");
  el.textContent = c ? pct(c.change24) : "";
  el.className = "h-change " + (c ? cls(c.change24) : "");
}

function renderAlerts(alerts) {
  $("#alerts").innerHTML = alerts.length ? alerts.map(a =>
    `<li><b>${a.coin}</b> reached ${usd(a.price)} (target ${usd(a.target)}) · ${new Date(a.ts * 1000).toLocaleString()}</li>`
  ).join("") : `<li>No alerts yet. Set an alert price when you add a coin.</li>`;
}

async function drawChart() {
  if (!selected) return;
  const h = await (await fetch("/api/history/" + selected)).json();
  const data = {
    labels: h.map(p => new Date(p.ts * 1000).toLocaleTimeString([], {hour: "2-digit", minute: "2-digit"})),
    datasets: [{data: h.map(p => p.usd), borderColor: "#FFD60A", backgroundColor: "rgba(255,214,10,.12)",
                fill: true, pointRadius: 0, tension: .25, borderWidth: 2}]};
  if (chart) { chart.data = data; chart.update("none"); return; }
  chart = new Chart($("#chart"), {type: "line", data, options: {
    maintainAspectRatio: false, plugins: {legend: {display: false}},
    scales: {x: {ticks: {color: "#9b9680", maxTicksLimit: 6}, grid: {color: "#1d1d1d"}},
             y: {ticks: {color: "#9b9680"}, grid: {color: "#1d1d1d"}}}}});
}

$("#list").addEventListener("click", async e => {
  const rm = e.target.dataset.remove;
  if (rm) {
    await fetch("/api/watchlist/" + rm, {method: "DELETE"});
    if (selected === rm) selected = null;
    if (chart) { chart.destroy(); chart = null; }
  } else {
    const row = e.target.closest(".row");
    if (!row) return;
    selected = row.dataset.coin;
  }
  load();
});

$("#add").addEventListener("submit", async e => {
  e.preventDefault();
  const r = await fetch("/api/watchlist", {method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({coin: $("#coin").value, target: $("#target").value})});
  $("#msg").textContent = r.ok ? "" : (await r.json()).error;
  if (r.ok) { selected = $("#coin").value.trim().toLowerCase(); e.target.reset(); load(); }
});

load();
setInterval(load, 30000);
