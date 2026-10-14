// Explorer tab: search the full transaction table through /api/search (server mode only).
// Depends on helpers from app.js: $, api, num, esc, table, data, COLORS.

window.explorer = { onDetail: [] }; // other tabs (cases) add buttons to the detail card: (row, container) => void

(() => {
  const form = $("searchForm");
  const state = { skip: 0, limit: 25, total: 0, rows: [], query: {} };
  let exampleClicks = 0;

  const latency = (v) => (v >= 100 ? Math.round(v) : v.toFixed(1)) + " ms";
  const when = (r) => `day ${r.day}, ${String(r.hour_of_day).padStart(2, "0")}:00`;
  const acct = (id) => `<a href="#explorer" class="acct" data-account="${esc(id)}">${esc(id)}</a>`;
  const label = (r) => (r.is_fraud ? '<span class="badge fraud">fraud</span>' : '<span class="badge ok">legit</span>');

  function risk(r) {
    if (r.fraud_probability === null) return '<span class="badge" title="The model was trained on TRANSFER and CASH_OUT only">not modelled</span>';
    const width = Math.max(2, Math.round(r.fraud_probability * 60));
    return `<span class="risk ${r.model_flag ? "hot" : ""}"><i style="width:${width}px"></i>${r.fraud_probability.toFixed(3)}</span>`;
  }

  function queryString(skip) {
    const q = new URLSearchParams({ skip, limit: state.limit });
    for (const [k, v] of Object.entries(state.query)) q.set(k, v);
    return q.toString();
  }

  function readForm() {
    const query = {};
    for (const [k, v] of new FormData(form).entries()) {
      const value = String(v).trim();
      if (value !== "") query[k] = k === "account" ? value.toUpperCase() : value;
    }
    return query;
  }

  function fillForm(values) {
    form.reset();
    for (const [k, v] of Object.entries(values)) if (form.elements[k]) form.elements[k].value = v;
  }

  function status(html, isError) {
    const el = $("searchStatus");
    el.hidden = false;
    el.classList.toggle("error", !!isError);
    el.innerHTML = html;
  }

  async function run(skip = 0) {
    state.skip = skip;
    $("txDetail").innerHTML = "";
    status("Searching&hellip;");
    try {
      const res = await api(`/api/search?${queryString(skip)}`);
      state.rows = res.items;
      state.total = res.total;
      render(res);
    } catch (err) {
      state.rows = [];
      table($("searchTable"), [], []);
      $("searchPager").hidden = true;
      status(`Could not search: ${esc(err.message)}`, true);
    }
  }

  function render(res) {
    const how = res.access === "index" ? "index lookup" : "table scan";
    status(
      `<b>${num(res.total)}</b> transaction${res.total === 1 ? "" : "s"} found in <b>${latency(res.took_ms)}</b> ` +
      `<span class="badge">${how}</span> <span class="note">server time, excluding the network</span>`);

    table($("searchTable"),
      ["ID", "When", "Type", "Amount", "Sender", "Sender balance", "Receiver", "Receiver balance", "Label", "Model score"],
      res.items.map((r, i) => ({
        cls: "clickable",
        cells: [r.tx_id, when(r), r.type, num(r.amount, 2), acct(r.name_orig),
          `${num(r.old_balance_orig)} → ${num(r.new_balance_orig)}`, acct(r.name_dest),
          `${num(r.old_balance_dest)} → ${num(r.new_balance_dest)}`, label(r), risk(r)],
        attrs: `data-i="${i}"`,
      })));
    document.querySelectorAll("#searchTable tbody tr").forEach((tr, i) => tr.setAttribute("data-i", i));

    const page = Math.floor(res.skip / state.limit) + 1;
    const pages = Math.max(1, Math.ceil(res.total / state.limit));
    $("searchPager").hidden = res.total <= state.limit;
    $("searchPageInfo").textContent = `Page ${page} of ${num(pages)}`;
    $("searchPrev").disabled = res.skip <= 0;
    $("searchNext").disabled = res.skip + state.limit >= Math.min(res.total, 10000 + state.limit);

    loadAccountCard();
  }

  async function loadAccountCard() {
    const card = $("accountCard");
    card.innerHTML = "";
    const id = state.query.account;
    if (!id) return;
    try {
      const a = await api(`/api/accounts/${encodeURIComponent(id)}`);
      const side = (s, verb) => (s.transactions
        ? `${verb} <b>${num(s.transactions)}</b> (${num(s.total_amount, 2)}${s.fraud_labelled ? `, <b>${s.fraud_labelled}</b> labelled fraud` : ""})`
        : `${verb} none`);
      card.innerHTML = `<div class="card"><b>${esc(a.account)}</b> <span class="badge">${a.kind}</span> &middot; ` +
        `${side(a.as_sender, "sent")} &middot; ${side(a.as_receiver, "received")} ` +
        `<span class="note">(${latency(a.took_ms)})</span></div>`;
    } catch (err) {
      card.innerHTML = `<div class="card note">No account with id ${esc(id)} in the dataset.</div>`;
    }
  }

  function showDetail(row) {
    const kv = (k, v) => `<dt>${k}</dt><dd>${v}</dd>`;
    const decision = row.fraud_probability === null
      ? "not modelled (the model only covers TRANSFER and CASH_OUT)"
      : `${row.fraud_probability.toFixed(4)} &rarr; <b>${row.model_flag ? "flagged as fraud" : "looks legitimate"}</b>`;
    $("txDetail").innerHTML = `<div class="card"><h3>Transaction ${row.tx_id}</h3><dl class="kv">` +
      kv("Time", `${when(row)} (hour ${row.step} of the simulation)`) +
      kv("Type / amount", `${row.type}, ${num(row.amount, 2)}`) +
      kv("Sender", `${acct(row.name_orig)}: ${num(row.old_balance_orig, 2)} → ${num(row.new_balance_orig, 2)}`) +
      kv("Receiver", `${acct(row.name_dest)}: ${num(row.old_balance_dest, 2)} → ${num(row.new_balance_dest, 2)}`) +
      kv("Balance errors", `sender ${num(row.new_balance_orig + row.amount - row.old_balance_orig, 2)}, ` +
        `receiver ${num(row.old_balance_dest + row.amount - row.new_balance_dest, 2)}`) +
      kv("Dataset label", label(row) + (row.is_flagged_fraud ? ' <span class="badge">flagged by the provider\'s rule</span>' : "")) +
      kv("Model score", decision) +
      `</dl><div class="form-actions" id="txActions" style="margin-top:10px"></div></div>`;
    window.explorer.onDetail.forEach((fn) => fn(row, $("txActions")));
  }

  // ---- events ----
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    state.query = readForm();
    run(0);
  });
  $("searchReset").addEventListener("click", () => {
    form.reset();
    state.query = {};
    $("searchStatus").hidden = true;
    $("accountCard").innerHTML = "";
    $("txDetail").innerHTML = "";
    $("searchPager").hidden = true;
    table($("searchTable"), [], []);
  });
  $("searchPrev").addEventListener("click", () => run(Math.max(0, state.skip - state.limit)));
  $("searchNext").addEventListener("click", () => run(state.skip + state.limit));

  $("searchTable").addEventListener("click", (e) => {
    const link = e.target.closest("a.acct");
    if (link) {
      e.preventDefault();
      fillForm({ account: link.dataset.account });
      state.query = readForm();
      run(0);
      return;
    }
    const tr = e.target.closest("tr[data-i]");
    if (!tr) return;
    document.querySelectorAll("#searchTable tr.selected").forEach((x) => x.classList.remove("selected"));
    tr.classList.add("selected");
    showDetail(state.rows[Number(tr.dataset.i)]);
  });
  $("txDetail").addEventListener("click", (e) => {
    const link = e.target.closest("a.acct");
    if (!link) return;
    e.preventDefault();
    fillForm({ account: link.dataset.account });
    state.query = readForm();
    run(0);
    window.scrollTo({ top: $("searchForm").offsetTop - 20, behavior: "smooth" });
  });

  const EXAMPLES = {
    async fraudSender() {
      const skips = [0, 40, 120, 300];
      const r = await api(`/api/search?fraud=true&type=TRANSFER&limit=1&skip=${skips[exampleClicks % skips.length]}`);
      return { account: r.items[0].name_orig, role: "sender" };
    },
    async mule() {
      const mules = await data.result("repeat_fraud_destinations");
      return { account: mules[exampleClicks % mules.length].name_dest, role: "receiver" };
    },
    async merchant() {
      const r = await api(`/api/search?type=PAYMENT&limit=1&skip=${1000 * (exampleClicks + 1)}`);
      return { account: r.items[0].name_dest, role: "receiver" };
    },
    async bigFraud() {
      return { fraud: "true", type: "TRANSFER", min_amount: "5000000", sort: "amount", order: "desc" };
    },
  };
  document.querySelectorAll("[data-search-example]").forEach((b) => b.addEventListener("click", async () => {
    try {
      const values = await EXAMPLES[b.dataset.searchExample]();
      exampleClicks += 1;
      fillForm(values);
      state.query = readForm();
      run(0);
    } catch (err) {
      status(`Could not load an example: ${esc(err.message)}`, true);
    }
  }));

  // ---- benchmark ----
  async function showBenchmark() {
    let b;
    try {
      b = (await data.result("search_benchmark"))[0];
    } catch (err) {
      return;
    }
    if (!b) return;
    $("benchIntro").textContent =
      `Task: ${b.query}, among ${num(b.rows_searched)} transactions. The same lookup for ${b.accounts_tested} random accounts, run four ways.`;
    const engines = b.engines;
    table($("benchTable"), ["Method", "Median", "95th percentile", "vs index"],
      engines.map((e) => ({
        cls: e.name.includes("ART") ? "highlight" : "",
        cells: [`${e.name}<br><span class="note">${e.method}</span>`, latency(e.median_ms), latency(e.p95_ms),
          e.slowdown_vs_index === 1 ? "–" : `${num(e.slowdown_vs_index, 0)}× slower`],
      })));

    const valueLabels = {
      id: "valueLabels",
      afterDatasetsDraw(chart) {
        const { ctx } = chart;
        ctx.save();
        ctx.fillStyle = "#1f2328";
        ctx.font = "12px sans-serif";
        chart.getDatasetMeta(0).data.forEach((bar, i) => ctx.fillText(latency(engines[i].median_ms), bar.x + 6, bar.y + 4));
        ctx.restore();
      },
    };
    new Chart($("benchChart"), {
      type: "bar",
      plugins: [valueLabels],
      data: {
        labels: engines.map((e) => e.name),
        datasets: [{
          data: engines.map((e) => e.median_ms),
          backgroundColor: engines.map((e) => (e.name.includes("ART") ? COLORS.accent : COLORS.legit)),
        }],
      },
      options: {
        indexAxis: "y",
        layout: { padding: { right: 60 } },
        plugins: { legend: { display: false }, title: { display: true, text: "Median lookup time (log scale, lower is faster)" } },
        scales: { x: { type: "logarithmic", min: 1, title: { display: true, text: "milliseconds" } } },
      },
    });
    const env = b.environment;
    $("benchNote").textContent =
      `Measured ${env.generated_at} on ${env.os} (${env.cpu_threads} threads, DuckDB ${env.duckdb}). Times are server-side and exclude the network; ` +
      `Spark's time excludes JVM start-up. Numbers differ between machines: rerun with python -m scripts.benchmark_search.`;
  }

  views.explorer = {
    async show() {
      try {
        const health = await api("/api/health");
        if (!health.search_index) throw health;
        $("explorerRows").textContent = num(health.search_index.rows);
      } catch (err) {
        const state = err && err.search_index_state;
        status(state === "downloading"
          ? "The search index is still downloading on the server. Try again in a minute."
          : state === "failed"
            ? "The search index could not be downloaded. Check the server logs."
            : "The search index has not been built yet. Run <code>python -m serving.build_search_index</code> and restart the server.", true);
        form.querySelectorAll("input, select, button").forEach((el) => (el.disabled = true));
        return;
      }
      showBenchmark();
    },
  };
})();
