from dataclasses import dataclass


@dataclass
class Scenario:
    name: str
    duration_s: float = 3600.0
    arrival_rate: float = 0.15
    stress_window: tuple = None


def get_scenarios():
    return [
        Scenario("baseline"),
        Scenario("brownout"),
        Scenario("hard_outage", stress_window=(900, 2400)),
        Scenario("generation_outage", stress_window=(900, 2400)),
        Scenario("throttle_storm", arrival_rate=0.33, stress_window=(900, 2700)),
        Scenario("webhook_loss"),
        Scenario("demand_spike", arrival_rate=0.12, stress_window=(900, 1800)),
    ]
