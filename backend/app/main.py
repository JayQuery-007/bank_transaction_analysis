import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .routes import anomalies, detect, stats, transactions

app = FastAPI(
    title="Bank Transaction Anomaly Detection API",
    description="Spark + MongoDB + ML pipeline for bank transaction fraud/anomaly analytics.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(stats.router)
app.include_router(transactions.router)
app.include_router(anomalies.router)
app.include_router(detect.router)


@app.get("/api/health")
def health():
    return {"status": "ok"}


FRONTEND_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "frontend")
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
