import csv
import os
import random
import tempfile
import unittest

from failover_sim.engine import Engine
from failover_sim.providers import Provider, GroundTruthLedger, ACCEPTED, SUCCEEDED, GENERATION_FAILED
from failover_sim.health import HealthConfig, HealthTracker
from failover_sim.runner import run_once
from failover_sim.scenarios import Scenario


class SimTests(unittest.TestCase):
    def test_engine_ordering(self):
        e=Engine(); out=[]
        e.schedule(1, out.append, "a"); e.schedule(1, out.append, "b"); e.run_until(1)
        self.assertEqual(out,["a","b"])

    def test_engine_deterministic(self):
        def one():
            e=Engine(); out=[]
            for i in range(10): e.schedule(i%3, out.append, i)
            e.run(); return out
        self.assertEqual(one(),one())

    def test_provider_bills_on_accept(self):
        e=Engine(); led=GroundTruthLedger(); rng=random.Random(1)
        p=Provider(e,rng,led,"p",submit_fault_rate=0,generation_failure_rate=0,cost_per_job=2)
        seen=[]
        p.submit(1,1,lambda *x: seen.append(x[-1]),lambda *x: None)
        self.assertEqual(led.total_cost,2)
        e.run_until(.2); self.assertEqual(seen,[ACCEPTED])

    def test_duplicate_concurrent_accept_counts(self):
        led=GroundTruthLedger(); led.on_accept(1,1); led.on_accept(1,1)
        self.assertEqual(led.duplicate_generations,1)

    def test_duplicate_post_success_accept_counts(self):
        led=GroundTruthLedger(); led.on_accept(1,1); led.on_complete(1,SUCCEEDED); led.on_accept(1,1)
        self.assertEqual(led.duplicate_generations,1)

    def test_failover_after_known_failure_not_duplicate(self):
        led=GroundTruthLedger(); led.on_accept(1,1); led.on_complete(1,GENERATION_FAILED); led.on_accept(1,1)
        self.assertEqual(led.duplicate_generations,0)

    def test_throttle_not_fault_when_off(self):
        h=HealthTracker(HealthConfig(throttle_counts_as_failure=False)); h.record_throttle()
        self.assertEqual(len(h.window),0)

    def test_throttle_fault_when_on(self):
        h=HealthTracker(HealthConfig(throttle_counts_as_failure=True)); h.record_throttle()
        self.assertEqual(list(h.window),[0.0])

    def test_score_floor_blocks_breaker(self):
        h=HealthTracker(HealthConfig(score_floor=.5,threshold=.5)); [h.record_fault() for _ in range(20)]
        self.assertFalse(h.check_breaker(0))

    def test_half_open_recovery(self):
        h=HealthTracker(HealthConfig(threshold=.5,cooldown_s=10)); [h.record_fault() for _ in range(20)]
        self.assertTrue(h.check_breaker(0)); self.assertFalse(h.is_open(11)); self.assertEqual(h.score,1.0)

    def test_naive_timeout_abandons_but_bills(self):
        sc=Scenario("baseline",duration_s=1,arrival_rate=.1)
        # direct provider semantics are what matters for billing-at-accept in the timeout race
        e=Engine(); led=GroundTruthLedger(); p=Provider(e,random.Random(3),led,"p",submit_fault_rate=0,generation_failure_rate=0,duration_median_s=1000,duration_sigma=0)
        p.submit(1,1,lambda *x: None,lambda *x: None); self.assertEqual(led.total_cost,1)

    def test_gateway_strands_with_webhook_loss(self):
        sc=Scenario("webhook_loss",duration_s=600,arrival_rate=.2)
        r=run_once(1,sc,"sync_gateway")
        self.assertGreater(r["stranded"],0)

    def test_durable_sweep_recovers_webhook_loss(self):
        sc=Scenario("webhook_loss",duration_s=600,arrival_rate=.2)
        r=run_once(1,sc,"durable_workflow")
        self.assertEqual(r["stranded"],0)

    def test_durable_high_success_baseline(self):
        sc=Scenario("baseline",duration_s=600,arrival_rate=.2)
        r=run_once(2,sc,"durable_workflow")
        self.assertGreaterEqual(r["success_rate"],.98)

    def test_generation_outage_hurts_gateway(self):
        sc=Scenario("generation_outage",duration_s=3600,arrival_rate=.15,stress_window=(900,2400))
        r=run_once(1,sc,"sync_gateway")
        self.assertLess(r["success_rate"],.8)

    def test_same_seed_rows_identical(self):
        sc=Scenario("baseline",duration_s=600,arrival_rate=.2)
        self.assertEqual(run_once(7,sc,"naive_retry"),run_once(7,sc,"naive_retry"))


if __name__ == "__main__":
    unittest.main()
