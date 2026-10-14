"""Draws the architecture diagram used in the project report (docs/report_assets/fig_architecture.png)."""
import os

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs", "report_assets", "fig_architecture.png")
BLUE, GREY, LIGHT = "#1f4e79", "#59636e", "#eaf1f8"


def box(ax, x, y, w, h, text, fill=LIGHT, size=9):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.02,rounding_size=0.08", fc=fill, ec=BLUE, lw=1.3))
    ax.text(x + w / 2, y + h / 2, text, ha="center", va="center", fontsize=size, color="#1f2328", linespacing=1.35)


def arrow(ax, x1, y1, x2, y2, label=None):
    ax.annotate("", xy=(x2, y2), xytext=(x1, y1), arrowprops=dict(arrowstyle="-|>", color=GREY, lw=1.3))
    if label:
        ax.text((x1 + x2) / 2, (y1 + y2) / 2 + 0.1, label, fontsize=7.5, color=GREY, ha="center")


def main():
    nl = chr(10)
    fig, ax = plt.subplots(figsize=(10, 6.6))
    ax.set_xlim(0, 10)
    ax.set_ylim(0, 6.6)
    ax.axis("off")

    ax.text(0.1, 6.3, "Offline batch pipeline (run once)", fontsize=10.5, fontweight="bold", color=BLUE)
    box(ax, 0.1, 5.4, 2.2, 0.65, "Transaction CSV" + nl + "6.36 million rows")
    box(ax, 3.2, 5.4, 2.4, 0.65, "Ingest (Spark)" + nl + "clean and derive columns")
    box(ax, 6.5, 5.4, 3.3, 0.65, "Parquet data lake" + nl + "partitioned by transaction type")
    arrow(ax, 2.3, 5.72, 3.2, 5.72)
    arrow(ax, 5.6, 5.72, 6.5, 5.72)

    # a bus from the lake to the three jobs
    ax.plot([8.15, 8.15], [5.4, 5.15], color=GREY, lw=1.3)
    ax.plot([1.6, 8.35], [5.15, 5.15], color=GREY, lw=1.3)
    for x in (1.6, 5.0, 8.35):
        arrow(ax, x, 5.15, x, 4.9)
    box(ax, 0.1, 4.35, 3.0, 0.55, "Spark SQL analytics" + nl + "(7 queries)", size=8.8)
    box(ax, 3.5, 4.35, 3.0, 0.55, "Spark MLlib" + nl + "model training", size=8.8)
    box(ax, 6.9, 4.35, 2.9, 0.55, "Search index builder", size=8.8)

    for x in (1.6, 5.0, 8.35):
        arrow(ax, x, 4.35, x, 4.05)
    box(ax, 0.1, 3.4, 3.0, 0.65, "Results store (MongoDB)" + nl + "analysis results, JSON copies", size=8.6)
    box(ax, 3.5, 3.4, 3.0, 0.65, "Trained model" + nl + "(60 decision trees)", size=8.6)
    box(ax, 6.9, 3.4, 2.9, 0.65, "Search index" + nl + "(account ids indexed)", size=8.6)

    ax.plot([0.1, 9.9], [3.05, 3.05], color="#d1d9e0", lw=1, ls="--")
    ax.text(0.1, 2.8, "Online application (always on)", fontsize=10.5, fontweight="bold", color=BLUE)

    box(ax, 3.2, 0.9, 3.6, 1.5, "REST API server" + nl + "search, suggestions, predict," + nl +
        "cases (create, read, update, delete)," + nl + "uploads, datasets", fill="#ffffff", size=8.8)
    box(ax, 0.1, 1.15, 2.2, 1.0, "Web interface" + nl + "Overview, Explorer," + nl + "Cases, Upload", fill="#ffffff")
    box(ax, 7.6, 1.55, 2.3, 0.85, "Streaming upload scoring" + nl + "and per-upload index", fill="#ffffff", size=8.4)
    box(ax, 7.6, 0.55, 2.3, 0.8, "Cases and upload" + nl + "records (MongoDB)", fill="#ffffff", size=8.4)
    arrow(ax, 2.3, 1.85, 3.2, 1.85, "HTTP / JSON")
    arrow(ax, 3.2, 1.45, 2.3, 1.45)
    arrow(ax, 6.8, 1.95, 7.6, 1.95)
    arrow(ax, 6.8, 1.25, 7.6, 0.95)
    arrow(ax, 1.6, 3.4, 3.6, 2.4)
    arrow(ax, 5.0, 3.4, 5.0, 2.4)
    arrow(ax, 8.35, 3.4, 6.5, 2.4)
    ax.text(5.0, 0.2, "The server reads the search index, the model and the stored results, and reads and writes cases and uploads.",
            fontsize=8, color=GREY, ha="center")
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    fig.savefig(OUT, dpi=200, bbox_inches="tight", facecolor="white")


if __name__ == "__main__":
    main()
