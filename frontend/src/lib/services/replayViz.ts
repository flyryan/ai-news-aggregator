/**
 * Shared visual vocabulary for the replay views: colour tokens, formatters, and
 * the stage layout that turns the cast into columns.
 *
 * Colours are literal hex (not Tailwind class names) because most of them are
 * consumed by inline `style=` on dynamically-keyed elements, where Tailwind's
 * content scanner cannot see a constructed class name.
 */

import type { ReplayAgent, ReplayCall, ReplayProfile, ReplayRole } from '$lib/types/replay';

/** Route/provider tags. Deliberately distinct from the category palette. */
const PROVIDER_PALETTE: Record<string, string> = {
	aws: '#f59e0b',
	gcp: '#3b82f6',
	anthropic: '#d97757',
	azure: '#0ea5e9',
	vertex: '#8b5cf6',
	bedrock: '#f59e0b',
	// Not an LLM route: the hero image client. Matched to the `imagegen` kind tint so
	// the Illustrator reads as one thing across the stage and the transcript.
	image: '#ec4899'
};

const PROVIDER_FALLBACKS = ['#14b8a6', '#a855f7', '#ec4899', '#84cc16', '#6366f1'];

export function providerColor(providerId: string | null | undefined): string {
	if (!providerId) return '#737373';
	const key = providerId.toLowerCase();
	const known = PROVIDER_PALETTE[key];
	if (known) return known;
	let hash = 0;
	for (let i = 0; i < key.length; i++) hash = (hash * 31 + key.charCodeAt(i)) >>> 0;
	return PROVIDER_FALLBACKS[hash % PROVIDER_FALLBACKS.length];
}

export const PROFILE_COLORS: Record<ReplayProfile, string> = {
	QUICK: '#10b981',
	STANDARD: '#3b82f6',
	DEEP: '#8b5cf6',
	ULTRATHINK: '#E63946'
};

export function profileColor(profile: string | null | undefined): string {
	return PROFILE_COLORS[(profile as ReplayProfile) ?? 'STANDARD'] ?? '#737373';
}

const CATEGORY_COLORS: Record<string, string> = {
	news: '#667eea',
	research: '#10b981',
	social: '#f59e0b',
	reddit: '#ef4444'
};

const KIND_COLORS: Record<string, string> = {
	gatherer: '#0ea5e9',
	analyzer: '#8b5cf6',
	ranker: '#7c3aed',
	synthesizer: '#E63946',
	enricher: '#14b8a6',
	imagegen: '#ec4899'
};

/** An agent's identity colour: category tint when it has one, else kind tint. */
export function agentColor(agent: Pick<ReplayAgent, 'category' | 'kind'> | undefined): string {
	if (!agent) return '#737373';
	if (agent.category && CATEGORY_COLORS[agent.category]) return CATEGORY_COLORS[agent.category];
	return KIND_COLORS[agent.kind] ?? '#737373';
}

export const ROLE_LABELS: Record<ReplayRole, string> = {
	map: 'Map',
	reduce: 'Reduce',
	filter: 'Filter',
	synthesize: 'Synthesize',
	enrich: 'Enrich',
	match: 'Match',
	curate: 'Curate',
	check: 'Check',
	image: 'Image'
};

export const OUTCOME_COLORS: Record<string, string> = {
	ok: '#10b981',
	truncated: '#f59e0b',
	refused: '#f97316',
	failed: '#ef4444',
	retried: '#a855f7'
};

/**
 * Stage columns, left to right, following the work rather than the org chart.
 *
 * Reading and ranking used to share one column because they shared one agent. They
 * are different jobs at different cost tiers — a wide cheap pass over everything,
 * then one expensive pass that decides the running order — so they get their own
 * columns now, and each column has a single effort tier worth naming.
 */
export type StageColumnId = 'scouts' | 'readers' | 'editors' | 'desk';

export interface StageColumn {
	id: StageColumnId;
	title: string;
	caption: string;
	agents: ReplayAgent[];
}

const DESK_ORDER = [
	'continuity',
	'storyliner',
	'freshness',
	'orchestrator',
	'link_enricher',
	'ecosystem',
	'hero'
];

export function buildStage(agents: ReplayAgent[]): StageColumn[] {
	const scouts = agents.filter((a) => a.kind === 'gatherer');
	const readers = agents.filter((a) => a.kind === 'analyzer');
	const editors = agents.filter((a) => a.kind === 'ranker');
	const placed = new Set([...scouts, ...readers, ...editors]);
	const desk = agents
		.filter((a) => !placed.has(a))
		.sort((a, b) => {
			const ai = DESK_ORDER.indexOf(a.id);
			const bi = DESK_ORDER.indexOf(b.id);
			return (ai === -1 ? 99 : ai) - (bi === -1 ? 99 : bi);
		});

	return [
		{ id: 'scouts', title: 'Scouts', caption: 'Collecting from the wire', agents: scouts },
		{ id: 'readers', title: 'Readers', caption: 'Summarizing every item', agents: readers },
		{ id: 'editors', title: 'Editors', caption: 'Ranking what matters', agents: editors },
		{ id: 'desk', title: 'The Desk', caption: 'Synthesis, copy, art', agents: desk }
	];
}

/** Which downstream column a finished call should throw its packet toward. */
export function downstreamOf(kind: string): StageColumnId | null {
	if (kind === 'gatherer') return 'readers';
	if (kind === 'analyzer') return 'editors';
	if (kind === 'ranker') return 'desk';
	return null;
}

// ---------------------------------------------------------------- formatters

export function formatClock(ms: number): string {
	const total = Math.max(0, Math.floor(ms / 1000));
	const h = Math.floor(total / 3600);
	const m = Math.floor((total % 3600) / 60);
	const s = total % 60;
	const mm = String(m).padStart(2, '0');
	const ss = String(s).padStart(2, '0');
	return h > 0 ? `${h}:${mm}:${ss}` : `${mm}:${ss}`;
}

export function formatDuration(ms: number): string {
	if (ms < 1000) return `${Math.round(ms)}ms`;
	if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`;
	const m = Math.floor(ms / 60_000);
	const s = Math.round((ms % 60_000) / 1000);
	return `${m}m ${String(s).padStart(2, '0')}s`;
}

export function formatWallTime(t0: string, offsetMs: number): string {
	const base = Date.parse(t0);
	if (Number.isNaN(base)) return '--:--:--';
	const d = new Date(base + offsetMs);
	return d.toLocaleTimeString(undefined, { hour12: false });
}

export function formatTokens(n: number): string {
	if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`;
	if (n >= 10_000) return `${Math.round(n / 1000)}k`;
	if (n >= 1000) return `${(n / 1000).toFixed(1)}k`;
	return String(Math.round(n));
}

export function formatCost(usd: number): string {
	if (usd >= 100) return `$${usd.toFixed(0)}`;
	if (usd >= 1) return `$${usd.toFixed(2)}`;
	return `$${usd.toFixed(3)}`;
}

/**
 * True for the image-generation call (the hero).
 *
 * These go through a separate image client rather than an LLM route, so every
 * token/cost field on them is a structural zero, not a measurement. Anywhere the
 * UI would otherwise print "0 tok" or "$0.000" it must branch on this instead —
 * the page's whole contract is that a number on screen was measured.
 */
export function isImageCall(call: Pick<ReplayCall, 'role'>): boolean {
	return call.role === 'image';
}

export function callDuration(call: ReplayCall): number {
	return Math.max(0, call.end_ms - call.start_ms);
}

export function ttftOf(call: ReplayCall): number | null {
	if (call.first_token_ms == null) return null;
	return Math.max(0, call.first_token_ms - call.start_ms);
}
