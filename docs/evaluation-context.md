# Context-preserving component checks

Status: a preparation-of-requirements defect has been corrected in code. The
semantic quality of this correction has NOT yet been validated with live Ollama.
The grading-quality stage remains open; this change does not add a new CLI mode.

## Evidence and diagnosis

The latest Windows log after a5a5999 reports 492 non-integration tests passed and
6/8 live text controls passed. The two failing cases contain complete source
references, no validation errors, and semantically wrong component decisions.

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
writing that there was none.

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
`evaluation_context_version=1`, `reference_prompt_version=2`, `evaluation_checks`,
`evaluation_checks_sha256`. The reference response contract remains version 1.

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

Locally executed on Linux, Python 3.13.5 / pytest 9.0.2:

```
python -m pytest tests/test_evaluation_context.py -q
47 passed
```

These are tests of the real stdlib compiler, source-fragment/profile checks,
request/audit construction, and the stronger assertion helper. Three tests execute
the actual request function in isolation until a mocked transport failure;
extraction and error-result construction there are explicitly isolated doubles.
They do NOT test real HTTP, a model's meaning judgments, the complete scoring loop,
or full CLI import. The changed existing source files were reconstructed and
verified against their original Git blob hashes before editing. Compileall passed.
Full repository regression tests, Windows/macOS, the sixteen live-model controls
and actual student grading have NOT been executed here with this correction.

## Validation on Windows

Update with `git pull --ff-only`, run all non-integration tests, then run the same
`tests/integration/test_checked_text_real.py` using the already installed qwen3:8b
for the text comparison. The module now collects 16 cases. Use a fresh artifact
folder, preserve all cases, and inspect the complete new text-controls journal.
Do not rerun until a lucky pass; do not run a student report on the text-only model.
No student DOCX, names, images, source answers or diagnostic JSON are committed.
