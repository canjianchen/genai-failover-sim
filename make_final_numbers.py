import csv
import json
import os
import random
import statistics
import hashlib
import numpy as np

BASE_SEED = 20260921

NUMERIC_FIELDS = [
    "jobs","success_rate","stranded","duplicate_generations","total_cost",
    "cost_per_1k_success","retry_amplification","p50_completion_s",
    "p95_completion_s","p99_completion_s","window_jobs","window_success_rate",
    "window_p50_completion_s","window_p95_completion_s","window_p99_completion_s",
]


def read_csv(path):
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def val(row, k):
    x = row.get(k)
    if x in (None, "", "None"):
        return None
    return float(x)


def ci95(values, key):
    if not values:
        return [None, None]
    seed = (BASE_SEED + int(hashlib.sha256(key.encode()).hexdigest()[:8], 16)) % (2**32 - 1)
    rng = np.random.RandomState(seed)
    arr = np.asarray(values, dtype=float)
    n = len(arr)
    idx = rng.randint(0, n, size=(10000, n))
    means = arr[idx].mean(axis=1)
    means.sort()
    lo = means[int(.025*len(means))]
    hi = means[min(len(means)-1, int(.975*len(means)))]
    return [round(float(lo),4), round(float(hi),4)]


def summarize(rows, key_prefix):
    out = {}
    for field in NUMERIC_FIELDS:
        xs = [val(r, field) for r in rows]
        xs = [x for x in xs if x is not None]
        if not xs:
            continue
        out[field] = {
            "mean": round(sum(xs)/len(xs),4),
            "min": round(min(xs),4),
            "max": round(max(xs),4),
            "ci95": ci95(xs, f"{key_prefix}:{field}"),
            "n": len(xs),
        }
    return out


def main():
    outdir = "results"
    matrix = read_csv(os.path.join(outdir,"matrix.csv"))
    breaker = read_csv(os.path.join(outdir,"breaker_floor.csv"))
    signal = read_csv(os.path.join(outdir,"signal_separation.csv"))

    data = {"n_seeds": len(set(int(r["seed"]) for r in matrix)), "matrix":{}, "breaker_floor":{}, "signal_separation":{}}
    keys = sorted(set((r["scenario"],r["strategy"]) for r in matrix))
    for sc, st in keys:
        rs = [r for r in matrix if r["scenario"]==sc and r["strategy"]==st]
        data["matrix"][f"{sc}/{st}"] = summarize(rs, f"matrix:{sc}:{st}")
    for label in sorted(set(r["label"] for r in breaker), key=lambda s: float(s.split("=")[1])):
        data["breaker_floor"][label] = summarize([r for r in breaker if r["label"]==label], f"breaker:{label}")
    for label in ["throttle=capacity_fact","throttle=failure"]:
        data["signal_separation"][label] = summarize([r for r in signal if r["label"]==label], f"signal:{label}")

    with open("FINAL-NUMBERS.json","w") as f:
        json.dump(data,f,indent=2,sort_keys=True)
        f.write("\n")

    def metric(path, field):
        return data["matrix"][path][field]
    lines = ["# FINAL-NUMBERS (auto-generated — do not hand-edit)","",f"Seeds per cell: {data['n_seeds']}. CI = bootstrap 95% over seeds.","Every article number must quote this file.","","## Headline cells"]
    for path, field in [
        ("baseline/naive_retry","duplicate_generations"),
        ("brownout/naive_retry","duplicate_generations"),
        ("webhook_loss/naive_retry","duplicate_generations"),
        ("generation_outage/sync_gateway","success_rate"),
        ("webhook_loss/sync_gateway","stranded"),
        ("brownout/durable_workflow","duplicate_generations"),
        ("brownout/durable_workflow","p95_completion_s"),
        ("brownout/naive_retry","p95_completion_s"),
    ]:
        m = metric(path,field)
        lines.append(f"- {path} · {field}: {m['mean']:.3f} [{m['ci95'][0]:.3f}, {m['ci95'][1]:.3f}] (min {m['min']}, max {m['max']})")
    lines += ["","## Durable workflow success, all scenarios"]
    durable = [v["success_rate"] for k,v in data["matrix"].items() if k.endswith("/durable_workflow")]
    lines.append(f"- min over all runs: {min(x['min'] for x in durable):.4f}")
    lines.append(f"- mean of means: {sum(x['mean'] for x in durable)/len(durable):.4f}")
    lines += ["","## Breaker-floor ablation (by floor)"]
    for label, vals in data["breaker_floor"].items():
        p=vals["window_p95_completion_s"]; c=vals["cost_per_1k_success"]; a=vals["retry_amplification"]; s=vals["success_rate"]
        lines.append(f"- {label}: win_p95 {p['mean']:.1f} [{p['ci95'][0]}, {p['ci95'][1]}] · cost/1k {c['mean']:.1f} [{c['ci95'][0]}, {c['ci95'][1]}] · amp {a['mean']:.3f} · success {s['mean']:.4f}")
    lines += ["","## Signal-separation ablation"]
    for label, vals in data["signal_separation"].items():
        p=vals["window_p95_completion_s"]; c=vals["cost_per_1k_success"]; a=vals["retry_amplification"]; s=vals["window_success_rate"]
        lines.append(f"- {label}: win_p95 {p['mean']:.1f} [{p['ci95'][0]}, {p['ci95'][1]}] · cost/1k {c['mean']:.1f} [{c['ci95'][0]}, {c['ci95'][1]}] · amp {a['mean']:.3f} · win_success {s['mean']:.4f} [{s['ci95'][0]}, {s['ci95'][1]}]")
    with open("FINAL-NUMBERS.md","w") as f:
        f.write("\n".join(lines)+"\n")


if __name__ == "__main__":
    main()
