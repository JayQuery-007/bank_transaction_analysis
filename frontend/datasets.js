// Which dataset the Overview, Explorer and Cases tabs work on: the main ledger, or one of your uploaded files.
// Server mode only. Other scripts listen for the "datasetchange" event on window.
// Depends on helpers from app.js: $, api, apiAs, num, esc.

const datasets = {
  items: [{ id: "paysim", name: "Main ledger", kind: "builtin", rows: 6362604, labelled: true }],
  current: { id: "paysim", name: "Main ledger", kind: "builtin", rows: 6362604, labelled: true },

  isUpload() {
    return this.current.id !== "paysim";
  },

  // API call for the current dataset: an upload is private, so it needs the workspace header
  call(path, options) {
    return this.isUpload() ? apiAs(path, options) : api(path, options);
  },

  // adds ?dataset=<id> to a query string when an upload is selected
  withDataset(params) {
    if (this.isUpload()) params.set("dataset", this.current.id);
    return params;
  },
};

(() => {
  if (window.STATIC_SITE) return;
  const select = () => $("datasetSelect");
  const stored = () => { try { return localStorage.getItem("bda_dataset") || "paysim"; } catch (e) { return "paysim"; } };
  const remember = (id) => { try { localStorage.setItem("bda_dataset", id); } catch (e) { /* private mode */ } };
  const label = (d) => (d.kind === "builtin" ? `${d.name}, ${num(d.rows)} rows` : `${d.name} (${num(d.rows)} rows)`);

  function announce() {
    window.dispatchEvent(new CustomEvent("datasetchange", { detail: datasets.current }));
  }

  window.selectDataset = function (id) {
    const found = datasets.items.find((d) => d.id === id) || datasets.items[0];
    const changed = found.id !== datasets.current.id;
    datasets.current = found;
    remember(found.id);
    select().value = found.id;
    if (changed) announce();
  };

  // Reloads the list (after an upload finishes or is deleted, or when the page opens).
  window.refreshDatasets = async function (preferId) {
    let items;
    try {
      items = (await apiAs("/api/datasets")).items;
    } catch (err) {
      return; // the database is down; PaySim alone is still usable
    }
    datasets.items = items;
    select().innerHTML = items.map((d) => `<option value="${esc(d.id)}">${esc(label(d))}</option>`).join("");
    const wanted = preferId || (datasets.current.id !== "paysim" ? datasets.current.id : stored());
    const found = items.find((d) => d.id === wanted) || items[0];
    const changed = found.id !== datasets.current.id || found.rows !== datasets.current.rows;
    const switched = found.id !== datasets.current.id;
    datasets.current = found;
    select().value = found.id;
    remember(found.id);
    if (switched) announce();
    else if (changed) datasets.current = found;
  };

  window.addEventListener("DOMContentLoaded", () => {
    select().addEventListener("change", () => selectDataset(select().value));
    refreshDatasets();
  });
})();
