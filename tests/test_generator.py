"""The synthetic data generator: reproducible, consistent balances, labels optional, accepted by the upload."""
import csv

import numpy as np

from scripts.generate_dataset import generate, write


def make(tmp_path, labels, rows=30_000, seed=5, name="t.csv"):
    path = tmp_path / name
    write(generate(rows, seed, labels), str(path), labels)
    return path


def read(path):
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def test_same_seed_gives_the_same_file(tmp_path):
    a = make(tmp_path, True, name="a.csv").read_bytes()
    b = make(tmp_path, True, name="b.csv").read_bytes()
    c = make(tmp_path, True, seed=6, name="c.csv").read_bytes()
    assert a == b and a != c


def test_labelled_and_unlabelled_columns(tmp_path):
    labelled = read(make(tmp_path, True, name="l.csv"))
    plain = read(make(tmp_path, False, name="p.csv"))
    assert "isFraud" in labelled[0] and "isFraud" not in plain[0]
    assert {"step", "type", "amount", "nameOrig", "oldbalanceOrg", "newbalanceOrig", "nameDest", "oldbalanceDest", "newbalanceDest"} <= set(plain[0])


def test_rows_are_valid_and_balances_consistent(tmp_path):
    rows = read(make(tmp_path, True))
    assert abs(len(rows) - 30_000) < 100
    steps = [int(r["step"]) for r in rows]
    assert steps == sorted(steps) and 1 <= steps[0] and steps[-1] <= 31 * 24
    for r in rows:
        amount, old, new = float(r["amount"]), float(r["oldbalanceOrg"]), float(r["newbalanceOrig"])
        assert amount > 0 and old >= 0 and new >= 0 and float(r["oldbalanceDest"]) >= 0 and float(r["newbalanceDest"]) >= 0
        if r["type"] == "CASH_IN":
            assert new == round(old + amount, 2) or abs(new - old - amount) < 0.02
        elif old > 0:
            assert abs(old - amount - new) < 0.02 or new == 0  # emptied or reduced by the amount
        assert r["type"] in {"CASH_OUT", "PAYMENT", "CASH_IN", "TRANSFER", "DEBIT"}


def test_fraud_only_in_transfers_and_cash_outs_at_the_expected_rate(tmp_path):
    rows = read(make(tmp_path, True, rows=100_000))
    fraud = [r for r in rows if r["isFraud"] == "1"]
    assert 0.001 < len(fraud) / len(rows) < 0.004
    assert {r["type"] for r in fraud} <= {"TRANSFER", "CASH_OUT"}
    assert np.mean([r["newbalanceOrig"] == "0.0" or float(r["newbalanceOrig"]) == 0 for r in fraud]) > 0.8
