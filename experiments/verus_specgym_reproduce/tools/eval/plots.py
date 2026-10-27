"""Regenerate empirical paper figures from this campaign's scored Harbor trials.

The public release provides bucket parsing but no plotting sources. The finite
population calculation below follows paper Appendix F.8 exactly.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
import tomllib
from collections import Counter, defaultdict
from pathlib import Path

import yaml

BUCKETS = ("pre_sound", "pre_complete", "post_complete", "post_sound")
EXPERIMENT = Path(__file__).resolve().parents[2]


def sample_pass_probability(passed: int, total: int, budget: int) -> float:
    k = min(budget, total)
    if not 0 <= passed <= total or budget < 0:
        raise ValueError("invalid testcase counts")
    if k == 0:
        return 1.0
    if passed < k:
        return 0.0
    return math.prod((passed - i) / (total - i) for i in range(k))


def mean(values) -> float:
    values = list(values)
    return sum(values) / len(values) if values else float("nan")


def save(fig, destination: Path, name: str) -> None:
    fig.tight_layout()
    for extension in ("pdf", "png"):
        fig.savefig(destination / f"{name}.{extension}", dpi=180, bbox_inches="tight")
    plt.close(fig)


def dataset_figures(destination: Path, manifest: dict, metadata: dict) -> None:
    stats = {}
    fig, axes = plt.subplots(1, 4, figsize=(14, 4))
    for ax, bucket in zip(
        axes, ("pre_sound", "pre_complete", "post_sound", "post_complete"), strict=True
    ):
        values = [x["bucket_totals"][bucket] for x in manifest["tasks"]]
        stats[bucket] = {
            "min": min(values),
            "median": statistics.median(values),
            "mean": mean(values),
            "max": max(values),
        }
        ax.hist(
            values, bins=20, range=(0, max(values)), color="lightskyblue", edgecolor="steelblue"
        )
        for (label, value), color in zip(
            stats[bucket].items(), ("tomato", "royalblue", "seagreen", "darkorchid"), strict=True
        ):
            ax.axvline(
                value, color=color, linestyle="--", linewidth=1, label=f"{label}: {value:.0f}"
            )
        ax.set(title=bucket, xlabel="Testcases", ylabel="Tasks")
        ax.legend(fontsize=7)
    save(fig, destination, "figure-05-testcase-counts")
    ratings = [x["codeforces_rating"] for x in metadata.values()]
    edges = list(range(600, 3001, 300))
    counts = [
        sum(lo <= r < hi for r in ratings) for lo, hi in zip(edges[:-1], edges[1:], strict=True)
    ]
    fig, ax = plt.subplots(figsize=(10, 5))
    bars = ax.bar(
        [x + 150 for x in edges[:-1]],
        [c / len(ratings) for c in counts],
        width=295,
        color="lightskyblue",
        edgecolor="steelblue",
    )
    ax.bar_label(bars, labels=[str(c) for c in counts], fontsize=8)
    rating_stats = {
        "min": min(ratings),
        "median": statistics.median(ratings),
        "mean": mean(ratings),
        "max": max(ratings),
    }
    for (label, value), color in zip(
        rating_stats.items(), ("tomato", "royalblue", "seagreen", "darkorchid"), strict=True
    ):
        ax.axvline(value, color=color, linestyle="--", linewidth=1, label=f"{label}: {value:.0f}")
    ax.set_xticks(
        [x + 150 for x in edges[:-1]], [f"{x}–{x + 299}" for x in edges[:-1]], rotation=25
    )
    ax.set(xlabel="Codeforces rating", ylabel="Fraction of tasks", ylim=(0, 0.36))
    ax.secondary_yaxis(
        "right", functions=(lambda v: v * len(ratings), lambda v: v / len(ratings))
    ).set_ylabel("Tasks")
    ax.legend(fontsize=8)
    save(fig, destination, "figure-10-ratings")
    tags = Counter(t for x in metadata.values() for t in x["codeforces_tags"])
    fig, ax = plt.subplots(figsize=(9, 7))
    pairs = tags.most_common(20)[::-1]
    bars = ax.barh(
        [k for k, _ in pairs],
        [v / len(metadata) for _, v in pairs],
        color="lightskyblue",
        edgecolor="steelblue",
    )
    ax.bar_label(bars, labels=[str(v) for _, v in pairs], fontsize=8)
    ax.set_xlabel("Fraction of tasks (multiple tags per task)")
    ax.margins(x=0.1)
    save(fig, destination, "figure-11-tags")
    (destination / "dataset-statistics.json").write_text(
        json.dumps(
            {
                "tasks": len(metadata),
                "buckets": stats,
                "ratings": rating_stats,
                "rating_bin_edges": edges,
                "rating_bin_counts": counts,
                "tags": dict(tags),
            },
            indent=2,
        )
        + "\n"
    )


def generate(output: Path, *, dataset_only: bool = False) -> None:
    import matplotlib

    matplotlib.use("Agg")
    global plt
    import matplotlib.pyplot as plt

    manifest = json.loads((EXPERIMENT / "tasks/manifest.json").read_text())
    if manifest["missing"] or manifest["imported"] != 581:
        raise ValueError("Paper comparison requires the complete 581-task dataset")
    metadata = {
        p.parent.name: tomllib.loads(p.read_text())["metadata"]
        for p in (EXPERIMENT / "tasks/verus-specgym").glob("*/task.toml")
    }
    destination = output / "results"
    destination.mkdir(parents=True, exist_ok=True)
    dataset_figures(destination, manifest, metadata)
    if dataset_only:
        return
    state = json.loads((output / "campaign.json").read_text())
    expected = {item["task"] for item in manifest["tasks"]}
    records = defaultdict(list)
    for shard in state["shards"]:
        summary = json.loads((Path(shard["output"]) / "results/summary.json").read_text())
        if not summary["complete"] or summary.get("audit_schema") != 2:
            raise ValueError(f"Incomplete shard: {shard['id']}")
        records[shard["model"]].extend(summary["problems"])
    for model in state["models"]:
        names = [r["task"] for r in records[model]]
        if len(names) != 581 or len(names) != len(set(names)) or set(names) != expected:
            raise ValueError(
                f"{model} does not have exactly one scored attempt for each of 581 tasks"
            )
    config = yaml.safe_load((EXPERIMENT / "reproduction.yaml").read_text())
    rows = []
    for model, problems in records.items():
        values = [
            mean(p["passed"] for p in problems),
            mean(
                all(p["buckets"][b]["passed"] == p["buckets"][b]["total"] for b in BUCKETS[1:3])
                for p in problems
            ),
            *[
                mean(p["buckets"][b]["passed"] / p["buckets"][b]["total"] for p in problems)
                for b in BUCKETS
            ],
        ]
        columns = ("pass_at_1", "pass_at_1_completeness", *BUCKETS)
        reference = config["paper_table_1"].get(model)
        row = {"model": model, "n": len(problems), **dict(zip(columns, values, strict=True))}
        row.update(
            {f"paper_{k}": reference[i] if reference else None for i, k in enumerate(columns)}
        )
        row.update(
            {
                f"delta_{k}": values[i] - reference[i] if reference else None
                for i, k in enumerate(columns)
            }
        )
        rows.append(row)
    with (destination / "table-01.csv").open("w") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    (destination / "summary.json").write_text(
        json.dumps(
            {
                "complete": True,
                "models": rows,
                "suite": state.get("suite", "paper"),
                "execution": state.get("execution", config["local_execution"]),
                "comparison_note": (
                    config.get("early_comparison_note") if state.get("suite") == "early" else None
                ),
                "deviations": config["deviations"],
                "excluded": config["excluded_artifacts"],
            },
            indent=2,
        )
        + "\n"
    )
    fig, axes = plt.subplots(2, 3, figsize=(13, 7))
    for ax, column in zip(axes.flat, columns, strict=True):
        ax.bar([r["model"] for r in rows], [r[column] for r in rows], label="Local run")
        reference_rows = [r for r in rows if r[f"paper_{column}"] is not None]
        if reference_rows:
            ax.scatter(
                [r["model"] for r in reference_rows],
                [r[f"paper_{column}"] for r in reference_rows],
                color="black",
                marker="x",
                label="Paper",
            )
        ax.set(title=column, ylim=(0, 1))
        ax.tick_params(axis="x", rotation=20)
    axes[0, 0].legend()
    save(fig, destination, "figure-13-performance")
    edges = list(range(600, 3001, 300))
    fig, axes = plt.subplots(2, 1, figsize=(9, 7), sharex=True)
    for model, problems in records.items():
        axes[0].plot(
            edges[:-1],
            [
                mean(
                    p["passed"]
                    for p in problems
                    if lo <= metadata[p["task"]]["codeforces_rating"] < hi
                )
                for lo, hi in zip(edges[:-1], edges[1:], strict=True)
            ],
            marker="o",
            label=model,
        )
    axes[0].set(ylabel="Pass@1", ylim=(0, 1))
    axes[0].legend()
    axes[1].hist([m["codeforces_rating"] for m in metadata.values()], bins=edges)
    axes[1].set(xlabel="Codeforces rating", ylabel="Tasks")
    save(fig, destination, "figure-14-difficulty")
    categories = (
        "compile_error",
        "symbolic_correct",
        "symbolic_incorrect",
        "exec_correct",
        "exec_incorrect",
        "indeterminate",
    )
    fig, axes = plt.subplots(2, 2, figsize=(12, 8))
    for ax, bucket in zip(axes.flat, BUCKETS, strict=True):
        bottoms = [0.0] * len(records)
        for category in categories:
            values = [
                sum(p["resolutions"][bucket].get(category, 0) for p in problems)
                / sum(p["buckets"][bucket]["total"] for p in problems)
                for problems in records.values()
            ]
            ax.bar(list(records), values, bottom=bottoms, label=category)
            bottoms = [a + b for a, b in zip(bottoms, values, strict=True)]
        ax.set(title=bucket, ylabel="Fraction of testcases", ylim=(0, 1))
    axes[0, 0].legend(fontsize=7)
    save(fig, destination, "figure-15-resolution")
    # Same set-intersection analysis as F.5, explicitly using the local model set.
    solved = {m: {p["task"] for p in ps if p["passed"]} for m, ps in records.items()}
    intersections = Counter(tuple(m for m in records if task in solved[m]) for task in expected)
    pairs = sorted(intersections.items(), key=lambda x: -x[1])
    fig, (ax, membership) = plt.subplots(2, 1, figsize=(10, 6), sharex=True, height_ratios=(3, 1))
    bars = ax.bar(range(len(pairs)), [v for _, v in pairs])
    ax.bar_label(bars, fontsize=8)
    ax.set(ylabel="Tasks", title="Solved-task intersections: local models")
    model_names = list(records)
    for column, (members, _) in enumerate(pairs):
        membership.scatter([column] * len(model_names), range(len(model_names)), color="0.85")
        selected = [i for i, name in enumerate(model_names) if name in members]
        if selected:
            membership.plot([column] * len(selected), selected, "o-", color="black")
    membership.set_yticks(range(len(model_names)), model_names)
    membership.set_xticks(range(len(pairs)), ["None" if not k else "" for k, _ in pairs])
    membership.set_ylim(-0.5, len(model_names) - 0.5)
    membership.set_xlabel("Models solving exactly this intersection")
    membership.spines[["top", "right", "bottom"]].set_visible(False)
    save(fig, destination, "figure-16-local-model-overlap")
    budgets = list(range(0, 201))
    for conditional in (True, False):
        fig, axes = plt.subplots(1, 5, figsize=(19, 4))
        for ax, buckets in zip(axes, [(b,) for b in BUCKETS] + [BUCKETS], strict=True):
            for model, problems in records.items():
                subset = [
                    p
                    for p in problems
                    if not conditional
                    or any(p["buckets"][b]["passed"] < p["buckets"][b]["total"] for b in buckets)
                ]
                values = []
                for budget in budgets:
                    probability = mean(
                        math.prod(
                            sample_pass_probability(
                                p["buckets"][b]["passed"], p["buckets"][b]["total"], budget
                            )
                            for b in buckets
                        )
                        for p in subset
                    )
                    values.append(1 - probability if conditional else probability)
                ax.plot(budgets, values, label=model)
                if not conditional:
                    converged = next(
                        i
                        for i, value in enumerate(values)
                        if math.isclose(value, values[-1], abs_tol=1e-6)
                    )
                    ax.scatter(budgets[converged], values[converged], s=18)
            ax.set(
                title=" + ".join(buckets) if len(buckets) == 1 else "all buckets",
                xlabel="Tests per bucket",
                ylim=(0, 1),
            )
        axes[0].set_ylabel(
            "P(detect failure | full-suite failure)" if conditional else "Expected Pass@1"
        )
        axes[0].legend(fontsize=7)
        save(fig, destination, "figure-17-detection" if conditional else "figure-18-test-budget")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--dataset-only", action="store_true")
    args = parser.parse_args()
    generate(args.output, dataset_only=args.dataset_only)
