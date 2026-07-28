<script lang="ts">
	import type { ReplayIndex } from '$lib/types/replay';
	import { formatClock, formatDuration } from '$lib/services/replayViz';

	export let index: ReplayIndex;
	export let t: number;
	export let onSeek: (ms: number) => void = () => {};

	$: duration = Math.max(1, index.duration_ms || 1);
	$: phases = index.phases ?? [];

	const STATUS_COLOR: Record<string, string> = {
		success: '#10b981',
		partial: '#f59e0b',
		failed: '#ef4444',
		skipped: '#a3a3a3',
		running: '#3b82f6'
	};

	// `now` must be an explicit parameter, not a read of the `t` prop from inside the
	// body: Svelte derives an `{@const}`'s dependencies from the expression it can
	// see, so `stateOf(p)` would only re-evaluate when `p` changed and the rail would
	// stay frozen on whichever phase was current at mount.
	function stateOf(p: (typeof phases)[number], now: number): 'past' | 'now' | 'future' {
		if (now >= p.end_ms) return 'past';
		if (now >= p.start_ms) return 'now';
		return 'future';
	}
</script>

<nav class="rail" aria-label="Pipeline phases">
	{#each phases as p (p.id)}
		{@const st = stateOf(p, t)}
		{@const share = ((p.end_ms - p.start_ms) / duration) * 100}
		<button
			type="button"
			class="chip"
			class:past={st === 'past'}
			class:now={st === 'now'}
			class:future={st === 'future'}
			style="flex-grow: {Math.max(0.6, share)}; --sc: {STATUS_COLOR[p.status] ?? '#a3a3a3'}"
			on:click={() => onSeek(p.start_ms)}
			title="{p.label} — {p.detail ?? p.status} · starts {formatClock(
				p.start_ms
			)} · runs {formatDuration(p.end_ms - p.start_ms)}"
			aria-current={st === 'now' ? 'step' : undefined}
		>
			{#if st === 'now'}
				<span
					class="chip-fill"
					style="width: {Math.min(100, ((t - p.start_ms) / Math.max(1, p.end_ms - p.start_ms)) * 100)}%"
				></span>
			{/if}
			<span class="chip-body">
				<span class="chip-ord">{p.ordinal}</span>
				<span class="chip-label">{p.label}</span>
			</span>
			<span class="chip-status" aria-hidden="true"></span>
		</button>
	{/each}
</nav>

<style>
	.rail {
		display: flex;
		flex-wrap: wrap;
		gap: 3px;
		width: 100%;
	}

	.chip {
		position: relative;
		/* grow by time share, but never shrink a label into an ellipsis */
		flex: 0 1 auto;
		min-width: 0;
		overflow: hidden;
		border-radius: 6px;
		padding: 4px 6px 5px;
		text-align: left;
		background: rgb(0 0 0 / 0.045);
		border: 1px solid transparent;
		transition: background 160ms ease, border-color 160ms ease, opacity 160ms ease;
	}
	:global(.dark) .chip {
		background: rgb(255 255 255 / 0.05);
	}
	.chip:hover {
		border-color: rgb(230 57 70 / 0.45);
	}

	.chip.future {
		opacity: 0.42;
	}
	.chip.past {
		opacity: 0.78;
	}
	.chip.now {
		opacity: 1;
		border-color: #E63946;
		background: rgb(230 57 70 / 0.09);
	}

	.chip-fill {
		position: absolute;
		left: 0;
		top: 0;
		bottom: 0;
		background: rgb(230 57 70 / 0.16);
		transition: width 120ms linear;
	}

	.chip-body {
		position: relative;
		display: flex;
		align-items: baseline;
		gap: 0.25rem;
		min-width: 0;
	}

	.chip-ord {
		font-size: 0.55rem;
		font-weight: 800;
		font-variant-numeric: tabular-nums;
		color: #E63946;
		flex: none;
	}
	.chip-label {
		font-size: 0.6rem;
		font-weight: 600;
		color: #404040;
		white-space: nowrap;
	}
	:global(.dark) .chip-label {
		color: #d4d4d4;
	}

	.chip-status {
		position: absolute;
		left: 0;
		right: 0;
		bottom: 0;
		height: 2px;
		background: var(--sc);
		opacity: 0.85;
	}

	@media (max-width: 720px) {
		.chip {
			flex-grow: 0 !important;
		}
	}

	@media (prefers-reduced-motion: reduce) {
		.chip,
		.chip-fill {
			transition: none;
		}
	}
</style>
