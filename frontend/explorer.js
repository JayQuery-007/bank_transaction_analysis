// Explorer tab: search the full transaction table through /api/search (server mode only).
// Depends on helpers from app.js: $, api, num, esc, table, data, COLORS, and on datasets.js (the selected dataset).

window.explorer = { onDetail: [] }; // other tabs (cases) add buttons to the detail card: (row, container) => void

(() => {
  const form = $("searchForm");
  const state = { skip: 0, limit: 25, total: 0, rows: [], query: {} };
  let exampleClicks = 0;

  const latency = (v) => (v >= 100 ? Math.round(v) : v.toFixed(1)) + " ms";
  const hh = (r) => `${String(r.hour_of_day).padStart(2, "0")}:00`;
  const when = (r) => (r.day === null || r.day === undefined ? hh(r) : `day ${r.day}, ${hh(r)}`);
  const acct = (id) => `<a href="#explorer" class="acct" data-account="${esc(id)}">${esc(id)}</a>`;
  const label = (r) => (r.is_fraud === null || r.is_fraud === undefined
    ? '<span class="badge" title="The file has no isFraud column">no label</span>'
    : r.is_fraud ? '<span class="badge fraud">fraud</span>' : '<span class="badge ok">legit</span>');

  function risk(r) {
    if (r.fraud_probability === null) return '<span class="badge" title="The model was trained on TRANSFER and CASH_OUT only">not modelled</span>';
    const width = Math.max(2, Math.round(r.fraud_probability * 60));
    return `<span class="risk ${r.model_flag ? "hot" : ""}"><i style="width:${width}px"></i>${r.fraud_probability.toFixed(3)}</span>`;
  }

  function queryString(skip) {
    const q = new URLSearchParams({ skip, limit: state.limit });
    for (const [k, v] of Object.entries(state.query)) q.set(k, v);
    datasets.withDataset(q);
    return q.toString();
  }

  function readForm() {
    const account = form.elements.account.value.trim().toUpperCase();
    return account ? { account } : {};
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
      const res = await datasets.call(`/api/search?${queryString(skip)}`);
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
      const a = await datasets.call(`/api/accounts/${encodeURIComponent(id)}?${datasets.withDataset(new URLSearchParams())}`);
      const side = (s, verb) => (s.transactions
        ? `${verb} <b>${num(s.transactions)}</b> (${num(s.total_amount, 2)}${s.fraud_labelled ? `, <b>${s.fraud_labelled}</b> labelled fraud` : ""})`
        : `${verb} none`);
      card.innerHTML = `<div class="card"><b>${esc(a.account)}</b> <span class="badge">${a.kind}</span> &middot; ` +
        `${side(a.as_sender, "sent")} &middot; ${side(a.as_receiver, "received")} ` +
        `<span class="note">(${latency(a.took_ms)})</span></div>`;
    } catch (err) {
      card.innerHTML = `<div class="card note">No account with id ${esc(id)} in this dataset.</div>`;
    }
  }

  function showDetail(row) {
    const kv = (k, v) => `<dt>${k}</dt><dd>${v}</dd>`;
    const decision = row.fraud_probability === null
      ? "not modelled (the model only covers TRANSFER and CASH_OUT)"
      : `${row.fraud_probability.toFixed(4)} &rarr; <b>${row.model_flag ? "flagged as fraud" : "looks legitimate"}</b>`;
    $("txDetail").innerHTML = `<div class="card"><h3>Transaction ${row.tx_id}</h3><dl class="kv">` +
      kv("Time", row.step ? `${when(row)} (hour ${row.step} of the period)` : when(row)) +
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


  // ---- account suggestions while typing ----
  const input = form.elements.account;
  const list = $("accountList");
  let suggestions = [], activeIndex = -1, suggestTimer = null, suggestSeq = 0;

  function hideSuggestions() {
    clearTimeout(suggestTimer);
    suggestSeq += 1; // a late answer must not reopen the list
    list.hidden = true;
    input.setAttribute("aria-expanded", "false");
    suggestions = [];
    activeIndex = -1;
  }

  function showSuggestions(prefix, items) {
    suggestions = items;
    activeIndex = -1;
    list.innerHTML = items.length
      ? items.map((id, i) => `<li role="option" data-i="${i}"><b>${esc(id.slice(0, prefix.length))}</b>${esc(id.slice(prefix.length))}</li>`).join("")
      : `<li class="empty">No account starts with ${esc(prefix)}</li>`;
    list.hidden = false;
    input.setAttribute("aria-expanded", "true");
  }

  function highlight(i) {
    activeIndex = i;
    list.querySelectorAll("li").forEach((li, n) => li.classList.toggle("active", n === i));
    const li = list.querySelector("li.active");
    if (li) li.scrollIntoView({ block: "nearest" });
  }

  function choose(id) {
    input.value = id;
    hideSuggestions();
    search();
  }

  async function fetchSuggestions() {
    const prefix = input.value.trim().toUpperCase();
    if (!prefix || !/^[A-Z0-9_-]{1,32}$/.test(prefix)) return hideSuggestions();
    const seq = ++suggestSeq;
    try {
      const q = datasets.withDataset(new URLSearchParams({ prefix, limit: 10 }));
      const res = await datasets.call(`/api/account-suggestions?${q}`);
      if (seq === suggestSeq) showSuggestions(prefix, res.items);
    } catch (err) {
      if (seq === suggestSeq) hideSuggestions();
    }
  }

  input.addEventListener("input", () => {
    clearTimeout(suggestTimer);
    suggestTimer = setTimeout(fetchSuggestions, 120);
  });
  input.addEventListener("keydown", (e) => {
    if (list.hidden || !suggestions.length) return;
    if (e.key === "ArrowDown") { e.preventDefault(); highlight((activeIndex + 1) % suggestions.length); }
    else if (e.key === "ArrowUp") { e.preventDefault(); highlight((activeIndex - 1 + suggestions.length) % suggestions.length); }
    else if (e.key === "Enter" && activeIndex >= 0) { e.preventDefault(); choose(suggestions[activeIndex]); }
    else if (e.key === "Escape") hideSuggestions();
  });
  input.addEventListener("blur", () => setTimeout(hideSuggestions, 150));
  list.addEventListener("mousedown", (e) => { // before the input loses focus
    const li = e.target.closest("li[data-i]");
    if (li) { e.preventDefault(); choose(suggestions[Number(li.dataset.i)]); }
  });

  // ---- events ----
  function search() {
    state.query = readForm();
    if (!state.query.account) {
      status("Enter an account ID to search, or pick one of the examples below.", true);
      return;
    }
    run(0);
  }
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    hideSuggestions();
    search();
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

  // each example finds a real account in the selected dataset
  const pick = async (params, side) => {
    const find = async (skip) => {
      const q = datasets.withDataset(new URLSearchParams({ limit: 1, skip, ...params }));
      return (await datasets.call(`/api/search?${q}`)).items;
    };
    // vary the account between clicks; fall back to the first match when there are fewer matches than that
    let items = await find(params.skip === undefined ? (exampleClicks * 7) % 50 : params.skip);
    if (!items.length) items = await find(0);
    if (!items.length) throw new Error("this dataset has no such account");
    return { account: items[0][side] };
  };
  const EXAMPLES = {
    // uploaded files
    topRisk: () => pick({ flagged: "true", sort: "fraud_probability", order: "desc" }, "name_orig"),
    bigFlagged: () => pick({ flagged: "true", sort: "amount", order: "desc" }, "name_dest"),
    missed: () => pick({ fraud: "true", flagged: "false" }, "name_orig"),
    falseAlarm: () => pick({ fraud: "false", flagged: "true", sort: "fraud_probability", order: "desc" }, "name_orig"),
    // main ledger
    fraudSender() {
      const skips = [0, 40, 120, 300];
      return pick({ fraud: "true", type: "TRANSFER", skip: skips[exampleClicks % skips.length] }, "name_orig");
    },
    async mule() {
      const mules = await data.result("repeat_fraud_destinations");
      return { account: mules[exampleClicks % mules.length].name_dest };
    },
    merchant: () => pick({ type: "PAYMENT", skip: 1000 * (exampleClicks + 1) }, "name_dest"),
  };
  document.querySelectorAll("[data-search-example]").forEach((b) => b.addEventListener("click", async () => {
    try {
      const values = await EXAMPLES[b.dataset.searchExample]();
      exampleClicks += 1;
      fillForm(values);
      hideSuggestions();
      search();
    } catch (err) {
      status(`Could not load an example: ${esc(err.message)}`, true);
    }
  }));

  const PAYSIM_INTRO = $("explorerIntro").innerHTML;

  function resetResults() {
    form.reset();
    state.query = {};
    $("searchStatus").hidden = true;
    $("accountCard").innerHTML = "";
    $("txDetail").innerHTML = "";
    $("searchPager").hidden = true;
    table($("searchTable"), [], []);
  }

  // Shows the controls that make sense for the selected dataset and clears the previous results.
  function applyDataset() {
    const up = datasets.isUpload();
    form.querySelectorAll("input, select, button").forEach((el) => (el.disabled = false));
    $("examplesPaysim").hidden = up;
    $("examplesUpload").hidden = !up;
    document.querySelectorAll("#examplesUpload [data-needs-labels]").forEach((b) => (b.hidden = !datasets.current.labelled));
    $("explorerIntro").innerHTML = up
      ? `Look up an account in <b>${esc(datasets.current.name)}</b> (${num(datasets.current.rows)} rows). Start typing and matching accounts appear; ` +
        `pick one to see everything it sent and received, with the model's score for each transaction.`
      : PAYSIM_INTRO;
    resetResults();
  }

  window.addEventListener("datasetchange", () => {
    applyDataset();
    if (!datasets.isUpload() && !$("view-explorer").hidden) openMainLedger();
  });

  window.explorer.open = function (values) { // used by the Overview tab to jump to an account
    location.hash = "explorer";
    showView("explorer");
    fillForm(values);
    state.query = readForm();
    run(0);
  };

  // the main ledger needs its search index; checks that its search index is ready
  async function openMainLedger() {
      try {
        const health = await api("/api/health");
        if (!health.search_index) throw health;
        $("explorerRows").textContent = num(health.search_index.rows);
      } catch (err) {
        const state = err && err.search_index_state;
        status(state === "downloading"
          ? "Search is still being prepared on the server. Try again in a minute."
          : "Search is not available right now. Please try again later.", true);
        form.querySelectorAll("input, select, button").forEach((el) => (el.disabled = true));
        return;
      }
  }

  views.explorer = {
    async show() {
      applyDataset();
      if (!datasets.isUpload()) await openMainLedger();
    },
  };
})();
