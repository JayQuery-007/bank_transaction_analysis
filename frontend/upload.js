// Upload tab: send a CSV to /api/uploads, show the scored result, keep a history (server mode only).
// Depends on helpers from app.js: $, apiAs, ownerId, num, esc, table, toast, views.

(() => {
  const MAX_BYTES = 25 * 1024 * 1024;
  const state = { current: null, page: 0, pageSize: 15 };

  const time = (iso) => new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
  const pct = (v) => (v === null || v === undefined ? "n/a" : (v * 100).toFixed(1) + "%");
  const risk = (p) => `<span class="risk hot"><i style="width:${Math.max(2, Math.round(p * 60))}px"></i>${p.toFixed(3)}</span>`;

  function status(html, isError) {
    const el = $("uploadStatus");
    el.hidden = !html;
    el.classList.toggle("error", !!isError);
    el.innerHTML = html || "";
  }

  // ---- sending ----
  async function send(file) {
    if (file.size > MAX_BYTES) return status(`${esc(file.name)} is ${(file.size / 1048576).toFixed(1)} MB; the limit is 25 MB.`, true);
    status(`Uploading and scoring <b>${esc(file.name)}</b> (${(file.size / 1024).toFixed(0)} KB)&hellip;`);
    $("uploadResult").innerHTML = "";
    const form = new FormData();
    form.append("file", file, file.name);
    try {
      const batch = await apiAs("/api/uploads", { method: "POST", body: form });
      status("");
      render(batch);
      toast(`${num(batch.rows_valid)} rows scored, ${num(batch.flagged)} flagged`);
      loadList();
    } catch (err) {
      status(`Could not process the file: ${esc(err.message)}`, true);
    }
  }

  // ---- result ----
  function render(b) {
    state.current = b;
    state.page = 0;
    const cards = [
      ["Rows read", b.rows_total], ["Scored by the model", b.rows_scored], ["Flagged as fraud", b.flagged],
      ["Not modelled", b.rows_not_modelled], ["Skipped (invalid)", b.rows_invalid],
    ].map(([label, value], i) => `<div class="stat ${i === 2 ? "fraud" : ""}"><div class="label">${label}</div><div class="value">${num(value)}</div></div>`).join("");

    const m = b.metrics;
    const metrics = m ? `
      <h3>Accuracy against the isFraud column in your file</h3>
      <div class="cols">
        <table class="data">
          <thead><tr><th></th><th>Value</th></tr></thead>
          <tbody>
            <tr><td>Precision (flagged rows that are fraud)</td><td>${pct(m.precision)}</td></tr>
            <tr><td>Recall (frauds that were flagged)</td><td>${pct(m.recall)}</td></tr>
            <tr><td>F1</td><td>${pct(m.f1)}</td></tr>
            <tr><td>Rows compared</td><td>${num(m.rows)}</td></tr>
          </tbody>
        </table>
        <table class="confusion">
          <tr><th></th><th>Predicted legit</th><th>Predicted fraud</th></tr>
          <tr><th>Actual legit</th><td class="good">${num(m.tn)}</td><td class="bad">${num(m.fp)}</td></tr>
          <tr><th>Actual fraud</th><td class="bad">${num(m.fn)}</td><td class="good">${num(m.tp)}</td></tr>
        </table>
      </div>` : `<p class="note">Add an <code>isFraud</code> column (0 or 1) to see precision and recall on your own data.</p>`;

    const errors = b.errors.length ? `
      <h3>Rows that were skipped</h3>
      <ul class="errors">${b.errors.map((e) => `<li>Row ${e.row}: ${esc(e.message)}</li>`).join("")}</ul>
      ${b.rows_invalid > b.errors.length ? `<p class="note">${num(b.rows_invalid - b.errors.length)} more not shown.</p>` : ""}` : "";
    const ignored = b.ignored_columns.length ? `<p class="note">Ignored columns: ${b.ignored_columns.map(esc).join(", ")}.</p>` : "";
    const notModelled = b.rows_not_modelled ? `<p class="note">${num(b.rows_not_modelled)} rows are of a type the model does not score (it was trained on TRANSFER and CASH_OUT only, the only types with fraud in the data).</p>` : "";

    $("uploadResult").innerHTML = `
      <div class="card">
        <div class="result-head">
          <h3 style="margin:0">${esc(b.filename)} <span class="note">scored in ${num(b.scoring_ms, 0)} ms</span></h3>
          <div>
            <button type="button" id="dlScored" class="primary" ${b.download_available ? "" : "disabled title='expired after 24 hours'"}>Download scored CSV</button>
            <button type="button" id="delUpload" class="danger">Delete upload</button>
          </div>
        </div>
        <div class="stats stats3">${cards}</div>
        ${notModelled}${ignored}${metrics}${errors}
        <h3>Highest-risk rows ${b.flagged > b.flagged_shown ? `<span class="note">(top ${num(b.flagged_shown)} of ${num(b.flagged)} flagged)</span>` : ""}</h3>
        <div class="scroll"><table id="flaggedUploadTable" class="data small"></table></div>
        <div class="pager" id="uploadPager"><button id="upPrev">&larr; Previous</button><span id="upInfo"></span><button id="upNext">Next &rarr;</button></div>
      </div>`;
    $("dlScored").onclick = () => download(b);
    $("delUpload").onclick = () => confirmDelete(b);
    $("upPrev").onclick = () => { state.page -= 1; renderFlagged(); };
    $("upNext").onclick = () => { state.page += 1; renderFlagged(); };
    renderFlagged();
  }

  function renderFlagged() {
    const b = state.current;
    const rows = b.flagged_rows;
    const pages = Math.max(1, Math.ceil(rows.length / state.pageSize));
    const slice = rows.slice(state.page * state.pageSize, (state.page + 1) * state.pageSize);
    table($("flaggedUploadTable"), ["Row", "Type", "Amount", "Sender", "Sender balance", "Receiver", "Receiver balance", "Hour", "Label", "Model score"],
      slice.map((r) => ({
        cells: [r.row, r.type, num(r.amount, 2), esc(r.name_orig), `${num(r.old_balance_orig)} → ${num(r.new_balance_orig)}`,
          esc(r.name_dest), `${num(r.old_balance_dest)} → ${num(r.new_balance_dest)}`, `${String(r.hour_of_day).padStart(2, "0")}:00`,
          r.is_fraud === null ? "–" : r.is_fraud ? '<span class="badge fraud">fraud</span>' : '<span class="badge ok">legit</span>', risk(r.fraud_probability)],
      })));
    if (!slice.length) $("flaggedUploadTable").insertAdjacentHTML("beforeend", '<tbody><tr><td colspan="10" class="empty">No rows were flagged.</td></tr></tbody>');
    $("uploadPager").hidden = rows.length <= state.pageSize;
    $("upInfo").textContent = `Page ${state.page + 1} of ${pages}`;
    $("upPrev").disabled = state.page <= 0;
    $("upNext").disabled = state.page + 1 >= pages;
  }

  // The download needs the X-Owner header, so it is fetched and saved as a blob instead of being a plain link.
  async function download(b) {
    try {
      const res = await fetch(`api/uploads/${b.id}/scored.csv`, { headers: { "X-Owner": ownerId() } });
      if (!res.ok) throw new Error(describeError((await res.json().catch(() => ({}))).detail, res.statusText));
      const url = URL.createObjectURL(await res.blob());
      const a = document.createElement("a");
      a.href = url;
      a.download = `${b.filename.replace(/\.csv$/i, "") || "upload"}_scored.csv`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 5000);
    } catch (err) {
      toast(err.message, "error");
    }
  }

  function confirmDelete(b) {
    const button = $("delUpload");
    button.outerHTML = `<span>Delete this upload?</span> <button type="button" id="delYes" class="danger">Yes, delete</button> <button type="button" id="delNo">Cancel</button>`;
    $("delNo").onclick = () => render(b);
    $("delYes").onclick = async () => {
      try {
        await apiAs(`/api/uploads/${b.id}`, { method: "DELETE" });
        state.current = null;
        $("uploadResult").innerHTML = "";
        toast("Upload deleted");
        loadList();
      } catch (err) {
        toast(err.message, "error");
      }
    };
  }

  // ---- history ----
  async function loadList() {
    try {
      const { items, limit } = await apiAs("/api/uploads");
      table($("uploadTable"), ["File", "Uploaded", "Rows", "Flagged", "Precision / recall", ""],
        items.map((b) => ({
          cells: [esc(b.filename), time(b.created_at), num(b.rows_valid), num(b.flagged),
            b.metrics ? `${pct(b.metrics.precision)} / ${pct(b.metrics.recall)}` : "–",
            `<button type="button" data-view-id="${b.id}">View</button> <button type="button" data-dl-id="${b.id}" ${b.download_available ? "" : "disabled"}>Download</button> <button type="button" data-del-id="${b.id}" class="danger">Delete</button>`],
        })));
      if (!items.length) $("uploadTable").insertAdjacentHTML("beforeend", `<tbody><tr><td colspan="6" class="empty">No uploads yet. You can keep up to ${limit}.</td></tr></tbody>`);
      state.items = items;
    } catch (err) {
      $("uploadBanner").innerHTML = `<div class="search-status error">${esc(err.message)}</div>`;
    }
  }

  $("uploadTable").addEventListener("click", async (e) => {
    const view = e.target.dataset.viewId, dl = e.target.dataset.dlId, del = e.target.dataset.delId;
    try {
      if (view) { render(await apiAs(`/api/uploads/${view}`)); $("uploadResult").scrollIntoView({ behavior: "smooth" }); }
      if (dl) download(state.items.find((b) => b.id === dl));
      if (del) {
        await apiAs(`/api/uploads/${del}`, { method: "DELETE" });
        if (state.current && state.current.id === del) { state.current = null; $("uploadResult").innerHTML = ""; }
        toast("Upload deleted");
        loadList();
      }
    } catch (err) {
      toast(err.message, "error");
    }
  });

  // ---- file selection ----
  const zone = $("dropzone");
  ["dragenter", "dragover"].forEach((ev) => zone.addEventListener(ev, (e) => { e.preventDefault(); zone.classList.add("drag"); }));
  ["dragleave", "drop"].forEach((ev) => zone.addEventListener(ev, (e) => { e.preventDefault(); zone.classList.remove("drag"); }));
  zone.addEventListener("drop", (e) => { if (e.dataTransfer.files.length) send(e.dataTransfer.files[0]); });
  $("fileInput").addEventListener("change", (e) => { if (e.target.files.length) send(e.target.files[0]); e.target.value = ""; });
  $("useSample").addEventListener("click", async () => {
    try {
      const res = await fetch("api/uploads/sample.csv");
      if (!res.ok) throw new Error("The sample file is not available");
      send(new File([await res.blob()], "sample_transactions.csv", { type: "text/csv" }));
    } catch (err) {
      status(esc(err.message), true);
    }
  });

  views.upload = { show: loadList };
})();
