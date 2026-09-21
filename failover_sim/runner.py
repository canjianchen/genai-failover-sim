import csv
import random
from dataclasses import asdict
from .engine import Engine
from .providers import Provider, GroundTruthLedger
from .health import HealthConfig
from .strategies import NaiveRetryRouter, SyncGatewayRouter, DurableWorkflowRouter
from .metrics import nearest_rank
from .scenarios import Scenario


def _base_provider_specs(throttle=False):
    if throttle:
        return [
            dict(name="provider-a", cost_per_job=1.0, max_concurrent=30, duration_median_s=75),
            dict(name="provider-b", cost_per_job=1.4, max_concurrent=20, submit_latency_s=.3, duration_median_s=90),
            dict(name="provider-c", cost_per_job=2.0, max_concurrent=12, submit_latency_s=.4, duration_median_s=110, generation_failure_rate=.05),
        ]
    return [
        dict(name="provider-a", cost_per_job=1.0, max_concurrent=30, duration_median_s=75),
        dict(name="provider-b", cost_per_job=1.4, max_concurrent=25, submit_latency_s=.3, duration_median_s=90),
        dict(name="provider-c", cost_per_job=2.0, max_concurrent=15, submit_latency_s=.4, duration_median_s=110, generation_failure_rate=.05),
    ]


def _build_providers(engine, rng, ledger, scenario):
    specs = _base_provider_specs(scenario.name == "throttle_storm")
    providers = [Provider(engine, rng, ledger, **spec) for spec in specs]
    if scenario.name == "webhook_loss":
        for p in providers:
            p.webhook_loss_rate = .08
    return providers


def _schedule_hooks(engine, providers, scenario):
    a = providers[0]
    if scenario.name == "brownout":
        for t, lm, gf in [(1200,1.75,.12),(1500,2.5,.24),(1800,3.25,.36),(2100,4.0,.48)]:
            def hook(lm=lm, gf=gf):
                a.latency_multiplier = lm
                a.extra_generation_failure_rate = gf
            engine.schedule_at(t, hook)
    elif scenario.name == "hard_outage":
        engine.schedule_at(900, setattr, a, "hard_down", True)
        engine.schedule_at(2400, setattr, a, "hard_down", False)
    elif scenario.name == "generation_outage":
        engine.schedule_at(900, setattr, a, "extra_generation_failure_rate", 1.0)
        engine.schedule_at(2400, setattr, a, "extra_generation_failure_rate", 0.0)
    elif scenario.name == "throttle_storm":
        engine.schedule_at(900, setattr, a, "max_concurrent", 8)
        engine.schedule_at(2700, setattr, a, "max_concurrent", 30)


def _rate_at(scenario, t):
    if scenario.name == "demand_spike" and 900 <= t < 1800:
        return scenario.arrival_rate * 4.0
    return scenario.arrival_rate


def _next_boundary(scenario, t):
    if scenario.name != "demand_spike":
        return None
    for b in (900.0, 1800.0, scenario.duration_s):
        if b > t + 1e-12:
            return b
    return None


def _schedule_arrivals(engine, rng, scenario, router):
    counter = {"job_id": 0}

    def plan_from(t):
        if t >= scenario.duration_s:
            return
        rate = _rate_at(scenario, t)
        dt = rng.expovariate(rate)
        boundary = _next_boundary(scenario, t)
        if boundary is not None and t + dt >= boundary:
            engine.schedule_at(boundary, plan_from, boundary)
            return
        arrival = t + dt
        if arrival >= scenario.duration_s:
            return
        engine.schedule_at(arrival, arrive, arrival)

    def arrive(t):
        counter["job_id"] += 1
        router.start_job(counter["job_id"], t)
        plan_from(t)

    plan_from(0.0)
    return counter


def run_once(seed, scenario, strategy_name, score_floor=0.0,
             throttle_counts_as_failure=False):
    if not isinstance(scenario, Scenario):
        scenario = Scenario(**scenario)
    engine = Engine()
    rng_arrivals = random.Random(f"{seed}-arrivals")
    rng_providers = random.Random(f"{seed}-providers")
    ledger = GroundTruthLedger()
    providers = _build_providers(engine, rng_providers, ledger, scenario)
    _schedule_hooks(engine, providers, scenario)
    hc = HealthConfig(score_floor=score_floor,
                      throttle_counts_as_failure=throttle_counts_as_failure)
    if strategy_name == "naive_retry":
        router = NaiveRetryRouter(engine, providers, wait_timeout_s=300)
    elif strategy_name == "sync_gateway":
        router = SyncGatewayRouter(engine, providers, health_config=hc)
    elif strategy_name == "durable_workflow":
        router = DurableWorkflowRouter(engine, providers, health_config=hc,
                                       throttle_cooldown_s=30, park_timeout_s=600,
                                       sweep_interval_s=120, job_deadline_s=3600)
    else:
        raise ValueError(strategy_name)

    _schedule_arrivals(engine, rng_arrivals, scenario, router)
    engine.run_until(scenario.duration_s + 1800)

    jobs = list(router.jobs.values())
    total = len(jobs)
    successes = [j for j in jobs if j.terminal_outcome == "SUCCEEDED"]
    stranded = [j for j in jobs if j.terminal_outcome is None]
    completion = [j.terminal_time - j.arrival_time for j in successes]
    total_submits = sum(j.submits for j in jobs)
    cost_per_1k = (ledger.total_cost / len(successes) * 1000.0) if successes else float("inf")
    row = {
        "seed": seed,
        "scenario": scenario.name,
        "strategy": strategy_name,
        "jobs": total,
        "success_rate": len(successes) / total if total else 0.0,
        "stranded": len(stranded),
        "duplicate_generations": ledger.duplicate_generations,
        "total_cost": ledger.total_cost,
        "cost_per_1k_success": cost_per_1k,
        "retry_amplification": total_submits / total if total else 0.0,
        "p50_completion_s": nearest_rank(completion, 50),
        "p95_completion_s": nearest_rank(completion, 95),
        "p99_completion_s": nearest_rank(completion, 99),
    }
    if scenario.stress_window:
        lo, hi = scenario.stress_window
        wjobs = [j for j in jobs if lo <= j.arrival_time < hi]
        wsuccess = [j for j in wjobs if j.terminal_outcome == "SUCCEEDED"]
        wcomp = [j.terminal_time - j.arrival_time for j in wsuccess]
        row.update({
            "window_jobs": len(wjobs),
            "window_success_rate": len(wsuccess)/len(wjobs) if wjobs else 0.0,
            "window_p50_completion_s": nearest_rank(wcomp, 50),
            "window_p95_completion_s": nearest_rank(wcomp, 95),
            "window_p99_completion_s": nearest_rank(wcomp, 99),
        })
    return row


def write_csv(path, rows):
    if not rows:
        return
    keys = []
    for row in rows:
        for k in row:
            if k not in keys:
                keys.append(k)
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        w.writerows(rows)
