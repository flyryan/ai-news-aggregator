<script lang="ts">
	import { onDestroy, onMount, tick } from 'svelte';
	import { page } from '$app/stores';
	import { browser } from '$app/environment';
	import { currentDate, resolveLatestDate } from '$lib/stores/dateStore';
	import { parseDate, formatDate } from '$lib/services/dateUtils';
	import { loadReplayIndex, loadReplayPrompts, loadReplayStream } from '$lib/services/replayLoader';
	import {
		createReplayEngine,
		SPEEDS,
		type ReplayEngine,
		type ReplayFrame,
		type Speed
	} from '$lib/services/replayEngine';
	import { formatClock, formatTokens, formatCost, formatDuration } from '$lib/services/replayViz';
	import type {
		ReplayCall,
		ReplayCallStream,
		ReplayIndex,
		ReplayPrompts,
		ReplayStream
	} from '$lib/types/replay';
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
	let speed: Speed = 8;
	let loading = true;
	let loadError: string | null = null;
	let view: View = 'newsroom';
	let reduced = false;
	let showShortcuts = false;

	let selectedCallId: string | null = null;
	let stream: ReplayStream | null = null;
	let streamState: 'idle' | 'loading' | 'ready' | 'unavailable' = 'idle';
	// Prompts are the largest artifact and are only fetched when someone actually
	// unfolds one, so this stays idle through normal playback.
	let prompts: ReplayPrompts | null = null;
	let promptsState: 'idle' | 'loading' | 'ready' | 'unavailable' = 'idle';

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
		prompts = null;
		promptsState = 'idle';

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
		releasePin('teardown');
	}

	onDestroy(teardown);

	/** The transcript pane, which the pin below keeps in view. */
	let transcriptEl: HTMLDivElement | null = null;

	/**
	 * Keep the open transcript pinned at the bottom of the page.
	 *
	 * A one-shot scroll cannot hold this position: the Newsroom above the pane grows
	 * continuously during playback, so the transcript is pushed down the document
	 * while you read it. The pin re-asserts the position whenever the page's height
	 * changes, and releases the moment the user scrolls up — from then on the page
	 * is theirs.
	 *
	 * Target: the very bottom of the page, capped only so the pane's top edge never
	 * leaves the window. Page-bottom (rather than "pane bottom clears the dock") is
	 * what makes growth *anywhere* above land as new content sliding in from the top
	 * while the pane holds still — the chat-log pattern.
	 *
	 * Every scroll here is instant, never smooth. A smooth scroll animates across
	 * frames in which the layout keeps changing, so it chases a moving target — and
	 * it animates *while* the card sort (FLIP) animates, which composed into the
	 * "really weird and jerky" motion. An instant correction between paints is
	 * invisible: the pane simply holds still while content slides in above.
	 *
	 * Release is driven by explicit input only — wheel-up, touch, page-up keys —
	 * plus one structural signal: writes that stop sticking (see `holdPin`). Scroll
	 * events are deliberately NOT used to infer intent. Two attempts died there:
	 * a one-frame self-scroll flag saturated while the loop corrected every frame,
	 * and position heuristics lost races against the browser clamping transient
	 * layout shrinks (a phase transition swaps whole station blocks). Only inputs a
	 * person can produce are treated as a person.
	 */
	let pinning = false;
	let pinRaf = 0;
	/** The last position the pin wrote, to judge whether the write stuck. */
	let lastPinY = 0;

	/** The browser's actual scroll limit — NOT `scrollHeight - innerHeight`, which
	 * runs ~16px past it (root margins, scrollbar gutter). A target computed past
	 * the real limit gets clamped on every write, which the stuck-write detector
	 * would read as an external scroller and release. This off-by-a-little was the
	 * root cause of every "the pin just let go" failure in this feature's history. */
	function scrollLimit(): number {
		const se = document.scrollingElement ?? document.documentElement;
		return Math.max(0, se.scrollHeight - se.clientHeight);
	}

	function pinTarget(): number | null {
		if (!transcriptEl || typeof window === 'undefined') return null;
		const gap = 12;
		const paneTopAbs = window.scrollY + transcriptEl.getBoundingClientRect().top;
		return Math.max(0, Math.round(Math.min(scrollLimit(), paneTopAbs - gap)));
	}

	/** Consecutive frames where the position we wrote did not stick. */
	let pinMisses = 0;

	// Debug handle for diagnosing pin behaviour in a live session. Reads closure
	// state; costs nothing unless called.
	if (typeof window !== 'undefined') {
		(window as unknown as Record<string, unknown>).__pinDebug = () => ({
			pinning,
			pinMisses,
			lastPinY,
			releaseReason,
			target: pinTarget(),
			scrollY: window.scrollY,
			limit: scrollLimit()
		});
	}

	function holdPin() {
		if (!pinning) return;
		const target = pinTarget();
		if (target === null) return;
		const y = window.scrollY;
		// Did the previous frame's write hold? One miss is the browser clamping a
		// transient shrink (a phase transition swaps whole station blocks) — the
		// rewrite below sticks and the counter resets. Three consecutive misses
		// means an outside scroller (a scrollbar drag) owns the page: hand it over
		// rather than fight. ~50ms of contention, below perception.
		//
		// A write clamped at the page's true bottom is NOT a miss — it is the pin
		// succeeding against an optimistic target. Without this, any residual gap
		// between the computed target and the reachable limit reads as three
		// failures and self-releases.
		if (Math.abs(y - lastPinY) > 4) {
			if (y >= scrollLimit() - 2) {
				lastPinY = y;
				pinMisses = 0;
			} else {
				pinMisses += 1;
				if (pinMisses >= 3) {
					releasePin('writes-not-sticking');
					return;
				}
			}
		} else {
			pinMisses = 0;
		}
		if (Math.abs(target - y) < 2) {
			lastPinY = y;
			return;
		}
		// 'instant', never 'auto': app.css sets `html { scroll-behavior: smooth }`,
		// and 'auto' defers to CSS — so what looked like an instant jump was a
		// ~300ms animation restarted every frame. Position crept instead of landing,
		// the stuck-write detector read that as an external scroller, and the pin
		// released itself. 'instant' is the only value that overrides the CSS.
		window.scrollTo({ top: target, behavior: 'instant' });
		lastPinY = target;
	}

	/**
	 * Release is driven by explicit input, not by watching scroll positions.
	 *
	 * Two positional heuristics were tried and both lost races against the layout:
	 * a one-frame "self scroll" flag saturated while the loop corrected every frame,
	 * and a lands-on-target check broke when the document shrank (browser clamps
	 * scrollY up) and regrew within a frame — the clamp read as a user move. Wheel,
	 * touch and keys are unambiguous: they can only come from a person.
	 *
	 * Direction and place both matter. Wheel-up *over the pane* is someone reading
	 * the pane — the pin exists to serve exactly that, so it stays. Wheel-up over
	 * the stage or the background is someone leaving for the top of the page.
	 */
	function overPane(target: EventTarget | null): boolean {
		return !!(transcriptEl && target instanceof Node && transcriptEl.contains(target));
	}

	/**
	 * True when a wheel-up over the pane will be consumed by one of the pane's own
	 * scrollers (the transcript body, an open prompt block). If every scroller on
	 * the path is already at its top, the wheel chains out to the page — the reader
	 * is trying to go *above* the pane, which is a release.
	 */
	function paneConsumesWheelUp(target: EventTarget | null): boolean {
		let node: Element | null =
			target instanceof Element ? target : target instanceof Node ? target.parentElement : null;
		while (node && node !== transcriptEl) {
			if (node instanceof HTMLElement && node.scrollTop > 0) return true;
			node = node.parentElement;
		}
		return false;
	}

	function onWheel(event: WheelEvent) {
		if (!pinning || event.deltaY >= 0) return;
		if (overPane(event.target) && paneConsumesWheelUp(event.target)) return;
		releasePin('wheel-up');
	}

	function onTouchMove(event: TouchEvent) {
		if (!pinning) return;
		if (overPane(event.target)) return;
		releasePin('touch');
	}


	/** Why the pin last released — debug breadcrumb, visible via window.__pinDebug. */
	let releaseReason = '';

	function releasePin(reason = 'unknown') {
		releaseReason = reason;
		pinning = false;
		if (pinRaf) {
			cancelAnimationFrame(pinRaf);
			pinRaf = 0;
		}
		if (typeof document !== 'undefined') {
			document.documentElement.style.overflowAnchor = '';
		}
	}

	/**
	 * A plain rAF loop, not a ResizeObserver, and that is deliberate. The first
	 * version observed `document.body`, whose own box never resizes here — the page
	 * grows inside wrappers — so the observer fired once and went silent while the
	 * pane drifted away. Rather than hunt for the right element to observe (fragile
	 * against any layout refactor), the loop just compares target to position every
	 * frame while pinned. It no-ops within 2px, so a quiet page costs one rect read
	 * per frame — and a replay that is playing is re-rendering every frame anyway.
	 */
	function pinLoop() {
		if (!pinning) return;
		holdPin();
		pinRaf = requestAnimationFrame(pinLoop);
	}

	async function revealTranscript() {
		await tick();
		if (!transcriptEl || typeof window === 'undefined') return;

		if (pinRaf) cancelAnimationFrame(pinRaf);
		pinning = true;
		document.documentElement.style.overflowAnchor = 'none';

		// One frame later: `tick()` only guarantees Svelte patched the DOM, and the
		// pane is still growing at that point, so measuring immediately under-scrolls.
		requestAnimationFrame(() => {
			holdPin();
			pinRaf = requestAnimationFrame(pinLoop);
		});
	}

	// Closing the pane (× or Escape) removes the thing being pinned to.
	$: if (!selectedCall && pinning) releasePin('pane-closed');

	/**
	 * Fetch the day's prompts, once, on first unfold.
	 *
	 * Not fetched with the index: at ~600 KB gzipped it is the largest artifact, and
	 * most visits never open a prompt. The loader dedupes concurrent calls, so the
	 * `loading` guard here is only about the UI state.
	 */
	async function ensurePrompts() {
		if (promptsState !== 'idle' || !index) return;
		if (isDemo) {
			promptsState = 'unavailable';
			return;
		}
		// The index says whether the file was published, so an absent one costs no
		// request at all. Older days predate the flag; those still try, and fail soft.
		if (index.run.prompts_available === false) {
			promptsState = 'unavailable';
			return;
		}
		promptsState = 'loading';
		const loaded = await loadReplayPrompts(index.date);
		prompts = loaded;
		promptsState = loaded ? 'ready' : 'unavailable';
	}

	/** Opening a transcript is the only thing that fetches the (optional) stream file. */
	async function selectCall(callId: string) {
		selectedCallId = callId;
		void revealTranscript();
		if (streamState !== 'idle' || !index) return;

		// The hero call is an image, not a token stream — it carries its own result in
		// the index. Opening it must not pull the (potentially large) stream file for a
		// call that is declared `has_stream: false`.
		const call = index.calls.find((c) => c.id === callId);
		if (call && call.role === 'image') return;

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

		// Keyboard page-scrolling upward hands the page back, same as wheel-up.
		// These keys are not otherwise bound, so the browser scrolls as normal.
		if (pinning && (event.key === 'ArrowUp' || event.key === 'PageUp' || event.key === 'Home')) {
			releasePin('key-up');
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

	// Count distinct LLM routes actually used, not distinct model names: the three
	// provider routes (aws/gcp/anthropic) can all report the same model string, which
	// would otherwise render a three-route run as "1 route". The image provider is
	// excluded — it is not an LLM route.
	$: routeCount = index
		? new Set(
				index.calls.filter((c) => c.provider_id && c.provider_id !== 'image').map((c) => c.provider_id)
			).size || index.run.models.length
		: 0;

	// The run status is derived from phase statuses, and "partial" only ever means
	// "some phase did not fully succeed" — it says nothing about whether the reader
	// got a complete briefing. On 2026-07-28 the hero image failed mid-run and was
	// regenerated afterwards; every word of the report shipped. Labelling that
	// "Partial" with no other text reads as "your report is missing something".
	//
	// So the chip states the outcome the reader cares about (did the briefing
	// publish?) and the affected phases are named beneath it.
	$: statusPhases = index
		? (index.phases ?? []).filter((p) => p.status === 'failed' || p.status === 'partial')
		: [];
	$: publishedOk = index ? index.run.status !== 'failed' : false;
	$: statusWord = !index
		? ''
		: index.run.status === 'failed'
			? 'Incomplete'
			: statusPhases.length > 0
				? 'Published'
				: 'Clean';
	$: statusExplainer = (() => {
		if (!index) return '';
		if (statusPhases.length === 0) return 'Every phase succeeded.';
		const parts = statusPhases.map(
			(p) => `${p.ordinal} ${p.label}${p.detail ? ` — ${p.detail}` : ''}`
		);
		return `${publishedOk ? 'The briefing published in full. One step needed retrying: ' : 'A phase failed outright: '}${parts.join('; ')}`;
	})();
</script>

<svelte:head>
	<title>LLM Replay{index ? ` — ${index.date}` : ''} | AATF AI News Aggregator</title>
	<meta
		name="description"
		content="A time-scrubbed reconstruction of the AATF AI news pipeline: every agent, every LLM call, replayed from real timestamps."
	/>
</svelte:head>

<svelte:window on:keydown={onKeydown} on:wheel|passive={onWheel} on:touchmove|passive={onTouchMove} />

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
				<span class="rs-label">Briefing</span>
				<span class="rs-value" data-status={index.run.status} title={statusExplainer}
					>{statusWord}</span
				>
			</div>
		</div>

		{#if statusPhases.length > 0}
			<!-- Lead with what shipped, then name the step that had to be retried. -->
			<p class="status-why" data-status={index.run.status}>
				<strong
					>{publishedOk
						? 'Published in full.'
						: 'A phase failed and the report is incomplete.'}</strong
				>
				{publishedOk ? 'One step needed retrying:' : ''}
				{#each statusPhases as p, i (p.id)}{i > 0 ? '; ' : ' '}<span class="sw-phase"
						>{p.ordinal} {p.label}</span
					>{p.detail ? ` — ${p.detail}` : ''}{/each}
			</p>
		{/if}

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
			<div class="mb-3" bind:this={transcriptEl}>
				<Transcript
					call={selectedCall}
					stream={selectedStream as ReplayCallStream | null}
					{streamState}
					runHasStreams={index.run.stream_available}
					timingsMeasured={index.run.timings_measured !== false}
					t={frame.t}
					{reduced}
					{prompts}
					{promptsState}
					onLoadPrompts={ensurePrompts}
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
			{index.run.llm_calls} requests across {routeCount} route{routeCount === 1
				? ''
				: 's'} · real elapsed time {formatClock(index.duration_ms)}
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
	/* A run that published its briefing reads as success even when a step retried —
	   amber here implied the reader was missing content, which was never true. */
	.rs-value[data-status='partial'],
	.rs-value[data-status='success'] {
		color: #10b981;
	}
	.rs-value[data-status='failed'] {
		color: #ef4444;
	}

	.status-why {
		margin-top: 0.5rem;
		font-size: 0.7rem;
		line-height: 1.5;
		color: #737373;
		padding: 0.4rem 0.6rem;
		border-radius: 6px;
		border-left: 3px solid #f59e0b;
		background: rgb(245 158 11 / 0.07);
	}
	.status-why[data-status='failed'] {
		border-left-color: #ef4444;
		background: rgb(239 68 68 / 0.07);
	}
	.status-why strong {
		color: #b45309;
	}
	.status-why[data-status='failed'] strong {
		color: #b91c1c;
	}
	:global(.dark) .status-why strong {
		color: #fbbf24;
	}
	:global(.dark) .status-why[data-status='failed'] strong {
		color: #f87171;
	}
	.sw-phase {
		font-weight: 600;
		color: #525252;
	}
	:global(.dark) .sw-phase {
		color: #d4d4d4;
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

	.hint-btn {
		font-size: 0.68rem;
		color: #737373;
		display: inline-flex;
		align-items: center;
		gap: 0.3rem;
	}
	@media (hover: hover) and (pointer: fine) {
		.hint-btn:hover {
			color: #E63946;
		}
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
	@media (prefers-reduced-motion: reduce) {
		.viewswitch button {
			transition: none;
		}
	}
</style>
