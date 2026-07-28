<script lang="ts">
	import { onDestroy, onMount } from 'svelte';
	import { page } from '$app/stores';
	import { browser } from '$app/environment';
	import { currentDate, resolveLatestDate } from '$lib/stores/dateStore';
	import { parseDate, formatDate } from '$lib/services/dateUtils';
	import { loadReplayIndex, loadReplayStream } from '$lib/services/replayLoader';
	import {
		createReplayEngine,
		SPEEDS,
		type ReplayEngine,
		type ReplayFrame,
		type Speed
	} from '$lib/services/replayEngine';
	import { formatClock, formatTokens, formatCost, formatDuration } from '$lib/services/replayViz';
	import type { ReplayCall, ReplayCallStream, ReplayIndex, ReplayStream } from '$lib/types/replay';
	import { REPLAY_SAMPLE, REPLAY_SAMPLE_STREAM } from '$lib/fixtures/replaySample';
	import Newsroom from '$lib/components/replay/Newsroom.svelte';
	import Timeline from '$lib/components/replay/Timeline.svelte';
	import Transcript from '$lib/components/replay/Transcript.svelte';
	import PlaybackBar from '$lib/components/replay/PlaybackBar.svelte';
	import PhaseRail from '$lib/components/replay/PhaseRail.svelte';
	import LoadingSpinner from '$lib/components/common/LoadingSpinner.svelte';

	type View = 'newsroom' | 'timeline';

	let index: ReplayIndex | null = null;
	let engine: ReplayEngine | null = null;
	let frame: ReplayFrame | null = null;
	let playing = false;
	let speed: Speed = 16;
	let loading = true;
	let loadError: string | null = null;
	let view: View = 'newsroom';
	let reduced = false;
	let showShortcuts = false;

	let selectedCallId: string | null = null;
	let stream: ReplayStream | null = null;
	let streamState: 'idle' | 'loading' | 'ready' | 'unavailable' = 'idle';

	let unsubFrame: (() => void) | null = null;
	let unsubPlaying: (() => void) | null = null;
	let unsubSpeed: (() => void) | null = null;
	let lastLoadedKey = '';

	$: dateParam = $page.url.searchParams.get('date');
	$: isDemo = $page.url.searchParams.get('demo') === '1';
	$: effectiveDate = dateParam && parseDate(dateParam) ? dateParam : ($currentDate || null);
	$: loadKey = isDemo ? 'demo' : (effectiveDate ?? '');

	$: if (browser && loadKey && loadKey !== lastLoadedKey) {
		lastLoadedKey = loadKey;
		void boot(loadKey);
	}

	$: selectedCall = index && selectedCallId
		? (index.calls.find((c) => c.id === selectedCallId) ?? null)
		: null;
	$: selectedStream = selectedCall && stream ? (stream.calls[selectedCall.id] ?? null) : null;
	$: activeNow = frame ? frame.active.filter((a) => a.state !== 'queued').length : 0;

	onMount(() => {
		if (typeof window !== 'undefined' && window.matchMedia) {
			const mq = window.matchMedia('(prefers-reduced-motion: reduce)');
			reduced = mq.matches;
			const onChange = (e: MediaQueryListEvent) => (reduced = e.matches);
			mq.addEventListener('change', onChange);
			return () => mq.removeEventListener('change', onChange);
		}
	});

	async function boot(key: string) {
		teardown();
		loading = true;
		loadError = null;
		selectedCallId = null;
		stream = null;
		streamState = 'idle';

		try {
			if (key === 'demo') {
				index = REPLAY_SAMPLE;
			} else {
				let date: string | null = key;
				if (!date) date = await resolveLatestDate(false);
				if (!date) throw new Error('No reports available yet.');
				index = await loadReplayIndex(date);
			}
			attach(index);
		} catch (e) {
			index = null;
			loadError = e instanceof Error ? e.message : 'Failed to load replay data';
		} finally {
			loading = false;
		}
	}

	function attach(idx: ReplayIndex) {
		const eng = createReplayEngine(idx, speed);
		engine = eng;
		unsubFrame = eng.frame.subscribe((f) => (frame = f));
		unsubPlaying = eng.playing.subscribe((p) => (playing = p));
		unsubSpeed = eng.speed.subscribe((s) => (speed = s));
		if (!reduced) eng.play();
	}

	function teardown() {
		unsubFrame?.();
		unsubPlaying?.();
		unsubSpeed?.();
		unsubFrame = unsubPlaying = unsubSpeed = null;
		engine?.destroy();
		engine = null;
		frame = null;
	}

	onDestroy(teardown);

	/** Opening a transcript is the only thing that fetches the (optional) stream file. */
	async function selectCall(callId: string) {
		selectedCallId = callId;
		if (streamState !== 'idle' || !index) return;

		if (isDemo) {
			stream = REPLAY_SAMPLE_STREAM;
			streamState = 'ready';
			return;
		}
		if (!index.run.stream_available) {
			streamState = 'unavailable';
			return;
		}

		streamState = 'loading';
		const loaded = await loadReplayStream(index.date);
		stream = loaded;
		streamState = loaded ? 'ready' : 'unavailable';
	}

	function onKeydown(event: KeyboardEvent) {
		if (!engine) return;
		const target = event.target as HTMLElement | null;
		if (target && (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.isContentEditable)) {
			return;
		}

		if (event.key === ' ') {
			event.preventDefault();
			engine.toggle();
		} else if (event.key === 'ArrowLeft') {
			event.preventDefault();
			engine.seekBy(-(engine.duration / 60) * (event.shiftKey ? 5 : 1));
		} else if (event.key === 'ArrowRight') {
			event.preventDefault();
			engine.seekBy((engine.duration / 60) * (event.shiftKey ? 5 : 1));
		} else if (event.key === ',') {
			event.preventDefault();
			engine.prevEvent();
		} else if (event.key === '.') {
			event.preventDefault();
			engine.nextEvent();
		} else if (event.key >= '1' && event.key <= '9') {
			event.preventDefault();
			// Digit N selects the Nth speed, so the ladder stays the source of truth.
			engine.speed.set(SPEEDS[Number(event.key) - 1]);
		} else if (event.key === 'v' || event.key === 'V') {
			view = view === 'newsroom' ? 'timeline' : 'newsroom';
		} else if (event.key === 'Escape' && selectedCallId) {
			selectedCallId = null;
		} else if (event.key === '?') {
			showShortcuts = !showShortcuts;
		}
	}

	function setSpeed(s: Speed) {
		engine?.speed.set(s);
	}

	$: prettyDate = index ? formatDate(index.date, 'EEEE, MMMM d, yyyy') : '';
</script>

<svelte:head>
	<title>LLM Replay{index ? ` — ${index.date}` : ''} | AATF AI News Aggregator</title>
	<meta
		name="description"
		content="A time-scrubbed reconstruction of the AATF AI news pipeline: every agent, every LLM call, replayed from real timestamps."
	/>
</svelte:head>

<svelte:window on:keydown={onKeydown} />

<div class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
	<!-- Title block -->
	<header class="mb-6">
		<div class="flex flex-wrap items-end justify-between gap-3">
			<div>
				<p class="eyebrow">Pipeline replay</p>
				<h1 class="text-2xl sm:text-3xl font-bold text-trend-gray-800 dark:text-trend-gray-100">
					The newsroom, replayed
					<span class="built-by">(interface built by Opus 5)</span>
				</h1>
				<p class="mt-1 text-sm text-trend-gray-600 dark:text-trend-gray-400 max-w-2xl">
					Every animation below is driven by a real timestamp from a real request. Watch the cast
					wake up, take work, and report in.
					{#if index}
						<span class="whitespace-nowrap">
							<strong class="text-trend-gray-800 dark:text-trend-gray-200">{prettyDate}</strong>
						</span>
					{/if}
				</p>
			</div>

			{#if index}
				<a
					href="/?date={index.date}"
					class="text-sm font-medium text-trend-red hover:text-guardian-red transition-colors"
				>
					&larr; Back to the report
				</a>
			{/if}
		</div>
	</header>

	{#if loading}
		<div class="py-24"><LoadingSpinner size="lg" /></div>
	{:else if loadError || !index}
		<!-- The common case: historical dates predate replay capture. -->
		<div class="card text-center py-14 px-6">
			<div class="mx-auto mb-4 w-14 h-14 rounded-full grid place-items-center bg-trend-red/10">
				<svg
					class="w-7 h-7 text-trend-red"
					fill="none"
					viewBox="0 0 24 24"
					stroke="currentColor"
					stroke-width="1.6"
					aria-hidden="true"
				>
					<path stroke-linecap="round" stroke-linejoin="round" d="M12 6v6l4 2" />
					<circle cx="12" cy="12" r="9" />
				</svg>
			</div>
			<h2 class="text-lg font-semibold text-trend-gray-800 dark:text-trend-gray-100">
				No replay for {effectiveDate ?? 'this date'}
			</h2>
			<p class="mt-2 text-sm text-trend-gray-600 dark:text-trend-gray-400 max-w-md mx-auto">
				Replay capture records the pipeline as it runs, so it only exists for dates after the
				feature shipped. Older reports were generated before there was anything watching.
			</p>
			<div class="mt-5 flex flex-wrap items-center justify-center gap-3">
				<a href="/replay?demo=1" class="btn-primary text-sm">Watch a demo run</a>
				<a href="/?date={effectiveDate ?? ''}" class="btn-secondary text-sm">Back to the report</a>
			</div>
		</div>
	{:else if frame}
		{#if isDemo}
			<div class="demo-banner">
				<strong>Demo run.</strong> Synthetic-but-plausible data conforming to the replay schema — useful
				for seeing every state (route failover, truncation, a pruned stream) in one pass.
			</div>
		{/if}

		<!-- Run header stats -->
		<div class="run-stats">
			<div><span class="rs-label">Calls</span><span class="rs-value">{index.run.llm_calls}</span></div>
			<div>
				<span class="rs-label">Run time</span>
				<span class="rs-value">{formatDuration(index.duration_ms)}</span>
			</div>
			<div>
				<span class="rs-label">Items</span>
				<span class="rs-value">{index.run.total_items_analyzed.toLocaleString()}</span>
			</div>
			<div>
				<span class="rs-label">Tokens in</span>
				<span class="rs-value">{formatTokens(index.run.total_input_tokens)}</span>
			</div>
			<div>
				<span class="rs-label">Tokens out</span>
				<span class="rs-value">{formatTokens(index.run.total_output_tokens)}</span>
			</div>
			<div>
				<span class="rs-label">Cost</span>
				<span class="rs-value">{formatCost(index.run.total_cost_usd)}</span>
			</div>
			<div>
				<span class="rs-label">Peak conc.</span>
				<span class="rs-value">{index.run.peak_concurrency}</span>
			</div>
			<div>
				<span class="rs-label">Status</span>
				<span class="rs-value capitalize" data-status={index.run.status}>{index.run.status}</span>
			</div>
		</div>

		<!-- Phase rail -->
		<div class="my-4">
			<PhaseRail {index} t={frame.t} onSeek={(ms) => engine?.seek(ms)} />
		</div>

		<!-- View switch -->
		<div class="flex items-center justify-between gap-3 mb-3 flex-wrap">
			<div class="viewswitch" role="tablist" aria-label="Replay view">
				<button
					type="button"
					role="tab"
					aria-selected={view === 'newsroom'}
					class:on={view === 'newsroom'}
					on:click={() => (view = 'newsroom')}
				>
					Newsroom
				</button>
				<button
					type="button"
					role="tab"
					aria-selected={view === 'timeline'}
					class:on={view === 'timeline'}
					on:click={() => (view = 'timeline')}
				>
					Timeline
				</button>
			</div>

			<div class="flex items-center gap-3">
				<label class="reduced-toggle">
					<input type="checkbox" bind:checked={reduced} />
					<span>Reduce motion</span>
				</label>
				<button
					type="button"
					class="hint-btn"
					on:click={() => (showShortcuts = !showShortcuts)}
					aria-expanded={showShortcuts}
				>
					Keyboard <kbd>?</kbd>
				</button>
			</div>
		</div>

		{#if showShortcuts}
			<div class="shortcuts">
				<span><kbd>space</kbd> play / pause</span>
				<span><kbd>←</kbd><kbd>→</kbd> seek (hold shift for 5×)</span>
				<span><kbd>,</kbd><kbd>.</kbd> step event</span>
				<span><kbd>1</kbd>–<kbd>9</kbd> speed</span>
				<span><kbd>v</kbd> switch view</span>
				<span><kbd>esc</kbd> close transcript</span>
			</div>
		{/if}

		<!-- The stage -->
		<div class="mb-3">
			{#if view === 'newsroom'}
				<Newsroom {index} {frame} {reduced} onSelectCall={selectCall} />
			{:else}
				<Timeline
					{index}
					t={frame.t}
					{selectedCallId}
					onSelectCall={selectCall}
					onSeek={(ms) => engine?.seek(ms)}
				/>
			{/if}
		</div>

		<!-- Transcript, opened by clicking any call -->
		{#if selectedCall}
			<div class="mb-3">
				<Transcript
					call={selectedCall}
					stream={selectedStream as ReplayCallStream | null}
					{streamState}
					runHasStreams={index.run.stream_available}
					timingsMeasured={index.run.timings_measured !== false}
					t={frame.t}
					{reduced}
					onSeek={(ms) => engine?.seek(ms)}
					onClose={() => (selectedCallId = null)}
				/>
			</div>
		{:else}
			<p class="pick-hint">
				Click any agent's in-flight call{view === 'timeline' ? ' or timeline bar' : ''} to replay what
				the model actually wrote.
			</p>
		{/if}

		<!-- Persistent controls -->
		<div class="dock">
			<PlaybackBar
				{index}
				t={frame.t}
				{playing}
				{speed}
				activeCount={activeNow}
				onToggle={() => engine?.toggle()}
				onSeek={(ms) => engine?.seek(ms)}
				onSpeed={setSpeed}
				onNextEvent={() => engine?.nextEvent()}
				onPrevEvent={() => engine?.prevEvent()}
			/>
		</div>

		<p class="footnote">
			{index.run.llm_calls} requests across {index.run.models.length} route{index.run.models.length === 1
				? ''
				: 's'} · real elapsed time {formatClock(index.duration_ms)} · generated by
			<code>{index.generated_by}</code>
			{#if !index.run.stream_available}
				· no token-level capture for this date
			{/if}
			{#if index.run.timings_measured === false}
				· timings reconstructed from run logs
			{/if}
		</p>
	{/if}
</div>

<style>
	.eyebrow {
		font-size: 0.62rem;
		font-weight: 700;
		letter-spacing: 0.14em;
		text-transform: uppercase;
		color: #E63946;
		margin-bottom: 2px;
	}

	/* Credit for this interface, not for the pipeline. Sits on the title's baseline so
	   it reads as an aside; wraps under the title rather than widening it on mobile. */
	.built-by {
		display: inline-block;
		font-size: 0.72rem;
		font-weight: 500;
		letter-spacing: 0.01em;
		color: #737373;
		white-space: nowrap;
		vertical-align: baseline;
		margin-left: 0.15rem;
	}
	:global(.dark) .built-by {
		color: #8a8a8a;
	}
	@media (max-width: 420px) {
		.built-by {
			font-size: 0.66rem;
		}
	}

	.demo-banner {
		font-size: 0.75rem;
		line-height: 1.5;
		padding: 0.5rem 0.75rem;
		border-radius: 0.5rem;
		margin-bottom: 0.75rem;
		background: rgb(139 92 246 / 0.1);
		border-left: 3px solid #8b5cf6;
		color: #525252;
	}
	:global(.dark) .demo-banner {
		color: #d4d4d4;
	}

	.run-stats {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(5.5rem, 1fr));
		gap: 0.4rem;
	}
	.run-stats > div {
		display: flex;
		flex-direction: column;
		padding: 0.4rem 0.55rem;
		border-radius: 0.5rem;
		background: rgb(0 0 0 / 0.035);
	}
	:global(.dark) .run-stats > div {
		background: rgb(255 255 255 / 0.045);
	}
	.rs-label {
		font-size: 0.52rem;
		letter-spacing: 0.08em;
		text-transform: uppercase;
		color: #737373;
	}
	.rs-value {
		font-size: 0.95rem;
		font-weight: 700;
		font-variant-numeric: tabular-nums;
		color: #262626;
		line-height: 1.2;
	}
	:global(.dark) .rs-value {
		color: #f5f5f5;
	}
	.rs-value[data-status='partial'] {
		color: #f59e0b;
	}
	.rs-value[data-status='failed'] {
		color: #ef4444;
	}
	.rs-value[data-status='success'] {
		color: #10b981;
	}

	.viewswitch {
		display: inline-flex;
		border-radius: 8px;
		overflow: hidden;
		border: 1px solid rgb(0 0 0 / 0.12);
	}
	:global(.dark) .viewswitch {
		border-color: rgb(255 255 255 / 0.13);
	}
	.viewswitch button {
		font-size: 0.75rem;
		font-weight: 650;
		padding: 5px 14px;
		color: #525252;
		transition: background 140ms ease, color 140ms ease;
	}
	:global(.dark) .viewswitch button {
		color: #a3a3a3;
	}
	.viewswitch button.on {
		background: #E63946;
		color: #fff;
	}

	.reduced-toggle {
		display: inline-flex;
		align-items: center;
		gap: 0.3rem;
		font-size: 0.68rem;
		color: #737373;
		cursor: pointer;
	}
	.reduced-toggle input {
		accent-color: #E63946;
	}

	.hint-btn {
		font-size: 0.68rem;
		color: #737373;
		display: inline-flex;
		align-items: center;
		gap: 0.3rem;
	}
	.hint-btn:hover {
		color: #E63946;
	}

	.shortcuts {
		display: flex;
		flex-wrap: wrap;
		gap: 0.85rem;
		font-size: 0.66rem;
		color: #737373;
		padding: 0.45rem 0.7rem;
		border-radius: 0.5rem;
		background: rgb(0 0 0 / 0.035);
		margin-bottom: 0.6rem;
	}
	:global(.dark) .shortcuts {
		background: rgb(255 255 255 / 0.045);
		color: #a3a3a3;
	}

	kbd {
		font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
		font-size: 0.6rem;
		padding: 0 4px;
		border-radius: 3px;
		border: 1px solid rgb(0 0 0 / 0.18);
		background: rgb(255 255 255 / 0.8);
		color: #404040;
		margin: 0 1px;
	}
	:global(.dark) kbd {
		border-color: rgb(255 255 255 / 0.2);
		background: rgb(0 0 0 / 0.4);
		color: #d4d4d4;
	}

	.pick-hint {
		font-size: 0.7rem;
		color: #a3a3a3;
		text-align: center;
		margin-bottom: 0.75rem;
		font-style: italic;
	}

	.dock {
		position: sticky;
		bottom: 0.75rem;
		z-index: 20;
	}

	.footnote {
		margin-top: 0.9rem;
		font-size: 0.65rem;
		color: #a3a3a3;
		text-align: center;
	}
	.footnote code {
		font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
		font-size: 0.62rem;
	}

	@media (prefers-reduced-motion: reduce) {
		.viewswitch button {
			transition: none;
		}
	}
</style>
