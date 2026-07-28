/**
 * A synthetic-but-plausible replay run, used for `?demo=1`.
 *
 * Conforms to `docs/replay-schema.md` v1 exactly. Deliberately exercises the
 * awkward cases so the UI is developed against them rather than against a happy
 * path: parallel batch fan-out, a route failover, a truncated call, calls with
 * no stream, a thinking-heavy call and a thinking-empty one.
 */

import type { ReplayCall, ReplayIndex, ReplayStream } from '$lib/types/replay';

const MIN = 60_000;

interface CallSeed {
	id: string;
	agent_id: string;
	phase_id: string;
	caller: string;
	task: string;
	role: ReplayCall['role'];
	worker?: number | null;
	queued_ms: number;
	wait_ms: number;
	ttft_ms: number;
	dur_ms: number;
	provider_id: string;
	profile: ReplayCall['profile'];
	effort: ReplayCall['effort'];
	input_tokens: number;
	output_tokens: number;
	thinking_chars?: number;
	outcome?: ReplayCall['outcome'];
	stop_reason?: string;
	fallback_from?: string | null;
	retry_reason?: string | null;
	attempt?: number;
	has_stream?: boolean;
	/** Image-generation calls: no LLM route, so tokens/cost are zeroed, not derived. */
	model?: string;
	image_url?: string;
	image_prompt?: string;
}

const MODEL_BY_PROVIDER: Record<string, string> = {
	aws: 'claude-5-opus-aws',
	gcp: 'claude-5-opus-gcp',
	anthropic: 'claude-5-opus-anthropic'
};

function buildCall(seed: CallSeed): ReplayCall {
	const start_ms = seed.queued_ms + seed.wait_ms;
	const end_ms = start_ms + seed.dur_ms;
	const first_token_ms = seed.ttft_ms > 0 ? start_ms + seed.ttft_ms : null;
	const thinking = seed.thinking_chars ?? Math.round(seed.output_tokens * 1.4);
	// Image calls bypass the LLM cost/token derivation entirely: the real generator
	// writes literal zeros there, and the fixture must match or the demo would show
	// invented spend for a call that was never billed per token.
	const isImage = seed.role === 'image';
	return {
		id: seed.id,
		agent_id: seed.agent_id,
		phase_id: seed.phase_id,
		caller: seed.caller,
		task: seed.task,
		role: seed.role,
		worker: seed.worker ?? null,
		queued_ms: seed.queued_ms,
		start_ms,
		first_token_ms,
		end_ms,
		wait_ms: seed.wait_ms,
		provider_id: seed.provider_id,
		model: seed.model ?? MODEL_BY_PROVIDER[seed.provider_id] ?? `claude-5-opus-${seed.provider_id}`,
		profile: seed.profile,
		effort: seed.effort,
		input_tokens: isImage ? 0 : seed.input_tokens,
		output_tokens: isImage ? 0 : seed.output_tokens,
		cache_read_tokens: isImage ? 0 : Math.round(seed.input_tokens * 0.35),
		cost_usd: isImage
			? 0
			: +(seed.input_tokens * 0.000005 + seed.output_tokens * 0.000075).toFixed(4),
		thinking_chars: isImage ? 0 : thinking,
		text_chars: isImage ? (seed.image_prompt?.length ?? 0) : Math.round(seed.output_tokens * 3.6),
		stream_events: isImage ? 0 : Math.round(seed.output_tokens * 0.7),
		stop_reason: seed.stop_reason ?? 'end_turn',
		outcome: seed.outcome ?? 'ok',
		attempt: seed.attempt ?? 1,
		fallback_from: seed.fallback_from ?? null,
		retry_reason: seed.retry_reason ?? null,
		has_stream: seed.has_stream ?? !isImage,
		image_url: seed.image_url ?? null,
		image_prompt: seed.image_prompt ?? null
	};
}

const PROVIDERS = ['aws', 'gcp', 'anthropic'];

const seeds: CallSeed[] = [];

// --- Phase 2: four analysts, batch fan-out, then a reduce each -------------
const ANALYST_PLAN: { cat: string; batches: number; itemsPer: number }[] = [
	{ cat: 'news', batches: 5, itemsPer: 75 },
	{ cat: 'research', batches: 7, itemsPer: 75 },
	{ cat: 'social', batches: 4, itemsPer: 75 },
	{ cat: 'reddit', batches: 3, itemsPer: 75 }
];

let seq = 1;
const nextId = () => `c${String(seq++).padStart(3, '0')}`;

// --- Phase 1: the Scouts. The News gatherer asks the model which links from the
// social firehose are worth fetching, so gathering is genuinely LLM-driven and the
// left column of the stage wakes up first.
[
	{ n: 0, at: 24_000, urls: 31, dur: 21_000, provider: 'aws' },
	{ n: 1, at: 62_000, urls: 28, dur: 24_500, provider: 'gcp' },
	{ n: 2, at: 104_000, urls: 24, dur: 19_800, provider: 'anthropic' },
	{ n: 3, at: 147_000, urls: 17, dur: 16_400, provider: 'aws' }
].forEach((b) => {
	seeds.push({
		id: nextId(),
		agent_id: 'news_gatherer',
		phase_id: 'phase-1',
		caller: `news_gatherer.link_relevance_${b.n}`,
		task: `Triage ${b.urls} candidate links`,
		role: 'filter',
		worker: b.n,
		queued_ms: b.at,
		wait_ms: 140 + b.n * 60,
		ttft_ms: 3200 + b.n * 400,
		dur_ms: b.dur,
		provider_id: b.provider,
		profile: 'QUICK',
		effort: 'high',
		input_tokens: 14_800 + b.urls * 320,
		output_tokens: 640 + b.urls * 18,
		thinking_chars: 0
	});
});

// Research and Social gatherers each do one summarisation pass over what they pulled.
[
	{ agent: 'research_gatherer', at: 132_000, dur: 34_000, provider: 'gcp', task: 'Condense 441 arXiv abstracts', inp: 186_000, out: 4200 },
	{ agent: 'social_gatherer', at: 88_000, dur: 27_500, provider: 'anthropic', task: 'Cluster 226 posts by thread', inp: 74_000, out: 2600 },
	{ agent: 'reddit_gatherer', at: 156_000, dur: 22_000, provider: 'aws', task: 'Digest 148 comment threads', inp: 96_000, out: 3100 }
].forEach((g) => {
	seeds.push({
		id: nextId(),
		agent_id: g.agent,
		phase_id: 'phase-1',
		caller: `${g.agent}.summarize`,
		task: g.task,
		role: 'map',
		queued_ms: g.at,
		wait_ms: 220,
		ttft_ms: 4800,
		dur_ms: g.dur,
		provider_id: g.provider,
		profile: 'QUICK',
		effort: 'high',
		input_tokens: g.inp,
		output_tokens: g.out,
		thinking_chars: 0
	});
});

// News runs a pre-filter first.
seeds.push({
	id: nextId(),
	agent_id: 'news_analyzer',
	phase_id: 'phase-2',
	caller: 'news_analyzer.filter',
	task: 'Pre-filter articles',
	role: 'filter',
	queued_ms: 4.2 * MIN,
	wait_ms: 210,
	ttft_ms: 5400,
	dur_ms: 46_000,
	provider_id: 'aws',
	profile: 'QUICK',
	effort: 'high',
	input_tokens: 92_400,
	output_tokens: 3120,
	thinking_chars: 0
});

let cursor = 4.4 * MIN;
ANALYST_PLAN.forEach((plan, planIdx) => {
	for (let b = 0; b < plan.batches; b++) {
		const provider = PROVIDERS[(planIdx + b) % PROVIDERS.length];
		// Stagger so the semaphore ceiling is visible: batches queue in waves of 3.
		const wave = Math.floor(b / 3);
		const queued = cursor + planIdx * 9_000 + wave * 95_000 + (b % 3) * 900;
		const wait = b % 3 === 2 ? 24_000 + b * 1500 : 180 + b * 40;
		seeds.push({
			id: nextId(),
			agent_id: `${plan.cat}_analyzer`,
			phase_id: 'phase-2',
			caller: `${plan.cat}_analyzer.batch_${b}`,
			task: `Analyze batch ${b}`,
			role: 'map',
			worker: b,
			queued_ms: queued,
			wait_ms: wait,
			ttft_ms: 12_000 + (b % 4) * 3400,
			dur_ms: 88_000 + (b % 5) * 21_000,
			provider_id: provider,
			profile: 'QUICK',
			effort: 'high',
			input_tokens: 148_000 + b * 4200,
			output_tokens: 7400 + (b % 4) * 900
		});
	}
});

// One batch fails over to another route and retries. This is the money shot for
// "three providers load-balancing in real time".
seeds.push({
	id: nextId(),
	agent_id: 'research_analyzer',
	phase_id: 'phase-2',
	caller: 'research_analyzer.batch_3_retry',
	task: 'Retry batch 3',
	role: 'map',
	worker: 3,
	queued_ms: 9.1 * MIN,
	wait_ms: 640,
	ttft_ms: 15_500,
	dur_ms: 112_000,
	provider_id: 'anthropic',
	profile: 'QUICK',
	effort: 'high',
	input_tokens: 161_200,
	output_tokens: 8900,
	attempt: 2,
	fallback_from: 'gcp',
	retry_reason: 'overloaded_error (529)',
	outcome: 'retried'
});

// Theme detection per category (STANDARD), then the reduce/rank (DEEP).
ANALYST_PLAN.forEach((plan, i) => {
	seeds.push({
		id: nextId(),
		agent_id: `${plan.cat}_analyzer`,
		phase_id: 'phase-2',
		caller: `${plan.cat}_analyzer.reduce_rank`,
		task: 'Rank and select',
		role: 'reduce',
		queued_ms: 13.5 * MIN + i * 42_000,
		wait_ms: 320,
		ttft_ms: 26_000,
		dur_ms: 168_000 + i * 19_000,
		provider_id: PROVIDERS[i % PROVIDERS.length],
		profile: 'DEEP',
		effort: 'max',
		input_tokens: 214_000,
		output_tokens: 14_200 + i * 1100
	});
});

// --- Phase 2.5: continuity ---------------------------------------------------
ANALYST_PLAN.forEach((plan, i) => {
	seeds.push({
		id: nextId(),
		agent_id: 'continuity',
		phase_id: 'phase-2.5',
		caller: `continuity.matcher.${plan.cat}`,
		task: `Match ${plan.cat} to prior days`,
		role: 'match',
		queued_ms: 17.4 * MIN + i * 5000,
		wait_ms: 150,
		ttft_ms: 8200,
		dur_ms: 61_000 + i * 8000,
		provider_id: PROVIDERS[(i + 1) % PROVIDERS.length],
		profile: 'STANDARD',
		effort: 'xhigh',
		input_tokens: 74_500,
		output_tokens: 4100,
		// A pruned stream: index says the call happened, deltas were dropped.
		has_stream: i !== 2
	});
});

seeds.push({
	id: nextId(),
	agent_id: 'continuity',
	phase_id: 'phase-2.5',
	caller: 'continuity.curator',
	task: 'Curate storylines',
	role: 'curate',
	queued_ms: 19.2 * MIN,
	wait_ms: 240,
	ttft_ms: 31_000,
	dur_ms: 196_000,
	provider_id: 'aws',
	profile: 'DEEP',
	effort: 'max',
	input_tokens: 188_000,
	output_tokens: 16_800
});

seeds.push({
	id: nextId(),
	agent_id: 'freshness',
	phase_id: 'phase-2.5',
	caller: 'freshness.old_anchor',
	task: 'Check anchor freshness',
	role: 'check',
	queued_ms: 22.9 * MIN,
	wait_ms: 90,
	ttft_ms: 4100,
	dur_ms: 28_400,
	provider_id: 'gcp',
	profile: 'QUICK',
	effort: 'high',
	input_tokens: 31_200,
	output_tokens: 1450,
	thinking_chars: 0
});

// --- Phase 3/4: the marquee synthesis calls ---------------------------------
seeds.push({
	id: nextId(),
	agent_id: 'orchestrator',
	phase_id: 'phase-3',
	caller: 'orchestrator.topics',
	task: 'Detect cross-category topics',
	role: 'synthesize',
	queued_ms: 24.1 * MIN,
	wait_ms: 410,
	ttft_ms: 58_000,
	// The long one: a 296-second ULTRATHINK call.
	dur_ms: 296_000,
	provider_id: 'anthropic',
	profile: 'ULTRATHINK',
	effort: 'max',
	input_tokens: 421_800,
	output_tokens: 22_400,
	thinking_chars: 18_600
});

seeds.push({
	id: nextId(),
	agent_id: 'orchestrator',
	phase_id: 'phase-4',
	caller: 'orchestrator.summary',
	task: 'Write executive summary',
	role: 'synthesize',
	queued_ms: 29.4 * MIN,
	wait_ms: 300,
	ttft_ms: 44_000,
	dur_ms: 214_000,
	provider_id: 'aws',
	profile: 'DEEP',
	effort: 'max',
	input_tokens: 268_400,
	output_tokens: 18_900,
	thinking_chars: 11_200,
	// Hit the output ceiling — the UI must show this distinctly.
	outcome: 'truncated',
	stop_reason: 'max_tokens'
});

// --- Phase 4.5/4.6: copy desk ------------------------------------------------
const ENRICH_CONTEXTS = [
	'executive summary',
	'topic: Opus 5 Benchmarks',
	'topic: Open-Weight Reasoning',
	'news summary',
	'research summary',
	'social summary',
	'reddit summary'
];
ENRICH_CONTEXTS.forEach((ctx, i) => {
	seeds.push({
		id: nextId(),
		agent_id: 'link_enricher',
		phase_id: 'phase-4.5',
		caller: `link_enricher.${ctx}`,
		task: ctx.startsWith('topic:') ? `Link topic "${ctx.slice(6).trim()}"` : `Link ${ctx}`,
		role: 'enrich',
		queued_ms: 33.4 * MIN + Math.floor(i / 3) * 52_000 + (i % 3) * 700,
		wait_ms: i % 3 === 2 ? 9800 : 120,
		ttft_ms: 6400 + (i % 3) * 1800,
		dur_ms: 38_000 + (i % 4) * 9000,
		provider_id: PROVIDERS[i % PROVIDERS.length],
		profile: 'STANDARD',
		effort: 'xhigh',
		input_tokens: 58_000 + i * 2400,
		output_tokens: 3400 + (i % 3) * 600,
		thinking_chars: i === 0 ? 2400 : 0
	});
});

seeds.push({
	id: nextId(),
	agent_id: 'ecosystem',
	phase_id: 'phase-4.6',
	caller: 'ecosystem_context.enrichment',
	task: 'Detect model releases',
	role: 'enrich',
	queued_ms: 36.6 * MIN,
	wait_ms: 130,
	ttft_ms: 7100,
	dur_ms: 52_000,
	provider_id: 'gcp',
	profile: 'STANDARD',
	effort: 'xhigh',
	input_tokens: 96_400,
	output_tokens: 2900
});

// --- Phase 4.7: the Illustrator ---------------------------------------------
//
// Not an LLM call. It goes through a separate image client, so it has no tokens,
// no cost, and no token stream — only a duration and a picture. It is in the
// fixture so the demo exercises the image branch of the transcript, which is the
// only call in a run whose output you can actually look at.
//
// The demo points at a real committed hero from the same date as the fixture; if
// that file is ever pruned the UI degrades to prompt-only, which is also worth
// seeing in the demo.
const DEMO_HERO_PROMPT = `You are generating a daily hero image for an AI news aggregator website.

## Your Goal
Create a playful, colorful editorial illustration that visually represents today's top AI news stories. The scene should immediately convey the themes of the day's news to readers.

## The Mascot (CRITICAL)
The attached image shows our skunk mascot. You MUST:
- Keep the EXACT circuit board pattern on the skunk's body and tail - this is a core part of the brand identity
- Maintain the skunk's white and black coloring with the tech circuit pattern visible
- The skunk must be ACTIVELY DOING SOMETHING related to the topics - typing on a keyboard, reading papers, adjusting equipment, pointing at a screen, holding tools, etc. NOT just standing and smiling at the camera!
- Position the skunk in the lower-left or lower-right portion, engaged with the scene

## Today's Stories

**Topic 1: Inference Economics Squeeze**
Three separate announcements converged on serving cost rather than training scale: a hardware partnership aimed squarely at throughput, a serving-stack release claiming a 2.4x gain on identical silicon, and a pricing change that only makes sense if the first two hold up. The throughline is that frontier capability is no longer the scarce resource.

**Topic 2: Speculative Decoding Convergence**
arXiv carried a cluster of papers on speculative decoding and KV-cache compression, which reads less like coincidence and more like a field converging on the same bottleneck. Two are follow-ups to a paper from last week.

**Topic 3: Split Community Reaction**
Practitioners treated the pricing move as straightforwardly good news; community threads were more sceptical, focusing on rate limits rather than headline cost.

## Visual Direction
Create a scene that represents these stories. You must include Topic 1 (the top story), then pick 2-3 others that would make the best scene together. Consider:
- What visual metaphors could represent these themes?
- How can the skunk mascot interact with or observe these elements?
- Suggested scene elements: throughput gauges, stacked server racks, cost curves bending downward, comparison charts

## Style Requirements
- Playful cartoon illustration, tech editorial art style
- Vibrant colors with Trend Red (#E63946) accents
- Energetic, forward-looking, tech-optimistic mood
- No company logos or watermarks - but topic-relevant company logos are encouraged when relevant to the stories
- 21:9 ultra-wide banner composition`;

seeds.push({
	id: 'hero',
	agent_id: 'hero',
	phase_id: 'phase-4.7',
	caller: 'hero_generator.compose',
	task: "Paint the day's scene",
	role: 'image',
	queued_ms: 37.6 * MIN,
	wait_ms: 0,
	ttft_ms: 0,
	dur_ms: 1.8 * MIN,
	provider_id: 'image',
	model: 'gemini-3-pro-image',
	profile: 'STANDARD',
	effort: 'high',
	input_tokens: 0,
	output_tokens: 0,
	image_url: '/data/2026-07-27/hero.webp',
	image_prompt: DEMO_HERO_PROMPT
});

const calls = seeds.map(buildCall).sort((a, b) => a.queued_ms - b.queued_ms);
const lastEnd = calls.reduce((m, c) => Math.max(m, c.end_ms), 0);
const DURATION = Math.round(lastEnd + 3.2 * MIN);

// ------------------------------------------------------------------ derived
function agentAgg(id: string) {
	const own = calls.filter((c) => c.agent_id === id);
	return {
		call_count: own.length,
		cost_usd: +own.reduce((s, c) => s + c.cost_usd, 0).toFixed(3),
		output_tokens: own.reduce((s, c) => s + c.output_tokens, 0)
	};
}

const CAT_LABEL: Record<string, string> = {
	news: 'News',
	research: 'Research',
	social: 'Social',
	reddit: 'Reddit'
};

const GATHER_BLURB: Record<string, string> = {
	news: 'Pulls RSS feeds and chases links surfaced by social posts.',
	research: 'Queries the arXiv API and research blogs.',
	social: 'Collects posts from Twitter, Bluesky and Mastodon.',
	reddit: 'Fetches subreddit listings and top comment threads.'
};

const ANALYST_BLURB: Record<string, string> = {
	news: 'Reads product launches and company announcements off the RSS wire.',
	research: 'Works through arXiv preprints and alignment blogs.',
	social: 'Follows what practitioners are saying on Twitter, Bluesky and Mastodon.',
	reddit: 'Digests community threads and the arguments underneath them.'
};

const GATHERED: Record<string, number> = { news: 318, research: 494, social: 226, reddit: 148 };

const agents: ReplayIndex['agents'] = [];
for (const cat of ['news', 'research', 'social', 'reddit']) {
	agents.push({
		id: `${cat}_gatherer`,
		label: `${CAT_LABEL[cat]} Scout`,
		kind: 'gatherer',
		category: cat,
		phase_ids: ['phase-1'],
		call_count: 0,
		cost_usd: 0,
		output_tokens: 0,
		items_in: null,
		items_out: GATHERED[cat],
		status: 'success',
		blurb: GATHER_BLURB[cat]
	});
}
for (const plan of ANALYST_PLAN) {
	const agg = agentAgg(`${plan.cat}_analyzer`);
	agents.push({
		id: `${plan.cat}_analyzer`,
		label: `${CAT_LABEL[plan.cat]} Analyst`,
		kind: 'analyzer',
		category: plan.cat,
		phase_ids: ['phase-2'],
		...agg,
		items_in: GATHERED[plan.cat],
		items_out: Math.round(GATHERED[plan.cat] * 0.82),
		status: 'success',
		blurb: ANALYST_BLURB[plan.cat]
	});
}

const DESK: [string, string, ReplayIndex['agents'][number]['kind'], string, string[]][] = [
	['continuity', 'Continuity Editor', 'synthesizer', "Links today's stories to the days before them.", ['phase-2.5']],
	['freshness', 'Fact Checker', 'synthesizer', 'Checks whether older anchor stories have gone stale.', ['phase-2.5']],
	['orchestrator', 'Editor in Chief', 'synthesizer', 'Finds the threads running across categories and writes the brief.', ['phase-3', 'phase-4']],
	['link_enricher', 'Copy Editor', 'enricher', 'Wires every claim in the prose back to the item it came from.', ['phase-4.5']],
	['ecosystem', 'Archivist', 'enricher', 'Watches for model releases worth recording.', ['phase-4.6']],
	['hero', 'Illustrator', 'imagegen', "Paints the day's scene around the AATF skunk.", ['phase-4.7']]
];
for (const [id, label, kind, blurb, phase_ids] of DESK) {
	agents.push({
		id,
		label,
		kind,
		category: null,
		phase_ids,
		...agentAgg(id),
		items_in: null,
		items_out: null,
		status: 'success',
		blurb
	});
}

const sources: ReplayIndex['sources'] = [
	{ agent_id: 'news_gatherer', name: 'RSS feeds', items: 246, status: 'success', start_ms: 1400, end_ms: 121_000 },
	{ agent_id: 'news_gatherer', name: 'Followed links', items: 72, status: 'partial', start_ms: 121_000, end_ms: 238_000 },
	{ agent_id: 'research_gatherer', name: 'arXiv', items: 441, status: 'success', start_ms: 1600, end_ms: 176_000 },
	{ agent_id: 'research_gatherer', name: 'LessWrong', items: 53, status: 'success', start_ms: 176_000, end_ms: 229_000 },
	{ agent_id: 'social_gatherer', name: 'Twitter', items: 141, status: 'success', start_ms: 1500, end_ms: 98_000 },
	{ agent_id: 'social_gatherer', name: 'Bluesky', items: 62, status: 'success', start_ms: 98_000, end_ms: 141_000 },
	{ agent_id: 'social_gatherer', name: 'Mastodon', items: 23, status: 'partial', start_ms: 141_000, end_ms: 187_000 },
	{ agent_id: 'reddit_gatherer', name: 'ScrapeCreators', items: 148, status: 'success', start_ms: 2100, end_ms: 214_000 }
];

const phases: ReplayIndex['phases'] = [
	{ id: 'phase-0', label: 'Ecosystem Context', ordinal: '0', start_ms: 0, end_ms: 1400, status: 'success', detail: '184 models loaded', error: null },
	{ id: 'phase-1', label: 'Parallel Gathering', ordinal: '1', start_ms: 1400, end_ms: 246_000, status: 'partial', detail: '1,186 items from 8 sources', error: null },
	{ id: 'phase-2', label: 'Parallel Analysis', ordinal: '2', start_ms: 246_000, end_ms: 17.2 * MIN, status: 'success', detail: '4 categories, 1,186 items', error: null },
	{ id: 'phase-2.5', label: 'Continuity & Freshness', ordinal: '2.5', start_ms: 17.2 * MIN, end_ms: 24 * MIN, status: 'success', detail: '11 storylines carried forward', error: null },
	{ id: 'phase-3', label: 'Cross-Category Topics', ordinal: '3', start_ms: 24 * MIN, end_ms: 29.3 * MIN, status: 'success', detail: '6 topics detected', error: null },
	{ id: 'phase-4', label: 'Executive Summary', ordinal: '4', start_ms: 29.3 * MIN, end_ms: 33.3 * MIN, status: 'partial', detail: 'Hit output ceiling, escalated', error: null },
	{ id: 'phase-4.5', label: 'Link Enrichment', ordinal: '4.5', start_ms: 33.3 * MIN, end_ms: 36.5 * MIN, status: 'success', detail: '7 passages linked', error: null },
	{ id: 'phase-4.6', label: 'Ecosystem Enrichment', ordinal: '4.6', start_ms: 36.5 * MIN, end_ms: 37.6 * MIN, status: 'success', detail: '2 releases recorded', error: null },
	{ id: 'phase-4.7', label: 'Hero Image', ordinal: '4.7', start_ms: 37.6 * MIN, end_ms: 39.4 * MIN, status: 'success', detail: 'Gemini 3 Pro, 21:9', error: null },
	{ id: 'phase-5', label: 'Assembly & Output', ordinal: '5', start_ms: 39.4 * MIN, end_ms: DURATION, status: 'success', detail: 'JSON, feeds, search corpus', error: null }
];

// Concurrency samples derived from the calls so the sparkline matches the bars.
const INTERVAL = 2000;
const samples: [number, number, number][] = [];
for (let t = 0; t <= DURATION; t += INTERVAL) {
	let active = 0;
	let queued = 0;
	for (const c of calls) {
		if (t >= c.start_ms && t < c.end_ms) active++;
		else if (t >= c.queued_ms && t < c.start_ms) queued++;
	}
	samples.push([t, active, queued]);
}
const peak = samples.reduce((m, s) => Math.max(m, s[1]), 0);

export const REPLAY_SAMPLE: ReplayIndex = {
	schema: 1,
	date: '2026-07-27',
	coverage_date: '2026-07-26',
	t0: '2026-07-27T07:02:11.442Z',
	duration_ms: DURATION,
	generated_by: 'replay_generator/1.0 (demo fixture)',
	run: {
		status: 'partial',
		total_items_analyzed: 1186,
		total_cost_usd: +calls.reduce((s, c) => s + c.cost_usd, 0).toFixed(2),
		total_input_tokens: calls.reduce((s, c) => s + c.input_tokens, 0),
		total_output_tokens: calls.reduce((s, c) => s + c.output_tokens, 0),
		llm_calls: calls.length,
		models: [
			'claude-5-opus-aws',
			'claude-5-opus-gcp',
			'claude-5-opus-anthropic',
			'gemini-3-pro-image'
		],
		peak_concurrency: peak,
		stream_available: true
	},
	phases,
	agents,
	sources,
	calls,
	concurrency: { interval_ms: INTERVAL, samples }
};

// ---------------------------------------------------------------- demo stream
const THINKING_SNIPPETS = [
	'Let me look at what actually landed today. ',
	'The volume is high, so the first job is separating announcements from commentary. ',
	'Several of these reference the same underlying release, which means they should collapse into one thread rather than four. ',
	'I should check the ecosystem context before calling anything new — the dates matter here. ',
	'Okay: the throughline is inference cost, not capability. That reframes the ranking. ',
	'Two of the research items are follow-ups to a paper from last week, so continuity should pick them up. ',
	'Writing this up now, leading with the release and treating the reaction as supporting material. '
];

// Long calls cycle through these snippets, so the list must wrap cleanly: the last
// entry ends with a blank line and the first opens a heading. Without that the
// wrap-around splices `## Executive Summary` onto the tail of a sentence, and the
// markdown renderer — correctly — treats it as literal text mid-paragraph.
//
// The set deliberately exercises every block the renderer supports (heading,
// paragraph, bullet list, bold, inline code, link) so the demo shows real
// formatting rather than one undifferentiated wall of prose.
const TEXT_SNIPPETS = [
	'## Executive Summary\n\n',
	'The dominant story today is **inference economics**. ',
	'Three separate announcements — a hardware partnership, a serving-stack release, and a pricing change — ',
	'all point at the same pressure: frontier capability is no longer the scarce resource, throughput is. ',
	'\n\n### What moved\n\n',
	'- A hardware partnership aimed squarely at **serving cost**, not training scale\n',
	'- A serving-stack release claiming a `2.4x` throughput gain on the same silicon\n',
	'- A pricing change that only makes sense if the first two are real\n',
	'\n On the research side, ',
	'arXiv carried a cluster of papers on speculative decoding and KV-cache compression, ',
	'which reads less like coincidence and more like a field converging on the same bottleneck. ',
	'\n\nCommunity reaction was notably split. ',
	'Practitioners on Twitter treated the pricing move as straightforwardly good news; ',
	'the Reddit threads were more sceptical, focusing on rate limits rather than headline cost. ',
	'\n\nWorth watching: whether the serving-stack release ships with the benchmarks it references.\n\n'
];

function buildStreamFor(call: ReplayCall): ReplayCallStreamLocal {
	const t: number[] = [];
	const kind: (0 | 1)[] = [];
	const text: string[] = [];
	const startAt = call.first_token_ms ?? call.start_ms;
	const span = Math.max(1000, call.end_ms - startAt);

	const hasThinking = call.thinking_chars > 0;
	const thinkFrac = hasThinking ? 0.38 : 0;
	// Capped: the real recorder coalesces deltas, and an uncapped marquee call would
	// synthesise ~800 chunks here purely as fixture bloat.
	const thinkChunks = hasThinking
		? Math.min(90, Math.max(6, Math.round(call.thinking_chars / 260)))
		: 0;
	const textChunks = Math.min(180, Math.max(10, Math.round(call.text_chars / 190)));

	for (let i = 0; i < thinkChunks; i++) {
		t.push(Math.round(startAt + (span * thinkFrac * i) / thinkChunks));
		kind.push(0);
		text.push(THINKING_SNIPPETS[i % THINKING_SNIPPETS.length]);
	}
	for (let i = 0; i < textChunks; i++) {
		t.push(Math.round(startAt + span * thinkFrac + (span * (1 - thinkFrac) * i) / textChunks));
		kind.push(1);
		text.push(TEXT_SNIPPETS[i % TEXT_SNIPPETS.length]);
	}
	return { t, kind, text };
}

interface ReplayCallStreamLocal {
	t: number[];
	kind: (0 | 1)[];
	text: string[];
}

export const REPLAY_SAMPLE_STREAM: ReplayStream = {
	schema: 1,
	date: REPLAY_SAMPLE.date,
	calls: Object.fromEntries(
		calls.filter((c) => c.has_stream).map((c) => [c.id, buildStreamFor(c)])
	)
};
