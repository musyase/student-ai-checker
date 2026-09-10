# Exact source references for checked-scoring

Status: implemented and unit-tested; live model quality is not yet validated.
Stage 1 grading quality remains open. No GUI or desktop packaging is introduced.

## Why

In the latest eight-case live text run, six cases passed. In the two failed cases,
the proposed component verdicts matched the expectations, but retyped quotations
contained changed words. Strict quote validation correctly refused them. Accepting
approximate quotations or weakening the matching rule would be the wrong repair.

## What changes

`check --checked-scoring` now calls `check_text_references` for its five component-
based text criteria. The existing letter matching, visual v3 delegate, approval
hash, rubric, partial-visual-alternative guard and score calculations are unchanged.

The model chooses `source_refs`, such as `S000001`, from a catalog of original
paragraphs and table cells. It does not generate quotation strings. Python resolves
each selection to the original evidence ID and an exact source slice; spelling,
case, punctuation and whitespace are preserved. Long sources are partitioned into
successive slices of at most 6000 Unicode code points, without dropping characters.
Catalogs over the explicit limit are refused, never truncated.

The schema and local validation reject unknown references, extra score fields,
extra/missing/duplicate checks, and duplicate references. Metadata and images cannot
be selected as text. The catalog, source hash, offsets, raw responses and resolved
citations are retained in the local audit. `method=source_reference_components`,
`reference_contract_version=1`, `citation_origin=application_source_slice`.

A genuine citation still does NOT prove the model interpreted it correctly or
selected the relevant paragraph. Free-form reasons remain `model_reason_unverified`;
student_feedback remains blank for this text path. Numeric points are calculated
by the existing code. Valid uncertainty is not changed to a score or retried.
Only one structural repair is allowed; transport failures are not retried.
Missing text is NOT automatically assigned zero. Extraction/provenance failures
remain unresolved. This change does not fix all semantic or visual limitations.

## Tests and compatibility

`tests/test_source_references.py`: 66 passed in the Linux Python 3.13/Pydantic 2.13.4/
pytest 9 workspace. Same tests passed against both the repository's checked_text
and that file with the previous local partial-fix patch applied. The dependency
models, scoring helper and original modified files were checked against Git blob
hashes. Source-selection tests use explicit model doubles; the controller test
also uses an explicit visual double. No real Ollama was run here. Full CLI import,
full repository regression suite and Windows/macOS execution are not claimed.

The existing eight live cases, fixtures, expected scores and assertions stay the
same; only their implementation import changes to the reference path. The existing
controller unit test's mock response is adapted to that new contract; expectations
are unchanged. Both legacy quotation helpers and their tests remain available.
The files edited by the earlier local partial-fix patch are NOT overwritten here.

## Next validation

Run all non-integration tests, then the same eight live text controls with the same
text model used in the immediately preceding comparison. Preserve all cases, not
just passed ones. Inspect explanations as well as totals. After that, add held-out
paraphrases and mixed answers and test complete anonymized submissions against a
teacher reference. An eight-case pass alone cannot close the quality stage.

Ollama schema API reference: https://docs.ollama.com/capabilities/structured-outputs
