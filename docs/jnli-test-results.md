# JGLUE JNLI exploratory test results

**Evaluation type:** exploratory, non-blind
**Run date:** 2026-10-10 (Asia/Tokyo)
**Protocol:** [frozen JNLI selective prediction protocol](jnli-selective-protocol.md)
**Tracking:** [Issue #13](https://github.com/hshicalu/jev-selective-prediction/issues/13)

## Summary

The test split contains 2,508 examples. The runner received 2,508 HTTP 200 responses with no retries. One response failed probability-sum validation and was excluded from model metrics, leaving 2,507 valid predictions. Full-set accuracy on valid responses was **84.52%** (2,119/2,507), with macro-F1 **83.29%**.

The dev-frozen thresholds did not meet their intended test accuracy targets:

| Dev target | Frozen threshold | Accepted | Coverage¹ | Correct | Accepted accuracy | Exact two-sided 95% CI |
|---|---:|---:|---:|---:|---:|---:|
| 90% | 0.69 | 2,183 / 2,507 | 87.08% | 1,935 | 88.64% | 87.23–89.94% |
| 95% | 0.92 | 1,558 / 2,507 | 62.15% | 1,462 | 93.84% | 92.53–94.98% |
| 99% | No qualifying dev threshold | 0 / 2,507 | 0% | — | — | — |

¹ Coverage denominator is valid responses (n=2,507). The single invalid response is reported separately and is not treated as a model error.

The 90% and 95% targets were not reached at their frozen thresholds. These figures are test performance at thresholds selected on dev; test was not used to find replacement thresholds.

## Run conditions

- Dataset: official JGLUE JNLI v1.3.0, test split, 2,508 examples.
- Source: [Yahoo Japan JGLUE](https://github.com/yahoojapan/JGLUE), pinned commit `6f071c09316baae89c3d083a90985b4b1cb9968c`, file `datasets/jnli-v1.3/test-v1.3.json`.
- Local source SHA-256: `f40849584d4291ec172dcd869646800cc8c82457cde2260a0f45934eda307792`.
- License: the official repository identifies CC BY-SA 4.0. No benchmark sentences or payloads are reproduced in this repository or the companion article.
- Test-data access: the file was downloaded and parsed before the second-review gate to validate its shape; part of the text and labels also appeared in retrieval-tool output. This run is therefore **non-blind and exploratory**, not an untouched held-out evaluation. No test API requests or metric analysis happened before the run. This disclosure is part of every interpretation of the results.
- Frozen prompt/options and scoring: unchanged from the protocol; the protocol SHA-256 used by the run was `b8d8cdf3a46dffac07fdad3d7d409ce53f546ee3005305fced10136485abff58`.
- Model: requested `jev-latest`, resolved `jev-1.13.0` for all 2,508 responses.
- API: TypeSafe `POST https://api.typesafe.ai/v1/systemone`.
- Run ID: `37b8cf2e-2973-4dc9-9501-31b3de51d4bd`.
- Runtime: 2026-10-10 09:52:37–10:02:10 JST (00:52:37–01:02:10 UTC).
- Code commit: `c93187d06f9474a8e08f14393fa020bd9a406e15`.
- Frozen dev thresholds: 0.69 for the 90% target; 0.92 for the 95% target; no qualifying threshold for the 99% target.

## Response and cost accounting

All 2,508 requests returned HTTP 200 and were attempted once. One response had probabilities that did not sum to one within the pre-registered tolerance, so it was marked invalid and omitted from accuracy, coverage, and calibration metrics. There were no network/API failures or retries.

The run reported 1,387,315 input tokens and 107,236 output tokens. At the published input price of $0.042 per million tokens and free output, the token-derived charge estimate is **$0.058267** (about $0.0583). This is below the $0.18 reserve value passed to the runner. The runner checks the projected three-attempt reserve before starting; it does not enforce a provider-side hard billing cap or stop dynamically when observed spend approaches that value. This estimate is calculated from returned usage, not a billing-console receipt, and actual account billing should be verified in TypeSafe Usage. Pricing was rechecked on 2026-10-10; see the [official pricing announcement](https://typesafe.ai/blog/introducing-system-one-models-and-jev).

## Full-set class results

| Gold class | Support | Precision | Recall | F1 |
|---|---:|---:|---:|---:|
| entailment | 367 | 83.99% | 81.47% | 82.71% |
| contradiction | 776 | 92.42% | 70.75% | 80.15% |
| neutral | 1,364 | 81.63% | 93.18% | 87.02% |
| Macro-F1 | 2,507 | — | — | 83.29% |

The full valid-response confusion matrix (rows are gold labels, columns are predictions):

| Gold \\ Predicted | entailment | contradiction | neutral |
|---|---:|---:|---:|
| entailment | 299 | 5 | 63 |
| contradiction | 4 | 549 | 223 |
| neutral | 53 | 40 | 1,271 |

## Accepted-set class results

### Threshold 0.69

| Gold class | Accepted support | Precision | Recall | F1 |
|---|---:|---:|---:|---:|
| entailment | 310 | 90.33% | 87.42% | 88.85% |
| contradiction | 624 | 96.43% | 73.56% | 83.45% |
| neutral | 1,249 | 85.64% | 96.48% | 90.74% |
| Macro-F1 | 2,183 | — | — | 87.68% |

### Threshold 0.92

| Gold class | Accepted support | Precision | Recall | F1 |
|---|---:|---:|---:|---:|
| entailment | 210 | 96.60% | 94.76% | 95.67% |
| contradiction | 352 | 100.00% | 77.84% | 87.54% |
| neutral | 996 | 91.74% | 99.30% | 95.37% |
| Macro-F1 | 1,558 | — | — | 92.86% |

## Supporting calibration analysis

The registered score is the maximum returned option probability (`top_choice_confidence`), not a probability that the answer is correct. On 2,507 valid responses, the multiclass Brier score was **0.2305**, multiclass log loss **0.6327**, and 10-bin expected calibration error **0.0457**. In the 0.90–1.00 score bin (n=1,639), mean top-choice confidence was 0.9767 and observed accuracy was 93.29%. This dataset- and run-specific observation does not establish calibration on other data or future traffic.

## Error review

There were 388 valid-response errors. The largest confusion was gold `contradiction` predicted as `neutral` (223 cases, 57.5% of errors). At the 0.92 threshold, 78 of 96 accepted errors had this pattern. A spot check of eight accepted errors with score 1.00 found examples involving generic versus specific descriptions of an activity and scene/context inferences. Several gold contradiction pairs appeared compatible when considered from sentence text alone. This small qualitative review does not establish annotation errors; it shows that some label distinctions were difficult for this prompt/model combination. No benchmark sentence text is quoted or copied here.

## Interpretation and limits

- This is a single exploratory run on JGLUE JNLI v1.3.0 using one resolved Jev model, one prompt, and the TypeSafe API route. It is not a production guarantee or broad Japanese benchmark comparison.
- Because the test file and some content were accessed before measurement, the result is not a blinded final estimate. The access was disclosed before the API run; thresholds and scoring were held fixed from dev.
- The accuracy intervals are exact Clopper–Pearson intervals conditional on each accepted set and fixed threshold. They do not account for model/version drift, distribution shift, test-set exposure, or benchmark contamination.
- The 99% target had no dev-selected qualifying threshold. Zero coverage for that target means no test result was reported for a threshold; it does not mean a 0% accuracy estimate.
- Confidence values are ranking scores. Calibration metrics are supplementary and do not establish a probability of correctness guarantee.
- The estimated cost is calculated from API-reported input-token usage and the published price; actual account billing should be checked in TypeSafe Usage.
- The local per-example outputs remain under ignored `.local/jnli-test/`. No API key, benchmark payload, or source sentence is committed.

## References

- [Official JGLUE repository and license](https://github.com/yahoojapan/JGLUE)
- [Frozen evaluation protocol](jnli-selective-protocol.md)
- [Official TypeSafe pricing announcement](https://typesafe.ai/blog/introducing-system-one-models-and-jev)
- [TypeSafe Master Customer Agreement](https://typesafe.ai/legal/mca)
