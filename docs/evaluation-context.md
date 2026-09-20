# Context-preserving component checks

Status: the active source-reference route has been corrected and validated with
the local Ollama integration suite. The grading-quality stage on real student
submissions remains open; this change does not add a new CLI mode.

## Evidence and diagnosis

The current Windows run reports 571 non-integration tests passed and 20/20 live
integration controls passed. The controls cover the source-reference route,
CLI/Ollama integration, visual review and sixteen text cases.

For usability, the fixed component `requirement` was only the noun phrase naming
a problem, for example "слишком мелкий текст задач". The full parent criterion
and a general instruction about proposing a fix were also in the prompt, but the
per-component field did not retain the operation "осмысленное исправление".
The model treated worsening/retaining a problem as satisfying a requirement.
This is a concrete ambiguity introduced by our component rendering. The log does
not prove that this is the only possible cause of semantic errors.

For the partial installation report, the model confused the pre-existing state
with a reached stage and speculated about an unreported difficulty. The approved
criterion requires a reached stage; the problem/follow-up requirement is conditional
on a difficulty occurring. It does not require inventing a difficulty or always
writing that there was none. The compiled context now explicitly separates a
reported pre-existing installation from later actions and outcome.

The add-button criterion also has an explicit context: a concrete, understandable
policy for the input field is enough; clearing the field is allowed but is not the
only acceptable option.

## Correction

`evaluation_context.py` compiles these two criteria into standalone conditions.
For each usability item the operation is evaluating a proposed fix, and the
requirement explicitly includes the inherited repair action as well as the original
problem. Retaining or worsening the problem is not an improvement. Alternative
meaningful remedies are still allowed; no single example answer is mandatory.

The report items identify initial state, own actions, and reached stage separately.
The reached-stage condition retains the conditional problem/follow-up clause.
A described interruption can be a reached stage. A statement about initial state
alone does not imply actions or their result. No new requirement to repeat actions
or to complete installation successfully is added.

The compiler is bound to exact parent-description hashes and the original component
IDs, source fragments, and one-point weights. Changed supported tasks fail before
inference rather than receive old semantics. Unknown task IDs retain their original
requirements without these PR1-specific interpretations. The normal checked-scoring
controller still verifies the full approved rubric and template hash.

`referenced_text.py` sends the compiled checks and stores them in its local audit:
`evaluation_context_version=4`, `reference_prompt_version=6`, `evaluation_checks`,
`evaluation_checks_sha256`. The reference response contract remains version 1.
For the approved add-button criterion, an explicit same-sentence admission that a
list/count or input behavior is not described is also recorded as a narrow
`semantic_guard_version=1` override: the model verdict remains in
`model_verdict`, while the effective component verdict becomes `not_met`.

The approved 25-point rubric, PARTS arithmetic profile, source catalog, generated
response schema, source-reference resolution, point calculation, retries and
visual delegate are unchanged. In particular, a valid uncertain answer is NOT
converted to not_met and is NOT retried for a desired score. A genuine source
reference still does not prove the model understood the source correctly.
The old local partial-fix patch files are not modified by this update.

## Tests

The original eight real-model inputs and expected totals remain unchanged. Their
assertions now also require correct per-component verdicts and points, so two
compensating mistakes cannot pass via the same total. Eight additional synthetic
contrastive development controls cover wording variants, mixed correct/incorrect
fixes, initial state without a result, a full report without an explicit "no problems"
sentence, an interrupted installation, and allowed input-field behavior.
These are development controls, NOT an independent acceptance dataset. Their
expected values and sample answers never enter production prompts or source code.
A whole run writes one text-controls JSON including failed cases as before.

Locally executed on Windows, Python 3.14.3 / pytest 8.4.2:

```
python -m pytest -m "not integration" -q
571 passed, 20 deselected
```

These are tests of the real stdlib compiler, source-fragment/profile checks,
request/audit construction, and the stronger assertion helper. Three tests execute
the actual request function in isolation until a mocked transport failure;
extraction and error-result construction there are explicitly isolated doubles.
They do NOT test real HTTP, a model's meaning judgments, the complete scoring loop,
or full CLI import. The changed existing source files were reconstructed and
verified against their original Git blob hashes before editing. Compileall passed.
The full repository regression suite and all twenty live-model controls pass in
the current Windows environment. Actual quality on student submissions remains
unvalidated and requires a separate anonymized, teacher-scored acceptance set.

## Validation on Windows

For the next quality stage, use a fresh artifact folder, preserve all control
cases, and inspect the complete text-controls journal. Do not use these synthetic
controls as a substitute for real grading. No student DOCX, names, images,
source answers or diagnostic JSON are committed.
