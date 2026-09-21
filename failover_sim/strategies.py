from dataclasses import dataclass, field
from .providers import ACCEPTED, THROTTLED, SUBMIT_FAULT, SUCCEEDED, GENERATION_FAILED
from .health import HealthConfig, HealthTracker


@dataclass
class JobRecord:
    job_id: int
    arrival_time: float
    submits: int = 0
    accepted_attempts: int = 0
    terminal_outcome: str = None
    terminal_time: float = None
    providers_tried: set = field(default_factory=set)
    duplicate_webhooks_deduped: int = 0


class BaseRouter:
    name = "base"

    def __init__(self, engine, providers, health_config=None):
        self.engine = engine
        self.providers = providers
        self.jobs = {}
        self._attempt_seq = 0
        self.health = {
            p.name: HealthTracker(health_config or HealthConfig()) for p in providers
        }

    def new_attempt_id(self):
        self._attempt_seq += 1
        return self._attempt_seq

    def start_job(self, job_id, arrival_time):
        self.jobs[job_id] = JobRecord(job_id, arrival_time)
        raise NotImplementedError

    def _record_terminal(self, job_id, outcome):
        job = self.jobs[job_id]
        if job.terminal_outcome is not None:
            job.duplicate_webhooks_deduped += 1
            return False
        job.terminal_outcome = outcome
        job.terminal_time = self.engine.now
        return True

    def on_webhook(self, provider, job_id, attempt_id, outcome):
        raise NotImplementedError


class NaiveRetryRouter(BaseRouter):
    name = "naive_retry"

    def __init__(self, engine, providers, wait_timeout_s=300.0):
        super().__init__(engine, providers)
        self.wait_timeout_s = wait_timeout_s
        self.state = {}

    def start_job(self, job_id, arrival_time):
        self.jobs[job_id] = JobRecord(job_id, arrival_time)
        self.state[job_id] = {"index": 0, "waiting_attempt": None, "waiting_provider": None}
        self._try_next(job_id)

    def _try_next(self, job_id):
        job = self.jobs[job_id]
        if job.terminal_outcome is not None:
            return
        st = self.state[job_id]
        if st["index"] >= len(self.providers):
            self._record_terminal(job_id, "FAILED")
            return
        provider = self.providers[st["index"]]
        st["index"] += 1
        attempt_id = self.new_attempt_id()
        job.submits += 1
        job.providers_tried.add(provider.name)
        st["waiting_attempt"] = attempt_id
        st["waiting_provider"] = provider.name
        provider.submit(job_id, attempt_id, self._on_submit, self.on_webhook)

    def _on_submit(self, provider, job_id, attempt_id, status):
        job = self.jobs[job_id]
        if job.terminal_outcome is not None:
            return
        st = self.state[job_id]
        if attempt_id != st["waiting_attempt"]:
            return
        if status == ACCEPTED:
            job.accepted_attempts += 1
            self.engine.schedule(self.wait_timeout_s, self._on_timeout, job_id, attempt_id)
        else:
            self._try_next(job_id)

    def _on_timeout(self, job_id, attempt_id):
        job = self.jobs[job_id]
        if job.terminal_outcome is not None:
            return
        st = self.state[job_id]
        if st["waiting_attempt"] != attempt_id:
            return
        self._try_next(job_id)

    def on_webhook(self, provider, job_id, attempt_id, outcome):
        job = self.jobs.get(job_id)
        if job is None or job.terminal_outcome is not None:
            if job is not None:
                job.duplicate_webhooks_deduped += 1
            return
        st = self.state[job_id]
        # Once the loop has timed out and advanced, a late webhook is not the
        # synchronous caller's awaited result. It is ignored by this strategy.
        if st["waiting_attempt"] != attempt_id:
            job.duplicate_webhooks_deduped += 1
            return
        if outcome == SUCCEEDED:
            self._record_terminal(job_id, "SUCCEEDED")
        else:
            self._try_next(job_id)


class SyncGatewayRouter(BaseRouter):
    name = "sync_gateway"

    def __init__(self, engine, providers, health_config=None):
        super().__init__(engine, providers, health_config)
        self.state = {}

    def start_job(self, job_id, arrival_time):
        self.jobs[job_id] = JobRecord(job_id, arrival_time)
        self.state[job_id] = {"index": 0, "accepted": False}
        self._try_next(job_id)

    def _try_next(self, job_id):
        job = self.jobs[job_id]
        if job.terminal_outcome is not None:
            return
        st = self.state[job_id]
        while st["index"] < len(self.providers):
            provider = self.providers[st["index"]]
            st["index"] += 1
            tracker = self.health[provider.name]
            if tracker.check_breaker(self.engine.now):
                continue
            if not tracker.allow_request(self.engine.now):
                continue
            attempt_id = self.new_attempt_id()
            job.submits += 1
            job.providers_tried.add(provider.name)
            provider.submit(job_id, attempt_id, self._on_submit, self.on_webhook)
            return
        self._record_terminal(job_id, "FAILED")

    def _on_submit(self, provider, job_id, attempt_id, status):
        job = self.jobs[job_id]
        if job.terminal_outcome is not None:
            return
        tracker = self.health[provider.name]
        if status == ACCEPTED:
            job.accepted_attempts += 1
            self.state[job_id]["accepted"] = True
            # Deliberate blindness: acceptance is recorded as health success.
            tracker.record_success()
            tracker.check_breaker(self.engine.now)
        elif status == THROTTLED:
            tracker.record_throttle()
            tracker.check_breaker(self.engine.now)
            self._try_next(job_id)
        else:
            tracker.record_fault()
            tracker.check_breaker(self.engine.now)
            self._try_next(job_id)

    def on_webhook(self, provider, job_id, attempt_id, outcome):
        job = self.jobs.get(job_id)
        if job is None:
            return
        if job.terminal_outcome is not None:
            job.duplicate_webhooks_deduped += 1
            return
        if outcome == SUCCEEDED:
            self._record_terminal(job_id, "SUCCEEDED")
        else:
            self._record_terminal(job_id, "FAILED")


class DurableWorkflowRouter(BaseRouter):
    name = "durable_workflow"

    def __init__(self, engine, providers, health_config=None,
                 throttle_cooldown_s=30.0, park_timeout_s=600.0,
                 sweep_interval_s=120.0, job_deadline_s=3600.0):
        super().__init__(engine, providers, health_config)
        self.throttle_cooldown_s = throttle_cooldown_s
        self.park_timeout_s = park_timeout_s
        self.sweep_interval_s = sweep_interval_s
        self.job_deadline_s = job_deadline_s
        self.cooldown_until = {p.name: 0.0 for p in providers}
        self.attempts = {}  # attempt_id -> dict
        self.engine.schedule(self.sweep_interval_s, self._sweep)

    def start_job(self, job_id, arrival_time):
        self.jobs[job_id] = JobRecord(job_id, arrival_time)
        self.engine.schedule(self.job_deadline_s, self._deadline, job_id)
        self._advance(job_id)

    def _eligible(self, job):
        now = self.engine.now
        available = []
        waiting_until = []
        untried = 0
        for provider in self.providers:
            if provider.name in job.providers_tried:
                continue
            untried += 1
            tracker = self.health[provider.name]
            if tracker.check_breaker(now):
                if tracker.open_until > now:
                    waiting_until.append(tracker.open_until)
                continue
            if self.cooldown_until[provider.name] > now:
                waiting_until.append(self.cooldown_until[provider.name])
                continue
            if not tracker.allow_request(now):
                waiting_until.append(now + 5.0)
                continue
            available.append(provider)
        return available, waiting_until, untried

    def _advance(self, job_id):
        job = self.jobs[job_id]
        if job.terminal_outcome is not None:
            return
        available, waiting_until, untried = self._eligible(job)
        if available:
            self._submit(job_id, available[0])
            return
        if waiting_until and untried:
            delay = max(0.001, min(waiting_until) - self.engine.now)
            self.engine.schedule(delay, self._advance, job_id)
            return
        self._record_terminal(job_id, "FAILED")

    def _submit(self, job_id, provider):
        job = self.jobs[job_id]
        attempt_id = self.new_attempt_id()
        job.submits += 1
        self.attempts[attempt_id] = {
            "job_id": job_id, "provider": provider.name,
            "accepted": False, "resolved": False,
        }
        provider.submit(job_id, attempt_id, self._on_submit, self.on_webhook)

    def _on_submit(self, provider, job_id, attempt_id, status):
        job = self.jobs[job_id]
        if job.terminal_outcome is not None:
            return
        attempt = self.attempts.get(attempt_id)
        if not attempt or attempt["resolved"]:
            return
        tracker = self.health[provider.name]
        if status == ACCEPTED:
            attempt["accepted"] = True
            job.accepted_attempts += 1
            self.engine.schedule(self.park_timeout_s, self._park_timeout, job_id, attempt_id)
        elif status == THROTTLED:
            attempt["resolved"] = True
            tracker.record_throttle()
            self.cooldown_until[provider.name] = self.engine.now + self.throttle_cooldown_s
            self._advance(job_id)
        else:
            attempt["resolved"] = True
            tracker.record_fault()
            tracker.check_breaker(self.engine.now)
            job.providers_tried.add(provider.name)
            self._advance(job_id)

    def _park_timeout(self, job_id, attempt_id):
        job = self.jobs[job_id]
        if job.terminal_outcome is not None:
            return
        attempt = self.attempts.get(attempt_id)
        if not attempt or attempt["resolved"] or not attempt["accepted"]:
            return
        # Indeterminate: advance for this job, but do not feed health.
        attempt["resolved"] = True
        self.health[attempt["provider"]].record_indeterminate()
        job.providers_tried.add(attempt["provider"])
        self._advance(job_id)

    def on_webhook(self, provider, job_id, attempt_id, outcome):
        job = self.jobs.get(job_id)
        if job is None:
            return
        attempt = self.attempts.get(attempt_id)
        if attempt:
            attempt["resolved"] = True
        if job.terminal_outcome is not None:
            job.duplicate_webhooks_deduped += 1
            return
        tracker = self.health[provider.name]
        if outcome == SUCCEEDED:
            tracker.record_success()
            tracker.check_breaker(self.engine.now)
            self._record_terminal(job_id, "SUCCEEDED")
        else:
            tracker.record_fault()
            tracker.check_breaker(self.engine.now)
            job.providers_tried.add(provider.name)
            self._advance(job_id)

    def _sweep(self):
        now = self.engine.now
        for provider in self.providers:
            for _, p, job_id, attempt_id, outcome, callback in provider.drain_lost(now, self.sweep_interval_s):
                callback(p, job_id, attempt_id, outcome)
        self.engine.schedule(self.sweep_interval_s, self._sweep)

    def _deadline(self, job_id):
        job = self.jobs.get(job_id)
        if job and job.terminal_outcome is None:
            self._record_terminal(job_id, "FAILED")
