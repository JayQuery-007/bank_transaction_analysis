"""Generates a synthetic bank transaction file for demos and load tests.

Why synthetic: real bank transactions are confidential and fraud labels are rarely shared. A generator
gives a file of any size, with known ground truth, that is safe to share and to upload.

How it works (every step is vectorised numpy, seeded, so the same seed gives the same file):
  1. Genuine traffic. Each row draws a transaction type from a fixed mix (CASH_OUT 35%, PAYMENT 34%,
     CASH_IN 22%, TRANSFER 8.4%, DEBIT 0.6%), an hour of day from a daily activity curve (quiet at night),
     and an amount from a log-normal distribution per type (payments are small, transfers are large).
     Accounts come from fixed pools: customers (C...) and merchants (M...). A few receivers are far
     more popular than others. Balances are kept consistent: balance after = balance before -/+ amount.
     Around 0.4% of genuine transfers and cash-outs empty the sender's balance on purpose, so the data has
     look-alikes that are not fraud.
  2. Fraud events (about 0.2% of rows). Account takeover: a victim's whole balance is transferred to a
     mule account and the mule cashes it out within the hour. 20% are a direct cash-out, 15% take only
     part of the balance, and mule accounts are reused, so repeat receivers exist.
  3. Rows are ordered by time step. With --labels the file has an isFraud column, without it
     those columns are left out, like a real feed that has no verdicts yet.

The columns match the Kaggle PaySim layout, so the file can be uploaded as it is. The generator does not
copy any PaySim row: only the shape of the data (type mix, orders of magnitude) is borrowed.

Usage:
  python -m scripts.generate_dataset --rows 8000000 --labels --out data/samples/transactions_labelled_8M.csv
  python -m scripts.generate_dataset --rows 7000000 --out data/samples/transactions_unlabelled_7M.csv --seed 11
"""
import argparse
import os
import time

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.csv as pacsv

TYPES = np.array(["CASH_OUT", "PAYMENT", "CASH_IN", "TRANSFER", "DEBIT"])
MIX = np.array([0.35, 0.34, 0.22, 0.084, 0.006])
# relative activity per hour of day: quiet from midnight to 7, busy 9-21
HOURLY = np.array([0.4, 0.3, 0.3, 0.25, 0.25, 0.3, 0.5, 0.8, 1.2, 1.7, 1.9, 1.9, 1.8, 1.7, 1.7, 1.8, 1.9, 2.0, 2.0, 1.8, 1.5, 1.2, 0.9, 0.6])
MEDIAN = {"CASH_OUT": 80_000, "PAYMENT": 9_000, "CASH_IN": 90_000, "TRANSFER": 250_000, "DEBIT": 3_500}
SIGMA = {"CASH_OUT": 1.3, "PAYMENT": 1.0, "CASH_IN": 1.3, "TRANSFER": 1.4, "DEBIT": 1.0}
CAP = 10_000_000
FRAUD_RATE = 0.002
DAYS = 31


def names(prefix, ids):
    return pc.binary_join_element_wise(pa.scalar(prefix), pc.cast(pa.array(ids), pa.string()), "")


def generate(rows, seed, labels):
    rng = np.random.default_rng(seed)
    n_fraud_events = int(rows * FRAUD_RATE / 1.8)
    n_genuine = rows - int(n_fraud_events * 1.8)

    # ---- 1. genuine traffic
    kind = rng.choice(len(TYPES), size=n_genuine, p=MIX)
    hour = rng.choice(24, size=n_genuine, p=HOURLY / HOURLY.sum())
    day = rng.integers(0, DAYS, size=n_genuine)
    step = day * 24 + hour + 1

    amount = np.empty(n_genuine)
    for k, t in enumerate(TYPES):
        m = kind == k
        amount[m] = np.exp(np.log(MEDIAN[t]) + SIGMA[t] * rng.standard_normal(m.sum()))
    amount = np.round(np.clip(amount, 1.0, CAP), 2)

    customers = max(int(rows * 0.9), 1000)
    merchants = 400_000
    popular = max(int(rows * 0.3), 1000)
    orig_id = rng.integers(10_000_000, 10_000_000 + customers, size=n_genuine)
    payment = kind == 1
    dest_c = 400_000_000 + (rng.random(n_genuine) ** 2.2 * popular).astype(np.int64)  # a few receivers are very popular
    dest_m = 900_000_000 + rng.integers(0, merchants, size=n_genuine)
    dest_is_m = payment

    # sender balances
    out_flow = (kind == 0) | (kind == 1) | (kind == 3) | (kind == 4)  # money leaves the sender
    extra = np.exp(rng.normal(0.4, 1.3, n_genuine))
    old_orig = np.round(amount * (1 + extra), 2)
    empties = out_flow & np.isin(kind, [0, 3]) & (rng.random(n_genuine) < 0.004)
    old_orig = np.where(empties, amount, old_orig)
    no_balance = rng.random(n_genuine) < 0.12  # accounts with no recorded balance
    old_orig = np.where(no_balance & ~empties, 0.0, old_orig)
    new_orig = np.where(out_flow, np.maximum(old_orig - amount, 0.0), old_orig + amount)
    new_orig = np.where(no_balance & ~empties & out_flow, 0.0, new_orig)
    old_orig = np.round(old_orig, 2)
    new_orig = np.round(new_orig, 2)

    # receiver balances: merchants hold nothing, customers receiving cash-outs and transfers do
    recv_old = np.round(np.where(rng.random(n_genuine) < 0.4, 0.0, np.exp(rng.normal(11, 1.8, n_genuine))), 2)
    recv_new = np.where((kind == 0) | (kind == 3), recv_old + amount, np.maximum(recv_old - amount, 0.0))
    recv_old = np.where(dest_is_m | (kind == 2), 0.0, recv_old)
    recv_new = np.where(dest_is_m | (kind == 2), 0.0, np.round(recv_new, 2))

    g = {
        "step": step, "kind": kind, "amount": amount,
        "orig": orig_id, "old_orig": old_orig, "new_orig": new_orig,
        "dest": np.where(dest_is_m, dest_m, dest_c), "dest_is_m": dest_is_m,
        "old_dest": recv_old, "new_dest": recv_new, "fraud": np.zeros(n_genuine, dtype=np.int8),
    }

    # ---- 2. fraud events
    e = n_fraud_events
    f_step = rng.integers(0, DAYS * 24 - 1, size=e) + 1
    balance = np.round(np.clip(np.exp(rng.normal(12.0, 1.4, e)), 500, CAP), 2)
    partial = rng.random(e) < 0.15
    taken = np.round(np.where(partial, balance * rng.uniform(0.6, 0.95, e), balance), 2)
    victim = rng.integers(10_000_000, 10_000_000 + customers, size=e)
    mule_pool = max(e // 3, 10)
    mule = 700_000_000 + rng.integers(0, mule_pool, size=e)
    direct = rng.random(e) < 0.2  # account takeover with an immediate cash-out, no transfer
    credited = rng.random(e) < 0.5

    t_rows = ~direct  # the transfer leg
    transfer = {
        "step": f_step[t_rows], "kind": np.full(t_rows.sum(), 3), "amount": taken[t_rows],
        "orig": victim[t_rows], "old_orig": balance[t_rows], "new_orig": np.round(balance[t_rows] - taken[t_rows], 2),
        "dest": mule[t_rows], "dest_is_m": np.zeros(t_rows.sum(), dtype=bool),
        "old_dest": np.zeros(t_rows.sum()), "new_dest": np.where(credited[t_rows], taken[t_rows], 0.0),
        "fraud": np.ones(t_rows.sum(), dtype=np.int8),
    }
    cash_out = {  # the mule cashes out (or, for direct takeovers, the victim's own balance goes)
        "step": f_step + (rng.random(e) < 0.5), "kind": np.zeros(e, dtype=int), "amount": taken,
        "orig": np.where(direct, victim, mule), "old_orig": np.where(direct, balance, taken),
        "new_orig": np.where(direct, np.round(balance - taken, 2), 0.0),
        "dest": 400_000_000 + rng.integers(0, popular, size=e), "dest_is_m": np.zeros(e, dtype=bool),
        "old_dest": np.zeros(e), "new_dest": np.where(credited, taken, 0.0), "fraud": np.ones(e, dtype=np.int8),
    }

    cols = {k: np.concatenate([g[k], transfer[k], cash_out[k]]) for k in g}
    order = np.argsort(cols["step"], kind="stable")
    cols = {k: v[order] for k, v in cols.items()}
    return cols


def write(cols, path, labels):
    n = len(cols["step"])
    type_names = pa.array(TYPES[cols["kind"]])
    dest_prefix = np.where(cols["dest_is_m"], "M", "C")
    dest = pc.binary_join_element_wise(pa.array(dest_prefix), pc.cast(pa.array(cols["dest"]), pa.string()), "")
    table = {
        "step": pa.array(cols["step"]),
        "type": type_names,
        "amount": pa.array(cols["amount"]),
        "nameOrig": names("C", cols["orig"]),
        "oldbalanceOrg": pa.array(cols["old_orig"]),
        "newbalanceOrig": pa.array(cols["new_orig"]),
        "nameDest": dest,
        "oldbalanceDest": pa.array(cols["old_dest"]),
        "newbalanceDest": pa.array(cols["new_dest"]),
    }
    if labels:
        table["isFraud"] = pa.array(cols["fraud"])
    full = pa.table(table)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    options = pacsv.WriteOptions(include_header=False, quoting_style="none")  # plain CSV, no quotes around values
    with open(path, "wb") as f:
        f.write((",".join(full.schema.names) + chr(10)).encode())
        with pacsv.CSVWriter(f, full.schema, write_options=options) as w:
            for start in range(0, n, 1_000_000):
                w.write_table(full.slice(start, 1_000_000))
    return full


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rows", type=int, required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--labels", action="store_true", help="include the isFraud column")
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args()
    start = time.time()
    cols = generate(a.rows, a.seed, a.labels)
    write(cols, a.out, a.labels)
    n = len(cols["step"])
    fraud = int(cols["fraud"].sum())
    size = os.path.getsize(a.out) / 1e6
    print(f"{a.out}: {n:,} rows, {size:,.0f} MB, {'with' if a.labels else 'without'} labels, "
          f"{fraud:,} fraud rows ({100 * fraud / n:.2f}%, hidden in the file if unlabelled), {time.time() - start:.0f} s")


if __name__ == "__main__":
    main()
