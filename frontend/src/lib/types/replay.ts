/**
 * Types for the LLM Replay data contract (schema v1).
 *
 * These mirror `docs/replay-schema.md` verbatim — field names stay snake_case
 * exactly as they appear in `replay-index.json` / `replay-stream.json.gz`.
 * The schema is the source of truth; do not camelCase anything here.
 */

export type ReplayRunStatus = 'success' | 'partial' | 'failed';
export type ReplayPhaseStatus = 'success' | 'partial' | 'failed' | 'skipped' | 'running';
export type ReplaySourceStatus = 'success' | 'partial' | 'failed';

export type ReplayAgentKind = 'gatherer' | 'analyzer' | 'synthesizer' | 'enricher' | 'imagegen';

export type ReplayRole =
	| 'map'
	| 'reduce'
	| 'filter'
	| 'synthesize'
	| 'enrich'
	| 'match'
	| 'curate'
	| 'check'
	| 'image';

export type ReplayProfile = 'QUICK' | 'STANDARD' | 'DEEP' | 'ULTRATHINK';
export type ReplayEffort = 'low' | 'medium' | 'high' | 'xhigh' | 'max';
export type ReplayOutcome = 'ok' | 'truncated' | 'refused' | 'failed' | 'retried';

export interface ReplayRun {
	status: ReplayRunStatus;
	total_items_analyzed: number;
	total_cost_usd: number;
	total_input_tokens: number;
	total_output_tokens: number;
	llm_calls: number;
	models: string[];
	peak_concurrency: number;
	stream_available: boolean;
	/** Set when the generator had to shrink the stream file to fit the size cap. */
	stream_truncation?: string | null;
	/**
	 * Whether `replay-prompts.json.gz` was published for this date. False once the
	 * file ages out of retention; absent on days that predate prompt capture, which
	 * are treated as "try and fail soft" rather than assumed either way.
	 */
	prompts_available?: boolean;
	/**
	 * False when the index was reconstructed offline from run logs rather than
	 * recorded live. Those days have no per-call queue wait (`wait_ms` is 0) and no
	 * `first_token_ms`, so anything that segments a call by those must degrade.
	 * Absent means "assume measured" — the field post-dates schema v1's first draft.
	 */
	timings_measured?: boolean;
}

export interface ReplayPhase {
	id: string;
	label: string;
	ordinal: string;
	start_ms: number;
	end_ms: number;
	status: ReplayPhaseStatus;
	detail?: string | null;
	error?: string | null;
}

export interface ReplayAgent {
	id: string;
	label: string;
	kind: ReplayAgentKind;
	category: string | null;
	phase_ids: string[];
	call_count: number;
	cost_usd: number;
	output_tokens: number;
	items_in: number | null;
	items_out: number | null;
	status: string;
	/** Optional flavour text from the taxonomy; rendered in the agent detail popover. */
	blurb?: string | null;
}

export interface ReplaySource {
	agent_id: string;
	name: string;
	/**
	 * True only when the gatherer recorded this source's own span. False or absent
	 * means the row was stretched across the whole gathering phase and must not be
	 * drawn as a measurement.
	 *
	 * The default is deliberately the opposite of `run.timings_measured`: every day
	 * published before 2026-07-29 genuinely has no per-source timing, so treating
	 * absence as "measured" would present six identical phase-wide slabs as though
	 * each had been individually clocked.
	 */
	timing_measured?: boolean;
	items: number;
	status: ReplaySourceStatus;
	start_ms: number;
	end_ms: number;
}

export interface ReplayCall {
	id: string;
	agent_id: string;
	phase_id: string | null;
	caller: string;
	task: string;
	role: ReplayRole;
	worker: number | null;

	queued_ms: number;
	start_ms: number;
	first_token_ms: number | null;
	end_ms: number;
	wait_ms: number;

	provider_id: string;
	model: string;
	profile: ReplayProfile;
	effort: ReplayEffort;

	input_tokens: number;
	output_tokens: number;
	cache_read_tokens: number;
	cost_usd: number;

	thinking_chars: number;
	text_chars: number;
	stream_events: number;

	stop_reason: string | null;
	outcome: ReplayOutcome;
	attempt: number;
	fallback_from: string | null;
	retry_reason: string | null;

	has_stream: boolean;

	/**
	 * Image-generation calls only (`role: "image"`). The hero image runs through a
	 * separate image client, not an LLM route, so it has no tokens, no cost and no
	 * stream — but it does have the one output in the whole run you can look at.
	 * Site-relative and already cache-busted by the generator; use verbatim.
	 */
	image_url?: string | null;
	/** The prompt that produced `image_url`. Markdown-ish, ~5–6k chars. */
	image_prompt?: string | null;
	/**
	 * Image calls only. `usage_measured` distinguishes "the provider reported token
	 * counts" from "it reported nothing" — without it a zero cost is ambiguous
	 * between a free call and an unmeasured one. `image_tokens` is the subset of
	 * `output_tokens` billed at the image rate (~10× the text rate); the remainder
	 * is the model's own thinking before it drew.
	 */
	usage_measured?: boolean;
	image_tokens?: number;
	/**
	 * Failed attempts only. The cost tracker records nothing for a call that never
	 * returned a response, so its tokens are unknown — but it streamed real output
	 * and was really billed. `billed: false` means "cost unknown", NOT "free"; the
	 * UI must not render $0.00 for it.
	 */
	billed?: boolean;
	/**
	 * False when the call failed mid-stream: its tokens were read off the SSE
	 * events rather than a final response, so the figure is a floor — anything
	 * emitted after the last `message_delta` is unaccounted for. The spend is real
	 * and is included in the run total either way.
	 */
	billed_exact?: boolean;
	/**
	 * INFERRED, not measured — the only such field in the artifact.
	 *
	 * Set on a failed attempt that died before the provider sent any SSE event, so
	 * nothing at all was measured for it. The prompt was still ingested and charged,
	 * so this carries the input token count of the retry, which sent the identical
	 * prompt. Excluded from the run total, which stays a sum of measured spend.
	 *
	 * Any UI showing these must mark them as estimates (`~`), never as measurements.
	 */
	input_tokens_estimated?: number;
	/** Input-only cost for `input_tokens_estimated`. The model never wrote. */
	cost_usd_estimated?: number;
	error_type?: string | null;
	/** Set on a failed attempt: the id of the retry that succeeded in its place. */
	recovered_by?: string;
	/** Set on a successful call that replaced an earlier failed attempt. */
	recovers?: string;
}

export interface ReplayConcurrency {
	interval_ms: number;
	/** `[t_ms, active_calls, queued_calls]` */
	samples: [number, number, number][];
}

export interface ReplayIndex {
	schema: number;
	date: string;
	coverage_date: string;
	t0: string;
	duration_ms: number;
	generated_by: string;
	run: ReplayRun;
	phases: ReplayPhase[];
	agents: ReplayAgent[];
	sources: ReplaySource[];
	calls: ReplayCall[];
	concurrency: ReplayConcurrency;
}

/** Deltas for one call, as three parallel arrays. `kind`: 0 = thinking, 1 = text. */
export interface ReplayCallStream {
	t: number[];
	kind: (0 | 1)[];
	text: string[];
}

export interface ReplayStream {
	schema: number;
	date: string;
	calls: Record<string, ReplayCallStream>;
}

/** One call's prompt, exactly as it was sent to the provider. */
export interface ReplayCallPrompt {
	/** System prompt: ecosystem grounding + injection preamble + instructions. */
	system?: string | null;
	/** The user message(s), including the nonce-fenced source data. */
	messages?: string | null;
	/** Total chars actually sent, even if the text above was capped. */
	chars?: number;
	/** True when the recorder's size cap trimmed the text held here. */
	truncated?: boolean;
}

/**
 * Lazily-fetched companion to the index: `web/data/{date}/replay-prompts.json.gz`.
 *
 * Kept out of the index because it is the largest artifact by far (~600 KB
 * gzipped) and only matters once a detail pane is opened. Absent for days
 * published before prompt capture existed, and for days past the retention
 * window — both must degrade to "not retained", never to an error.
 */
export interface ReplayPrompts {
	schema: number;
	date: string;
	calls: Record<string, ReplayCallPrompt>;
}
