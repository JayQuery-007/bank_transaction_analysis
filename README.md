# Bank Transaction Analytics & Anomaly Detection

End-to-end big data pipeline that generates synthetic bank transactions, engineers
behavioural features with **Spark SQL**, flags anomalous/fraudulent transactions with
an unsupervised **Isolation Forest** model, stores results in **MongoDB**, and serves
them through a **FastAPI** backend to a live analytics dashboard.

**Stack (course-required combo):** `Python + Spark + MongoDB + Machine Learning`

## Architecture

```
data/generate_data.py        synthetic transaction generator (150k txns, injected fraud patterns)
        |
        v
spark_jobs/etl_features.py   Spark SQL: per-account behavioural features, z-scores,
        |                    velocity, night-time ratio, category/country aggregates
        v
ml/train_and_detect.py       Isolation Forest anomaly detection (scikit-learn),
        |                    scores every transaction 0-100, saves model.joblib
        v
scripts/load_mongo.py        loads scored transactions + aggregate stats into MongoDB
        |
        v
backend/app/ (FastAPI)       REST API: /api/stats, /api/transactions, /api/anomalies,
        |                    /api/detect (live-scores a new transaction with the model)
        v
frontend/ (HTML/JS/Chart.js) dashboard: KPIs, time series, category/country charts,
                             amount-vs-score scatter plot, live "test a transaction" form
```

If MongoDB or Java/Spark aren't available on a machine, both the ETL step and the API
transparently fall back to pandas / reading the pipeline's CSV+JSON output directly
(see `spark_jobs/etl_features.py::run_pandas_fallback` and
`backend/app/database.py::FileStore`), so the app still runs end to end. On this dev
machine both Spark (via a local JDK) and a real MongoDB instance are wired up and used.

## Quick start (any machine)

```bash
git clone https://github.com/JayQuery-007/bank_transaction_analysis.git
cd bank_transaction_analysis
python -m venv venv
venv\Scripts\activate          # macOS/Linux: source venv/bin/activate
pip install -r requirements.txt
python run.py
```

Then open **http://localhost:8000**. On the first run `run.py` builds everything
(data generation -> Spark ETL -> model training -> MongoDB load), then starts the app.
Later runs start instantly.

Optional, auto-detected if present:
- **Java 11/17** (`JAVA_HOME` set) -> the ETL runs on real Spark; otherwise it uses the pandas fallback with identical output.
- **MongoDB** on `localhost:27017` (or set `MONGO_URI`) -> data is stored/served from MongoDB; otherwise the API reads the local CSV/JSON output.

The dashboard loads Chart.js from a CDN, so the browser needs internet access.

To rebuild the data/model at any time: `python scripts/run_pipeline.py`.

## API endpoints

| Endpoint                              | Description                                    |
|----------------------------------------|-------------------------------------------------|
| `GET /api/stats/summary`               | totals, fraud rate, time series, category/country/channel breakdowns |
| `GET /api/stats/model-metrics`         | precision/recall/F1 of the anomaly model against injected fraud labels |
| `GET /api/transactions`                | paginated transactions (`skip`, `limit`, `anomaly_only`, `account_id`) |
| `GET /api/anomalies/top`               | top-N highest anomaly-score transactions        |
| `POST /api/detect`                     | score an arbitrary new transaction live with the trained model |

## Why Isolation Forest (unsupervised)

Real fraud detection systems rarely have complete, reliable fraud labels at training
time. Isolation Forest models "normal" behaviour per account (average spend, spending
volatility, night-time activity, velocity between transactions, number of distinct
countries/categories touched) and flags transactions that are structurally easy to
isolate from that pattern — no labels required at train time. The injected fraud
labels in the synthetic data are used only afterwards, to *evaluate* the model
(precision/recall/F1 reported in `data/processed/model_metrics.json`), the same way
you'd validate against a small labeled sample in production.

## Project structure

```
data/               synthetic data generator + generated CSVs (gitignored, regenerate via pipeline)
spark_jobs/          Spark SQL ETL + feature engineering
ml/                  Isolation Forest training + scoring
scripts/             pipeline orchestration + Mongo loader
backend/app/         FastAPI app (routes + Mongo/file storage adapter)
frontend/            static dashboard (HTML/CSS/Chart.js, no build step)
```
