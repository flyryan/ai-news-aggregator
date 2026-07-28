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
