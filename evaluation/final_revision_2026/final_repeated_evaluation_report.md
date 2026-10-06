# Final Repeated Evaluation Report — 2026

## Experiment Status

Post-defense repeated evaluation using a frozen current implementation and complete raw logging.

Final verdict: FINAL REPEATED EVALUATION COMPLETE

This post-defense repeated evaluation was executed using a frozen current implementation and complete raw logging. It is reported transparently as a revised reproducibility evaluation and is not represented as the missing original raw execution record underlying the defense-draft aggregate percentages.

## Frozen State and Dataset

- Git HEAD: `19361b35e27788fa1dea2cca020a519f41f14697`
- Environment record: `T:\thai-ecommerce-agent\evaluation\final_revision_2026\environment`
- Source-hash status: `ALL MATCH`
- Authoritative dataset: `T:\thai-ecommerce-agent\data\evaluation\all_scenarios.json`
- Dataset SHA-256: `ea45e38b068285dfd0ae174047773b05654313faac718a19d981b991900bb137`
- Frozen copy: `T:\thai-ecommerce-agent\evaluation\final_revision_2026\scenarios_120_frozen.json`
- Frozen-copy SHA-256: `ea45e38b068285dfd0ae174047773b05654313faac718a19d981b991900bb137`
- Composition: {"clarification": 30, "policy": 30, "routing": 30, "transaction": 30}
- Language mix: {"en": 16, "th": 72, "th-en": 32}
- Input types: {"abnormal": 18, "boundary": 32, "normal": 70}
- Status: simulated/researcher-designed controlled scenarios; no live customer or production orders.

## Systems and Configuration

### Multi-Agent current implementation

The evaluated implementation uses `app/agents/router.py` for deterministic intent routing, `app/agents/orchestrator.py` for coordination, `app/agents/transaction_tracker.py` for structured order evidence, `app/agents/policy_evaluator.py` for policy retrieval and evidence preparation, and `app/agents/llm_generator.py` for DeepSeek response generation where the current path calls it. Each scored scenario used a clean session and an isolated per-run SQLite database.

### Monolithic baseline

The established `scripts/run_monolithic_baseline.py` semantics were retained: one unified DeepSeek JSON pipeline per scenario, with no Router, Transaction Tracker, Store Policy Evaluator, direct SQLite lookup, or ChromaDB retrieval. The baseline received all five synthetic sample orders and all five local policy documents in one system prompt. The exact prompt is stored at `T:\thai-ecommerce-agent\evaluation\final_revision_2026\monolithic_system_prompt.txt` with SHA-256 `2a29c87abb662262c25387736154a0d4687be2da3fb2be0e8f5803ea1c3d97ba`; the source inventory is `T:\thai-ecommerce-agent\evaluation\final_revision_2026\monolithic_context_inventory.md`.

### Runtime values

- DeepSeek model: `deepseek-v4-flash`
- DeepSeek base URL: `https://api.deepseek.com`; credentials were not recorded.
- Multi-Agent temperature/max tokens/timeout: `0.1 / 512 / 30 seconds`
- Monolithic temperature/max tokens/timeout: `0.1 / 1024 / 60 seconds`
- Embedding model: `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`
- Chroma collection: `siamcart_store_policies`; distance metric: `cosine`
- Policy chunking: `Markdown H2 section-based chunks; bilingual Thai and English blocks split into separate chunks; complete section metadata preserved.`
- Maximum chunk characters: `800`
- Retrieval top-k: `3`; distance criterion: `0.6`
- Router: `deterministic rule-based`
- Routing threshold: `None`
- Routing threshold reason: Deterministic rule-based routing; confidence values are heuristic labels, not calibrated probabilities or an acceptance threshold.
- Observed heuristic confidence values: `{"explicit_intent": 1.0, "order_id_fallback": 1.0, "out_of_scope_or_human_review": 0.6, "unknown_clarification": 0.3}`
- Retry policy: maximum 3 retries after the first attempt, fixed short exponential backoff, no synthetic replacement.

## Warm-up Boundary

Before each run, the isolated DB was initialized, the embedding model loaded, the policy index built, one unscored policy retrieval was performed, and one harmless unscored DeepSeek preflight was performed. Warm-up and preflight latency were excluded from scored scenario latency.

- run_1: DB init 2.37 ms; embedding load 19253.06 ms; index build 749.29 ms; retrieval warm-up 53.76 ms; DeepSeek preflight passed in 1679.96 ms.
- run_2: DB init 81.02 ms; embedding load 21382.23 ms; index build 1597.43 ms; retrieval warm-up 39.69 ms; DeepSeek preflight passed in 2630.97 ms.
- run_3: DB init 47.17 ms; embedding load 15873.17 ms; index build 943.18 ms; retrieval warm-up 20.77 ms; DeepSeek preflight passed in 1847.86 ms.

## Completion and Results

Three runs × 120 scenarios × 2 systems = 720 attempted scenario-system evaluations.

| Run | MA routing | Monolithic routing | MA transaction | Monolithic transaction | MA success | Monolithic success |
|---|---:|---:|---:|---:|---:|---:|
| run_1 | 84/120 = 70.000000% | 79/120 = 65.833333% | 30/30 = 100.000000% | 12/30 = 40.000000% | 120/120 | 101/120 |
| run_2 | 84/120 = 70.000000% | 84/120 = 70.000000% | 30/30 = 100.000000% | 21/30 = 70.000000% | 120/120 | 110/120 |
| run_3 | 84/120 = 70.000000% | 81/120 = 67.500000% | 30/30 = 100.000000% | 16/30 = 53.333333% | 120/120 | 103/120 |

### Pooled metrics

| Metric | Multi-Agent pooled | Monolithic pooled |
|---|---:|---:|
| Routing accuracy | 252/360 = 70.000000% | 244/360 = 67.777778% |
| Transaction-answer accuracy | 90/90 = 100.000000% | 49/90 = 54.444444% |
| Execution success | 360/360 = 100.000000% | 314/360 = 87.222222% |
| Execution failures | 0/360 | 46/360 |
| Policy correctness | Pending manual review, 90 rows | Pending manual review, 90 rows |

### Clarification and escalation confusion results

Operational definition: the system's structured `requires_clarification` and `simulated_human_review` outputs are the booleans recorded as triggered; false negatives include failed executions with no structured output.

| Scope | Clarification | Simulated human review escalation |
|---|---|---|
| Multi-Agent pooled | raw=117/360; TP=57, FN=12, FP=60, TN=231 | raw=18/360; TP=18, FN=21, FP=0, TN=321 |
| Monolithic pooled | raw=80/360; TP=50, FN=19, FP=30, TN=261 | raw=13/360; TP=13, FN=26, FP=0, TN=321 |

Per-run confusion matrices are preserved in `final_metrics_summary.json` and each `run_metrics.json`.

### Latency

Sample standard deviation is used. P95 uses deterministic linear interpolation. The pooled 95% confidence interval is a deterministic nonparametric bootstrap for the mean with 10,000 resamples and fixed seed `20260901`.

- Multi-Agent run_1: n=120, mean=840.544083 ms, median=3.675 ms, sample SD=1920.703406 ms, P95=4336.1775 ms, min=0.2 ms, max=11645.08 ms.
- Multi-Agent run_2: n=120, mean=929.800333 ms, median=2.645 ms, sample SD=2496.756657 ms, P95=4484.3285 ms, min=0.16 ms, max=19120.59 ms.
- Multi-Agent run_3: n=120, mean=833.111167 ms, median=2.75 ms, sample SD=2141.377166 ms, P95=4252.119 ms, min=0.17 ms, max=18204.14 ms.
- Multi-Agent pooled: n=360, mean=867.818528 ms, median=2.85 ms, sample SD=2193.427396 ms, P95=4453.708 ms, min=0.16 ms, max=19120.59 ms, 95% bootstrap CI for mean=[651.290222, 1102.843448] ms.
- Monolithic run_1: n=120, mean=5262.567667 ms, median=3615.915 ms, sample SD=5135.987719 ms, P95=14316.9035 ms, min=1407.36 ms, max=33696.05 ms.
- Monolithic run_2: n=120, mean=5426.28475 ms, median=4041.735 ms, sample SD=4401.440681 ms, P95=14359.0805 ms, min=1743.61 ms, max=29691.8 ms.
- Monolithic run_3: n=120, mean=5359.473167 ms, median=3951.885 ms, sample SD=5053.010931 ms, P95=10819.0805 ms, min=1831.01 ms, max=43664.6 ms.
- Monolithic pooled: n=360, mean=5349.441861 ms, median=3931.035 ms, sample SD=4861.427587 ms, P95=14289.7305 ms, min=1407.36 ms, max=43664.6 ms, 95% bootstrap CI for mean=[4881.057852, 5881.873447] ms.

## Policy Review

Every policy response is included in `T:\thai-ecommerce-agent\evaluation\final_revision_2026\policy_manual_review.csv`: 30 policy scenarios × 2 systems × 3 runs = 180 rows. Criterion slots are left unscored; `reviewer_score` and `reviewer_notes` are blank. DeepSeek was not used to grade its own answers.

## Limitations

- This is a single three-repetition evaluation of simulated/researcher-designed scenarios, not a broad statistical study.
- External DeepSeek responses may vary with provider state, model version, and time.
- Current implementation behavior may differ from the frozen thesis implementation and is reported as a revised reproducibility result.
- Policy correctness remains pending human adjudication against the local policy documents.
- Warm-up is intentionally excluded, so latency represents steady-state scenario processing rather than cold-start application latency.

## Verification and Integrity

- Independent verifier: `T:\thai-ecommerce-agent\evaluation\final_revision_2026\verify_final_revision.py`
- Verification result: `PASSED`
- Scenario hash check: `PASSED`
- Raw records: 120 Multi-Agent + 120 Monolithic per run
- Pooled combined rows: 720
- Production DB path: `T:\thai-ecommerce-agent\data\orders.db`
- Production DB unchanged: `True`
- Secret scan: performed by independent verifier; no credential values were recorded.

## Artifacts

- `T:\thai-ecommerce-agent\evaluation\final_revision_2026\environment\git_state.txt`
- `T:\thai-ecommerce-agent\evaluation\final_revision_2026\environment\python_version.txt`
- `T:\thai-ecommerce-agent\evaluation\final_revision_2026\environment\platform.txt`
- `T:\thai-ecommerce-agent\evaluation\final_revision_2026\environment\pip_freeze.txt`
- `T:\thai-ecommerce-agent\evaluation\final_revision_2026\environment\configuration_snapshot.json`
- `T:\thai-ecommerce-agent\evaluation\final_revision_2026\environment\source_hashes.json`
- `T:\thai-ecommerce-agent\evaluation\final_revision_2026\scenarios_120_frozen.json`
- `T:\thai-ecommerce-agent\evaluation\final_revision_2026\monolithic_system_prompt.txt`
- `T:\thai-ecommerce-agent\evaluation\final_revision_2026\monolithic_context_inventory.md`
- `T:\thai-ecommerce-agent\evaluation\final_revision_2026\run_1`
- `T:\thai-ecommerce-agent\evaluation\final_revision_2026\run_2`
- `T:\thai-ecommerce-agent\evaluation\final_revision_2026\run_3`
- `T:\thai-ecommerce-agent\evaluation\final_revision_2026\policy_manual_review.csv`
- `T:\thai-ecommerce-agent\evaluation\final_revision_2026\final_metrics_summary.json`
- `T:\thai-ecommerce-agent\evaluation\final_revision_2026\final_repeated_evaluation_report.md`
- `T:\thai-ecommerce-agent\evaluation\final_revision_2026\verify_final_revision.py`
