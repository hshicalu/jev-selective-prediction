# JGLUE JNLI selective prediction protocol

**Status:** protocol draft for review  
**Issue:** [#3](https://github.com/hshicalu/jev-selective-prediction/issues/3)  
**Protocol frozen:** before any JNLI test request  
**Evaluation date:** not yet run

## Question

For Jev's three-way Japanese natural language inference decision, what accuracy and coverage are obtained when automatic decisions are accepted only above a confidence threshold selected on the development split?

This is a selective prediction evaluation. It is not a reproduction of broad Japanese benchmark comparisons, and it does not establish performance on production traffic.

## Dataset and split policy

Use the official [Yahoo Japan JGLUE repository](https://github.com/yahoojapan/JGLUE), task JNLI:

- Train: 20,073 examples; not used in this experiment.
- Dev: 2,434 examples; use for prompt/schema validation, threshold selection, and analysis.
- Test: 2,508 examples; make one locked final run only after this protocol, prompt, implementation, and thresholds are frozen.
- Labels: `entailment`, `contradiction`, `neutral`.

The current official README identifies JNLI as a Japanese NLI task, gives these split sizes, states that test is released, and lists the repository license as CC BY-SA 4.0. The dataset is derived from the Japanese MS COCO Caption Dataset / YJ Captions Dataset; attribution and share-alike obligations apply. Do not commit or publish JNLI payloads in this repository or the article. At retrieval, record the upstream repository commit (or release/version if supplied), exact source file paths, retrieval date, and SHA-256 checksums for downloaded files. Preserve upstream attribution in any derived public outputs and have the reuse/redistribution interpretation reviewed before redistributing adapted examples. The current branch name is `main`; its commit must be recorded when downloading. The source did not identify a distinct numbered JNLI data release in the README checked on 2026-10-08.

## Frozen request design

One request per example, one `choice` question, with the premise and hypothesis as the state. Use the official JNLI `sentence1` as premise and `sentence2` as hypothesis. Do not include gold labels or dataset split/row identifiers in the request.

- Model request ID: `jev-latest`; record the exact resolved `model` returned by each response. This is a moving alias, so the run is tied to its recorded resolution and execution date.
- API route: Jev Model hosted decision API, `POST https://jevmodel.org/v1/systemone`, Bearer authentication.
- Question name: `relation`.
- Question type: `choice`.
- Instructions (fixed Japanese text): 「前提文と仮説文の意味関係を判定してください。前提文が真であるとき、仮説文が必ず真なら entailment、仮説文と両立しないなら contradiction、どちらとも言えないなら neutral を選んでください。文に明示されていない情報を補わず、最も適切な関係を1つ選んでください。」
- Options, in this order:
  1. `entailment`: 「前提文が真なら、仮説文も必ず真である」
  2. `contradiction`: 「前提文が真なら、仮説文は成り立たない」
  3. `neutral`: 「前提文だけでは、仮説文が真か偽か決まらない」
- No few-shot examples, demonstrations, extra questions, or prompt variants.
- Record exact serialized request, prompt and option hashes, model request ID and resolved ID, API base URL/path, client/runtime version, UTC start time, local date/time zone, and run identifier. If the API contract or endpoint changes before execution, stop and revise/version this protocol before any test request.

The instructions/options above are the proposed frozen wording. Any edits must happen before test access and require a new protocol revision. Smoke checks must use a synthetic, non-JNLI example to confirm schema/field semantics and must not influence wording based on test behavior.

## API response semantics and confidence

The currently checked Jev documentation describes `choice` answers as a selected label, a probability map over the configured options, and a `confidence` field; its example sets confidence equal to the selected option's probability. The documentation also says a 200 response includes the resolved model and usage. Do not assume that the `confidence` field means probability of correctness.

For each valid response, store the raw response with secrets/headers removed and extract:

- `prediction`: returned selected choice;
- all three returned option probabilities, keyed by label;
- `top_choice_confidence`: maximum of the three returned probabilities;
- `returned_confidence`: API confidence field, kept separately for validation;
- `probability_sum`, `confidence_matches_max_probability`, and schema validation outcome;
- resolved model, input/output tokens if present, response latency, HTTP/API status, and retry count.

A valid model response is a successful API response with all expected fields, labels, finite probabilities in [0,1], a recognized predicted choice, and a probability for each of the three choices. The returned choice must be a maximum-probability option; if maximum probabilities tie, the returned choice may be any of the tied options. Do not silently renormalize. If probabilities are malformed, probabilities do not sum to 1 within absolute tolerance 0.01, the returned confidence differs from the maximum probability by more than 0.01, or the response is missing required fields, mark it as an invalid response and exclude it from accuracy ranking while reporting its count/rate. Investigate the API contract before the full run if the synthetic smoke response violates these checks.

Persist one row per example including split, stable source row key (not sent to API), gold label, prediction, all probabilities, derived confidence, returned confidence, latency, request timestamp, HTTP/API status, retry history, resolved model, token usage, and computed cost when the provider exposes enough information. Also keep a run manifest with prompt/schema hashes, source version/checksums, API route, package/runtime versions, start/end dates, and exclusions. Never store the API key or authorization headers. Separate network/API failures and invalid responses from valid model predictions; they are not model errors and are never assigned a guessed label.

## Retry and request policy

Use one in-flight request per example and a maximum of three total attempts for transient transport errors, HTTP 408, 429, and 5xx, with exponential backoff and jitter. Do not retry other 4xx responses. Record each attempt's status and latency. Use a stable idempotency key per split, row key, and attempt if the current API contract supports it; otherwise do not invent unsupported headers. Avoid accidental duplicate billing: confirm current idempotency behavior before the run.

The full run is 2,434 dev requests plus 2,508 test requests: **4,942 successful-example requests planned**, plus at most two retries per failed example. Run dev first, inspect completion and cost, freeze the analysis and thresholds, then execute test exactly once. A test retry is permitted only to recover a failed request for the same example; never repeat a successful test request for analysis or prompt iteration. Report valid-response denominator and failure/invalid-response counts for each split.

## Cost and pre-run gates

The current API docs state that input tokens are billed, output is free, and suggest estimating input tokens as `ceil(JSON.stringify({state, questions}).length / 4)`. Use the current price from the same API account's billing/dashboard on the execution date; public pages checked on 2026-10-08 showed inconsistent provider/price claims, so no monetary estimate is asserted here.

Before any paid/sizable run:

1. Configure the key as `JEV_API_KEY` in the process environment; read it only from the environment and never display, save, or commit it.
2. Run 3–5 synthetic schema smoke requests; record response schema, resolved model, latency, usage, and billed tokens/cost. Stop on contract mismatch.
3. Download the official dev split outside the repository and record its source commit, paths, and SHA-256 checksums. Do not send any JNLI text to Jev before confirming that the current API provider's terms permit processing and retention of this publicly licensed dataset.
4. Estimate dev and total input tokens from the exact serialized requests (or a measured dev sample), then estimate cost at the current account rate, including retry reserve. Record these estimates and the actual smoke charges in Issue #3 before proceeding.
5. Do not start the full dev run until estimates are recorded in Issue #3. Do not start the test run until dev results and frozen thresholds are reviewed and its separate estimate is recorded.

As of protocol drafting, `JEV_API_KEY` is not present in the execution environment. Therefore no smoke request, provider price confirmation, or monetary estimate has been made. Full-run execution is explicitly out of scope until those gates are satisfied.

## Selective metrics and pre-registered threshold rule

The estimand is performance among valid model responses. Report API/invalid-response rate separately against all source examples so failures cannot inflate the model's apparent performance.

For each example (i), define (c_i = \max_k p_{ik}), where (p_{ik}) is Jev's returned probability for option (k). Define correctness (z_i = 1[\hat y_i = y_i]). For threshold (t), accept examples with (c_i \ge t). Coverage is accepted valid responses divided by all valid responses. Accepted-set accuracy is correct accepted responses divided by accepted valid responses.

For each target (q \in \{0.90, 0.95, 0.99\}), choose on dev the threshold that maximizes coverage among thresholds whose one-sided 95% exact Clopper–Pearson lower confidence bound for accepted-set accuracy is at least (q). If no non-empty accepted set qualifies, report zero achievable coverage and “no qualifying threshold”. Evaluate the selected numeric threshold unchanged on test. Test must not be used to select, adjust, or round thresholds.

Threshold candidates are all distinct dev (c_i) values. Accept at equality ((c_i \ge t)). To handle equal confidence values, include the entire tied group; never split ties by row order. If multiple thresholds yield the same maximum coverage, select the lowest numeric threshold (they should define the same accepted set except for edge representation). Freeze thresholds as full-precision decimal values before test.

On dev and test, report for each target: selected threshold, accepted count/valid-response denominator, coverage, accepted-set accuracy, correct count, and two-sided 95% exact Clopper–Pearson interval for accepted-set accuracy. Also report full-set accuracy over valid responses; coverage/accuracy curves; number/rate of failures and invalid responses; per-class precision, recall, F1, support, and confusion matrix over valid responses; accepted-set per-class metrics at each threshold; and macro-F1.

### Confidence interval interpretation

Use exact Clopper–Pearson binomial intervals for accepted-set accuracy. They quantify binomial sampling uncertainty conditional on the accepted set and a fixed threshold; they do not account for threshold selection uncertainty, model/version drift, source-data shift, or benchmark contamination. Because dev is used to choose thresholds, treat dev intervals as descriptive. Test intervals are the final uncertainty report for the locked thresholds. In addition, report the accepted count so readers can judge interval width. Do not describe any finite test set as proof of a guaranteed future accuracy level.

## Supporting calibration analysis

On valid dev and test responses, report a reliability diagram, Brier score and log loss for the three-class probability vectors, and expected calibration error using 10 equal-width confidence bins on `c_i` versus correctness `z_i` (include bin counts). These are secondary descriptive analyses. Report unre-normalized raw probabilities and note that any calibration claim is limited to this dataset, prompt, API route, and resolved model version. Do not tune probabilities on test.

## Test-set lock procedure

Before sending any JNLI test text to the API:

1. Commit this protocol and the runner/schemas to a reviewable PR.
2. Complete dev run, resolve schema and retry issues, produce dev thresholds, and freeze prompt, code revision, model choice, threshold values, and analysis script.
3. Record the test request/cost estimate and test manifest checksum in Issue #3.
4. Have a second reviewer confirm that the test split has not been loaded, viewed, or queried and that all choices are frozen.
5. Execute test once with the frozen artifacts. Keep test predictions hidden from any prompt/threshold adjustment. Any later test run must be labeled a new exploratory evaluation and cannot replace this primary result.

## References checked (2026-10-08)

- [Official JGLUE README](https://github.com/yahoojapan/JGLUE) — split sizes, label/task description, test availability, license, source description.
- [Official JGLUE task guidelines](https://github.com/yahoojapan/JGLUE/blob/main/task_guidelines.md) — annotation procedure.
- [Jev Model API documentation](https://jevmodel.org/docs/) — endpoint, choice response fields, usage, current request limits, and cost estimation guidance. The API wording and price must be rechecked on execution date.
- [Jev API examples](https://jevmodel.org/api/) — example response shape and confidence/probability semantics.
- Existing broad comparison for scope context: [IVRy Zenn article](https://zenn.dev/ivry/articles/65c1c4242e1156).
- Business evaluation context: [Speaker Deck](https://speakerdeck.com/gotalab555/jev-o-gyoumu-o-ireru-madeni-yaru-koto).
