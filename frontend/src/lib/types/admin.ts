export interface HealthAnomaly {
	date: string;
	source: string;
	count: number;
	baseline: number;
	weekday: string;
	ratio: number;
	detail: string;
}

export interface HealthSeries {
	sources: string[];
	dates: string[];
	/** null means no report published that day — not "collected nothing". */
	series: Record<string, (number | null)[]>;
	anomalies: HealthAnomaly[];
}

export interface CostRun {
	date: string;
	cost_usd: number;
	llm_calls: number;
	input_tokens: number;
	output_tokens: number;
	items: number;
	status: string;
	duration_ms: number;
	/** false = reconstructed offline; timings are not measurements. */
	timings_measured: boolean;
}

export interface BalanceHistoryPoint {
	ts: string;
	balance: number;
	balance_usd: number | null;
}

export interface Balance {
	vendor: string;
	label: string;
	unit: string;
	balance: number | null;
	balance_usd: number | null;
	error: string;
	history: BalanceHistoryPoint[];
	burn_per_day: number | null;
	days_remaining: number | null;
	urgent: boolean;
}

export interface WorkflowRun {
	id: number;
	run_number: number;
	status: string;
	conclusion: string | null;
	event: string;
	display_title: string | null;
	actor: string | null;
	head_sha: string | null;
	created_at: string;
	updated_at: string;
	html_url: string;
	run_attempt: number;
	duration_seconds: number;
	/** The ET calendar date the run nominally reports on. */
	report_date: string | null;
	/** Whether web/data/<report_date>/summary.json exists. */
	published: boolean;
	/** Total LLM cost from that date's replay index, when one exists. */
	cost_usd: number | null;
	/** false = a schedule-gate no-op, not a real pipeline run. */
	did_real_work: boolean;
}

export interface RunStep {
	name: string;
	status: string;
	conclusion: string | null;
	duration_seconds: number | null;
}

export interface RunJob {
	id: number;
	name: string;
	status: string;
	conclusion: string | null;
	started_at: string | null;
	completed_at: string | null;
	duration_seconds: number | null;
	html_url: string;
	steps: RunStep[];
}

export interface LatestReport {
	date: string;
	total_items: number;
	categories: Record<string, { count: number; file_size: number }>;
	topics: number;
	generated_at: string | null;
	has_replay: boolean;
}

export interface ActionSpec {
	name: string;
	description: string;
	needs_arg: boolean;
	danger: 'low' | 'medium' | 'high';
}

export interface ActionStatus {
	unit: string;
	active_state: string;
	result: string;
	exit_code: number | null;
	finished: boolean;
	succeeded: boolean;
}

export interface AuditEntry {
	id: number;
	ts: string;
	principal: string;
	action: string;
	target: string | null;
	outcome: string;
	detail: string;
}

export interface PreviewJob {
	job_id: string;
	kind: 'hero' | 'report';
	date: string;
	created_at: string;
	size_bytes: number;
	url: string;
}

export interface SourceFeed {
	name: string;
	count: number;
}

export interface SourceDayDetail {
	source: string;
	date: string;
	published: boolean;
	count: number | null;
	status: string | null;
	error: string | null;
	display_name: string;
	baseline: number | null;
	weekday: string | null;
	ratio: number | null;
	anomalous: boolean;
	/** Which upstream feeds contributed. The absent ones are the diagnosis. */
	feeds: SourceFeed[];
	sample_titles: string[];
	report_url: string | null;
	replay_url: string | null;
	note: string | null;
}
