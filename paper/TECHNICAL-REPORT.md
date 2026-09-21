# When You Can't Just Retry: Designing Failover for Production Generative AI

*Canjian Chen*

*Independent Technical Report / Preprint — September 21, 2026*

**Code & reproducibility:** https://github.com/canjianchen/genai-failover-sim

An asynchronous generative-AI job is not a function call. It looks like one
in your code — submit, await, return — but a video-generation job that runs
for three minutes and is charged for execution behaves less like an RPC and more
like a ledger transaction: cost exposure can begin once execution starts,
independently of result delivery, and the process that opened the
transaction may
not live to see it close. Retry state, therefore, cannot live in a stack
frame that can vanish mid-transaction — it has to sink into a durable,
persisted state machine, with the final outcome arbitrated by a conditional
write so that only one terminal decision wins.

That one shift breaks the reliability toolkit most of us reach for. The
common LLM-gateway patterns — and the router libraries built around them —
model failover as request-level retry over completions:
[LiteLLM](https://docs.litellm.ai/docs/routing) retries generic errors
immediately and backs off on rate limits;
[OpenRouter](https://openrouter.ai/docs/guides/routing/model-fallbacks)
consults its fallback list when "the first model returns an error." The
unit of failure is one request/response; there is no concept of a job that
was already accepted and is still running. That model is right for calls
that cost a fraction of a cent and finish in seconds. Generation jobs are
something else:

| The pattern assumes | Generation jobs actually are |
|---|---|
| cheap — retry freely | **billed for execution, not delivery** |
| fast — block and wait | minutes-long |
| a duplicate is waste | a duplicate *succeeds* — and is billed as a success |
| the call returns the outcome | the outcome arrives on a webhook, minutes later |
| rate limits are per-request | quotas are account-level concurrency facts |

Two casualties of this regime are worth naming up front.

The first is the unified try/catch. To a retry loop, a 5xx, a 429, and a
timeout are all "an exception." They are three different statements. A 5xx
or a confirmed generation failure is a **fault** — a statement about the
provider's health, deserving failover and a health penalty. A 429 is a
**capacity** fact — a statement about your account's quota, needing backoff
and queuing, never punishment. And a timeout is **indeterminate** — a
statement about your timer, not about the provider; the work may be
failing, or quietly succeeding. Conflate capacity with fault — demote and
eject a provider because it throttled you — and you eject your healthiest
capacity at exactly the moment demand peaks. Conflate indeterminate with
fault, and you punish providers for your own impatience.

The second is blind trust in the success-rate dashboard. In a regime where
every execution-bearing attempt can carry a price tag, what is your
availability metric *not*
telling you? This article's answer: quite a lot — which is why billable
attempts per logical job and p95/p99 completion time must stand as
first-class metrics, cross-read against success rate, before the
fault-tolerance machinery quietly absorbs real money.

My perspective comes from co-designing model-selection and failover for a
generative-media platform. Everything here, though, is derived from public
principles, and every number is from an open synthetic simulator — mock
providers, synthetic workloads, **no production system, workload, customer
data, or internal company metric is modeled or measured**.

## The contract

You operate a service that turns user requests into generation jobs, with
accounts at several model providers differing in price, latency, quality,
and quota ceilings. The requirements:

1. **Route each job to an available, healthy provider** by policy, and
   fail over on trouble — submit rejection, generation failure, or
   silence — without a human.
2. **Bound duplicate execution, and never double-deliver.** Once an
   accepted asynchronous attempt may still be running, cross-provider
   failover cannot guarantee *zero* duplicate execution without
   provider-side cancellation or cross-attempt idempotency — neither of
   which you control. What you can guarantee is that duplicates are
   bounded, counted, and never delivered twice.
3. **Never strand a job.** Every job reaches a terminal state the caller
   can observe, even when a callback is lost. Silence is not an outcome.
4. **Keep attribution.** When a result lands, you must know which provider
   and which attempt produced it, or quality evaluation and incident
   forensics are impossible.

This looks like retries and bookkeeping. Let's build the standard solution
and be fair to it.

## The naive design

```python
def generate(job):
    for provider in providers_by_preference():
        try:
            result = provider.generate(job, timeout=TIMEOUT)
            record_success(provider)
            return result
        except (ProviderError, Timeout) as err:
            record_failure(provider)
    raise AllProvidersFailed
```

For cheap, fast, idempotent synchronous inference calls, this loop plus a
circuit breaker is often the right design — millisecond responses and
negligible per-call cost forgive blunt retries nearly everything. It earns
its ubiquity honestly. The trouble is not the pattern; it is what happens when the calls
stop being cheap, fast, and safe to repeat. Its unstated assumptions:

- the `try` block returns the *outcome* of the work;
- failure is observable as an exception, promptly, in-line;
- a retry costs nothing but time;
- everything caught is equally a "failure" — timeouts, 5xx, and 429s share
  one `except` clause.

Change only the shape of the call — submit now, outcome later — and all
four fail.

## Why it breaks

**A timeout is not a failure — it is silence.** Not hearing back does not
mean the work didn't happen. It's the online coffee order whose payment
screen spins until "network timeout": you cannot conclude the purchase
failed and tap *buy* again, because the timeout only tells you *your phone*
never received the result. It says nothing about whether the request never
left, or whether the coffee is already being made and the confirmation is
stuck in transit. Guess "failed," reorder, and two coffees arrive with two
charges. In this regime, `provider.generate()` returning a `202 Accepted`
receipt instead of a result makes every timeout exactly this ambiguous —
and the retry that follows a guess is a second, concurrent, billable
execution. Note that failure-refund policies don't rescue you here:
providers commonly refund outright *failures*
([fal.ai](https://fal.ai/docs/documentation/model-apis/pricing.md): "you
are never charged for server errors";
[Replicate](https://replicate.com/docs/topics/billing): "if a run fails, we
don't charge you") — but the run your timed-out retry races against is not
failing. It is *succeeding*, invisibly. When both succeed, both are billed
as successes, and one is garbage.


In the simulator, a fairly-tuned naive loop (timeout sized to the p99 of
the generation-duration distribution) produces about 1–2 duplicate
generations per ~540-job baseline run. Then conditions degrade and the
structure betrays it: under a latency brownout, roughly 90 duplicates per
~540 jobs; under 8% webhook loss, ~50 [synthetic, reproduced]. The loop's
pathology isn't waste under normal operation — it is that its failure mode
*activates exactly when the system is already degraded*, and each
activation costs money.

**The dangerous race is not two actions competing — it is the rescue
colliding with the latecomer.** The race that corrupts state is between the
*recovering* action you launch (failing over to attempt 2) and the *delayed
original* you gave up on. Mark the job failed and move on, and the original
attempt — stuck in some queue — can still complete afterward: now the
record says one thing while reality did another, and money moved on an
attempt your system disowned. This is why a terminal record must exist and
exactly one writer may decide a job: the late-arriving original and the
rescue must be *arbitrated*, not raced.

**The process that submitted the job is gone.** The webhook arrives minutes
later; the loop's stack frame — the only place that knew this job's retry
state — died with a deploy, a crash, or a scale-down. Either attempt state
lives somewhere durable, or a lost callback strands the job forever. In the
simulator, a gateway that fails over at submit time only, with no owner
afterward, strands jobs at exactly its webhook loss rate — by construction:
8% loss → ~8% stranded [synthetic, reproduced].

**Health measured at the front desk tells you nothing about the kitchen.**
Picture a restaurant whose greeter takes every order and hands out numbers
faster than anyone on the street — `202 Accepted`, instantly — while the
kitchen behind them plates nothing and tips every ticket into the trash.
Judge the restaurant by whether numbers get handed out, and your monitoring
dashboard scores it a top-rated establishment. That is precisely what
submit-fed health checks do: the probe sits at the submit path, watching
acceptance, and a provider that accepts everything and completes nothing
looks *perfect*. Worse, the router concludes it is fast and reliable, and
steers *more* traffic into the dead end. In the simulator, a submit-fed
gateway facing exactly this failure holds ~57% real success (CI 56–58%)
while rating the broken provider healthy; an outcome-fed design — webhook
results wired back into health — ejects the bad node and holds ~99.9%
[synthetic, reproduced]. Acceptance at the counter is not delivery from the
kitchen: put the probe at the delivery dock, not the cash register.

Of the three evils — duplicate execution, cost, and state uncertainty —
fear uncertainty most. A clean success or failure is easy. The blind box —
*did it succeed or not?* — is what turns every automatic rescue into
potential poison, because rescues launched against unknown state are how
duplicates happen. The first law here: admit "I don't know yet" rather than
guess "it failed." Timeouts are handled with a per-attempt identity and
scheduled reconciliation — never with a blind retry.

## A durable workflow owns the retry ladder

**A job that runs for minutes and can continue consuming billable
execution must never keep its
retry state in an in-memory `for` loop that a deploy, an OOM kill, or a
scale-down can erase — because when the process dies, the state evaporates,
and what remains is a ghost job: one you can no longer find, which may
continue accruing execution cost.**

Three realities make this the engineering decision rather than a style
preference. *Lifecycle mismatch:* for a 300ms HTTP call, the process
comfortably outlives the work; a generation job inverts that — keeping the
ladder in service memory bets that no rolling deploy, pod eviction, or
autoscaler decision occurs for minutes, on every job, forever. In a modern
cluster that assumption is false by design. *State evaporation is financial
loss:* when the deploy lands, the stack frame zeroes out — but the old
attempt isn't dead; the provider may still be running an execution-bearing
attempt, the
restarted service has no memory of submitting it (a stranded job), and it
submits again — which can create a second charge. One rolling deploy,
three failure modes.
*The ladder is a ledger, not control flow:* at this duration the retry
ladder stops being an `if` statement and becomes a state machine written to
storage. Handing it to a durable-workflow engine —
[Temporal](https://docs.temporal.io/evaluate/understanding-temporal), [AWS
Step Functions'](https://docs.aws.amazon.com/step-functions/latest/dg/connect-to-resource.html)
task-token callback pattern, or a hand-rolled state machine over a queue —
moves "who is watching this job" out of killable CPU memory into a
fault-tolerant, persisted layer. Processes may die freely; the engine
guarantees the next process that picks up the baton knows exactly which
attempt this is, which provider is throttle-cooling, and which provider has
an outstanding or completed execution-bearing attempt.


```python
def generate_workflow(job):
    tried = persisted_set()          # providers burned for THIS job
    deadline = workflow_now() + JOB_DEADLINE

    while workflow_now() < deadline:
        provider = select_provider(exclude=tried)
        if provider is None:
            if any_provider_throttle_cooling():
                workflow_sleep(BACKOFF)          # capacity may return
                continue
            return terminal(job, FAILED, reason=LADDER_EXHAUSTED)

        result = run_activity(attempt, job, provider,
                              attempt_id=new_attempt_id())

        if result is SUCCEEDED:
            return terminal(job, SUCCEEDED, provider)
        elif result is THROTTLED:
            # Capacity fact: no health penalty, NOT marked tried.
            note_throttle_cooldown(provider)
            workflow_sleep(BACKOFF)
        elif result is PARK_TIMEOUT:
            # Indeterminate: absence of an outcome within OUR window
            # says nothing about provider health. Advance the ladder,
            # do NOT punish health.
            tried.add(provider)
        else:  # SUBMIT_FAULT | GENERATION_FAILED
            record_health_fault(provider)
            tried.add(provider)

    return terminal(job, FAILED, reason=DEADLINE)
```

The load-bearing lines are the branch taxonomy — *what marks a provider
tried, and what feeds health*. A **throttle** neither marks tried nor
touches health: the same provider may serve this very job after a cooldown.
A **fault** does both. A **park timeout** — the outcome not arriving within
your chosen window — marks tried, because the ladder must advance, but does
*not* feed health: it is a statement about your timer, not about the
provider. (Provider SDKs already treat throttling as a distinct, retryable
class at the request level — [AWS SDKs](https://docs.aws.amazon.com/sdkref/latest/guide/feature-retry-behavior.html)
classify errors as transient, throttling, or non-retryable, with longer
backoff for throttles; the same distinction must survive into routing
policy.)

Two sizing notes. The park timeout bounds *failover latency against a
silent provider* and is sized from the duration tail: for a lognormal with
median 75s and σ = 0.5, p99 ≈ 240s, so a park of ~300s fails over on almost
nothing that was going to succeed. And the attempt carries an `attempt_id`
so a provider that supports client-supplied idempotency keys can dedupe an
engine-redelivered submission. Support is uneven —
[Bedrock's async invoke](https://docs.aws.amazon.com/bedrock/latest/APIReference/API_runtime_StartAsyncInvoke.html)
accepts a `clientRequestToken` for exactly this, while Replicate, OpenAI's
video API, and Runway document no submission key — and note what this key
does *not* do: it is fresh per attempt, so it cannot prevent the deliberate
second attempt failover itself creates. That protection is the persisted
tried-set and the terminal record, which are yours to build.

**The completion path.** A webhook receiver verifies signatures and
dedupes — webhook delivery is documented as at-least-once
([Svix](https://docs.svix.com/idempotency): "a message can sometimes be
processed twice"; [Stripe](https://docs.stripe.com/webhooks): endpoints
"might occasionally receive the same event more than once"). A periodic
status-poll sweep reconciles jobs whose callbacks never arrived — this
poll-based reconciliation is largely left to the integrator, which is why
it appears explicitly in this architecture. Both paths resume the workflow;
whichever arrives first wins:

```python
def record_terminal(job_id, outcome, attempt_id, provider):
    won = store.put(
        key       = ("job", job_id),
        item      = {"state": outcome, "attempt": attempt_id,
                     "requested_model": attempt.resolved_model_id,   # from submit
                     "served_model": callback.served_model,          # iff provider exposes it
                     "decided_at": now()},
        condition = "state NOT EXISTS",      # first writer wins
    )
    if not won:
        metrics.count("terminal.late_or_duplicate",
                      tags={"provider": provider})
        return DUPLICATE
    # Post-decision steps are NOT protected by the conditional write:
    # each must be independently idempotent and covered by a reconciler,
    # because this process can die on the next line.
    release_slot(provider, job_id)
    resume_workflow(job_id, outcome)
    metrics.count(f"job.{outcome}", tags={"provider": provider})
```

Losing this race is not an error — and the losers are not all equal. A
duplicated webhook or a sweep replay is a *duplicate notification*: one
execution, one bill, no harm. A late terminal from a superseded attempt —
attempt 1's success arriving after failover already started attempt 2 — is
the expensive class: two execution-bearing attempts, potentially two
charges, one deliverable. Two
distinct metrics fall out: `billable_attempts_per_logical_job` is the
spend-amplification metric, while a `superseded_attempt_completed` counter
separately tracks unnecessary *successful* work. Neither is the raw
conditional-write loss count. And note the conditional write makes the
*decision* atomic, nothing more: the release must be re-driveable from the
terminal record, the resume idempotent, and a reconciler must compare
decided-versus-resumed on a schedule.

## Accounting under crash and duplicate delivery

Concurrency quota is a distributed counter whose writers can die
mid-flight: a process that acquires a slot and is OOM-killed before
releasing it leaks that slot *permanently* if release depends on the
acquirer alone. What makes a quota leak lethal is that it is silent: it
never throws a 500 — it presents only as queues growing longer and timeouts
more frequent, concurrency slots seizing up one by one while the dashboard
insists everything is fine, until capacity is gone with nothing running.

```python
def acquire_slot(provider, job_id) -> bool:
    return store.update(
        key       = ("quota", provider),
        condition = "in_flight < :limit AND job_id NOT IN holders",
        update    = "in_flight += 1; holders[job_id] = now()",
    )   # False == a LOCAL throttle: capacity fact, not a fault

def release_slot(provider, job_id):
    store.update(
        key       = ("quota", provider),
        condition = "in_flight > 0 AND job_id IN holders",
        update    = "in_flight -= 1; del holders[job_id]",
    )   # condition failure is fine: a lost race is the system working
```

The guarded increment keeps `in_flight` under the limit; the `count > 0`
clamp survives duplicate release; the holder map makes release idempotent.
The governing principle: **every acquisition of a scarce resource needs a
recovery path that lives outside the acquirer** — because the acquirer can
die between acquire and release — and recovery must terminate in something
a human sees. In practice that means, beyond the happy-path release, a
reconciler that does not share the holder's fate (comparing held-versus-
live against a deadline and force-releasing what the holder abandoned), and
an alarmed escalation for whatever reconciliation cannot resolve. A leak
must end in a page, never in silence.

Capacity arithmetic, briefly, because quota is the binding constraint: at λ
jobs/sec and E[duration] seconds, you hold ≈ λ·E[duration] slots in steady
state (Little's law) — at 0.15 jobs/sec and ~85s expected duration, about
13 concurrent slots before burst headroom. When demand exceeds aggregate
quota this architecture parks and retries; that is a queue, and unbounded
queues need admission control in front of them — a deliberate scope cut
here, not a solved problem.

## Failing over between models is not failing over between servers

Swapping backend workers means a different worker computing *the same
formula* — the answer is identical and the user never knows. Swapping
models means *a different artist painting the picture*: what comes back is
a different work — different style, different composition, broken visual
continuity across a video.

The engineering consequence lives at the product layer: **a technical
failover policy is, in substance, a product policy.** An architect must not
quietly default to "if model A fails, silently switch to B" in the
infrastructure. Agree explicitly with product: which scenarios may trade
artistic consistency for availability (a free-tier quick preview), and
which must fail loudly rather than substitute models (commercial paid
generation).

Two adjacent ML-specific notes. If an evaluation stage rejects bad
generations, its rejects look like another health signal — resist wiring
evaluator scores straight into routing: an evaluator is a sensor, and a
defective sensor driving an actuator amplifies its own defect fleet-wide.
Evaluation-driven ejection wants a human in the loop at much lower gain
than fault-driven ejection. And record attribution honestly at both ends:
the resolved provider/model identifier at *submission*, the winning
attempt's attribution at *completion*, and the provider-reported served
model *only if the callback actually exposes one* — many don't. Version
drift is why this matters: some providers ship floating aliases
([OpenAI](https://developers.openai.com/api/docs/models) lists aliases
separately from pinned IDs), while others pin every ID — 
[Anthropic](https://platform.claude.com/docs/en/about-claude/models/model-ids-and-versions)
calls the evergreen-pointer reading "a common misconception" — yet serving
infrastructure around a pinned model can still change observable behavior.
Without the attribution trail, quality regressions are unattributable.

## What the simulator shows

The simulator exercises the retry, failover, health-signal, and
callback-recovery mechanisms above; the ML-quality and operational design
considerations are reasoned separately and are not simulator evidence. It
is an open discrete-event model (~1,000 lines of stdlib Python plus runner
and tests, deterministic per seed, MIT): three mock providers with
configurable latency, lognormal durations, fault and throttle behavior,
webhook delay/loss/duplication, cost incurred at execution; the naive loop,
a submit-time gateway, and the durable-workflow design over the same
arrival process; seven scenarios. All reported numbers are synthetic —
thirty seeds per cell, bootstrap 95% CIs in the repository's auto-generated
numbers file — and were reproduced in the pinned clean-container
environment described in the reproducibility report. Treat them as one
simulator's illustration of the mechanisms, not as field measurements.

The strategies land where first principles put them. The naive loop is
clean at baseline and degrades as argued. The submit-time gateway never
double-bills but strands under callback loss and collapses to ~57% under a
generation outage it cannot see. The durable workflow averages 99.96%
success across scenarios (its worst single run: 98.92%) with duplicates
bounded — mean ~12, worst run 24, per ~540 jobs in its hardest scenario —
nonzero, which is exactly the
"bounded, not zero" contract [synthetic, reproduced]. Honesty requires the
other column: the durable design's p95 is the *worst* of the three in some
degraded scenarios — under brownout, its insistence on completing every job
buys reliability with tail latency (p95 ~512s vs ~432s for the naive loop)
— and the gateway's flattering p95 under a generation outage is
survivorship: it completes ~57% of jobs quickly, and the dead ones don't
appear in a percentile of successes. Pick your metric knowing what it
hides.

**The paradox the two ablations expose: the better your failover, the
better it hides its own misconfiguration.** A well-built failover layer
running on a misconfigured reliability policy absorbs every impact the
policy causes: success rate sits at 99.9%, the dashboard glows green, and
the machinery has quietly become a painkiller — masking symptoms while
damage accrues underneath. Nobody notices until the bill arrives: finance
walks over with a noticeably higher API invoice, or users ask why generation feels
noticeably slower than last month.

- **Breaker floor — rescued at full price.** Any bounded composite health
  score raises the question: what happens as its floor approaches the
  breaker threshold? At the threshold, algebra takes over — the breaker can
  never open, and a provider failing 100% of generations is never ejected.
  Every job slams into the wall once, then failover rescues it. Success:
  0.9994 → 0.9986, essentially unchanged for the argument. The rescue's
  price: p95 +32% (247s → 327s), cost-per-thousand-successes +25%
  (1316 → 1643), retry amplification +32% — CIs non-overlapping
  [synthetic, reproduced].
- **Signal conflation — the star player benched, everyone queues.** Under a
  quota squeeze, 429s from your busiest, healthiest provider get fed to the
  breaker as faults, and the system ejects its best capacity at peak
  demand. Success: 0.9999, unchanged. The price is latency and churn:
  p95 +71% (267s → 457s) and retry amplification up materially (~+36%),
  while cost stays comparatively small at ~+1.7% [synthetic, reproduced].

This answers the question planted at the start: *if success rate never
moves, how would you know your failover is misconfigured?* **Watch for
metric divergence, not metric levels.** The stronger the fault tolerance,
the more a configuration error converts into latency, retry amplification,
and/or cost instead of failures — which metrics take the damage, and in
what mix, depends on the pathology. Flat
success with climbing p95, climbing `billable_attempts_per_logical_job`, or
climbing cost-per-success *is the failover machinery calling for help*. In
this regime, 99.9% success is the entry fee, not the achievement: a good
failover architecture doesn't just absorb failures — it must surface what
the absorption cost. This is the familiar truth that redundancy hides
partial failure — nothing novel — but here the hidden failure carries a
per-occurrence price tag, and the person who eventually notices may be in
finance rather than on call.

## What to measure

- **Emit outcome metrics once, at the terminal transition** — never per
  attempt, or one struggling job reads as an incident and retries hide the
  real success rate.
- **Alert on divergence**: success rate flat while p95, retry
  amplification, or cost-per-success climbs is the masking signature. The
  divergence itself is the page-worthy signal.
- **Track `billable_attempts_per_logical_job` as a first-class metric**,
  and classify terminal-race losses (duplicate notification vs
  superseded-attempt completion) — only the second class indicates
  potentially redundant execution cost.
- **Unit-test your breaker's reachable range.** If the score floor can meet
  the threshold under any configuration, the breaker is decorative.
- **Contract-test the error taxonomy.** Fault/capacity/indeterminate
  classification is an interface; drift disables failover silently.

## Try it yourself

The simulator, all scenarios, both ablations, and the data behind every
chart are available as an MIT-licensed repository, deterministic per seed
on a pinned Python version and reproducible with one command — the CSVs
you regenerate are the CSVs behind this article.

Repository: https://github.com/canjianchen/genai-failover-sim


In the era of generative AI — where every retry can create real cost — a
system's correctness no longer comes from how cleverly its retry loop is
written, but from how airtight its ledger is. Get the accounting right, and
the architecture follows.