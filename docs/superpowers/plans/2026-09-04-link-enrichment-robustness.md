# Link-enrichment robustness (2026-09-04)

## Why

The 2026-09-04 report published with an executive summary and three of four category
summaries carrying **zero internal links**. Phase 4.5 ran 11 enrichment calls against
`google/gemini-3.8-flash` over OpenRouter (`mode: openai-chat`):

- 3 calls (executive, social, news summaries) died to an **in-band SSE error chunk**
  `{"error": {"code": 504, "message": "Upstream idle timeout exceeded"}}` after
  139s/219s/256s of reasoning, 0 text chars, `attempt: 1`. They were **never retried**:
  `_openai_chat_apply_chunk` (`agents/llm_client.py:352-364`) raises a bare `RuntimeError`,
  and `_transient_retry_reason` (`agents/llm_client.py:193`) classifies only by exception
  type or a `status_code` attribute, so the transport retry loop (`agents/llm_client.py:1784`)
  re-raised without spending any of its 6-attempt/900s budget. The docstring at line 355
  claims the opposite.
- 1 call (reddit summary) hit the model's hard 65,536 completion cap: 79,784 chars of
  reasoning + clipped JSON, `stop_reason: max_tokens`. `agents/link_enricher.py:349-373`
  ignores `stop_reason` and tries to parse the clipped JSON; the regex fallback then
  failed validation. The analyzers already handle truncation
  (`agents/base.py:967` `_handle_truncated_batch`, reduce escalation at `:1386`).
- The run published because `scripts/validate_report.py:158` classifies `degradations`
  as warnings (deliberate, 2026-08-24). That policy stays. What is missing is retry,
  truncation handling, and a way to **repair** a degraded Phase 4.5 afterwards:
  `agents/orchestrator.py:459` loads the executive summary only when `resume_from > 4.5`,
  so `--resume-from 4.5` today regenerates the executive summary, re-enriches
  already-linked texts, and regenerates the hero.
- CI days are not repairable at all: `data/checkpoints` dies with the runner; only
  `llm_metrics.jsonl`, `cost_report_*.json` and `orchestrator_result_*.json` are uploaded.
- Side finding: `agents/cost_tracker.py` has no Gemini text entry, so the run was priced
  at the Opus fallback ($28.31 published vs ≈$4.23 at $0.75/$3.75 per MTok).

## Global constraints

- Python 3.11, stdlib `unittest`. Every test must run with
  `source venv/bin/activate && python3 -m unittest tests.<module> -v` from the repo root,
  with **no network, no LLM/API calls, and never by running `run_pipeline.py`**.
- New test modules are registered in `.github/workflows/tests.yml` under the
  `pipeline-contracts` job (its dependency list covers `agents/__init__` imports), each as
  its own `- name:` step with a dated one-line comment saying which outage it guards.
- Retry semantics are fixed: **every retry attempt is an independent call** with its own
  replay span, cost row, semaphore slot and rate token. Never retry inside
  `_create_message` or `_stream_message_openai_chat`; retries live in the transport loop
  (`agents/llm_client.py:1770-1876`) or in the caller.
- The replay artifact must never receive prompt text. Do not add logging of prompt or
  item content.
- Commit only the paths you changed (`git add <path>…`, never `git add -A`). An untracked
  `tests/hero_image_cost_test.py` from another session exists in the tree — leave it.
- Do not touch `config/providers.yaml` (git-ignored, holds secrets), anything under
  `web/data/`, or `data/`.
- Commits are SSH-signed automatically by git config. End every commit message with the
  trailer line `Claude-Session: https://claude.ai/code/session_01HdRcSt5QuRKhSCwUwRfUD6`.
- Match the surrounding code's voice: comments explain *why*, cite dates and incidents
  the way the existing code does (see the comments around `agents/llm_client.py:1770`).
- Existing public signatures keep working; new parameters get defaults.

## Operational follow-up (controller, not a task)

After the tasks merge, repair 2026-09-04 locally:

1. `python3 scripts/checkpoints_from_result.py <artifact>/processed/orchestrator_result_2026-09-04.json --data-dir ./data`
2. `TARGET_DATE=2026-09-04 python3 run_pipeline.py --resume-from 4.5 --config-dir ./config --data-dir ./data --web-dir ./web`
3. Verify `web/data/2026-09-04/summary.json` now has internal links in the executive,
   news, social and reddit summaries; restore `web/data/2026-09-04/replay-*.json*` from git
   (the repair run cannot rebuild the original replay — spans are memory-only); commit
   the data outputs; push.

---

## Task 1: Retry OpenRouter in-band stream errors

**Files:** `agents/llm_client.py`; `tests/openrouter_stream_error_retry_test.py` (new);
`.github/workflows/tests.yml`; `CLAUDE.md`.

### Change

1. Add, next to `_openai_chat_apply_chunk` in `agents/llm_client.py`:

   ```python
   class OpenRouterStreamError(RuntimeError):
       """An error object OpenRouter delivered inside the SSE stream.

       Carries the chunk's `code` as `status_code` so `_transient_retry_reason`
       can classify it exactly like an HTTP status: 429 and 5xx retry, other
       4xx fail fast. On 2026-09-04 three link-enrichment calls died to
       `{"error": {"code": 504, "message": "Upstream idle timeout exceeded"}}`
       after minutes of reasoning and were never retried, because the bare
       RuntimeError raised here had nothing the classifier recognised.
       """
       def __init__(self, message: str, status_code: Optional[int] = None):
           super().__init__(message)
           self.status_code = status_code
   ```

2. `_openai_chat_apply_chunk` raises `OpenRouterStreamError`. Parse `code` to `int` when it
   is an `int` or a string of digits; otherwise `status_code=None`. Keep the message text
   `OpenRouter stream error (code={code}): {msg}` exactly (it is grepped in logs).
   Rewrite the function docstring so it states what is actually retryable.

3. `_transient_retry_reason` already reads a `status_code` attribute; confirm it returns
   `"http_504"` for the new exception without further change. If it needs a change, make
   the smallest one.

4. `CLAUDE.md`, section "Multi-Provider LLM Routing", after the sentence
   `429/5xx/timeouts/connection drops retry; other 4xx fail fast.` add one sentence:
   `This includes OpenRouter's in-band SSE error chunks (a mid-stream
   {"error": {"code": 504, ...}} — seen 2026-09-04 as "Upstream idle timeout exceeded"),
   which raise OpenRouterStreamError carrying the chunk code as status_code.`

### Tests (TDD — write them first, watch them fail, then implement)

`tests/openrouter_stream_error_retry_test.py`, module docstring in the style of
`tests/muse_spark_pricing_test.py` (why it exists, what it locks in, how to run):

- `_openai_chat_apply_chunk({"error": {"code": 504, "message": "Upstream idle timeout exceeded"}}, state)`
  raises `OpenRouterStreamError`; it `isinstance` `RuntimeError`; `status_code == 504`;
  `str(err) == "OpenRouter stream error (code=504): Upstream idle timeout exceeded"`;
  `_transient_retry_reason(err) == "http_504"`.
- `{"error": {"code": "429", "message": "rate limited"}}` → `status_code == 429`,
  reason `"http_429"`.
- `{"error": {"code": 400, "message": "bad request"}}` → reason `None`.
- `{"error": {"message": "no code"}}` → `status_code is None`, reason `None`.
- Retry-loop integration, built the way `tests/llm_rate_limit_resilience_test.py` and
  `tests/openai_chat_transport_test.py:266` build a client
  (`AsyncAnthropicClient.__new__` + the attributes the retry loop reads; zero delays):
  patch `_create_message` to raise `OpenRouterStreamError("OpenRouter stream error (code=504): Upstream idle timeout exceeded", status_code=504)`
  on the first call and return a stub response on the second → the retrying wrapper
  returns that response and `_create_message` was called exactly twice. A
  `status_code=400` stream error propagates after exactly one call.

Register the module in `tests.yml` under `pipeline-contracts`:
`# 2026-09-04: in-band OpenRouter 504 chunks killed 3 of 11 enrichment calls, attempt=1.`

---

## Task 2: Truncation-aware link enricher

**Files:** `agents/link_enricher.py`; `tests/link_enricher_truncation_test.py` (new);
`.github/workflows/tests.yml`.

### Change

`LinkEnricher._enrich_text` gains a bounded escalation, mirroring the analyzers' truncation
handling (`agents/base.py:967-1013`, `:1386-1399`):

- Class constant `ENRICH_PROFILES = (ThinkingLevel.STANDARD, ThinkingLevel.QUICK)` — at
  most `len(ENRICH_PROFILES)` LLM calls per text. The `caller` string stays
  `f"link_enricher.{context_name}"` on every attempt (the replay taxonomy keys on it;
  attempts are already independent calls).
- For each profile in order, call `call_with_thinking(..., profile=profile, ...)`:
  - If `response.stop_reason == "max_tokens"`: log a WARNING naming the context, the
    profile, the output length, and that the JSON is clipped. **Do not parse.** Continue to
    the next profile.
  - Else parse as today (`extract_json_str` + `json.loads`). On success return
    `enriched_text` (log links added, as today). On `json.JSONDecodeError`: log, continue
    to the next profile.
- After the last profile:
  - if the last attempt was truncated: `self.degradations.append(f"{context_name}: truncated at max_tokens on every attempt")`, return the original text;
  - if the last attempt was unparseable: run the existing validated regex fallback on that
    response; if it passes validation return it, else append the existing note
    `f"{context_name}: unparseable enrichment response"` and return the original text.
- Any exception raised by `call_with_thinking` itself keeps today's behaviour exactly:
  log, `self.degradations.append(f"{context_name}: {type(e).__name__}")`, return the original
  text, no further attempts (the transport layer owns those retries — Task 1).
- Keep the class docstring's contract about `degradations` true; extend it with one line on
  the escalation.

### Tests (TDD)

`tests/link_enricher_truncation_test.py`. A fake async client whose
`call_with_thinking(**kwargs)` records the `profile` of each call and returns the next
scripted `SimpleNamespace(content=..., stop_reason=...)`. Drive `LinkEnricher.enrich_all`
with `category_reports` as plain dicts (the `_build_item_list` dict path) so at least one
item exists; use `asyncio.run`. A valid enrichment payload is
`{"enriched_text": "...[phrase](/?date=2026-09-04&category=news#item-abc123def456)...", "links": [{"phrase": "...", "item_id": "abc123def456", "category": "news"}]}`.

Cases (each asserts call count, the profiles used in order, the returned text, and
`enricher.degradations`):

1. success first try → 1 call at STANDARD, enriched text, no degradation.
2. `max_tokens` then success → 2 calls (STANDARD, QUICK), enriched text, no degradation.
3. `max_tokens` twice → 2 calls, original text, degradation contains `truncated at max_tokens`.
4. unparseable JSON then success → 2 calls, enriched text, no degradation.
5. unparseable twice, regex-unrecoverable → original text, `unparseable enrichment response`.
6. `call_with_thinking` raises `RuntimeError` → 1 call, original text, degradation `"<context>: RuntimeError"`.

Register in `tests.yml` under `pipeline-contracts`:
`# 2026-09-04: a 65536-cap reddit enrichment reply was parsed as if complete.`

---

## Task 3: Enrichment repair mode (`--resume-from 4.5`)

**Files:** `agents/orchestrator.py`; `agents/link_enricher.py`; `run_pipeline.py`;
`CLAUDE.md`; `tests/enrichment_repair_resume_test.py` (new); `.github/workflows/tests.yml`.

### Change — enricher

`agents/link_enricher.py`: module constant `INTERNAL_LINK_MARKER = "](/?date="`.
`enrich_all(..., only_unlinked: bool = False)`: when True, any text (executive summary,
category summary, topic description) that already contains `INTERNAL_LINK_MARKER` is
skipped — returned unchanged, logged at INFO as skipped, **not** a degradation.

### Change — orchestrator `run()`

Phase 4 / 4.5 become three branches:

```
if resume_from is not None and resume_from > 4.5:
    (existing: load summary checkpoint, restore everything, restore Phase 4 and 4.5)
elif resume_from is not None and resume_from > 4:
    # Repair: keep the executive summary, re-enrich only what still lacks links.
    load summary checkpoint (RuntimeError "Cannot resume: no checkpoint for Phase 4 (summary)" if missing)
    _absorb_replay_bundle(checkpoint)
    executive_summary, summary_thinking, category summaries, top_topics from the checkpoint
      (same restore code the first branch uses — factor it into a helper, do not duplicate)
    _restore_or_skip_phase(phases, "Phase 4: Executive Summary", checkpoint, "loaded from checkpoint (enrichment repair)")
    executive_summary, top_topics = await self._run_link_enrichment(phases, executive_summary, category_reports, top_topics, only_unlinked=True)
    save the summary checkpoint (same payload as today)
else:
    (existing Phase 4)
    executive_summary, top_topics = await self._run_link_enrichment(..., only_unlinked=False)
    save the summary checkpoint
```

`_run_link_enrichment` is the existing Phase 4.5 block (`agents/orchestrator.py:493-524`)
moved into a method, unchanged in behaviour, with `only_unlinked` passed through to
`enrich_all`.

Hero checkpoint:

- After Phase 4.7 produces a result, `self._save_checkpoint('hero', {'hero_image_url': …, 'hero_image_prompt': …, 'hero_image_usage': …})`.
- Before Phase 4.7, when `resume_from is not None and resume_from > 4`: if
  `self._load_checkpoint('hero')` returns a dict with a truthy `hero_image_url`, restore
  the three fields, `_restore_or_skip_phase(phases, "Phase 4.7: Hero Image", hero_checkpoint, "loaded from checkpoint")`,
  and skip generation entirely (this check precedes the `hero_generator`/`hero_topics`
  checks). Otherwise fall through to the existing logic; the existing `resume_from > 4.7`
  else-branch (which today leaves `hero_image_url = None`) stays as the legacy fallback —
  say so in a comment.

`_detect_resume_point` table becomes, in this order:
`('hero.json', 5.0), ('summary.json', 4.6), ('topics.json', 4.0), ('analysis.json', 3.0), ('gathering.json', 2.0)`
with a comment: a run that died between the summary and hero checkpoints must still
generate its hero; 4.6 re-runs ecosystem enrichment, which is idempotent (tracked models
are excluded).

`run_pipeline.py` `--resume-from` help text: append
`4.5 = repair: keep the executive summary and hero, re-run link enrichment only for summaries/topics that still lack internal links`.

`CLAUDE.md`: under "Local Development (Pipeline)" add
```
# Repair link enrichment only (keeps executive summary + hero; re-enriches texts with no internal links)
TARGET_DATE="2026-09-04" python3 run_pipeline.py --resume-from 4.5 --config-dir ./config --data-dir ./data --web-dir ./web
```
and in "Important Notes → Checkpointing" mention the `hero` checkpoint and the 4.5 repair mode.

### Tests (TDD)

`tests/enrichment_repair_resume_test.py`. Build the orchestrator the way
`tests/phase_timing_restore_test.py:90` does (`MainOrchestrator.__new__` + set exactly the
attributes `run()` touches: `target_date`, `config_dir`, `data_dir` (tmp), `web_dir` (tmp),
`provider_config=None`, `prompt_accessor=None`, `grounding_context=None`,
`ecosystem_manager` stub with `async initialize(...)` → `""` and `async enrich_from_news(...)`
→ `{"updates_made": 0}`, `gatherers={"news": SimpleNamespace(coverage_date="2026-09-03", start_time=None, end_time=None)}`,
`hero_generator` stub whose `generate` raises `AssertionError("hero must not regenerate")`,
`async_client` = fake whose `call_with_thinking` returns a valid enrichment payload and
records callers, `degradations=[]`, `_restored_replay=None`). Write checkpoints into the tmp
`data/checkpoints/2026-09-04/`: `gathering.json` (one `CollectedItem` per category,
`collection_status`), `analysis.json` (`CategoryReport.to_dict()` for a `news` report whose
`category_summary` has **no** link and a `research` report whose summary already contains
`](/?date=2026-09-04&category=research#item-…)`), `topics.json` (one topic with a linked
description, one without), `summary.json` (executive summary without links, the two
category summaries, the two topics), `hero.json` (`hero_image_url="/data/2026-09-04/hero.webp?v=1"`, a prompt, `usage=None`).
Monkeypatch `MainOrchestrator._generate_executive_summary` to raise
`AssertionError("Phase 4 must not re-run")`.

Assert after `asyncio.run(orch.run(resume_from=4.5))`:

- the executive summary, the `news` summary and the unlinked topic went through the fake
  client (callers `link_enricher.executive summary`, `link_enricher.news summary`,
  `link_enricher.topic: …`) and now contain `INTERNAL_LINK_MARKER`;
- the `research` summary and the linked topic are byte-identical to the checkpoint and
  were **not** sent to the client;
- `result.hero_image_url` equals the checkpoint value; the hero stub was not called;
- phase records: "Phase 4: Executive Summary" and "Phase 4.7: Hero Image" are not
  `failed`/`success`-fresh (they are restored or skipped-with-details), "Phase 4.5: Link
  Enrichment" is `success`, `result.degradations == []`;
- the rewritten `summary.json` checkpoint on disk holds the enriched executive summary.

Plus unit tests: `enrich_all(only_unlinked=True)` skips linked texts and never marks them
degraded; `_detect_resume_point` returns 5.0 with `hero.json` present, 4.6 with only
`summary.json`.

Register in `tests.yml` under `pipeline-contracts`:
`# 2026-09-04: a degraded Phase 4.5 could not be re-run without regenerating the summary and hero.`

---

## Task 4: Make CI-run days repairable

**Files:** `.github/workflows/daily-pipeline.yml`; `scripts/checkpoints_from_result.py`
(new); `tests/checkpoints_from_result_test.py` (new); `.github/workflows/tests.yml`;
`CLAUDE.md`.

### Change — workflow

In the `Upload pipeline diagnostics` step, add `data/checkpoints/**` to `path:` and extend
the step comment: checkpoints (~5 MB/day, gathering+analysis+topics+summary+hero, carrying
the `_replay` bundles) are what `--resume-from` needs to repair a published day without
re-running gathering and analysis; before 2026-09-04 a degraded CI day could only be fully
re-run.

### Change — script

`scripts/checkpoints_from_result.py` — stdlib + `agents.base` imports only (add the repo root
to `sys.path` the way `scripts/regenerate_hero.py` does). Usage:

```
python3 scripts/checkpoints_from_result.py RESULT_JSON --data-dir DATA_DIR [--force]
```

Structure: `build_checkpoints(result: dict) -> Dict[str, dict]` (pure),
`write_checkpoints(checkpoints, data_dir, date, force=False) -> List[Path]` (refuses to
overwrite an existing `checkpoints/<date>/` unless `force`), `main()`.

`build_checkpoints` returns, keyed by checkpoint name:

- `gathering`: `{'collection_status': result['collection_status'], 'categories': {cat: [item filtered to CollectedItem.__dataclass_fields__ keys for item in report['all_items']]}}`.
- `analysis`: `{'category_reports': result['category_reports']}`.
- `topics`: `{'top_topics': result['top_topics'], 'thinking': ''}`.
- `summary`: `{'executive_summary': result['executive_summary'], 'thinking': result.get('orchestrator_thinking') or '', 'enriched_category_summaries': {cat: report['category_summary']}, 'enriched_topics': result['top_topics']}`.
- `hero` (only when `result.get('hero_image_url')`): `{'hero_image_url', 'hero_image_prompt', 'hero_image_usage'}` from the result.

Every checkpoint carries `_phase_timings` built from `result['phase_status']` records that
have both `start_time` and `end_time`: `{rec['name']: {'start_time': …, 'end_time': …, 'status': rec['status']}}`
— the shape `PhaseTracker.export_timings()` produces. **No `_replay` key**: spans are
memory-only and cannot be rebuilt from the result file; the module docstring says so and
tells the operator to restore the day's `replay-*.json*` from git after a repair run.
`main()` prints each path written and the `--resume-from 4.5` command to run next.

### Tests (TDD)

`tests/checkpoints_from_result_test.py`, loading the script by path
(`importlib.util.spec_from_file_location`, as `run_pipeline._validate_generated_report`
does) since `scripts/` is not a package. Fixture: a small result dict with two categories
(`news`, `research`), two flattened `AnalyzedItem` dicts each (`AnalyzedItem(...).to_dict()`
from `agents.base` — build real objects so the shape is authoritative), one topic, four
`phase_status` records of which one lacks `end_time`, hero fields set. Assert:

- gathering entries round-trip through `CollectedItem.from_dict` and contain none of
  `summary`, `importance_score`, `reasoning`, `themes`;
- `CategoryReport.from_dict(checkpoints['analysis']['category_reports']['news'])` has 2
  `all_items` and the original `category_summary`;
- `_phase_timings` has exactly the three completed phases with matching bounds;
- `summary` fields equal the result's; `hero` present with the url, and absent when
  `hero_image_url` is `None`;
- `write_checkpoints` writes the five files and raises `FileExistsError` on a second call
  without `force`, succeeds with `force=True`.

Register in `tests.yml` under `pipeline-contracts`:
`# 2026-09-04: a CI day's checkpoints died with the runner; nothing could be resumed.`

`CLAUDE.md` "Daily Automation": one sentence that the diagnostics artifact now includes
`data/checkpoints`, and that `scripts/checkpoints_from_result.py` rebuilds them from an
`orchestrator_result_*.json` when only that survived.

---

## Task 5: Price Gemini 3.8 Flash in the cost tracker

**Files:** `agents/cost_tracker.py`; `tests/gemini_flash_pricing_test.py` (new);
`.github/workflows/tests.yml`.

### Change

In `CostTracker.__init__`'s model dispatch (`agents/cost_tracker.py:207-264`), add a branch
**before** the `"opus"` branch:

```python
elif "gemini-3.8-flash" in model.lower():
    # google/gemini-3.8-flash on OpenRouter (listed 2026-09-02, production
    # model from 2026-09-04). Verified live against
    # /api/v1/models/google/gemini-3.8-flash/endpoints on 2026-09-04:
    # $0.75/MTok prompt, $3.75/MTok completion, $0.075/MTok cache read on
    # every Google / Google AI Studio endpoint; internal_reasoning is billed
    # at the completion rate and arrives folded into output_tokens. The
    # explicit-cache write rate OpenRouter lists ($0.0417/MTok) is never
    # triggered by this transport, so writes bill at the prompt rate like the
    # other OpenRouter rows. The ":batch" slug is half price and is not
    # distinguished here. The 2026-09-04 run fell through to the Opus
    # fallback and published $28.31 for ≈$4.23 of real spend.
    self.input_price = 0.75
    self.output_price = 3.75
    self.cache_write_price = 0.75
    self.cache_hit_price = 0.075
```

### Tests (TDD)

`tests/gemini_flash_pricing_test.py`, mirroring `tests/muse_spark_pricing_test.py`:
rates are a measurement (`pricing_is_estimate` False) with the four values above;
`"GOOGLE/Gemini-3.8-Flash"` matches; 1M pure-reasoning output tokens cost exactly 3.75;
a 1M-token cached prefix bills `input_cost` 0.75 and `cache_hit_cost` 0.075.

Register in `tests.yml` under `pipeline-contracts`:
`# 2026-09-04: Gemini 3.8 Flash had no row; the run published Opus-rate $28.31 for ≈$4.23.`
