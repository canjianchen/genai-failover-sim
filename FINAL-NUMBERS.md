# FINAL-NUMBERS (auto-generated — do not hand-edit)

Seeds per cell: 30. CI = bootstrap 95% over seeds.
Every article number must quote this file.

## Headline cells
- baseline/naive_retry · duplicate_generations: 1.667 [1.300, 2.100] (min 0.0, max 5.0)
- brownout/naive_retry · duplicate_generations: 90.400 [88.400, 92.400] (min 78.0, max 104.0)
- webhook_loss/naive_retry · duplicate_generations: 50.633 [47.833, 53.533] (min 34.0, max 66.0)
- generation_outage/sync_gateway · success_rate: 0.568 [0.562, 0.575] (min 0.5332, max 0.6037)
- webhook_loss/sync_gateway · stranded: 42.967 [41.133, 44.867] (min 33.0, max 56.0)
- brownout/durable_workflow · duplicate_generations: 12.300 [10.467, 14.167] (min 1.0, max 24.0)
- brownout/durable_workflow · p95_completion_s: 511.513 [492.813, 528.510] (min 328.9356, max 602.4604)
- brownout/naive_retry · p95_completion_s: 432.058 [428.958, 435.272] (min 417.2541, max 453.0191)

## Durable workflow success, all scenarios
- min over all runs: 0.9892
- mean of means: 0.9996

## Breaker-floor ablation (by floor)
- floor=0.00: win_p95 247.1 [241.9987, 252.2415] · cost/1k 1315.5 [1307.3217, 1324.0063] · amp 1.109 · success 0.9994
- floor=0.10: win_p95 247.1 [241.8962, 252.2693] · cost/1k 1315.5 [1307.1891, 1324.02] · amp 1.109 · success 0.9994
- floor=0.20: win_p95 247.1 [241.8664, 252.2383] · cost/1k 1315.5 [1307.2349, 1324.0827] · amp 1.109 · success 0.9994
- floor=0.30: win_p95 247.1 [241.8755, 252.1307] · cost/1k 1315.5 [1307.2974, 1323.832] · amp 1.109 · success 0.9994
- floor=0.40: win_p95 247.1 [241.9047, 252.2499] · cost/1k 1315.5 [1307.0835, 1324.1465] · amp 1.109 · success 0.9994
- floor=0.50: win_p95 326.6 [320.7146, 333.1029] · cost/1k 1642.9 [1631.1934, 1654.5862] · amp 1.462 · success 0.9986
- floor=0.60: win_p95 326.6 [320.669, 332.9135] · cost/1k 1642.9 [1630.9684, 1654.5298] · amp 1.462 · success 0.9986
- floor=0.70: win_p95 326.6 [320.7121, 332.8741] · cost/1k 1642.9 [1631.1483, 1654.5978] · amp 1.462 · success 0.9986
- floor=0.80: win_p95 326.6 [320.7179, 333.0139] · cost/1k 1642.9 [1631.1904, 1654.2356] · amp 1.462 · success 0.9986

## Signal-separation ablation
- throttle=capacity_fact: win_p95 267.0 [253.8891, 281.3996] · cost/1k 1309.8 [1304.4855, 1315.3417] · amp 2.637 · win_success 0.9999 [0.9998, 1.0]
- throttle=failure: win_p95 457.0 [414.4991, 499.0001] · cost/1k 1332.5 [1325.4554, 1339.5511] · amp 3.588 · win_success 0.9999 [0.9997, 1.0]
