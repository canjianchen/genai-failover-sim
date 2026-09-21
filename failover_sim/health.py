from collections import deque
from dataclasses import dataclass


@dataclass
class HealthConfig:
    threshold: float = 0.5
    cooldown_s: float = 60.0
    score_floor: float = 0.0
    throttle_counts_as_failure: bool = False
    baseline_weight: float = 0.0


class HealthTracker:
    def __init__(self, config=None, window_size=20):
        self.config = config or HealthConfig()
        self.window = deque(maxlen=window_size)
        self.open_until = 0.0
        self.half_open = False
        self.probe_in_flight = False

    def _refresh(self, now):
        if self.open_until and now >= self.open_until:
            self.open_until = 0.0
            self.window.clear()
            self.half_open = True
            self.probe_in_flight = False

    def record_success(self):
        self.window.append(1.0)
        if self.half_open:
            self.half_open = False
            self.probe_in_flight = False

    def record_fault(self):
        self.window.append(0.0)
        if self.half_open:
            self.probe_in_flight = False

    def record_throttle(self):
        if self.config.throttle_counts_as_failure:
            self.record_fault()
        elif self.half_open:
            # Capacity did not answer the health question; release the probe
            # gate so a later request can probe again without penalizing score.
            self.probe_in_flight = False

    def record_indeterminate(self):
        if self.half_open:
            self.probe_in_flight = False

    @property
    def score(self):
        mean = 1.0 if not self.window else sum(self.window) / len(self.window)
        if self.config.baseline_weight:
            w = self.config.baseline_weight
            mean = (1 - w) * mean + w
        return max(mean, self.config.score_floor)

    def is_open(self, now):
        self._refresh(now)
        return self.open_until > now

    def check_breaker(self, now):
        self._refresh(now)
        if self.open_until > now:
            return True
        if self.score < self.config.threshold:
            self.open_until = now + self.config.cooldown_s
            self.half_open = False
            self.probe_in_flight = False
            return True
        return False

    def allow_request(self, now):
        self._refresh(now)
        if self.open_until > now:
            return False
        if self.half_open:
            if self.probe_in_flight:
                return False
            self.probe_in_flight = True
        return True
