// Cases tab: Create / Read / Update / Delete fraud cases through /api/cases (server mode only).
// Depends on helpers from app.js: $, apiAs, num, esc, table, toast, views.

(() => {
  const STATUS_LABEL = { new: "New", investigating: "Investigating", confirmed_fraud: "Confirmed fraud", false_positive: "False positive", closed: "Closed" };
  const STATUSES = Object.keys(STATUS_LABEL);
  const state = { skip: 0, limit: 15, total: 0, items: [], selected: null, status: "", all: false };
  const scope = () => (state.all ? "all" : datasets.current.id);
  let searchTimer = null;

  const badge = (s) => `<span class="badge s-${s}">${STATUS_LABEL[s]}</span>`;
  const prio = (p) => `<span class="prio ${p}">${p}</span>`;
  const time = (iso) => new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
  const score = (tx) => (tx.fraud_probability === null ? "–" : tx.fraud_probability.toFixed(3));

  // ---- stats cards (click to filter) ----
  async function loadStats() {
    const s = await apiAs(`/api/cases/stats?${new URLSearchParams({ dataset: scope() })}`);
    $("caseStats").innerHTML = STATUSES.map((k) =>
      `<button type="button" class="stat ${state.status === k ? "active" : ""}" data-status="${k}">` +
      `<div class="label">${STATUS_LABEL[k]}</div><div class="value">${s.by_status[k]}</div></button>`).join("");
    $("caseCount").textContent = `${s.total_all_datasets} of ${s.limit} cases used`;
    $("caseScope").textContent = state.all ? "Showing the cases of all your datasets."
      : `Showing the cases of ${datasets.current.name}. Tick "all datasets" to see every case.`;
  }

  // ---- list ----
  async function loadList(skip = 0) {
    state.skip = skip;
    const q = new URLSearchParams({ skip, limit: state.limit, sort: $("caseSort").value, dataset: scope() });
    if (state.status) q.set("status", state.status);
    if ($("caseQuery").value.trim()) q.set("q", $("caseQuery").value.trim());
    const res = await apiAs(`/api/cases?${q}`);
    state.items = res.items;
    state.total = res.total;
    table($("caseTable"), ["Case", "Dataset", "Transaction", "Accounts", "Score", "Status", "Priority", "Assignee", "Notes", "Updated"],
      res.items.map((c) => ({
        cls: "clickable" + (state.selected === c.id ? " selected" : ""),
        cells: [c.id.slice(-6), esc(c.dataset_name || "PaySim"), `#${c.tx_id} ${c.tx.type} ${num(c.tx.amount)}`, `${esc(c.tx.name_orig)} → ${esc(c.tx.name_dest)}`,
          score(c.tx), badge(c.status), prio(c.priority), esc(c.assignee) || "–", c.notes.length, time(c.updated_at)],
      })));
    document.querySelectorAll("#caseTable tbody tr").forEach((tr, i) => (tr.dataset.i = i));
    if (!res.items.length) $("caseTable").insertAdjacentHTML("beforeend",
      `<tbody><tr><td colspan="10" class="empty">No cases yet. Open one from the Explorer or enter a transaction id above.</td></tr></tbody>`);
    const page = Math.floor(skip / state.limit) + 1;
    $("casePager").hidden = res.total <= state.limit;
    $("casePageInfo").textContent = `Page ${page} of ${Math.max(1, Math.ceil(res.total / state.limit))}`;
    $("casePrev").disabled = skip <= 0;
    $("caseNext").disabled = skip + state.limit >= res.total;
  }

  async function refresh() {
    try {
      $("casesBanner").innerHTML = "";
      await Promise.all([loadStats(), loadList(state.skip)]);
    } catch (err) {
      $("casesBanner").innerHTML = `<div class="search-status error">${esc(err.message)}</div>`;
    }
  }

  // ---- detail / edit ----
  function renderDetail(c) {
    state.selected = c.id;
    const tx = c.tx;
    const options = (list, current) => list.map((v) => `<option value="${v}" ${v === current ? "selected" : ""}>${STATUS_LABEL[v] || v}</option>`).join("");
    const statusList = [c.status, ...c.next_statuses];
    $("caseDetail").innerHTML = `
      <div class="card" id="caseCard">
        <h3>Case ${c.id.slice(-6)} &middot; transaction #${c.tx_id} ${badge(c.status)}</h3>
        <dl class="kv">
          <dt>Dataset</dt><dd>${esc(c.dataset_name || "PaySim")}</dd>
          <dt>Transaction</dt><dd>${esc(tx.type)}, ${num(tx.amount, 2)} on day ${tx.day} at ${String(tx.hour_of_day).padStart(2, "0")}:00</dd>
          <dt>Sender</dt><dd>${esc(tx.name_orig)}: ${num(tx.old_balance_orig, 2)} → ${num(tx.new_balance_orig, 2)}</dd>
          <dt>Receiver</dt><dd>${esc(tx.name_dest)}: ${num(tx.old_balance_dest, 2)} → ${num(tx.new_balance_dest, 2)}</dd>
          <dt>Model score</dt><dd>${tx.fraud_probability === null ? "not modelled" : `${tx.fraud_probability.toFixed(4)} (${tx.model_flag ? "flagged" : "not flagged"})`}</dd>
          <dt>Dataset label</dt><dd>${tx.is_fraud ? '<span class="badge fraud">fraud</span>' : '<span class="badge ok">legit</span>'}</dd>
          <dt>Opened</dt><dd>${time(c.created_at)}</dd>
        </dl>
        <form id="caseEditForm" class="inline-form">
          <label>Status <select name="status">${options(statusList, c.status)}</select></label>
          <label>Priority <select name="priority">${options(["low", "medium", "high"], c.priority)}</select></label>
          <label>Assignee <input name="assignee" maxlength="60" value="${esc(c.assignee)}"></label>
          <button type="submit" class="primary">Save changes</button>
        </form>
        <h3>Notes</h3>
        <ul class="notes">${c.notes.map((n) => `<li>${esc(n.text)} <span class="note">${time(n.at)}</span></li>`).join("") || '<li class="note">No notes yet.</li>'}</ul>
        <form id="caseNoteForm" class="inline-form">
          <textarea name="add_note" rows="2" maxlength="2000" placeholder="Add a note" required></textarea>
          <button type="submit">Add note</button>
        </form>
        <h3>History</h3>
        <ul class="notes">${c.history.map((h) => `<li>${h.from ? `${STATUS_LABEL[h.from]} → ` : "Opened as "}<b>${STATUS_LABEL[h.to]}</b> <span class="note">${time(h.at)}</span></li>`).join("")}</ul>
        <div class="form-actions" id="caseDeleteRow"><button type="button" id="caseDelete" class="danger">Delete case</button></div>
      </div>`;
    $("caseEditForm").addEventListener("submit", (e) => { e.preventDefault(); save(c, new FormData(e.target)); });
    $("caseNoteForm").addEventListener("submit", async (e) => {
      e.preventDefault();
      await patch(c.id, { add_note: new FormData(e.target).get("add_note") }, "Note added");
    });
    $("caseDelete").addEventListener("click", () => {
      $("caseDeleteRow").innerHTML = `<span>Delete this case permanently?</span> <button type="button" id="caseDeleteYes" class="danger">Yes, delete</button> <button type="button" id="caseDeleteNo">Cancel</button>`;
      $("caseDeleteNo").onclick = () => (renderDetail(c));
      $("caseDeleteYes").onclick = () => remove(c.id);
    });
  }

  async function patch(id, body, message) {
    try {
      const updated = await apiAs(`/api/cases/${id}`, { method: "PATCH", body: JSON.stringify(body) });
      toast(message);
      renderDetail(updated);
      await Promise.all([loadStats(), loadList(state.skip)]);
    } catch (err) {
      toast(err.message, "error");
    }
  }

  async function save(c, form) {
    const body = {};
    for (const key of ["status", "priority", "assignee"]) {
      const value = String(form.get(key)).trim();
      if (value !== (c[key] || "")) body[key] = value;
    }
    if (!Object.keys(body).length) return toast("Nothing changed", "ok");
    await patch(c.id, body, "Case updated");
  }

  async function remove(id) {
    try {
      await apiAs(`/api/cases/${id}`, { method: "DELETE" });
      state.selected = null;
      $("caseDetail").innerHTML = "";
      toast("Case deleted");
      await refresh();
    } catch (err) {
      toast(err.message, "error");
    }
  }

  async function open(id) {
    try {
      renderDetail(await apiAs(`/api/cases/${id}`));
      await loadList(state.skip);
    } catch (err) {
      toast(err.message, "error");
    }
  }

  // ---- create ----
  async function create(body) {
    try {
      const c = await apiAs("/api/cases", { method: "POST", body: JSON.stringify({ dataset: datasets.current.id, ...body }) });
      toast(`Case opened for transaction #${c.tx_id}`, "ok", { label: "View case", run: () => { location.hash = "cases"; showView("cases"); open(c.id); } });
      if (shown.has("cases")) await refresh();
      return c;
    } catch (err) {
      toast(err.message, "error");
    }
  }

  $("caseOpenForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = new FormData(e.target);
    const c = await create({ tx_id: Number(f.get("tx_id")), assignee: String(f.get("assignee") || "") });
    if (c) { e.target.reset(); renderDetail(c); }
  });

  $("caseStats").addEventListener("click", (e) => {
    const card = e.target.closest("[data-status]");
    if (!card) return;
    state.status = state.status === card.dataset.status ? "" : card.dataset.status;
    $("caseStatusFilter").value = state.status;
    refresh();
  });
  $("caseStatusFilter").addEventListener("change", () => { state.status = $("caseStatusFilter").value; refresh(); });
  $("caseSort").addEventListener("change", () => loadList(0).catch((e) => toast(e.message, "error")));
  $("caseQuery").addEventListener("input", () => { clearTimeout(searchTimer); searchTimer = setTimeout(() => loadList(0).catch((e) => toast(e.message, "error")), 250); });
  $("caseRefresh").addEventListener("click", refresh);
  $("casePrev").addEventListener("click", () => loadList(Math.max(0, state.skip - state.limit)));
  $("caseNext").addEventListener("click", () => loadList(state.skip + state.limit));
  $("caseTable").addEventListener("click", (e) => {
    const tr = e.target.closest("tr[data-i]");
    if (!tr) return;
    document.querySelectorAll("#caseTable tr.selected").forEach((x) => x.classList.remove("selected"));
    tr.classList.add("selected");
    renderDetail(state.items[Number(tr.dataset.i)]);
  });

  // "Open case" button on the Explorer's transaction detail card
  window.explorer.onDetail.push((row, container) => {
    const button = document.createElement("button");
    button.className = "primary";
    button.textContent = "Open case";
    button.onclick = async () => {
      button.disabled = true;
      await create({ tx_id: row.tx_id });
      button.disabled = false;
    };
    container.appendChild(button);
  });

  function updateOpenLabel() {
    $("caseOpenLabel").textContent = datasets.isUpload()
      ? `Open a case for row number in ${datasets.current.name}` : "Open a case for transaction id";
  }

  $("caseAllDatasets").addEventListener("change", (e) => { state.all = e.target.checked; state.selected = null; $("caseDetail").innerHTML = ""; refresh(); });
  window.addEventListener("datasetchange", () => {
    state.selected = null;
    state.skip = 0;
    $("caseDetail").innerHTML = "";
    updateOpenLabel();
    if (shown.has("cases")) refresh();
  });

  views.cases = { show() { updateOpenLabel(); return refresh(); } };
})();
