# September 7 research analysis association failure

The published research briefing reported shuffled title/summary metadata. This
was a real source association failure, not a frontend rendering defect.

## Evidence

The publishing run was GitHub Actions `34099766517`, committed as `f695a4eb`.
Its `pipeline-diagnostics` artifact contains original gathering and analysis
checkpoints, LLM replay responses, and the final orchestrator result.

- Research collected 476 items in seven map batches of up to 75 items.
- The logs marked all seven batches successful.
- The final report contains 146 research items with the exact fallback reasoning
  `Not analyzed (batch processing)`. Their summaries are shortened abstracts and
  their initial scores are the fallback value of 30, not LLM analysis.
- Social and Reddit also contain 7 and 14 such fallback entries respectively.
- `7cadf1af16b3` has MaxKernel's correct title/abstract but a summary about market
  risk. `b32b8cb0daf0` has a financial fine-tuning paper's metadata but MaxKernel's
  summary. `9651c52fbe59` has an automated program repair paper's metadata but a
  summary about multilingual sparse autoencoders.
- Original model output already contains those wrong pairings. Several batches
  include IDs absent from their source batch, omit expected IDs, or repeat IDs.
- A lexical comparison flags 19 strong research mismatch candidates; this is a
  diagnostic lead, not a semantic certification of the remaining 457 entries.
- All 476 published research source IDs, titles, abstracts, and URLs exactly
  match the saved gathering checkpoint. Recollection is unnecessary.

## Failure mechanism

The map phase accepted structurally usable JSON without checking source coverage
or association. Merge built one global ID dictionary: a returned ID belonging to
another batch could overwrite that source's analysis. Missing IDs then received
fallback abstracts and low scores. Ranking noticed mismatched titles and
summaries, but turned that observation into reader-facing prose. Topic detection,
executive synthesis, and enrichment consumed the corrupted report, spreading the
problem to rankings and links (including two incompatible claims linked to
MaxKernel).

## Safeguards

All shared map analyzers now require exactly one result per supplied source ID,
with an echoed `source_title` matching apart from outer whitespace, nonempty
summary/reasoning, and a numeric
score. They validate identities before schema sanitization. Smaller batches
reduce association errors; invalid responses split into progressively smaller
requests. Single-source requests and exhausted transient provider failures retry
with exponential backoff capped at 60 seconds. Cancellation and nonretryable
configuration/authorization failures remain interruptible/fatal.

The merge step independently refuses duplicate, missing, or extra IDs.
`AnalysisIntegrityError` escapes the orchestrator's empty-category fallback, so
failure cannot overwrite a publication with missing analysis. Validated batches
are cached under `data/checkpoints/<date>/analysis_batches/<category>/`, keyed by
source context, prompt, grounding, and model. The existing diagnostics artifact
includes this checkpoint tree. To resume on another host/runner, restore it first.

Environment settings:

| Setting | Default | Behavior |
| --- | --- | --- |
| `ANALYZER_IDENTITY_BATCH_SIZE` | `25` | Upper cap alongside `ANALYZER_BATCH_SIZE` |
| `ANALYZER_RESULT_MAX_ATTEMPTS` | `0` | Zero means continue until valid or cancelled; a positive limit fails closed |
| `ANALYZER_RESULT_RETRY_SECONDS` | `5` | Initial retry delay; exponential growth caps at 60 seconds |

A workflow timeout can still terminate a run. Successful batch checkpoints can
be reused, but GitHub does not automatically restore a previous run's artifacts.
These changes protect analysis results; they do not rewrite every gatherer's
HTTP retry policy. Exact IDs/titles protect associations, but cannot guarantee
that every claim in an LLM-written summary is correct.

## Prepared repair

`scripts/repair_analysis.py` defaults to an offline inspection. `--execute`
performs paid reanalysis in a separate staging directory. It uses the saved
sources, redoes the selected category, topics and executive briefing, re-enriches
new text, and rebuilds feeds/search. Other category files, the hero, and original
run replay are preserved. New repair cost/replay diagnostics are saved separately
in the repair work directory. The script never copies outputs to the publication,
commits, pushes, or deploys.

```bash
venv/bin/python scripts/repair_analysis.py \
  --result data/repairs/2026-09-07/original/processed/orchestrator_result_2026-09-07.json \
  --gathering data/repairs/2026-09-07/original/checkpoints/2026-09-07/gathering.json \
  --output data/repairs/2026-09-07/staged-web
```

Add `--execute` to run the repair. The default repair category is `research`;
`--categories research,social,reddit` also rebuilds the other two categories with
known missing analyses. `--max-attempts` bounds retries of incomplete downstream
repair phases; `ANALYZER_RESULT_MAX_ATTEMPTS` separately bounds map calls.

Before publication, inspect the regenerated items against their abstracts,
check internal links and the rendered page, and review the data diff. Do not just
remove the warning from the old report. The original replay represents the
original run; repair calls remain in separate diagnostics rather than rewriting
that historical record.

## Verification status

The user approved and ran the staged repair. All 476 source IDs were retained,
all 476 analyses passed identity checks, and the 146 research fallbacks were
eliminated. An independent lexical comparison found zero strong cross-source
mismatch candidates after repair (19 before); the original 19 candidates were
also reviewed directly. This is association evidence, not a guarantee against
all possible model inaccuracies.

The rendered research page displayed 476 cards without the metadata warning.
Its fetched JSON SHA-256 matched the staged file. The report validator passed.
Review also caught new executive synthesis copying old links, causing
`only_unlinked=True` to skip incomplete executive enrichment. The repair script
now removes internal links from newly generated text before fully enriching it,
while preserving untouched category summaries. The eight remaining executive research references were added using reviewed
source-ID mappings; stripping the links confirmed that the prose was unchanged.
The redundant LLM enrichment request was cancelled after it continued reasoning
for more than ten minutes; its partial cost/replay diagnostics were retained.

Python syntax parsing and `git diff --check` completed. Regression cases were
added in `tests/analysis_identity_test.py` but not executed, per AGENTS.md.

For the user to run:

```bash
venv/bin/python -m unittest tests.analysis_identity_test -v
```
