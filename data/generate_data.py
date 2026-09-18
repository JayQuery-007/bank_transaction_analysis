"""
Synthetic bank transaction dataset generator.

Produces a realistic transactions.csv with an injected fraud/anomaly pattern
so the downstream Spark ETL + ML anomaly detection pipeline has real signal
to find. No external download required, fully reproducible (seeded).
"""
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
import random

SEED = 42
random.seed(SEED)
np.random.seed(SEED)

N_ACCOUNTS = 2000
N_TRANSACTIONS = 150_000
FRAUD_RATE = 0.025
START_DATE = datetime(2025, 1, 1)
DAYS = 180

MERCHANT_CATEGORIES = [
    "groceries", "electronics", "restaurants", "travel", "utilities",
    "entertainment", "healthcare", "clothing", "fuel", "online_retail",
    "atm_withdrawal", "wire_transfer", "subscription", "insurance",
]

COUNTRIES = ["US", "US", "US", "US", "US", "CA", "GB", "IN", "DE", "FR", "NG", "RU", "CN", "BR"]
HIGH_RISK_COUNTRIES = {"NG", "RU", "CN"}

CHANNELS = ["POS", "ONLINE", "ATM", "MOBILE", "WIRE"]


def make_accounts(n):
    account_ids = [f"ACC{100000 + i}" for i in range(n)]
    home_country = np.random.choice(["US", "CA", "GB", "IN", "DE"], size=n, p=[0.6, 0.1, 0.1, 0.1, 0.1])
    avg_spend = np.random.gamma(shape=2.0, scale=60, size=n) + 20
    risk_profile = np.random.choice(["low", "medium", "high"], size=n, p=[0.75, 0.20, 0.05])
    return pd.DataFrame({
        "account_id": account_ids,
        "home_country": home_country,
        "avg_spend": avg_spend,
        "risk_profile": risk_profile,
    })


def random_timestamp():
    day_offset = random.randint(0, DAYS - 1)
    # bias toward daytime hours, small tail into late night
    hour = int(np.clip(np.random.normal(14, 5), 0, 23))
    minute = random.randint(0, 59)
    second = random.randint(0, 59)
    return START_DATE + timedelta(days=day_offset, hours=hour, minutes=minute, seconds=second)


def generate():
    accounts = make_accounts(N_ACCOUNTS)
    acc_lookup = accounts.set_index("account_id")

    n_fraud = int(N_TRANSACTIONS * FRAUD_RATE)
    n_normal = N_TRANSACTIONS - n_fraud

    rows = []
    tx_id = 0

    # ---- normal transactions ----
    sampled_accounts = np.random.choice(accounts["account_id"], size=n_normal)
    for acc_id in sampled_accounts:
        tx_id += 1
        acc = acc_lookup.loc[acc_id]
        amount = float(np.clip(np.random.normal(acc["avg_spend"], acc["avg_spend"] * 0.4), 1, None))
        ts = random_timestamp()
        category = random.choice(MERCHANT_CATEGORIES)
        country = acc["home_country"] if random.random() > 0.05 else random.choice(COUNTRIES)
        channel = random.choices(CHANNELS, weights=[0.35, 0.35, 0.15, 0.13, 0.02])[0]
        rows.append((
            f"TXN{tx_id:08d}", acc_id, round(amount, 2), ts, category, country, channel, 0, "normal"
        ))

    # ---- fraud / anomalous transactions ----
    fraud_patterns = ["amount_spike", "odd_hour_burst", "foreign_high_risk", "rapid_succession"]
    sampled_fraud_accounts = np.random.choice(accounts["account_id"], size=n_fraud)
    for acc_id in sampled_fraud_accounts:
        tx_id += 1
        acc = acc_lookup.loc[acc_id]
        pattern = random.choice(fraud_patterns)
        ts = random_timestamp()
        channel = random.choice(CHANNELS)
        category = random.choice(MERCHANT_CATEGORIES)
        country = acc["home_country"]

        if pattern == "amount_spike":
            amount = float(acc["avg_spend"] * np.random.uniform(8, 25))
            category = random.choice(["electronics", "wire_transfer", "online_retail"])
        elif pattern == "odd_hour_burst":
            ts = ts.replace(hour=random.choice([1, 2, 3, 4]))
            amount = float(acc["avg_spend"] * np.random.uniform(2, 6))
            channel = "ATM"
        elif pattern == "foreign_high_risk":
            country = random.choice(list(HIGH_RISK_COUNTRIES))
            amount = float(acc["avg_spend"] * np.random.uniform(3, 10))
            channel = "ONLINE"
        else:  # rapid_succession
            amount = float(acc["avg_spend"] * np.random.uniform(1.5, 4))
            channel = "WIRE"
            category = "wire_transfer"

        rows.append((
            f"TXN{tx_id:08d}", acc_id, round(max(amount, 1), 2), ts, category, country, channel, 1, pattern
        ))

    df = pd.DataFrame(rows, columns=[
        "transaction_id", "account_id", "amount", "timestamp", "category",
        "country", "channel", "is_fraud", "fraud_pattern"
    ])
    df = df.sample(frac=1, random_state=SEED).reset_index(drop=True)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    return df, accounts


if __name__ == "__main__":
    df, accounts = generate()
    df.to_csv("data/transactions.csv", index=False)
    accounts.to_csv("data/accounts.csv", index=False)
    print(f"Generated {len(df):,} transactions "
          f"({df['is_fraud'].sum():,} flagged fraud, {df['is_fraud'].mean()*100:.2f}%)")
    print(f"Generated {len(accounts):,} accounts")
