"""Decision-weighted detection and the review cost of the frozen monitor."""

from pathlib import Path
import json
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "figures"
OUT.mkdir(exist_ok=True)
summary = json.loads((ROOT / "results/test_summary.json").read_text())
winner = json.loads((ROOT / "results/selection.json").read_text())["winner"]
monitors = ["structural", "structural_replay", winner]
labels = ["Structural", "+ Replay", "+ Numeric\n(selected)"]
colors = ["#8a9ba8", "#4c788d", "#147d73"]
plt.rcParams.update(
    {
        "font.family": "DejaVu Sans",
        "font.size": 11,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "axes.titleweight": "bold",
        "axes.labelcolor": "#263746",
        "text.color": "#152f3d",
        "axes.edgecolor": "#9aabb5",
        "savefig.facecolor": "white",
    }
)


def finish(fig, name, title, subtitle, foot):
    fig.suptitle(title, x=0.055, y=0.98, ha="left", fontsize=20, weight="bold")
    fig.text(0.055, 0.91, subtitle, fontsize=11)
    fig.text(
        0.055,
        0.045,
        "SYNTHETIC FAULTS ON MEASURED PUBLIC HISTORY",
        fontsize=10,
        weight="bold",
        color="#9a4b25",
    )
    fig.text(0.055, 0.012, foot, fontsize=9, color="#536670")
    fig.savefig(OUT / f"{name}.png", dpi=170)
    fig.savefig(OUT / f"{name}.pdf")
    plt.close(fig)


all_metrics = {r["monitor"]: r for r in summary["metrics"] if r["stratum"] == "all"}
fig, axes = plt.subplots(1, 3, figsize=(13.5, 6.8))
fig.subplots_adjust(left=0.075, right=0.975, top=0.79, bottom=0.24, wspace=0.36)
for ax, key, title, limit in zip(
    axes,
    ["decision_weighted_recall", "fault_recall", "background_burden"],
    ["Decision change detected", "Fault cases detected", "Unmodified months flagged"],
    [108, 108, 10],
):
    values = [100 * all_metrics[m][key] for m in monitors]
    bars = ax.bar(range(3), values, color=colors, width=0.64)
    ax.set_xticks(range(3), labels)
    ax.set_ylim(0, limit)
    ax.set_ylabel("Percent (%)")
    ax.set_title(title, fontsize=12, pad=15)
    ax.grid(axis="y", alpha=0.2)
    ax.set_axisbelow(True)
    for bar, value, m in zip(bars, values, monitors):
        count = ""
        if key == "fault_recall":
            count = f"\n{all_metrics[m]['flagged_faults']}/180"
        elif key == "background_burden":
            count = f"\n{all_metrics[m]['background_flags']}/30"
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            value + limit * 0.025,
            f"{value:.1f}%" + count,
            ha="center",
            va="bottom",
            fontsize=10,
        )
    if key == "background_burden":
        ax.axhline(5, color="#b04730", ls="--", lw=1.5)
        ax.text(
            0.02,
            5.2,
            "5% validation budget",
            transform=ax.get_yaxis_transform(),
            fontsize=9,
            color="#a03927",
        )
finish(
    fig,
    "detection-review-tradeoff",
    "More corrupted decisions caught; review budget exceeded",
    "Later test: Jan 2024 to Jun 2026 | 30 months | six isolated fault mechanisms per month",
    "CFPB public aggregates; controlled reserve allocation, not actual staffing need. Review burden is not a verified production false-positive rate.",
)

trials = pd.read_csv(ROOT / "results/search.csv")
fig, ax = plt.subplots(figsize=(13.5, 6.8))
fig.subplots_adjust(left=0.09, right=0.96, top=0.79, bottom=0.23)
ax.axvspan(0, 5, color="#dcf0e7", alpha=0.7)
ax.axvline(5, color="#b04730", ls="--", lw=1.5, label="5% review budget")
groups = trials.groupby(["background_burden", "decision_weighted_recall"], sort=True)
for (burden, recall), group in groups:
    x, y = 100 * burden, 100 * recall
    selected = winner in set(group["name"])
    ax.scatter(
        x,
        y,
        s=160 if selected else 85,
        color="#147d73" if selected else "#526e84",
        marker="*" if selected else "o",
        zorder=3,
    )
    text = f"{len(group)} setting{'s' if len(group)>1 else ''}"
    if selected:
        text += " | selected tie"
    offset = (9, -18 if recall > 0.99 else 7)
    if selected:
        offset = (35, -29)
    elif 0 < burden < 0.05:
        offset = (35, -58)
    elif 0.05 < burden < 0.06:
        offset = (12, 13)
    ax.annotate(
        text,
        (x, y),
        xytext=offset,
        textcoords="offset points",
        fontsize=10,
        arrowprops=(
            {"arrowstyle": "-", "color": "#8498a4", "lw": 0.7}
            if burden > 0 and burden < 0.06
            else None
        ),
    )
ax.set(
    xlim=(-1, 53),
    ylim=(35, 112),
    xlabel="Unmodified validation months flagged (%)",
    ylabel="Decision-change-weighted detection (%)",
)
ax.grid(alpha=0.2)
ax.text(1.1, 39, "Feasible\nreview budget", color="#22745b", fontsize=10)
finish(
    fig,
    "validation-budget-frontier",
    "The best detection setting depends on review capacity",
    "Validation: 2021 to 2023 | 14 declared settings | four synthetic mechanisms | 36 unmodified months",
    "Coincident settings are grouped with counts. Selected: 6-month window, z > 8, relative deviation > 0.10; all trials retained.",
)

families = [
    "duplicate_largest",
    "drop_largest",
    "scale_double",
    "replay",
    "scale_half",
    "exchange_labels",
]
family_labels = [
    "Duplicate\nlargest row",
    "Drop\nlargest row",
    "Double counts\n+ header",
    "Replay prior\nmonth",
    "Half counts\n+ header *",
    "Exchange\nlabels *",
]
matrix = []
annotations = []
for m in monitors:
    rows = {
        r["value"]: r
        for r in summary["metrics"]
        if r["monitor"] == m and r["stratum"] == "family"
    }
    matrix.append([100 * rows[f]["decision_weighted_recall"] for f in families])
    annotations.append(
        [
            f"{100*rows[f]['decision_weighted_recall']:.1f}%\n{rows[f]['flagged_faults']}/30 cases"
            for f in families
        ]
    )
fig, ax = plt.subplots(figsize=(13.5, 6.8))
fig.subplots_adjust(left=0.16, right=0.91, top=0.79, bottom=0.25)
im = ax.imshow(matrix, vmin=0, vmax=100, cmap="YlGnBu", aspect="auto")
ax.set_xticks(range(6), family_labels)
ax.set_yticks(range(3), ["Structural", "+ Replay", "+ Numeric (selected)"])
for row in range(3):
    for col in range(6):
        ax.text(
            col,
            row,
            annotations[row][col],
            ha="center",
            va="center",
            fontsize=10,
            color="white" if matrix[row][col] > 65 else "#152f3d",
        )
ax.axvline(3.5, color="white", lw=4)
fig.colorbar(
    im, ax=ax, fraction=0.025, pad=0.03, label="Decision-change-weighted detection (%)"
)
finish(
    fig,
    "fault-family-transfer",
    "Total-preserving faults can still change allocations",
    "Later test by mechanism | colour weights each case by its absolute allocation change",
    "* Two mechanisms excluded from monitor selection. Cases share source months; they are not 180 independent production incidents.",
)
print(
    "Saved three PNG/PDF figures with explicit synthetic labels and review-budget limitation."
)
