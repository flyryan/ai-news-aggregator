<script lang="ts">
	import type { ReplayIndex } from '$lib/types/replay';
	import { SPEEDS, type Speed } from '$lib/services/replayEngine';
	import { formatClock, formatWallTime, providerColor } from '$lib/services/replayViz';

	export let index: ReplayIndex;
	export let t: number;
	export let playing = false;
	export let speed: Speed = 8;
	export let activeCount = 0;
	export let onToggle: () => void = () => {};
	export let onSeek: (ms: number) => void = () => {};
	export let onSpeed: (s: Speed) => void = () => {};
	export let onNextEvent: () => void = () => {};
	export let onPrevEvent: () => void = () => {};

	$: duration = Math.max(1, index.duration_ms || 1);
	$: progress = (t / duration) * 100;

	// Density strip: where the calls actually are, so dead air is visible on the bar.
	$: density = (() => {
		const BUCKETS = 160;
		const buckets = new Array<number>(BUCKETS).fill(0);
		for (const call of index.calls ?? []) {
			const from = Math.floor((call.start_ms / duration) * BUCKETS);
			const to = Math.min(BUCKETS - 1, Math.floor((call.end_ms / duration) * BUCKETS));
			for (let i = Math.max(0, from); i <= to; i++) buckets[i]++;
		}
		const max = Math.max(1, ...buckets);
		return buckets.map((v) => v / max);
	})();

	// Provider stripes on the scrub bar: at a glance, who served what, when.
	$: providerMarks = (index.calls ?? []).map((c) => ({
		id: c.id,
		left: (c.start_ms / duration) * 100,
		width: Math.max(0.15, ((c.end_ms - c.start_ms) / duration) * 100),
		color: providerColor(c.provider_id)
	}));

	function seekFromEvent(event: MouseEvent | { currentTarget: EventTarget | null; clientX: number }) {
		const el = event.currentTarget as HTMLElement | null;
		if (!el) return;
		const rect = el.getBoundingClientRect();
		onSeek(((event.clientX - rect.left) / rect.width) * duration);
	}

	let dragging = false;
	function onPointerDown(event: PointerEvent) {
		dragging = true;
		(event.currentTarget as HTMLElement).setPointerCapture(event.pointerId);
		seekFromEvent(event);
	}
	function onPointerMove(event: PointerEvent) {
		if (dragging) seekFromEvent(event);
	}
	function onPointerUp(event: PointerEvent) {
		dragging = false;
		try {
			(event.currentTarget as HTMLElement).releasePointerCapture(event.pointerId);
		} catch {
			/* capture may already be gone */
		}
	}

	function onKey(event: KeyboardEvent) {
		const step = duration / 60;
		if (event.key === 'ArrowLeft') {
			event.preventDefault();
			onSeek(t - step);
		} else if (event.key === 'ArrowRight') {
			event.preventDefault();
			onSeek(t + step);
		} else if (event.key === 'Home') {
			event.preventDefault();
			onSeek(0);
		} else if (event.key === 'End') {
			event.preventDefault();
			onSeek(duration);
		}
	}
</script>

<div class="bar">
	<div class="controls">
		<button
			type="button"
			class="btn-play"
			on:click={onToggle}
			aria-label={playing ? 'Pause replay' : 'Play replay'}
			aria-pressed={playing}
		>
			{#if playing}
				<svg viewBox="0 0 16 16" width="15" height="15" aria-hidden="true" fill="currentColor">
					<rect x="3.5" y="2.5" width="3.2" height="11" rx="1" />
					<rect x="9.3" y="2.5" width="3.2" height="11" rx="1" />
				</svg>
			{:else}
				<svg viewBox="0 0 16 16" width="15" height="15" aria-hidden="true" fill="currentColor">
					<path d="M4 2.6v10.8a.8.8 0 0 0 1.22.68l8.5-5.4a.8.8 0 0 0 0-1.36l-8.5-5.4A.8.8 0 0 0 4 2.6Z" />
				</svg>
			{/if}
		</button>

		<button type="button" class="btn-step" on:click={onPrevEvent} aria-label="Previous event" title="Previous event (,)">
			⏴
		</button>
		<button type="button" class="btn-step" on:click={onNextEvent} aria-label="Skip to next event" title="Skip to next event (.)">
			⏵
		</button>

		<div class="clock" aria-live="off">
			<span class="clock-elapsed">{formatClock(t)}</span>
			<span class="clock-sep">/</span>
			<span class="clock-total">{formatClock(duration)}</span>
			<span class="clock-wall">{formatWallTime(index.t0, t)}</span>
		</div>
	</div>

	<div class="scrub-area">
		<div
			class="scrub"
			role="slider"
			tabindex="0"
			aria-label="Playback position"
			aria-valuemin={0}
			aria-valuemax={Math.round(duration / 1000)}
			aria-valuenow={Math.round(t / 1000)}
			aria-valuetext="{formatClock(t)} of {formatClock(duration)}"
			on:pointerdown={onPointerDown}
			on:pointermove={onPointerMove}
			on:pointerup={onPointerUp}
			on:pointercancel={onPointerUp}
			on:keydown={onKey}
		>
			<div class="density" aria-hidden="true">
				{#each density as d, i (i)}
					<span style="height: {Math.max(6, d * 100)}%; opacity: {0.18 + d * 0.5}"></span>
				{/each}
			</div>

			<div class="stripes" aria-hidden="true">
				{#each providerMarks as m (m.id)}
					<span style="left: {m.left}%; width: {m.width}%; background: {m.color}"></span>
				{/each}
			</div>

			<div class="phase-ticks" aria-hidden="true">
				{#each index.phases ?? [] as p (p.id)}
					<span
						class="phase-tick"
						class:passed={t >= p.start_ms}
						style="left: {(p.start_ms / duration) * 100}%"
						title="{p.label}"
					></span>
				{/each}
			</div>

			<!--
				The two elements that move every frame are driven by `transform`, never by
				`width`/`left`. Animating a layout property re-lays-out the bar 60×/second and
				lands the 1.5px playhead on fractional pixels, which the compositor resolves
				differently frame to frame — the edge shimmers. A scaleX'd fill and a
				translated line stay on the compositor and hold still.

				The playhead wrapper is full-width on purpose: translateX(%) resolves against
				the element's own box, so a 100%-wide wrapper makes the percentage read as a
				percentage of the track.
			-->
			<div class="filled" style="transform: scaleX({progress / 100})"></div>
			<div class="thumb" style="transform: translateX({progress}%)">
				<span class="thumb-line"></span>
				<!-- Kept mounted and faded rather than toggled: mounting/unmounting a badge
				     as the count crosses zero is its own flicker. -->
				<span class="thumb-count" class:visible={activeCount > 0}>{activeCount}</span>
			</div>
		</div>
	</div>

	<div class="speeds" role="group" aria-label="Playback speed">
		{#each SPEEDS as s (s)}
			<button
				type="button"
				class:on={speed === s}
				class:realtime={s === 1}
				on:click={() => onSpeed(s)}
				aria-pressed={speed === s}
				title={s === 1 ? 'Real time (the run took ' + formatClock(duration) + ')' : `${s}× speed`}
			>
				{s}×
			</button>
		{/each}
	</div>
</div>

<style>
	.bar {
		display: flex;
		align-items: center;
		gap: 0.75rem;
		padding: 0.55rem 0.8rem;
		border-radius: 0.75rem;
		background: rgb(255 255 255 / 0.9);
		border: 1px solid rgb(0 0 0 / 0.1);
		box-shadow: 0 4px 18px -6px rgb(0 0 0 / 0.22);
		backdrop-filter: blur(8px);
	}
	:global(.dark) .bar {
		background: rgb(23 23 23 / 0.92);
		border-color: rgb(255 255 255 / 0.11);
	}

	.controls {
		display: flex;
		align-items: center;
		gap: 0.3rem;
		flex: none;
	}

	.btn-play {
		display: grid;
		place-items: center;
		width: 2rem;
		height: 2rem;
		border-radius: 999px;
		background: #E63946;
		color: #fff;
		flex: none;
		transition: background 150ms ease, transform 120ms ease;
	}
	.btn-play:hover {
		background: #C1272D;
	}
	.btn-play:active {
		transform: scale(0.94);
	}

	.btn-step {
		width: 1.5rem;
		height: 1.5rem;
		border-radius: 5px;
		font-size: 0.7rem;
		color: #737373;
	}
	.btn-step:hover {
		color: #E63946;
		background: rgb(230 57 70 / 0.1);
	}

	.clock {
		display: flex;
		align-items: baseline;
		gap: 0.2rem;
		font-variant-numeric: tabular-nums;
		margin-left: 0.3rem;
		white-space: nowrap;
	}
	.clock-elapsed {
		font-size: 0.85rem;
		font-weight: 700;
		color: #262626;
	}
	:global(.dark) .clock-elapsed {
		color: #f5f5f5;
	}
	.clock-sep,
	.clock-total {
		font-size: 0.68rem;
		color: #737373;
	}
	.clock-wall {
		font-size: 0.58rem;
		color: #a3a3a3;
		margin-left: 0.35rem;
	}

	.scrub-area {
		flex: 1;
		min-width: 0;
	}

	.scrub {
		position: relative;
		height: 2.1rem;
		cursor: pointer;
		border-radius: 5px;
		overflow: hidden;
		background: rgb(0 0 0 / 0.05);
		touch-action: none;
	}
	:global(.dark) .scrub {
		background: rgb(255 255 255 / 0.06);
	}
	.scrub:focus-visible {
		outline: 2px solid #E63946;
		outline-offset: 1px;
	}

	.density {
		position: absolute;
		inset: 0;
		display: flex;
		align-items: flex-end;
		gap: 0;
	}
	.density span {
		flex: 1;
		background: #8b5cf6;
	}

	.stripes {
		position: absolute;
		left: 0;
		right: 0;
		bottom: 0;
		height: 3px;
	}
	.stripes span {
		position: absolute;
		top: 0;
		bottom: 0;
		opacity: 0.85;
	}

	.phase-ticks {
		position: absolute;
		inset: 0;
	}
	.phase-tick {
		position: absolute;
		top: 0;
		bottom: 3px;
		width: 1px;
		background: rgb(0 0 0 / 0.22);
	}
	:global(.dark) .phase-tick {
		background: rgb(255 255 255 / 0.25);
	}
	.phase-tick.passed {
		background: rgb(230 57 70 / 0.5);
	}

	/* Full-width and squashed by scaleX, so the fill never triggers layout. */
	.filled {
		position: absolute;
		left: 0;
		top: 0;
		bottom: 0;
		width: 100%;
		transform-origin: left center;
		background: rgb(230 57 70 / 0.14);
		pointer-events: none;
		will-change: transform;
	}

	.thumb {
		position: absolute;
		left: 0;
		top: 0;
		bottom: 0;
		width: 100%;
		pointer-events: none;
		will-change: transform;
	}
	/* The playhead rides at the wrapper's left edge; the wrapper does the moving. */
	.thumb-line {
		position: absolute;
		top: 0;
		bottom: 0;
		left: -0.75px;
		width: 1.5px;
		background: #E63946;
	}
	.thumb-count {
		position: absolute;
		top: 1px;
		left: 3px;
		font-size: 0.55rem;
		font-weight: 700;
		font-variant-numeric: tabular-nums;
		color: #fff;
		background: #E63946;
		border-radius: 3px;
		padding: 0 3px;
		white-space: nowrap;
		opacity: 0;
		transition: opacity 140ms ease;
	}
	.thumb-count.visible {
		opacity: 1;
	}

	.speeds {
		display: flex;
		gap: 1px;
		flex: none;
		border-radius: 6px;
		overflow: hidden;
		border: 1px solid rgb(0 0 0 / 0.12);
	}
	:global(.dark) .speeds {
		border-color: rgb(255 255 255 / 0.13);
	}
	.speeds button {
		font-size: 0.6rem;
		font-weight: 650;
		font-variant-numeric: tabular-nums;
		padding: 3px 5px;
		color: #737373;
		background: transparent;
		transition: background 120ms ease, color 120ms ease;
	}
	.speeds button:hover {
		color: #E63946;
		background: rgb(230 57 70 / 0.08);
	}
	.speeds button.on {
		background: #E63946;
		color: #fff;
	}
	.speeds button.realtime {
		position: relative;
	}
	.speeds button.realtime::after {
		content: '';
		position: absolute;
		bottom: 1px;
		left: 50%;
		transform: translateX(-50%);
		width: 3px;
		height: 3px;
		border-radius: 999px;
		background: currentColor;
		opacity: 0.55;
	}

	@media (max-width: 780px) {
		.bar {
			flex-wrap: wrap;
		}
		.scrub-area {
			order: 3;
			flex-basis: 100%;
		}
		.clock-wall {
			display: none;
		}
	}

	@media (prefers-reduced-motion: reduce) {
		.btn-play,
		.speeds button,
		.thumb-count {
			transition: none;
		}
	}
</style>
