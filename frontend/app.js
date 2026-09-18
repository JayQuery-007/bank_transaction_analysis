const API = "";

const palette = {
  accent: "#5b8cff",
  accent2: "#34d3a3",
  danger: "#ff5c72",
  warning: "#ffb454",
  muted: "#8a93a8",
  grid: "rgba(255,255,255,0.06)",
};

Chart.defaults.color = "#8a93a8";
Chart.defaults.font.family = "'Segoe UI', Inter, sans-serif";
Chart.defaults.borderColor = palette.grid;

function fmtMoney(n) {
  return "$" + Number(n).toLocaleString(undefined, { maximumFractionDigits: 0 });
}
function fmtNum(n) {
  return Number(n).toLocaleString();
}

async function fetchJSON(path) {
  const res = await fetch(API + path);
  if (!res.ok) throw new Error(`${path} -> ${res.status}`);
  return res.json();
}

let charts = {};

function upsertChart(id, config) {
  const ctx = document.getElementById(id).getContext("2d");
  if (charts[id]) charts[id].destroy();
  charts[id] = new Chart(ctx, config);
}

async function loadDashboard() {
  const [summary, metrics, anomalies] = await Promise.all([
    fetchJSON("/api/stats/summary"),
    fetchJSON("/api/stats/model-metrics"),
    fetchJSON("/api/anomalies/top?limit=25"),
  ]);

  document.getElementById("storageBadge").textContent = `storage: ${summary.storage_engine}`;
  document.getElementById("refreshedBadge").textContent = `updated: ${new Date().toLocaleTimeString()}`;

  document.getElementById("kpiTotal").textContent = fmtNum(summary.total_transactions);
  document.getElementById("kpiFraud").textContent = fmtNum(summary.total_fraud);
  document.getElementById("kpiRate").textContent =
    ((summary.total_fraud / summary.total_transactions) * 100).toFixed(2) + "%";
  document.getElementById("kpiVolume").textContent = fmtMoney(summary.total_amount);
  document.getElementById("kpiF1").textContent = metrics
    ? (metrics.f1_vs_injected_fraud * 100).toFixed(1) + "%"
    : "n/a";

  // ---- time series ----
  const byDay = summary.by_day || [];
  upsertChart("timeSeriesChart", {
    type: "line",
    data: {
      labels: byDay.map((d) => d.day),
      datasets: [
        {
          label: "Transactions",
          data: byDay.map((d) => d.tx_count),
          borderColor: palette.accent,
          backgroundColor: "rgba(91,140,255,0.12)",
          fill: true,
          tension: 0.3,
          pointRadius: 0,
          yAxisID: "y",
        },
        {
          label: "Fraud flagged",
          data: byDay.map((d) => d.fraud_count),
          borderColor: palette.danger,
          backgroundColor: "rgba(255,92,114,0.15)",
          fill: true,
          tension: 0.3,
          pointRadius: 0,
          yAxisID: "y1",
        },
      ],
    },
    options: {
      responsive: true,
      interaction: { mode: "index", intersect: false },
      scales: {
        x: { grid: { display: false }, ticks: { maxTicksLimit: 10 } },
        y: { position: "left", grid: { color: palette.grid } },
        y1: { position: "right", grid: { display: false } },
      },
      plugins: { legend: { position: "top", align: "end" } },
    },
  });

  // ---- pie: normal vs anomalous ----
  upsertChart("pieChart", {
    type: "doughnut",
    data: {
      labels: ["Normal", "Anomalous"],
      datasets: [
        {
          data: [summary.total_transactions - summary.total_fraud, summary.total_fraud],
          backgroundColor: [palette.accent2, palette.danger],
          borderWidth: 0,
        },
      ],
    },
    options: {
      plugins: { legend: { position: "bottom" } },
      cutout: "68%",
    },
  });

  // ---- category bar ----
  const byCategory = (summary.by_category || []).slice(0, 10);
  upsertChart("categoryChart", {
    type: "bar",
    data: {
      labels: byCategory.map((c) => c.category),
      datasets: [
        {
          label: "Transactions",
          data: byCategory.map((c) => c.tx_count),
          backgroundColor: palette.accent,
          borderRadius: 6,
        },
      ],
    },
    options: {
      indexAxis: "y",
      plugins: { legend: { display: false } },
      scales: { x: { grid: { color: palette.grid } }, y: { grid: { display: false } } },
    },
  });

  // ---- country fraud rate ----
  const byCountry = (summary.by_country || []).slice(0, 10);
  upsertChart("countryChart", {
    type: "bar",
    data: {
      labels: byCountry.map((c) => c.country),
      datasets: [
        {
          label: "Fraud rate %",
          data: byCountry.map((c) => ((c.fraud_count / c.tx_count) * 100).toFixed(2)),
          backgroundColor: byCountry.map((c) =>
            c.fraud_count / c.tx_count > 0.05 ? palette.danger : palette.warning
          ),
          borderRadius: 6,
        },
      ],
    },
    options: {
      plugins: { legend: { display: false } },
      scales: { x: { grid: { display: false } }, y: { grid: { color: palette.grid } } },
    },
  });

  // ---- scatter: amount vs anomaly score ----
  const scatterPoints = anomalies.items.map((a) => ({ x: a.amount, y: a.anomaly_score }));
  upsertChart("scatterChart", {
    type: "scatter",
    data: {
      datasets: [
        {
          label: "Flagged anomalies",
          data: scatterPoints,
          backgroundColor: "rgba(255,92,114,0.75)",
        },
      ],
    },
    options: {
      plugins: { legend: { display: false } },
      scales: {
        x: { title: { display: true, text: "Amount ($)" }, grid: { color: palette.grid } },
        y: { title: { display: true, text: "Anomaly score" }, grid: { color: palette.grid } },
      },
    },
  });

  renderAnomalyTable(anomalies.items);
}

function renderAnomalyTable(items) {
  const tbody = document.querySelector("#anomalyTable tbody");
  tbody.innerHTML = items
    .map(
      (a) => `
    <tr>
      <td>${a.transaction_id}</td>
      <td>${a.account_id}</td>
      <td>${fmtMoney(a.amount)}</td>
      <td>${new Date(a.timestamp).toLocaleString()}</td>
      <td>${a.category}</td>
      <td>${a.country}</td>
      <td>${a.channel}</td>
      <td><span class="score-pill">${Number(a.anomaly_score).toFixed(1)}</span></td>
      <td>${a.fraud_pattern ?? "-"}</td>
    </tr>`
    )
    .join("");
}

document.getElementById("refreshAnomalies").addEventListener("click", loadDashboard);

document.getElementById("detectForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const payload = {
    amount: parseFloat(document.getElementById("f_amount").value),
    hour: parseInt(document.getElementById("f_hour").value, 10),
    account_avg_amount: parseFloat(document.getElementById("f_avg").value),
    account_std_amount: parseFloat(document.getElementById("f_std").value || 0),
    distinct_countries: parseInt(document.getElementById("f_countries").value || 1, 10),
    seconds_since_prev_tx: parseFloat(document.getElementById("f_seconds").value || 3600),
  };
  const resultBox = document.getElementById("detectResult");
  resultBox.innerHTML = `<div class="result-box">Scoring…</div>`;
  try {
    const res = await fetch(API + "/api/detect", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    const cls = data.is_anomaly ? "flag" : "ok";
    resultBox.innerHTML = `
      <div class="result-box ${cls}">
        <div class="result-title">${data.is_anomaly ? "⚠ Flagged as anomalous" : "✓ Looks normal"}</div>
        <div>Anomaly score: <strong>${data.raw_anomaly_score}</strong></div>
        <div>Amount z-score vs account history: <strong>${data.amount_zscore}</strong></div>
      </div>`;
  } catch (err) {
    resultBox.innerHTML = `<div class="result-box flag">Error scoring transaction: ${err.message}</div>`;
  }
});

loadDashboard().catch((err) => {
  console.error(err);
  document.querySelector("main").insertAdjacentHTML(
    "afterbegin",
    `<div class="card" style="border-color:#ff5c72;margin-bottom:20px;">
       Failed to load data from API: ${err.message}. Is the backend running (uvicorn) and has the pipeline been run?
     </div>`
  );
});
