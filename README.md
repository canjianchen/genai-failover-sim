# genai-failover-sim

A clean-room, discrete-event simulation of failover strategies for asynchronous, non-idempotent generative-AI jobs.

**All results are synthetic. No production system, workload, customer data, or internal company metric is modeled or measured.**

## Technical report

**When You Can't Just Retry: Designing Failover for Production Generative AI** — Independent Technical Report / Preprint, September 21, 2026.

- [Read the technical report](paper/TECHNICAL-REPORT.md)
- [Preprint on Zenodo](https://zenodo.org/records/22870512)
- DOI: [10.5281/zenodo.22870512](https://doi.org/10.5281/zenodo.22870512)
- Reproducibility commit: `4cd6ee1c5350f899681b8deed638ba3074a62fa7`

### Citation

Chen, Canjian. *When You Can't Just Retry: Designing Failover for Production Generative AI.* Independent Technical Report / Preprint, 2026. DOI: [10.5281/zenodo.22870512](https://doi.org/10.5281/zenodo.22870512).

## Reproduce

The reference environment is CPython 3.9. Run:

```bash
docker build -t genai-failover-sim .
docker run --rm --network=none genai-failover-sim
```

Or locally:

```bash
pip install -r requirements.txt
./reproduce.sh
```

`reproduce.sh` runs the unit tests, the full 30-seed experiment matrix, both ablations, chart generation, final-number generation, and an exact consistency check against `FINAL-NUMBERS-FROZEN.json`.

## Methodology

- Seven synthetic scenarios are evaluated across three strategies: `naive_retry`, `sync_gateway`, and `durable_workflow`.
- Seeds 1–30 are used for each cell.
- Arrivals use a dedicated `Random(f"{seed}-arrivals")` stream so the workload is common across strategies. Provider/router randomness uses a separate stream and may diverge as strategies make different decisions.
- Completion percentiles use nearest-rank percentiles without interpolation.
- Confidence intervals are bootstrap 95% intervals over seeds with 10,000 resamples and a fixed bootstrap seed derived from `20260921`.
- Determinism depends on CPython 3.9's `random` behavior; the Docker image is the reference environment.
- Charts are watermarked `SIMULATION — synthetic data, not production measurements`.

## Outputs

- `results/matrix.csv`
- `results/breaker_floor.csv`
- `results/signal_separation.csv`
- five charts under `results/`
- `FINAL-NUMBERS.md`
- `FINAL-NUMBERS.json`

## Scope

The simulator is intended to illustrate retry/failover mechanics, outcome-vs-ack health signals, webhook-loss recovery, quota behavior, and two reliability-policy ablations. It is not a model of any production service and does not make claims about real provider reliability or cost.

## License

MIT © 2026 Canjian Chen