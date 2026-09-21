import argparse
import os
import csv
from collections import defaultdict
import matplotlib.pyplot as plt

from failover_sim.runner import run_once, write_csv
from failover_sim.scenarios import get_scenarios


def mean(xs):
    return sum(xs)/len(xs) if xs else 0.0


def chart_matrix(rows, outdir, metric, filename, ylabel):
    groups = defaultdict(list)
    for r in rows:
        v = r.get(metric)
        if v not in (None, ""):
            groups[(r["scenario"], r["strategy"])].append(float(v))
    scenarios = [s.name for s in get_scenarios()]
    strategies = ["naive_retry","sync_gateway","durable_workflow"]
    x = list(range(len(scenarios)))
    width = 0.24
    fig, ax = plt.subplots(figsize=(12,6))
    for i, st in enumerate(strategies):
        ys = [mean(groups.get((s,st), [])) for s in scenarios]
        ax.bar([j + (i-1)*width for j in x], ys, width, label=st)
    ax.set_xticks(x)
    ax.set_xticklabels(scenarios, rotation=25, ha="right")
    ax.set_ylabel(ylabel)
    ax.legend()
    ax.text(0.5, 0.5, "SIMULATION — synthetic data, not production measurements",
            transform=ax.transAxes, ha="center", va="center", alpha=.18,
            fontsize=16, rotation=20)
    fig.tight_layout()
    fig.savefig(os.path.join(outdir, filename), dpi=160)
    plt.close(fig)


def chart_breaker(rows, outdir):
    by = defaultdict(list)
    for r in rows:
        by[r["label"]].append(r)
    labels = sorted(by, key=lambda x: float(x.split("=")[1]))
    floors = [float(x.split("=")[1]) for x in labels]
    p95 = [mean([float(r["window_p95_completion_s"]) for r in by[l]]) for l in labels]
    cost = [mean([float(r["cost_per_1k_success"]) for r in by[l]]) for l in labels]
    fig, ax = plt.subplots(figsize=(8,5))
    ax.plot(floors, p95, marker="o", label="window p95 (s)")
    ax.set_xlabel("score floor")
    ax.set_ylabel("window p95 (s)")
    ax2 = ax.twinx()
    ax2.plot(floors, cost, marker="x", label="cost / 1k success")
    ax2.set_ylabel("cost / 1k success")
    ax.text(0.5, 0.5, "SIMULATION — synthetic data, not production measurements",
            transform=ax.transAxes, ha="center", va="center", alpha=.18,
            fontsize=14, rotation=20)
    fig.tight_layout()
    fig.savefig(os.path.join(outdir, "breaker_floor.png"), dpi=160)
    plt.close(fig)


def chart_signal(rows, outdir):
    by = defaultdict(list)
    for r in rows:
        by[r["label"]].append(r)
    labels = ["throttle=capacity_fact","throttle=failure"]
    p95 = [mean([float(r["window_p95_completion_s"]) for r in by[l]]) for l in labels]
    fig, ax = plt.subplots(figsize=(7,5))
    ax.bar(labels, p95)
    ax.set_ylabel("window p95 (s)")
    ax.text(0.5, 0.5, "SIMULATION — synthetic data, not production measurements",
            transform=ax.transAxes, ha="center", va="center", alpha=.18,
            fontsize=14, rotation=20)
    fig.tight_layout()
    fig.savefig(os.path.join(outdir, "signal_separation.png"), dpi=160)
    plt.close(fig)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=30)
    ap.add_argument("--outdir", default="results")
    args = ap.parse_args()
    os.makedirs(args.outdir, exist_ok=True)

    matrix = []
    for sc in get_scenarios():
        for strategy in ["naive_retry","sync_gateway","durable_workflow"]:
            for seed in range(1, args.seeds+1):
                matrix.append(run_once(seed, sc, strategy))
    write_csv(os.path.join(args.outdir, "matrix.csv"), matrix)

    generation_outage = next(s for s in get_scenarios() if s.name == "generation_outage")
    breaker = []
    for floor in [i/10 for i in range(9)]:
        label = f"floor={floor:.2f}"
        for seed in range(1, args.seeds+1):
            row = run_once(seed, generation_outage, "durable_workflow", score_floor=floor)
            row["label"] = label
            breaker.append(row)
    write_csv(os.path.join(args.outdir, "breaker_floor.csv"), breaker)

    throttle = next(s for s in get_scenarios() if s.name == "throttle_storm")
    signal = []
    for flag, label in [(False,"throttle=capacity_fact"),(True,"throttle=failure")]:
        for seed in range(1, args.seeds+1):
            row = run_once(seed, throttle, "durable_workflow",
                           throttle_counts_as_failure=flag)
            row["label"] = label
            signal.append(row)
    write_csv(os.path.join(args.outdir, "signal_separation.csv"), signal)

    chart_matrix(matrix, args.outdir, "success_rate", "success_rate.png", "success rate")
    chart_matrix(matrix, args.outdir, "duplicate_generations", "duplicate_generations.png", "duplicate generations")
    chart_matrix(matrix, args.outdir, "p95_completion_s", "p95_completion.png", "p95 completion (s)")
    chart_breaker(breaker, args.outdir)
    chart_signal(signal, args.outdir)


if __name__ == "__main__":
    main()
