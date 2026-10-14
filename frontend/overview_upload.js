// Overview tab for an uploaded dataset: numbers computed by the server with SQL over the scored file.
// Shown instead of the main ledger's overview while an upload is selected in the dataset picker.
// Depends on helpers from app.js: $, apiAs, num, money, pct, esc, table, COLORS, and on datasets.js.

(() => {
  let charts = [];
  const destroy = () => { charts.forEach((c) => c.destroy()); charts = []; };
  const chart = (id, config) => { charts.push(new Chart($(id), config)); };
  const rate = (part, whole) => (whole ? (100 * part) / whole : 0);
  const BAND = { 1: "< 100", 2: "100 – 1k", 3: "1k – 10k", 4: "10k – 100k", 5: "100k – 1M", 6: "1M – 10M", 7: "≥ 10M" };
  const seconds = (ms) => (ms === null || ms === undefined ? "–" : `${(ms / 1000).toFixed(ms < 10000 ? 1 : 0)} s`);

  async function show() {
    const box = $("overviewUpload");
    const paysim = $("overviewPaysim");
    destroy();
    if (!datasets.isUpload()) {
      box.hidden = true;
      paysim.hidden = false;
      return;
    }
    paysim.hidden = true;
    box.hidden = false;
    box.innerHTML = '<p class="note" style="padding:20px 0">Loading the overview of your file&hellip;</p>';
    try {
      render(box, await apiAs(`/api/datasets/${datasets.current.id}/overview`));
    } catch (err) {
      box.innerHTML = `<div class="search-status error" style="margin-top:20px">Could not load the overview: ${esc(err.message)}. ` +
        `If this upload is older than a day it has expired; upload the file again.</div>`;
    }
  }

  function render(box, o) {
    const a = o.analytics;
    const ov = a.overview;
    const labelled = o.metrics !== null && ov.labelled_fraud !== null;
    const cards = [
      ["Transactions", num(ov.transactions)],
      ["Total value", money(ov.total_amount)],
      ["Scored by the model", num(ov.scored)],
      ["Flagged as fraud", num(ov.flagged), "fraud"],
      ["Flagged value", money(ov.flagged_amount), "fraud"],
      labelled ? ["Labelled fraud", num(ov.labelled_fraud), "fraud"] : ["Flag rate", `${rate(ov.flagged, ov.scored).toFixed(2)}%`, "fraud"],
    ].map(([label, value, cls]) => `<div class="stat ${cls || ""}"><div class="label">${label}</div><div class="value">${value}</div></div>`).join("");

    // types
    const flaggedTypes = a.type_summary.filter((t) => t.flagged > 0).map((t) => t.type);
    const typeText = flaggedTypes.length
      ? `The model flagged transactions only in <b>${flaggedTypes.join(" and ")}</b>. Other types are never scored, because fraud only occurs in TRANSFER and CASH_OUT transactions, which is what the model was built for.`
      : "The model flagged no transactions in this file.";

    // hours: only hours with enough scored rows count towards the peak
    const minScored = Math.max(20, Math.round(ov.scored * 0.01));
    const eligible = a.hourly.filter((h) => h.scored >= minScored && h.flagged > 0);
    const peak = eligible.reduce((best, h) => (!best || rate(h.flagged, h.scored) > rate(best.flagged, best.scored) ? h : best), null);
    const hourText = peak
      ? `The flag rate is highest at <b>${rate(peak.flagged, peak.scored).toFixed(1)}%</b> around ${String(peak.hour_of_day).padStart(2, "0")}:00 ` +
        `(${num(peak.flagged)} of ${num(peak.scored)} scored rows).`
      : "No hour has enough flagged transactions to show a pattern.";

    const high = (a.score_histogram.find((b) => b.bin === 9) || { transactions: 0 }).transactions;
    const low = (a.score_histogram.find((b) => b.bin === 0) || { transactions: 0 }).transactions;
    const scoreText = `${num(low)} scored rows score below 0.1 and ${num(high)} score 0.9 or higher; the model flags anything above 0.5.`;

    const m = o.metrics;
    const accuracy = labelled ? `
      <section>
        <h2>5. Model accuracy on this file</h2>
        <p>Measured against the <code>isFraud</code> column of your file, on the ${num(m.rows)} rows the model scores.</p>
        <div class="cols">
          <table class="data"><thead><tr><th></th><th>Value</th></tr></thead><tbody>
            <tr><td>Precision (flagged rows that are fraud)</td><td>${pct(m.precision === null ? 0 : m.precision * 100, 1)}</td></tr>
            <tr><td>Recall (frauds that were flagged)</td><td>${pct(m.recall === null ? 0 : m.recall * 100, 1)}</td></tr>
            <tr><td>F1</td><td>${pct(m.f1 === null ? 0 : m.f1 * 100, 1)}</td></tr></tbody></table>
          <table class="confusion">
            <tr><th></th><th>Predicted legit</th><th>Predicted fraud</th></tr>
            <tr><th>Actual legit</th><td class="good">${num(m.tn)}</td><td class="bad">${num(m.fp)}</td></tr>
            <tr><th>Actual fraud</th><td class="bad">${num(m.fn)}</td><td class="good">${num(m.tp)}</td></tr>
          </table>
        </div>
        <p class="note">Accuracy is measured by comparing the model's verdicts with the <code>isFraud</code> column of your file.</p>
      </section>` : "";

    const receivers = a.top_flagged_receivers.length ? `
      <section>
        <h2>${labelled ? "6" : "5"}. Accounts receiving the most flagged money</h2>
        <p class="sub">Receivers of flagged transactions, by total amount. Open one in the Explorer to see everything it received and sent.</p>
        <table class="data" id="upReceivers"><thead><tr><th>Receiver account</th><th>Flagged transactions</th><th>Total amount</th><th></th></tr></thead><tbody>
          ${a.top_flagged_receivers.map((r) => `<tr><td>${esc(r.name_dest)}</td><td>${num(r.flagged)}</td><td>${num(r.total_amount, 2)}</td>
            <td><button type="button" data-account="${esc(r.name_dest)}">Explore</button></td></tr>`).join("")}
        </tbody></table>
      </section>` : "";

    box.innerHTML = `
      <div class="dataset-banner">
        <span>Showing your upload <b>${esc(o.name)}</b>: ${num(o.rows)} rows, uploaded ${new Date(o.created_at).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" })},
          scored in ${seconds(o.processing_ms)} and indexed in ${seconds(o.indexing_ms)}.</span>
        <button type="button" id="upBackToPaysim">Back to the main ledger</button>
      </div>
      <section>
        <h2>1. Dataset overview</h2>
        <div class="stats">${cards}</div>
        <p class="note">${num(ov.senders)} distinct sender accounts and ${num(ov.receivers)} distinct receiver accounts${ov.days ? `, over ${ov.days} days` : ""}.
          ${o.rows_invalid ? `${num(o.rows_invalid)} rows were skipped as invalid.` : "No rows were skipped."}
          ${ov.transactions - ov.scored ? `${num(ov.transactions - ov.scored)} rows are PAYMENT, CASH_IN or DEBIT and are not scored.` : ""}</p>
      </section>
      <section>
        <h2>2. Which transaction types are flagged?</h2>
        <p>${typeText}</p>
        <div class="cols">
          <div class="chart-box"><canvas id="upTypeChart"></canvas></div>
          <table class="data" id="upTypeTable"></table>
        </div>
      </section>
      <section>
        <h2>3. When are transactions flagged?</h2>
        <p>${hourText}</p>
        <div class="chart-box wide"><canvas id="upHourChart"></canvas></div>
      </section>
      <section>
        <h2>4. Amounts and model scores</h2>
        <div class="cols">
          <div><h3>Transaction amounts</h3><div class="chart-box"><canvas id="upAmountChart"></canvas></div></div>
          <div><h3>Distribution of model scores</h3><div class="chart-box"><canvas id="upScoreChart"></canvas></div><p class="note">${scoreText}</p></div>
        </div>
      </section>
      ${accuracy}${receivers}`;

    $("upBackToPaysim").onclick = () => selectDataset("paysim");
    const rec = $("upReceivers");
    if (rec) rec.addEventListener("click", (e) => { if (e.target.dataset.account) explorer.open({ account: e.target.dataset.account, role: "receiver" }); });

    table($("upTypeTable"), ["Type", "Transactions", "Avg amount", "Flagged", "Flag rate"].concat(labelled ? ["Labelled fraud"] : []),
      a.type_summary.map((t) => ({
        cls: t.flagged > 0 ? "highlight" : "",
        cells: [t.type, num(t.transactions), num(t.avg_amount), num(t.flagged), t.scored ? pct(rate(t.flagged, t.scored), 2) : "not scored"]
          .concat(labelled ? [num(t.labelled_fraud || 0)] : []),
      })));

    const unit = Math.max(...a.type_summary.map((t) => t.transactions)) >= 100000 ? 1000 : 1;
    chart("upTypeChart", {
      type: "bar",
      data: { labels: a.type_summary.map((t) => t.type), datasets: [{
        label: "Transactions", data: a.type_summary.map((t) => t.transactions / unit),
        backgroundColor: a.type_summary.map((t) => (t.flagged > 0 ? COLORS.fraud : COLORS.legit)) }] },
      options: { plugins: { legend: { display: false },
        title: { display: true, text: `Transactions by type${unit === 1000 ? " (thousands)" : ""}, red = contains flagged rows` } } },
    });

    const datasetsHour = [
      { type: "bar", label: "All transactions", data: a.hourly.map((h) => h.transactions), backgroundColor: "#d0d7de", yAxisID: "y" },
      { type: "line", label: "Flag rate (% of scored)", data: a.hourly.map((h) => rate(h.flagged, h.scored)), borderColor: COLORS.fraud, backgroundColor: COLORS.fraud, yAxisID: "y1" },
    ];
    if (labelled) {
      datasetsHour.push({ type: "line", label: "Labelled fraud rate (%)", data: a.hourly.map((h) => rate(h.labelled_fraud || 0, h.transactions)),
        borderColor: COLORS.accent, backgroundColor: COLORS.accent, borderDash: [5, 4], yAxisID: "y1" });
    }
    chart("upHourChart", {
      data: { labels: a.hourly.map((h) => `${h.hour_of_day}:00`), datasets: datasetsHour },
      options: { interaction: { mode: "index", intersect: false }, scales: {
        y: { title: { display: true, text: "Transactions" } },
        y1: { position: "right", grid: { drawOnChartArea: false }, title: { display: true, text: "Rate (%)" } } } },
    });

    chart("upAmountChart", {
      type: "bar",
      data: { labels: a.amount_bands.map((b) => BAND[b.band]), datasets: [
        { label: "All transactions", data: a.amount_bands.map((b) => b.transactions), backgroundColor: COLORS.legit },
        { label: "Flagged", data: a.amount_bands.map((b) => b.flagged), backgroundColor: COLORS.fraud }] },
      options: { scales: { y: { type: "logarithmic", min: 1, title: { display: true, text: "rows (log scale)" } } } },
    });

    const bins = Array.from({ length: 10 }, (_, i) => a.score_histogram.find((b) => b.bin === i) || { transactions: 0, labelled_fraud: 0 });
    const scoreSets = labelled
      ? [{ label: "Labelled legit", data: bins.map((b) => b.transactions - (b.labelled_fraud || 0)), backgroundColor: COLORS.legit, stack: "s" },
         { label: "Labelled fraud", data: bins.map((b) => b.labelled_fraud || 0), backgroundColor: COLORS.fraud, stack: "s" }]
      : [{ label: "Scored rows", data: bins.map((b) => b.transactions), backgroundColor: COLORS.accent }];
    chart("upScoreChart", {
      type: "bar",
      data: { labels: bins.map((_, i) => `${(i / 10).toFixed(1)}–${((i + 1) / 10).toFixed(1)}`), datasets: scoreSets },
      options: { scales: { x: { stacked: true, title: { display: true, text: "model score" } }, y: { stacked: true, type: "logarithmic", min: 1, title: { display: true, text: "rows (log scale)" } } } },
    });
  }

  window.addEventListener("datasetchange", show);
  window.addEventListener("DOMContentLoaded", () => { if (datasets.isUpload()) show(); });
})();
