// Scores a transaction with the trained Spark Random Forest, exported to data/forest.json
// by deploy/build_static.py. Mirrors Spark's RandomForestClassificationModel: each tree
// routes the transaction left when feature <= threshold, the leaf's class distribution is
// that tree's vote, and the fraud probability is the average over all trees.

let forestPromise = null;

function loadForest() {
  if (!forestPromise) forestPromise = fetch("data/forest.json").then((r) => r.json());
  return forestPromise;
}

async function scoreWithForest(tx) {
  const forest = await loadForest();
  const errorOrig = tx.new_balance_orig + tx.amount - tx.old_balance_orig;
  const errorDest = tx.old_balance_dest + tx.amount - tx.new_balance_dest;
  const x = [
    tx.type === "TRANSFER" ? 1 : 0, tx.amount, tx.old_balance_orig, tx.new_balance_orig,
    tx.old_balance_dest, tx.new_balance_dest, errorOrig, errorDest, tx.hour_of_day,
  ];

  let total = 0;
  for (const tree of forest.trees) {
    let i = 0;
    while (tree.f[i] !== -1) i = x[tree.f[i]] <= tree.t[i] ? tree.l[i] : tree.r[i];
    total += tree.p[i];
  }
  const p = total / forest.trees.length;
  return {
    fraud_probability: Math.round(p * 10000) / 10000,
    is_fraud: p > 0.5, // Spark picks the class with the highest probability
    error_balance_orig: Math.round(errorOrig * 100) / 100,
    error_balance_dest: Math.round(errorDest * 100) / 100,
  };
}
