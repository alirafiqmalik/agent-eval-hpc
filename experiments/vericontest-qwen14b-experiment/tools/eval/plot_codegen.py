"""Plot a completed CodeGen pilot from saved evaluation artifacts."""

import argparse
import csv
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir", type=Path)
    args = parser.parse_args()

    results = args.run_dir / "results"
    summary = json.loads((results / "summary.json").read_text())
    runtime = json.loads((results / "runtime.json").read_text())

    if not summary.get("scored_complete"):
        raise ValueError("Run is incomplete or contains unscored attempts.")

    with (results / "attempts.csv").open(newline="") as stream:
        attempts = list(csv.DictReader(stream))

    settings = ["nl2code", "spec2code", "nl_spec2code"]
    labels = ["Natural language", "Specification", "Both"]
    rows = []

    for setting in settings:
        selected = [r for r in attempts if r["task"] == setting]
        problems = [r["problem"] for r in selected]

        if len(selected) != 3 or set(problems) != {"lc121", "lc217", "lc242"}:
            raise ValueError(f"Expected exactly three distinct problems: {setting}")
        if any(r["scored"].lower() != "true" for r in selected):
            raise ValueError(f"Unscored attempts in {setting}")

        passed = sum(float(r["reward"]) == 1 for r in selected)
        syntax = sum(r["category"] == "Syntax Error" for r in selected)

        rows.append(
            {
                "run_id": args.run_dir.name,
                "model": runtime["model"],
                "task": setting,
                "attempts": len(selected),
                "passed": passed,
                "syntax_errors": syntax,
                "pass_rate_pct": 100 * passed / len(selected),
                "syntax_error_rate_pct": 100 * syntax / len(selected),
            }
        )

    with (results / "codegen_task_summary.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    fig, axes = plt.subplots(1, 2, figsize=(10, 4), sharey=True)

    for ax, metric, title, color in [
        (axes[0], "pass_rate_pct", "Pass rate", "#2563eb"),
        (axes[1], "syntax_error_rate_pct", "Syntax-error rate", "#d97706"),
    ]:
        values = [r[metric] for r in rows]
        bars = ax.bar(labels, values, color=color, width=0.6)
        ax.bar_label(bars, labels=[f"{v:.1f}%" for v in values], padding=4)
        ax.set_title(title)
        ax.set_ylim(0, 115)
        ax.set_yticks([0, 25, 50, 75, 100])
        ax.grid(axis="y", alpha=0.2)
        ax.set_axisbelow(True)
        ax.spines[["top", "right"]].set_visible(False)

    axes[0].set_ylabel("Percentage of attempts")
    fig.suptitle(
        runtime["model"] + "\nThree-problem CodeGen pilot; one answer per task",
        fontsize=11,
    )
    fig.tight_layout()

    for extension in ("png", "svg"):
        fig.savefig(
            results / f"codegen_pilot.{extension}",
            dpi=200,
            bbox_inches="tight",
        )
    plt.close(fig)

    for row in rows:
        print(f"{row['task']}: {row['passed']}/3 passed; {row['syntax_errors']}/3 syntax errors")
    print("Reports saved to:", results)


if __name__ == "__main__":
    main()
