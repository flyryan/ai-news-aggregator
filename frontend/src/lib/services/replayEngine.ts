/**
 * Playback engine for the LLM Replay.
 *
 * One `requestAnimationFrame` loop drives a single clock (`t`, ms since run
 * start). Every view — Newsroom, Timeline, Transcript — reads derived state
 * from this one clock, so switching views never loses your place.
 *
 * Performance contract:
 *   - Calls are pre-sorted once into by-start and by-end arrays with prefix
 *     sums, so per-frame derivation is O(log n + in-flight) rather than a scan.
 *   - Seeking backwards is the same cost as seeking forwards: state is derived
 *     from `t`, never accumulated by replaying from zero.
 *   - The RAF loop stops entirely when paused or when the tab is hidden.
 */

import { writable, derived, get, type Readable, type Writable } from 'svelte/store';
import type { ReplayCall, ReplayIndex, ReplayPhase } from '$lib/types/replay';

// 1x (true real time) is the floor worth watching -- a ~27 minute run is already
// slow enough to follow token by token. 0.5x is kept as the single slow notch for
// inspecting a busy moment; everything else climbs, since compressing the run is
// the point.
export const SPEEDS = [0.5, 1, 2, 4, 8, 16, 30, 60, 120] as const;
export type Speed = (typeof SPEEDS)[number];

/** How long a "reporting in" pulse stays on screen, in run-milliseconds at 1x. */
export const PULSE_WINDOW_MS = 1400;
/** How long an agent keeps its "just finished" flash. */
export const FLASH_WINDOW_MS = 2200;

export type CallPhaseState = 'pending' | 'queued' | 'waiting' | 'streaming' | 'done';

export interface ActiveCall {
	call: ReplayCall;
	/** 0..1 through the whole queued→end span. */
	progress: number;
	state: CallPhaseState;
	/** Output tokens emitted so far, linearly interpolated across the stream span. */
	tokens: number;
}

export interface CallPulse {
	call: ReplayCall;
	/** 0 at the instant the call ended, 1 when the pulse has fully faded. */
	age: number;
}

export interface AgentFrameState {
	agent_id: string;
	/** idle → the agent has not started; active → at least one call in flight. */
	status: 'idle' | 'active' | 'done';
	active: ActiveCall[];
	completed: number;
	total: number;
	output_tokens: number;
	cost_usd: number;
	/** 0..1, decaying — drives the completion flash. */
	flash: number;
	/** Most recent finished call, for the "reported in" caption. */
	last_done: ReplayCall | null;
}

export interface SourceFrameState {
	agent_id: string;
	name: string;
	items: number;
	status: string;
	progress: number;
	active: boolean;
	done: boolean;
}

export interface ReplayFrame {
	t: number;
	active: ActiveCall[];
	pulses: CallPulse[];
	agents: Map<string, AgentFrameState>;
	sources: SourceFrameState[];
	phase: ReplayPhase | null;
	phaseProgress: number;
	completedCalls: number;
	output_tokens: number;
	input_tokens: number;
	cost_usd: number;
	concurrency: { active: number; queued: number };
}

interface Prepared {
	index: ReplayIndex;
	byStart: ReplayCall[];
	byEnd: ReplayCall[];
	startTimes: number[];
	endTimes: number[];
	/** Prefix sums aligned to `byEnd`; element i is the total *after* i calls end. */
	cumOutput: number[];
	cumInput: number[];
	cumCost: number[];
	/** Per-agent prefix sums, keyed by agent id, aligned to that agent's byEnd list. */
	agentEnds: Map<string, { times: number[]; calls: ReplayCall[]; cumOutput: number[]; cumCost: number[] }>;
	/** Every distinct moment something happens, for "skip to next event". */
	eventTimes: number[];
}

function upperBound(arr: number[], value: number): number {
	let lo = 0;
	let hi = arr.length;
	while (lo < hi) {
		const mid = (lo + hi) >> 1;
		if (arr[mid] <= value) lo = mid + 1;
		else hi = mid;
	}
	return lo;
}

function lowerBound(arr: number[], value: number): number {
	let lo = 0;
	let hi = arr.length;
	while (lo < hi) {
		const mid = (lo + hi) >> 1;
		if (arr[mid] < value) lo = mid + 1;
		else hi = mid;
	}
	return lo;
}

function prepare(index: ReplayIndex): Prepared {
	const calls = index.calls ?? [];
	const byStart = [...calls].sort((a, b) => a.queued_ms - b.queued_ms);
	const byEnd = [...calls].sort((a, b) => a.end_ms - b.end_ms);

	const startTimes = byStart.map((c) => c.queued_ms);
	const endTimes = byEnd.map((c) => c.end_ms);

	const cumOutput: number[] = [0];
	const cumInput: number[] = [0];
	const cumCost: number[] = [0];
	for (let i = 0; i < byEnd.length; i++) {
		cumOutput.push(cumOutput[i] + (byEnd[i].output_tokens || 0));
		cumInput.push(cumInput[i] + (byEnd[i].input_tokens || 0));
		cumCost.push(cumCost[i] + (byEnd[i].cost_usd || 0));
	}

	const agentEnds = new Map<string, { times: number[]; calls: ReplayCall[]; cumOutput: number[]; cumCost: number[] }>();
	for (const call of byEnd) {
		let entry = agentEnds.get(call.agent_id);
		if (!entry) {
			entry = { times: [], calls: [], cumOutput: [0], cumCost: [0] };
			agentEnds.set(call.agent_id, entry);
		}
		entry.times.push(call.end_ms);
		entry.calls.push(call);
		entry.cumOutput.push(entry.cumOutput[entry.cumOutput.length - 1] + (call.output_tokens || 0));
		entry.cumCost.push(entry.cumCost[entry.cumCost.length - 1] + (call.cost_usd || 0));
	}

	const events = new Set<number>([0]);
	for (const call of calls) {
		events.add(call.queued_ms);
		events.add(call.start_ms);
		if (call.first_token_ms != null) events.add(call.first_token_ms);
		events.add(call.end_ms);
	}
	for (const source of index.sources ?? []) {
		events.add(source.start_ms);
		events.add(source.end_ms);
	}
	for (const phase of index.phases ?? []) {
		events.add(phase.start_ms);
		events.add(phase.end_ms);
	}
	const eventTimes = [...events].filter((t) => Number.isFinite(t) && t >= 0).sort((a, b) => a - b);

	return { index, byStart, byEnd, startTimes, endTimes, cumOutput, cumInput, cumCost, agentEnds, eventTimes };
}

function callStateAt(call: ReplayCall, t: number): CallPhaseState {
	if (t < call.queued_ms) return 'pending';
	if (t >= call.end_ms) return 'done';
	if (t < call.start_ms) return 'queued';
	const firstToken = call.first_token_ms;
	if (firstToken != null && t < firstToken) return 'waiting';
	if (firstToken == null) return 'waiting';
	return 'streaming';
}

function deriveFrame(p: Prepared, t: number): ReplayFrame {
	const { index } = p;

	// Only calls that have been queued by now can possibly be active.
	const queuedUpto = upperBound(p.startTimes, t);
	const active: ActiveCall[] = [];
	for (let i = 0; i < queuedUpto; i++) {
		const call = p.byStart[i];
		if (call.end_ms <= t) continue;
		const span = Math.max(1, call.end_ms - call.queued_ms);
		const progress = Math.min(1, Math.max(0, (t - call.queued_ms) / span));
		const state = callStateAt(call, t);
		let tokens = 0;
		if (state === 'streaming' && call.first_token_ms != null) {
			const streamSpan = Math.max(1, call.end_ms - call.first_token_ms);
			tokens = Math.round(
				(call.output_tokens || 0) * Math.min(1, (t - call.first_token_ms) / streamSpan)
			);
		}
		active.push({ call, progress, state, tokens });
	}

	// Calls that finished inside the pulse window are "reporting in" right now.
	const endedUpto = upperBound(p.endTimes, t);
	const pulseFrom = lowerBound(p.endTimes, t - PULSE_WINDOW_MS);
	const pulses: CallPulse[] = [];
	for (let i = pulseFrom; i < endedUpto; i++) {
		const call = p.byEnd[i];
		pulses.push({ call, age: Math.min(1, Math.max(0, (t - call.end_ms) / PULSE_WINDOW_MS)) });
	}

	const agents = new Map<string, AgentFrameState>();
	for (const agent of index.agents ?? []) {
		const entry = p.agentEnds.get(agent.id);
		const completed = entry ? upperBound(entry.times, t) : 0;
		const agentActive = active.filter((a) => a.call.agent_id === agent.id);
		const lastDone = entry && completed > 0 ? entry.calls[completed - 1] : null;
		const flash =
			lastDone != null
				? Math.max(0, 1 - (t - lastDone.end_ms) / FLASH_WINDOW_MS)
				: 0;

		let status: AgentFrameState['status'] = 'idle';
		if (agentActive.length > 0) status = 'active';
		else if (completed > 0) status = 'done';

		agents.set(agent.id, {
			agent_id: agent.id,
			status,
			active: agentActive,
			completed,
			total: agent.call_count ?? (entry ? entry.calls.length : 0),
			output_tokens: entry ? entry.cumOutput[completed] : 0,
			cost_usd: entry ? entry.cumCost[completed] : 0,
			flash,
			last_done: lastDone
		});
	}

	// Gatherers do non-LLM work; they get their own liveness from `sources`.
	const sources: SourceFrameState[] = (index.sources ?? []).map((s) => {
		const span = Math.max(1, s.end_ms - s.start_ms);
		const progress = Math.min(1, Math.max(0, (t - s.start_ms) / span));
		const active = t >= s.start_ms && t < s.end_ms;
		const done = t >= s.end_ms;
		if (active || done) {
			const agentState = agents.get(s.agent_id);
			if (agentState && agentState.status === 'idle') {
				agentState.status = active ? 'active' : 'done';
			}
		}
		return { agent_id: s.agent_id, name: s.name, items: s.items, status: s.status, progress, active, done };
	});

	let phase: ReplayPhase | null = null;
	for (const candidate of index.phases ?? []) {
		if (t >= candidate.start_ms && t < candidate.end_ms) {
			phase = candidate;
			break;
		}
		if (t >= candidate.end_ms) phase = candidate;
	}
	const phaseProgress = phase
		? Math.min(1, Math.max(0, (t - phase.start_ms) / Math.max(1, phase.end_ms - phase.start_ms)))
		: 0;

	const samples = index.concurrency?.samples ?? [];
	const interval = index.concurrency?.interval_ms || 2000;
	const sampleIdx = Math.min(samples.length - 1, Math.max(0, Math.floor(t / interval)));
	const sample = samples.length > 0 ? samples[sampleIdx] : [0, 0, 0];

	return {
		t,
		active,
		pulses,
		agents,
		sources,
		phase,
		phaseProgress,
		completedCalls: endedUpto,
		output_tokens: p.cumOutput[endedUpto],
		input_tokens: p.cumInput[endedUpto],
		cost_usd: p.cumCost[endedUpto],
		concurrency: { active: sample[1] ?? 0, queued: sample[2] ?? 0 }
	};
}

export interface ReplayEngine {
	frame: Readable<ReplayFrame>;
	playing: Readable<boolean>;
	speed: Writable<Speed>;
	duration: number;
	eventTimes: number[];
	play(): void;
	pause(): void;
	toggle(): void;
	seek(t: number): void;
	seekBy(delta: number): void;
	nextEvent(): void;
	prevEvent(): void;
	restart(): void;
	destroy(): void;
}

export function createReplayEngine(index: ReplayIndex, initialSpeed: Speed = 16): ReplayEngine {
	const prepared = prepare(index);
	const duration = Math.max(1, index.duration_ms || 1);

	const frame = writable<ReplayFrame>(deriveFrame(prepared, 0));
	const playing = writable(false);
	const speed = writable<Speed>(initialSpeed);

	let t = 0;
	let raf = 0;
	let lastWall = 0;
	let currentSpeed: Speed = initialSpeed;
	const unsubSpeed = speed.subscribe((v) => (currentSpeed = v));

	function emit() {
		frame.set(deriveFrame(prepared, t));
	}

	function stopLoop() {
		if (raf) {
			cancelAnimationFrame(raf);
			raf = 0;
		}
	}

	function tick(now: number) {
		if (!lastWall) lastWall = now;
		const dtWall = Math.min(250, now - lastWall);
		lastWall = now;
		t += dtWall * currentSpeed;
		if (t >= duration) {
			t = duration;
			emit();
			pause();
			return;
		}
		emit();
		raf = requestAnimationFrame(tick);
	}

	function play() {
		if (get(playing)) return;
		if (t >= duration) t = 0;
		playing.set(true);
		lastWall = 0;
		stopLoop();
		raf = requestAnimationFrame(tick);
	}

	function pause() {
		if (!get(playing)) {
			stopLoop();
			return;
		}
		playing.set(false);
		stopLoop();
	}

	function toggle() {
		if (get(playing)) pause();
		else play();
	}

	function seek(next: number) {
		t = Math.min(duration, Math.max(0, next));
		lastWall = 0;
		emit();
	}

	function seekBy(delta: number) {
		seek(t + delta);
	}

	function nextEvent() {
		const idx = upperBound(prepared.eventTimes, t + 1);
		seek(idx < prepared.eventTimes.length ? prepared.eventTimes[idx] : duration);
	}

	function prevEvent() {
		const idx = lowerBound(prepared.eventTimes, t - 1) - 1;
		seek(idx >= 0 ? prepared.eventTimes[idx] : 0);
	}

	function restart() {
		seek(0);
		play();
	}

	function onVisibility() {
		if (typeof document !== 'undefined' && document.hidden) {
			// Freeze rather than pause: resuming shouldn't need a click.
			stopLoop();
		} else if (get(playing)) {
			lastWall = 0;
			stopLoop();
			raf = requestAnimationFrame(tick);
		}
	}

	if (typeof document !== 'undefined') {
		document.addEventListener('visibilitychange', onVisibility);
	}

	function destroy() {
		stopLoop();
		unsubSpeed();
		if (typeof document !== 'undefined') {
			document.removeEventListener('visibilitychange', onVisibility);
		}
	}

	return {
		frame,
		playing,
		speed,
		duration,
		eventTimes: prepared.eventTimes,
		play,
		pause,
		toggle,
		seek,
		seekBy,
		nextEvent,
		prevEvent,
		restart,
		destroy
	};
}

/** Convenience: a store of just the clock, for components that need only `t`. */
export function clockOf(engine: ReplayEngine): Readable<number> {
	return derived(engine.frame, (f) => f.t);
}
