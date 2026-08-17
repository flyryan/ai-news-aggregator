# Replay: per-unit source steps — design

**Date:** 2026-08-17
**Status:** implemented
**Touches:** `docs/replay-schema.md` (binding contract), capture, generator, frontend

## Problem

On the `/replay` page, the Social, Reddit and Research gatherers render as progress
bars that all advance together and finish together. Two distinct defects produce that:

### 1. Social per-platform timing is measured and then thrown away

`social_gatherer.py` wraps each platform in `BaseGatherer.time_source`, but with the
bare keys `twitter` / `bluesky` / `mastodon`. The orchestrator publishes those rows to
`collection_status` under `social_twitter` / `social_bluesky` / `social_mastodon`
(`orchestrator.py:780`). So `_attach_source_timing` creates three orphan rows under the
bare names — which `_SOURCE_LABELS` does not recognise and the generator drops — while
the three real rows never receive a span and fall back to the whole gathering phase.

Confirmed in published data: on 2026-08-15, -16 and -17 all three social rows carry
`timing_measured: false` and byte-identical `start_ms` / `end_ms`. That is the literal
"all progresses together".

### 2. One bar covers many independent units

Reddit is a single row spanning 15 subreddits; `news` a single row over 26 RSS feeds;
`research_arxiv` a single row over ~7 category feeds plus an OAI-PMH leg. The span is
real, but the fill between `start_ms` and `end_ms` is linear interpolation — an
animation, not a measurement. That contradicts the replay's stated design principle:
*if we can't measure it, we don't draw it.*

The diagnostic cost is concrete. On 2026-08-17 the `research_arxiv` row spans
131 → 120,775 ms and returns **zero papers**. One flat bar hides that the OAI leg
burned two minutes on its own.

## Design

### Timebase and semantics

Progress is a **step function over completions**. A source's bar fill is
`completed_steps / total_steps` at time `t`. A unit that has been dispatched but has
not returned contributes **nothing** to the fill; it is named as in-flight instead.
No partial credit, no interpolation. Every pixel corresponds to a request that came
back.

This is correct under concurrency, which matters: RSS (26 feeds), Reddit (6 workers),
Bluesky and Mastodon all fan out in thread pools, so steps overlap in wall-clock time.
Twitter chunks are the exception and run strictly sequentially. A rendering that
implied left-to-right sequencing would be a new lie in place of the old one.

### Capture — `BaseGatherer.time_step`

A sibling to the existing `time_source`:

```python
with self.time_step('reddit', f'r/{subreddit}') as step:
    posts = self._fetch_subreddit(subreddit)
    step.items = len(posts)
```

- Records `{name, started_at, ended_at, items, status}` into `self.source_steps[key]`.
- Yields a mutable handle so the caller can attach a count the context manager cannot
  observe from outside the block.
- Records an end on the exception path, marked `failed`, and re-raises. A subreddit
  that 403'd at 4 s is a fact worth drawing; leaving the step open would make the bar
  read as a hang.
- The entire `finally` body is guarded. A bug in recording must never mask the real
  exception nor fail a run — the same non-negotiable the replay recorder already holds.
- Appends under a `threading.Lock`. These fire once per HTTP fetch, not once per token,
  so this is nothing like the SSE hot path the recorder deliberately keeps lock-free.
- Bounded: `_MAX_STEPS_PER_SOURCE = 250`. Overflow is counted and marked, never silent.

Timestamps are `time.time()` epoch floats, matching `time_source` and `PhaseTracker`
exactly, so the generator subtracts `t0` the same way for all three.

### Instrumentation sites

| Source key | Step unit | Count | Label |
|---|---|---|---|
| `news` | one RSS feed | 26 | feed host |
| `reddit` | one subreddit | 15 | `r/LocalLLaMA` |
| `research_arxiv` | one category RSS, plus the OAI leg | ~8 | `cs.AI`, `OAI-PMH` |
| `research_blogs` | one feed, plus LessWrong GraphQL | 18 | host, `LessWrong` |
| `social_twitter` | one search chunk | ~7 | `chunk 3/7` |
| `social_bluesky` | one handle | 9 | handle |
| `social_mastodon` | one account | 3 | account |

Twitter steps are chunks rather than the 167 configured accounts: the chunk is the
actual unit of work issued and returned, and 167 rows would be noise. Analyzers are
untouched — their batches are already real LLM calls with measured spans.

### Swallowed failures, added during implementation

Three helpers catch their own transport errors and return a short list rather than
raising, so `time_step` cannot infer failure from an exception and would draw a clean
green bar over a dead unit. Worse, a step marked `failed` inside a row marked
`success` contradicts itself on screen, so the row status has to be able to move too.

- `reddit._fetch_subreddit` — wrapper marks the step `partial` when `_stop_calls` is
  set, i.e. a fatal ScrapeCreators error or a blown credit budget aborted the run.
- `research._fetch_via_oai` — marks the step `partial` on an incomplete harvest.
- `social._fetch_bluesky_user` / `_fetch_mastodon_user` — outer handler now re-raises.
  This also revives `failed_handles` / `failed_accounts`, which were dead: the
  existing counters increment off `future.result()`, which could never throw. A dead
  handle previously reported `success`. Both helpers issue their requests before the
  post loop, so `posts` is necessarily empty on that path and raising discards
  nothing. The inner per-post handler still swallows — one unparseable post should
  not discard the handle's other 29.

The last of these changes an observable status: a platform with dead accounts now
reports `partial`, and one with all accounts dead reports `failed`. That is the
correct signal and matches the direction of the existing source-anomaly work, but it
is a behaviour change beyond the original scope and is called out here for that
reason.

### Orchestrator

`_attach_source_timing` also folds `gatherer.source_steps` into
`collection_status[key]['steps']`. Purely additive, exactly as `source_timing` is
today: a gatherer that records nothing leaves its row untouched.

### Contract — `docs/replay-schema.md`

`sources[]` gains an optional `steps`:

```jsonc
"steps": [
  {"name": "r/LocalLLaMA", "start_ms": 142, "end_ms": 21033, "items": 83, "status": "success"}
]
```

Rules, mirroring the parent row's:

- **Absent** on every day published before this ships, and on any gatherer not yet
  instrumented. The frontend must render those sources exactly as it does today.
- A step whose offsets do not resolve — a resumed run replaying gathering from a
  checkpoint written under an earlier `t0`, producing pre-origin epochs — is
  **dropped, not clamped**. Clamping would invent a measurement.
- Names are **host or handle labels, never full feed URLs.** Step names are the first
  new free-text field to enter the index, and `_assert_publishable` has already cost
  this project a full day's replay over a kebab-case slug inside a URL path
  (2026-07-31). Hostnames carry no path segments and cannot reproduce that.

Size budget: ~90 steps adds roughly 8–10 KB to a 46 KB index, landing near 56 KB —
inside the documented 15–60 KB target but close to the top. Measure after the first
real run and tighten the encoding if it lands over.

### Frontend

- `ReplaySource` gains `steps?: ReplayStep[]`.
- `replayEngine` derives `progress = completed / total` when steps exist, and also
  exposes `completed`, `total`, `inflight[]` and `failed`. When steps are absent it
  keeps today's linear fill and flags the row estimated.
- `AgentStation` fills in jumps, shows `9/15`, names what is currently out, and
  surfaces failed units.
- The existing `transition: width 120ms` is removed for stepped rows. Leaving it would
  smear the honest jumps back into the decorative motion this change exists to delete.

### Testing and backfill

`tests/replay_steps_test.py` — 25 cases, added to the dependency-light guard job in
`tests.yml` (which enumerates tests individually, so a new file would otherwise never
run). Covers capture semantics, epoch→ms conversion, the pre-origin drop, completion
ordering, the per-source cap, thread-safety, URL labelling including the 2026-07-31
killer slug, and the publishability gate over real feed hosts.

Three of them are source-level guards over `social_gatherer.py`, because importing it
pulls requests/feedparser and the generator-side tests cannot see the gatherer — which
is exactly where the original bug lived. Both were mutation-checked: reverting
`time_source(f'social_{platform}')` to the bare name, and re-swallowing the Bluesky
transport error, each turn the suite red.

There is no frontend test infrastructure in this repository; the UI is verified by
regenerating a day offline and viewing `/replay`.

**Backfill is impossible.** `data/processed/` holds no per-unit spans, so offline
regeneration of a past date cannot recover steps. Older days keep the linear bar rather
than a reconstructed one — the same honesty rule `run.timings_measured` already applies
to resumed runs.

## Sequencing

The social key fix is independently correct and ships as its own commit ahead of the
feature, so it can be reasoned about and reverted separately.
