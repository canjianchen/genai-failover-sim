import math

ACCEPTED = "ACCEPTED"
THROTTLED = "THROTTLED"
SUBMIT_FAULT = "SUBMIT_FAULT"
SUCCEEDED = "SUCCEEDED"
GENERATION_FAILED = "GENERATION_FAILED"


class GroundTruthLedger:
    def __init__(self):
        self.inflight_by_job = {}
        self.succeeded_jobs = set()
        self.duplicate_generations = 0
        self.total_cost = 0.0

    def on_accept(self, job_id, cost):
        if self.inflight_by_job.get(job_id, 0) > 0 or job_id in self.succeeded_jobs:
            self.duplicate_generations += 1
        self.inflight_by_job[job_id] = self.inflight_by_job.get(job_id, 0) + 1
        self.total_cost += cost

    def on_complete(self, job_id, outcome):
        if self.inflight_by_job.get(job_id, 0) > 0:
            self.inflight_by_job[job_id] -= 1
        if outcome == SUCCEEDED:
            self.succeeded_jobs.add(job_id)


class Provider:
    def __init__(self, engine, rng, ledger, name,
                 cost_per_job=1.0, max_concurrent=20,
                 submit_latency_s=0.2, duration_median_s=75.0,
                 duration_sigma=0.5, latency_multiplier=1.0,
                 submit_fault_rate=0.01, generation_failure_rate=0.03,
                 webhook_loss_rate=0.0, webhook_duplicate_rate=0.0,
                 webhook_delay_s=1.0, down_submit_timeout_s=30.0):
        self.engine = engine
        self.rng = rng
        self.ledger = ledger
        self.name = name
        self.cost_per_job = float(cost_per_job)
        self.max_concurrent = int(max_concurrent)
        self.nominal_max_concurrent = int(max_concurrent)
        self.submit_latency_s = float(submit_latency_s)
        self.duration_median_s = float(duration_median_s)
        self.duration_sigma = float(duration_sigma)
        self.latency_multiplier = float(latency_multiplier)
        self.submit_fault_rate = float(submit_fault_rate)
        self.generation_failure_rate = float(generation_failure_rate)
        self.extra_generation_failure_rate = 0.0
        self.webhook_loss_rate = float(webhook_loss_rate)
        self.webhook_duplicate_rate = float(webhook_duplicate_rate)
        self.webhook_delay_s = float(webhook_delay_s)
        self.down_submit_timeout_s = float(down_submit_timeout_s)
        self.hard_down = False
        self.throttle_storm = False
        self.in_flight = 0
        self.lost_outcomes = []

    def submit(self, job_id, attempt_id, on_submit_result, on_webhook):
        if self.hard_down:
            self.engine.schedule(self.down_submit_timeout_s, on_submit_result,
                                 self, job_id, attempt_id, SUBMIT_FAULT)
            return
        if self.throttle_storm or self.in_flight >= self.max_concurrent:
            self.engine.schedule(self.submit_latency_s, on_submit_result,
                                 self, job_id, attempt_id, THROTTLED)
            return
        if self.rng.random() < self.submit_fault_rate:
            self.engine.schedule(self.submit_latency_s, on_submit_result,
                                 self, job_id, attempt_id, SUBMIT_FAULT)
            return

        self.in_flight += 1
        self.ledger.on_accept(job_id, self.cost_per_job)
        self.engine.schedule(self.submit_latency_s, on_submit_result,
                             self, job_id, attempt_id, ACCEPTED)
        duration = self.rng.lognormvariate(math.log(self.duration_median_s), self.duration_sigma)
        duration *= self.latency_multiplier
        self.engine.schedule(duration, self._complete,
                             job_id, attempt_id, on_webhook)

    def _complete(self, job_id, attempt_id, on_webhook):
        self.in_flight = max(0, self.in_flight - 1)
        fail_prob = min(1.0, self.generation_failure_rate + self.extra_generation_failure_rate)
        outcome = GENERATION_FAILED if self.rng.random() < fail_prob else SUCCEEDED
        self.ledger.on_complete(job_id, outcome)
        event = (self.engine.now, self, job_id, attempt_id, outcome, on_webhook)
        if self.rng.random() < self.webhook_loss_rate:
            self.lost_outcomes.append(event)
            return
        self.engine.schedule(self.webhook_delay_s, on_webhook,
                             self, job_id, attempt_id, outcome)
        if self.rng.random() < self.webhook_duplicate_rate:
            self.engine.schedule(2 * self.webhook_delay_s, on_webhook,
                                 self, job_id, attempt_id, outcome)

    def drain_lost(self, now, older_than):
        ready, keep = [], []
        for item in self.lost_outcomes:
            completed_at = item[0]
            if now - completed_at >= older_than:
                ready.append(item)
            else:
                keep.append(item)
        self.lost_outcomes = keep
        return ready
