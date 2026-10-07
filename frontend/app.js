const COLORS = { fraud: "#c0392b", legit: "#8c959f", accent: "#1f4e79" };
Chart.defaults.font.family = '-apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif';
Chart.defaults.color = "#59636e";
Chart.defaults.maintainAspectRatio = false;

const num = (n, digits = 0) => Number(n).toLocaleString("en-US", { maximumFractionDigits: digits });
const money = (n) => {
  const v = Number(n);
  if (v >= 1e9) return (v / 1e9).toFixed(1) + " bn";
  if (v >= 1e6) return (v / 1e6).toFixed(1) + " M";
  return num(v, 2);
};
const pct = (n, digits = 2) => Number(n).toFixed(digits) + "%";
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const $ = (id) => document.getElementById(id);

async function api(path, options) {
  const res = await fetch(path, options);
  if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || res.statusText);
  return res.json();
}

function table(el, headers, rows) {
  el.innerHTML =
    "<thead><tr>" + headers.map((h) => `<th>${h}</th>`).join("") + "</tr></thead>" +
    "<tbody>" + rows.map((r) => `<tr${r.cls ? ` class="${r.cls}"` : ""}>` + r.cells.map((c) => `<td>${c}</td>`).join("") + "</tr>").join("") + "</tbody>";
}

function renderOverview(o, quality) {
  const stats = [
    ["Transactions", num(o.transactions)],
    ["Total value", money(o.total_amount)],
    ["Period", `${o.days_covered} days`],
    ["Fraudulent transactions", num(o.fraud_transactions), "fraud"],
    ["Fraud value", money(o.fraud_amount), "fraud"],
    ["Fraud rate", pct(o.fraud_rate_pct, 3), "fraud"],
  ];
  $("stats").innerHTML = stats
    .map(([label, value, cls]) => `<div class="stat ${cls || ""}"><div class="label">${label}</div><div class="value">${value}</div></div>`)
    .join("");
  $("qualityNote").textContent =
    `Raw CSV: ${num(quality.total_rows)} rows, ${quality.rows_with_nulls} nulls, ${quality.duplicate_rows} duplicates. ` +
    `${quality.zero_amounts} zero-amount rows were removed. ${num(o.origin_accounts)} distinct sender accounts and ` +
    `${num(o.destination_accounts)} distinct receiver accounts. Ingested to Parquet in ${quality.seconds}s.`;
}

function renderTypes(types, o) {
  const fraudTypes = types.filter((t) => t.fraud_transactions > 0).map((t) => t.type);
  $("typeFinding").innerHTML =
    `Fraud occurs <b>only in ${fraudTypes.join(" and ")}</b> transactions. The other types have no fraud at all, ` +
    `so the model is trained on these two types only. The dataset's existing rule (<code>isFlaggedFraud</code>) ` +
    `flagged just <b>${o.flagged_by_existing_rule}</b> of ${num(o.fraud_transactions)} frauds.`;

  table($("typeTable"), ["Type", "Transactions", "Avg amount", "Fraud", "Fraud rate"],
    types.map((t) => ({
      cls: t.fraud_transactions > 0 ? "highlight" : "",
      cells: [t.type, num(t.transactions), num(t.avg_amount), num(t.fraud_transactions), pct(t.fraud_rate_pct, 3)],
    })));

  new Chart($("typeChart"), {
    type: "bar",
    data: {
      labels: types.map((t) => t.type),
      datasets: [{
        label: "Transactions (millions)",
        data: types.map((t) => t.transactions / 1e6),
        backgroundColor: types.map((t) => (t.fraud_transactions > 0 ? COLORS.fraud : COLORS.legit)),
      }],
    },
    options: { plugins: { legend: { display: false }, title: { display: true, text: "Transactions by type (millions), red = contains fraud" } } },
  });
}

function renderHourly(hours) {
  const peak = hours.reduce((a, b) => (b.fraud_rate_pct > a.fraud_rate_pct ? b : a));
  const frauds = hours.map((h) => h.fraud_transactions);
  $("hourFinding").innerHTML =
    `The number of frauds stays almost constant through the day (${Math.min(...frauds)}&ndash;${Math.max(...frauds)} per hour slot). ` +
    `Legitimate activity drops sharply overnight, so the <b>fraud rate peaks at ${pct(peak.fraud_rate_pct, 1)} around ${peak.hour_of_day}:00</b>, ` +
    `compared with under 0.1% during business hours.`;

  new Chart($("hourChart"), {
    data: {
      labels: hours.map((h) => `${h.hour_of_day}:00`),
      datasets: [
        { type: "bar", label: "All transactions", data: hours.map((h) => h.transactions), backgroundColor: "#d0d7de", yAxisID: "y" },
        { type: "line", label: "Fraud rate (%)", data: hours.map((h) => h.fraud_rate_pct), borderColor: COLORS.fraud, backgroundColor: COLORS.fraud, yAxisID: "y1" },
      ],
    },
    options: {
      interaction: { mode: "index", intersect: false },
      scales: {
        y: { title: { display: true, text: "Transactions" } },
        y1: { position: "right", grid: { drawOnChartArea: false }, title: { display: true, text: "Fraud rate (%)" } },
      },
    },
  });
}

function renderAmounts(bands) {
  const labels = { 1: "< 100", 2: "100 – 1k", 3: "1k – 10k", 4: "10k – 100k", 5: "100k – 1M", 6: "1M – 10M", 7: "≥ 10M" };
  const legitTotal = bands.reduce((s, b) => s + b.legit, 0);
  const fraudTotal = bands.reduce((s, b) => s + b.fraud, 0);
  new Chart($("amountChart"), {
    type: "bar",
    data: {
      labels: bands.map((b) => labels[b.band]),
      datasets: [
        { label: "Legitimate (% of class)", data: bands.map((b) => (100 * b.legit) / legitTotal), backgroundColor: COLORS.legit },
        { label: "Fraud (% of class)", data: bands.map((b) => (100 * b.fraud) / fraudTotal), backgroundColor: COLORS.fraud },
      ],
    },
    options: { scales: { y: { title: { display: true, text: "% of transactions" } } } },
  });
}

function renderBalances(rows) {
  const fraud = rows.find((r) => r.class === "fraud");
  const legit = rows.find((r) => r.class === "legit");
  const metric = (label, key, fmt) => ({ cells: [label, fmt(legit[key]), fmt(fraud[key])] });
  table($("balanceTable"), ["", "Legitimate", "Fraud"], [
    metric("Transactions", "transactions", num),
    metric("Average amount", "avg_amount", num),
    metric("Median amount", "median_amount", num),
    metric("Sender account emptied", "pct_origin_emptied", pct),
    metric("Receiver balance stays 0", "pct_dest_balance_unchanged_zero", pct),
    metric("Amounts at the 10M cap", "at_10m_cap", num),
  ]);
  $("balanceFinding").innerHTML =
    `${pct(fraud.pct_origin_emptied, 1)} of frauds empty the sender's account (vs ${pct(legit.pct_origin_emptied, 1)} of legitimate transactions), ` +
    `and in ${pct(fraud.pct_dest_balance_unchanged_zero, 1)} of frauds the receiver's recorded balance stays at 0 even though money arrived.`;
}

function renderMules(rows) {
  const total = rows.reduce((s, r) => s + r.fraud_transactions, 0);
  $("muleFinding").textContent =
    `${rows.length} receiver accounts were the destination of more than one fraudulent transaction (${total} frauds in total). Top 10 by value:`;
  table($("muleTable"), ["Receiver account", "Fraud transactions", "Total amount", "First seen (hour)", "Last seen (hour)"],
    rows.slice(0, 10).map((r) => ({ cells: [esc(r.name_dest), r.fraud_transactions, num(r.total_amount, 2), r.first_step, r.last_step] })));
}

function renderModel(metrics, info, features, curve) {
  $("modelIntro").innerHTML =
    `Trained on ${num(info.training_rows)} TRANSFER/CASH_OUT transactions and evaluated on a held-out test set of ` +
    `${num(info.test_rows)} (${num(info.test_fraud)} frauds). Fraud is only ${(100 / (info.fraud_weight + 1)).toFixed(2)}% of this data, ` +
    `so accuracy is meaningless: the table reports precision, recall and area under the precision-recall curve.`;

  const f = (v, d = 3) => (v === undefined || v === null ? "&ndash;" : Number(v).toFixed(d));
  table($("modelTable"), ["Model", "Precision", "Recall", "F1", "AUC-ROC", "AUC-PR", "Frauds caught", "False alarms", "Train time"],
    metrics.map((m) => ({
      cls: m.model === info.selected_model ? "highlight" : "",
      cells: [m.model, f(m.precision), f(m.recall), f(m.f1), f(m.areaUnderROC), f(m.areaUnderPR),
        `${num(m.tp)} / ${num(m.tp + m.fn)}`, num(m.fp), m.train_seconds ? `${m.train_seconds}s` : "&ndash;"],
    })));

  const rf = metrics.find((m) => m.model === info.selected_model);
  $("confusion").innerHTML = `
    <tr><th></th><th>Predicted legit</th><th>Predicted fraud</th></tr>
    <tr><th>Actual legit</th><td class="good">${num(rf.tn)}</td><td class="bad">${num(rf.fp)}</td></tr>
    <tr><th>Actual fraud</th><td class="bad">${num(rf.fn)}</td><td class="good">${num(rf.tp)}</td></tr>`;

  new Chart($("featureChart"), {
    type: "bar",
    data: { labels: features.map((x) => x.feature), datasets: [{ data: features.map((x) => x.importance), backgroundColor: COLORS.accent }] },
    options: { indexAxis: "y", plugins: { legend: { display: false } } },
  });

  new Chart($("thresholdChart"), {
    type: "line",
    data: {
      labels: curve.map((c) => c.threshold.toFixed(1)),
      datasets: [
        { label: "Precision", data: curve.map((c) => c.precision), borderColor: COLORS.accent, backgroundColor: COLORS.accent },
        { label: "Recall", data: curve.map((c) => c.recall), borderColor: COLORS.fraud, backgroundColor: COLORS.fraud },
      ],
    },
    options: { scales: { x: { title: { display: true, text: "Threshold" } }, y: { suggestedMin: 0.95, suggestedMax: 1 } } },
  });
}

const flaggedState = { outcome: "", skip: 0, limit: 25, total: 0 };
const OUTCOME_TAG = { "true positive": "tp", "false positive": "fp", "missed fraud": "fn" };

async function loadFlagged() {
  const q = new URLSearchParams({ skip: flaggedState.skip, limit: flaggedState.limit });
  if (flaggedState.outcome) q.set("outcome", flaggedState.outcome);
  const data = await api(`/api/flagged?${q}`);
  flaggedState.total = data.total;
  table($("flaggedTable"),
    ["Day / hour", "Type", "Amount", "Sender", "Sender bal. before → after", "Receiver", "Receiver bal. before → after", "Fraud prob.", "Outcome"],
    data.items.map((r) => ({
      cells: [
        `${r.day} / ${r.hour_of_day}:00`, r.type, num(r.amount, 2), esc(r.name_orig),
        `${num(r.old_balance_orig)} → ${num(r.new_balance_orig)}`, esc(r.name_dest),
        `${num(r.old_balance_dest)} → ${num(r.new_balance_dest)}`, Number(r.fraud_probability).toFixed(3),
        `<span class="tag ${OUTCOME_TAG[r.outcome]}">${r.outcome}</span>`,
      ],
    })));
  const page = Math.floor(flaggedState.skip / flaggedState.limit) + 1;
  const pages = Math.max(1, Math.ceil(data.total / flaggedState.limit));
  $("pageInfo").textContent = `Page ${page} of ${pages}`;
  $("flaggedCount").textContent = `${num(data.total)} transactions`;
  $("prevPage").disabled = page <= 1;
  $("nextPage").disabled = page >= pages;
}

$("filters").addEventListener("click", (e) => {
  if (e.target.tagName !== "BUTTON") return;
  document.querySelectorAll("#filters button").forEach((b) => b.classList.toggle("active", b === e.target));
  flaggedState.outcome = e.target.dataset.outcome;
  flaggedState.skip = 0;
  loadFlagged();
});
$("prevPage").onclick = () => { flaggedState.skip -= flaggedState.limit; loadFlagged(); };
$("nextPage").onclick = () => { flaggedState.skip += flaggedState.limit; loadFlagged(); };

const LEGIT_EXAMPLE = { type: "CASH_OUT", amount: 18500, hour_of_day: 14, old_balance_orig: 42000, new_balance_orig: 23500, old_balance_dest: 125000, new_balance_dest: 143500 };

async function fillExample(kind) {
  let tx = LEGIT_EXAMPLE;
  if (kind === "fraud") {
    const data = await api("/api/flagged?outcome=true%20positive&limit=1&skip=40");
    tx = data.items[0];
  }
  const form = $("predictForm");
  for (const key of Object.keys(LEGIT_EXAMPLE)) form.elements[key].value = tx[key];
}
document.querySelectorAll("[data-example]").forEach((b) => (b.onclick = () => fillExample(b.dataset.example)));

$("predictForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const form = e.target;
  const body = { type: form.elements.type.value };
  for (const key of Object.keys(LEGIT_EXAMPLE)) if (key !== "type") body[key] = Number(form.elements[key].value);
  const out = $("predictResult");
  out.innerHTML = `<div class="result">Scoring&hellip;</div>`;
  try {
    const r = await api("/api/predict", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    out.innerHTML = `
      <div class="result ${r.is_fraud ? "fraud" : ""}">
        <strong>${r.is_fraud ? "Likely fraud" : "Looks legitimate"}</strong> &mdash; fraud probability ${r.fraud_probability.toFixed(3)}<br>
        <span class="note">Sender balance error: ${num(r.error_balance_orig, 2)} &middot; Receiver balance error: ${num(r.error_balance_dest, 2)}</span>
      </div>`;
  } catch (err) {
    out.innerHTML = `<div class="result fraud">Could not score: ${esc(err.message)}</div>`;
  }
});

async function main() {
  const names = ["overview", "data_quality", "type_summary", "hourly", "amount_bands", "balance_patterns",
    "repeat_fraud_destinations", "model_metrics", "model_info", "feature_importance", "threshold_curve"];
  const [src, ...results] = await Promise.all([api("/api/source"), ...names.map((n) => api(`/api/results/${n}`))]);
  const r = Object.fromEntries(names.map((n, i) => [n, results[i]]));

  $("source").textContent = src.source;
  renderOverview(r.overview[0], r.data_quality[0]);
  renderTypes(r.type_summary, r.overview[0]);
  renderHourly(r.hourly);
  renderAmounts(r.amount_bands);
  renderBalances(r.balance_patterns);
  renderMules(r.repeat_fraud_destinations);
  renderModel(r.model_metrics, r.model_info[0], r.feature_importance, r.threshold_curve);
  await loadFlagged();
  fillExample("legit");
}

main().catch((err) => {
  document.querySelector("main").insertAdjacentHTML("afterbegin",
    `<p class="result fraud">Could not load results: ${esc(err.message)}. Run the pipeline first (python run_pipeline.py).</p>`);
});
