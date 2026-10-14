"""Draws the architecture diagram used in the report and the presentation (docs/report_assets/fig_architecture.png).

It shows the whole platform: two data sources (bulk historical data and uploaded CSV files), their two ingestion
paths, the shared stores, the REST API and the web interface.
"""
import os

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs", "report_assets", "fig_architecture.png")
BLUE, GREY, LIGHT, WARM = "#1f4e79", "#59636e", "#eaf1f8", "#f7ece9"
NL = chr(10)


def box(ax, x, y, w, h, text, fill=LIGHT, size=9, edge=BLUE, bold_first=True):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.02,rounding_size=0.08", fc=fill, ec=edge, lw=1.3))
    lines = text.split(NL)
    n = len(lines)
    for i, line in enumerate(lines):
        yy = y + h / 2 + (n / 2 - i - 0.5) * (size * 0.026 + 0.03)
        ax.text(x + w / 2, yy, line, ha="center", va="center", fontsize=size if i else size + 0.6, color="#1f2328",
                fontweight="bold" if (i == 0 and bold_first) else "normal")


def arrow(ax, x1, y1, x2, y2, label=None):
    ax.annotate("", xy=(x2, y2), xytext=(x1, y1), arrowprops=dict(arrowstyle="-|>", color=GREY, lw=1.3))
    if label:
        ax.text((x1 + x2) / 2 + 0.08, (y1 + y2) / 2, label, fontsize=7.5, color=GREY, ha="left", va="center")


def main():
    fig, ax = plt.subplots(figsize=(10, 7.4))
    ax.set_xlim(0, 10)
    ax.set_ylim(0, 7.4)
    ax.axis("off")

    # data sources
    box(ax, 0.1, 6.45, 4.5, 0.8, "Bulk transaction data" + NL + "historical records, millions of rows", size=9)
    box(ax, 5.4, 6.45, 4.5, 0.8, "Uploaded CSV files" + NL + "any size, with or without fraud labels", size=9, fill=WARM, edge="#a5483a")

    # ingestion paths
    box(ax, 0.1, 5.1, 4.5, 1.05, "Batch pipeline (Apache Spark)" + NL + "ingest and clean, Parquet data lake," + NL + "SQL analytics, model training", size=8.8)
    box(ax, 5.4, 5.1, 4.5, 1.05, "Streaming upload job" + NL + "validate, score in blocks," + NL + "build the file's own index and analysis", size=8.8, fill=WARM, edge="#a5483a")
    arrow(ax, 2.35, 6.45, 2.35, 6.15)
    arrow(ax, 7.65, 6.45, 7.65, 6.15)

    # shared stores
    box(ax, 0.1, 3.55, 2.9, 0.95, "Trained fraud model" + NL + "60 decision trees," + NL + "scored without Spark", size=8.6)
    box(ax, 3.55, 3.55, 2.9, 0.95, "Search indexes" + NL + "one per dataset," + NL + "account ids indexed", size=8.6)
    box(ax, 7.0, 3.55, 2.9, 0.95, "Results and operational store" + NL + "analytics, cases," + NL + "upload records (MongoDB)", size=8.6)
    # both ingestion paths feed the three stores through one bus
    ax.plot([2.35, 2.35], [5.1, 4.88], color=GREY, lw=1.3)
    ax.plot([7.65, 7.65], [5.1, 4.88], color=GREY, lw=1.3)
    ax.plot([1.55, 8.45], [4.88, 4.88], color=GREY, lw=1.3)
    for x in (1.55, 5.0, 8.45):
        arrow(ax, x, 4.88, x, 4.5)

    # API and interface
    ax.plot([0.05, 9.95], [3.25, 3.25], color="#d1d9e0", lw=1, ls="--")
    box(ax, 1.4, 1.5, 7.2, 1.25, "REST API server (FastAPI)" + NL + "search and suggestions, scoring, cases (create, read, update, delete)," + NL
        + "uploads, datasets; input validation and per-browser workspaces", fill="#ffffff", size=8.8)
    for x in (1.55, 5.0, 8.45):
        arrow(ax, x, 3.55, x, 2.75)
    box(ax, 1.4, 0.2, 7.2, 0.9, "Web interface" + NL + "Overview  |  Explorer  |  Cases  |  Upload,  with a dataset picker", fill="#ffffff", size=8.8)
    arrow(ax, 4.6, 1.5, 4.6, 1.1)
    arrow(ax, 5.4, 1.1, 5.4, 1.5, "")
    ax.text(5.55, 1.3, "HTTP / JSON", fontsize=7.5, color=GREY, ha="left", va="center")
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    fig.savefig(OUT, dpi=200, bbox_inches="tight", facecolor="white")


if __name__ == "__main__":
    main()
