<script lang="ts">
	import type { ReplayCall, ReplayIndex } from '$lib/types/replay';
	import {
		agentColor,
		providerColor,
		formatClock,
		formatDuration,
		formatTokens,
		isImageCall,
		ROLE_LABELS
	} from '$lib/services/replayViz';

	export let index: ReplayIndex;
	export let t: number;
	export let selectedCallId: string | null = null;
	export let onSelectCall: (callId: string) => void = () => {};
	export let onSeek: (ms: number) => void = () => {};

	// Two colour systems live here, separated by *region* rather than by a toggle:
	//   · label gutter — agent identity, matching the Newsroom's cast colours
	//   · plot area    — the provider that served each request
	// They never touch, so neither needs a caption to stay unambiguous. An earlier
	// version let the reader switch the bars between provider and effort, which meant
	// the same swatches silently changed meaning.
	//
	// Effort needs no encoding of its own: it is a property of the role, and the
	// taxonomy gives each role its own lane, so a lane's effort is constant and can
	// simply be written next to its name.

	// Offline-reconstructed runs carry start/end only: `wait_ms` is 0 and
	// `first_token_ms` is null on every call. Segmenting a bar into wait / TTFT /
	// streaming would then render one flat slab of "time to first token", which reads
	// as a bug. Fall back to undivided bars and say so instead of faking the split.
	$: measured = index.run.timings_measured !== false;

	// The key must only name segments that are actually on screen. Queue wait is
	// drawn from `queued_ms → start_ms`; when the concurrency cap is never reached
	// that span is 0 on every call and no hatched segment is ever rendered, so
	// listing it would describe a stripe the user cannot find.
	$: hasQueueWait = measured && (index.calls ?? []).some((c) => c.start_ms - c.queued_ms > 0);

	// Row height also has to clear the two-line gutter label (name + effort tier).
	const LANE_H = 22;
	const LANE_GAP = 2;

	$: duration = Math.max(1, index.duration_ms || 1);
	$: agentById = new Map((index.agents ?? []).map((a) => [a.id, a]));

	// Swimlanes: one per agent that actually made calls, in stage order. Within an
	// agent, parallel workers get sub-rows so the fan-out doesn't overlap.
	$: lanes = (() => {
		const order = (index.agents ?? []).map((a) => a.id);
		const byAgent = new Map<string, ReplayCall[]>();
		for (const call of index.calls ?? []) {
			const list = byAgent.get(call.agent_id) ?? [];
			list.push(call);
			byAgent.set(call.agent_id, list);
		}
		const result: { agent_id: string; label: string; color: string; rows: ReplayCall[][] }[] = [];
		for (const id of order) {
			const calls = byAgent.get(id);
			if (!calls || calls.length === 0) continue;
			const sorted = [...calls].sort((a, b) => a.queued_ms - b.queued_ms);
			// Greedy row packing: a call goes in the first row whose last bar ended.
			const rows: ReplayCall[][] = [];
			for (const call of sorted) {
				let placed = false;
				for (const row of rows) {
					if (row[row.length - 1].end_ms <= call.queued_ms) {
						row.push(call);
						placed = true;
						break;
					}
				}
				if (!placed) rows.push([call]);
			}
			const agent = agentById.get(id);
			result.push({
				agent_id: id,
				label: agent?.label ?? id,
				color: agentColor(agent),
				rows
			});
		}
		// Any caller the taxonomy didn't know about still gets a lane.
		for (const [id, calls] of byAgent) {
			if (order.includes(id)) continue;
			result.push({
				agent_id: id,
				label: id,
				color: '#737373',
				rows: [[...calls].sort((a, b) => a.queued_ms - b.queued_ms)]
			});
		}
		return result;
	})();

	$: totalRows = lanes.reduce((n, l) => n + l.rows.length, 0);
	$: chartHeight = Math.max(80, totalRows * (LANE_H + LANE_GAP));

	function pct(ms: number): number {
		return (ms / duration) * 100;
	}

	function barColor(call: ReplayCall): string {
		return providerColor(call.provider_id);
	}

	// Concurrency sparkline, drawn as an SVG polygon over the same x scale.
	//
	// Two series share one y scale: `concActiveMax` is in-flight requests (and matches
	// run.peak_concurrency in the header), while `concMax` adds the queued backlog on
	// top and is therefore the taller of the two. They are labelled separately —
	// reporting the stacked ceiling as "peak concurrency" would contradict the header.
	$: conc = index.concurrency?.samples ?? [];
	$: concMax = Math.max(1, ...conc.map((s) => s[1] + s[2]));
	$: concActiveMax = Math.max(1, ...conc.map((s) => s[1]));
	$: activePath = (() => {
		if (conc.length === 0) return '';
		const pts = conc.map((s) => `${(s[0] / duration) * 100},${100 - (s[1] / concMax) * 100}`);
		return `0,100 ${pts.join(' ')} 100,100`;
	})();
	$: totalPath = (() => {
		if (conc.length === 0) return '';
		const pts = conc.map(
			(s) => `${(s[0] / duration) * 100},${100 - ((s[1] + s[2]) / concMax) * 100}`
		);
		return `0,100 ${pts.join(' ')} 100,100`;
	})();

	// Tick marks every ~5 minutes of run time, snapped to something readable.
	$: ticks = (() => {
		const target = 8;
		const raw = duration / target;
		const steps = [30_000, 60_000, 120_000, 300_000, 600_000, 900_000, 1_800_000];
		const step = steps.find((s) => s >= raw) ?? steps[steps.length - 1];
		const out: number[] = [];
		for (let v = 0; v <= duration; v += step) out.push(v);
		return out;
	})();

	// Every provider that served a request, with its share of the run. The count is
	// what makes this a load-balancing readout rather than decoration.
	$: routeLegend = (() => {
		const counts = new Map<string, number>();
		for (const c of index.calls ?? []) counts.set(c.provider_id, (counts.get(c.provider_id) ?? 0) + 1);
		return [...counts.entries()]
			.sort((a, b) => b[1] - a[1])
			.map(([id, n]) => ({
				id,
				count: n,
				color: providerColor(id),
				note: id === 'image' ? 'image model, not an LLM route' : 'LLM route'
			}));
	})();

	/**
	 * The effort every call in a lane runs at.
	 *
	 * Each agent performs exactly one role and each role has one effort tier, so this
	 * is a property of the lane, not of the individual bars — it belongs beside the
	 * agent's name rather than encoded into the chart. Read from the agent when the
	 * generator supplied it, else from the lane's own calls, so days generated before
	 * the cast was split still label correctly.
	 */
	function laneEffort(agentId: string): string | null {
		const declared = agentById.get(agentId)?.effort;
		if (declared) return declared;
		const efforts = new Set((index.calls ?? []).filter((c) => c.agent_id === agentId).map((c) => c.effort));
		return efforts.size === 1 ? [...efforts][0] : null;
	}

	function handleTrackClick(event: MouseEvent) {
		const el = event.currentTarget as HTMLElement;
		const rect = el.getBoundingClientRect();
		onSeek(((event.clientX - rect.left) / rect.width) * duration);
	}
</script>

<div class="timeline card !p-4">
	<div class="tl-head">
		<div>
			<h3 class="tl-title">Call timeline</h3>
			<p class="tl-sub">
				{index.calls.length} requests · peak concurrency {index.run.peak_concurrency} · click a bar to
				open it{measured ? '' : ' · reconstructed timings'}
			</p>
		</div>

		<!-- No toggle, no caption: a swatch beside a provider name and a count reads
		     as "this color is this provider, and it took N calls" on its own. -->
		<ul class="legend">
			{#each routeLegend as r (r.id)}
				<li title="{r.count} of {index.calls.length} requests · {r.note}">
					<span class="swatch" style="background: {r.color}"></span>{r.id}
					<span class="legend-n">{r.count}</span>
				</li>
			{/each}
		</ul>
	</div>

	<!-- What the shading inside a bar means. Swatches use the same base-plus-overlay
	     recipe as the bars, so a shade in the key is findable in the chart. -->
	<div class="key">
		{#if measured}
			{#if hasQueueWait}
				<span><span class="key-swatch key-wait"></span>queue wait</span>
			{/if}
			<span><span class="key-swatch key-ttft"></span>waiting for first token</span>
			<span><span class="key-swatch key-stream"></span>writing</span>
			{#if !hasQueueWait}
				<span class="key-note">nothing queued this run — every request started immediately</span>
			{/if}
		{:else}
			<span><span class="key-swatch key-stream"></span>request start → end</span>
			<span class="key-note"
				>timings reconstructed from run logs — no queue wait or first-token split recorded</span
			>
		{/if}
	</div>

	<div class="tl-body">
		<div class="gutter">
			<div class="gutter-rows" style="height: {chartHeight}px">
				{#each lanes as lane (lane.agent_id)}
					{@const eff = laneEffort(lane.agent_id)}
					<div
						class="gutter-label"
						style="height: {lane.rows.length * (LANE_H + LANE_GAP)}px; --accent: {lane.color}"
						title={eff ? `${lane.label} — every call at ${eff} effort` : lane.label}
					>
						<span class="gutter-tick"></span>
						<span class="gutter-text">
							{lane.label}
							{#if eff}<span class="gutter-effort" data-effort={eff}>{eff}</span>{/if}
						</span>
					</div>
				{/each}
			</div>
		</div>

		<div class="track-wrap">
			<!-- eslint-disable-next-line svelte/valid-compile -->
			<div
				class="track"
				style="height: {chartHeight}px"
				on:click={handleTrackClick}
				on:keydown={(e) => {
					if (e.key === 'Enter') handleTrackClick(e as unknown as MouseEvent);
				}}
				role="slider"
				tabindex="0"
				aria-label="Seek within the run"
				aria-valuemin={0}
				aria-valuemax={Math.round(duration / 1000)}
				aria-valuenow={Math.round(t / 1000)}
				aria-valuetext="{formatClock(t)} elapsed"
			>
				{#each ticks as tick (tick)}
					<span class="grid-line" style="left: {pct(tick)}%"></span>
				{/each}

				{#each lanes as lane (lane.agent_id)}
					{#each lane.rows as row, ri (ri)}
						<div class="row" style="height: {LANE_H}px; margin-bottom: {LANE_GAP}px">
							{#each row as call (call.id)}
								{@const left = pct(call.queued_ms)}
								{@const width = Math.max(0.12, pct(call.end_ms - call.queued_ms))}
								{@const waitW = pct(call.start_ms - call.queued_ms)}
								{@const ttftW = call.first_token_ms ? pct(call.first_token_ms - call.start_ms) : 0}
								<button
									type="button"
									class="bar"
									class:done={call.end_ms <= t}
									class:live={call.queued_ms <= t && call.end_ms > t}
									class:future={call.queued_ms > t}
									class:selected={call.id === selectedCallId}
									style="left: {left}%; width: {width}%; --c: {barColor(call)}"
									on:click|stopPropagation={() => onSelectCall(call.id)}
									title={isImageCall(call)
										? `${call.task} · ${call.model} · ${formatDuration(
												call.end_ms - call.start_ms
											)} · 1 image (no token metering)`
										: `${call.task} · ${call.provider_id} · ${call.effort} effort · ${formatDuration(
												call.end_ms - call.start_ms
											)} · ${formatTokens(call.output_tokens)} tok${
												call.outcome !== 'ok' ? ` · ${call.outcome}` : ''
											}`}
								>
									{#if waitW > 0.05}
										<span class="seg-wait" style="width: {(waitW / width) * 100}%"></span>
									{/if}
									<span
										class="seg-ttft"
										style="left: {(waitW / width) * 100}%; width: {(ttftW / width) * 100}%"
									></span>
									<span
										class="seg-stream"
										style="left: {((waitW + ttftW) / width) * 100}%; right: 0"
									></span>
									{#if call.outcome === 'truncated' || call.outcome === 'failed' || call.outcome === 'refused'}
										<span class="bar-flag" data-outcome={call.outcome}></span>
									{/if}
									{#if call.fallback_from}
										<span class="bar-fallback" title="Failed over from {call.fallback_from}"></span>
									{/if}
								</button>
							{/each}
						</div>
					{/each}
				{/each}

				<!-- Moved with transform, not `left`: see PlaybackBar for why a 1.5px line
				     animated via a layout property shimmers on subpixel boundaries. -->
				<!-- The clipping layer keeps the full-width playhead wrapper from widening
				     the track once it is translated past the right edge. -->
				<span class="playhead-layer">
					<span class="playhead" style="transform: translateX({pct(t)}%)">
						<span class="playhead-line"></span>
					</span>
				</span>
			</div>

			<!-- Concurrency: the pipeline breathing -->
			<div class="conc">
				<svg viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">
					<polygon points={totalPath} class="conc-total" />
					<polygon points={activePath} class="conc-active" />
				</svg>
				<span class="conc-playhead" style="transform: translateX({pct(t)}%)">
					<span class="playhead-line"></span>
				</span>
				<span class="conc-label"
					>concurrency · peak {concActiveMax} in flight{concMax > concActiveMax
						? ` · ${concMax} incl. queue`
						: ''}</span
				>
			</div>

			<div class="axis">
				{#each ticks as tick (tick)}
					<span class="axis-tick" style="left: {pct(tick)}%">{formatClock(tick)}</span>
				{/each}
			</div>
		</div>
	</div>
</div>

<style>
	.tl-head {
		display: flex;
		flex-wrap: wrap;
		align-items: flex-start;
		justify-content: space-between;
		gap: 0.75rem;
		margin-bottom: 0.6rem;
	}
	.tl-title {
		font-size: 0.95rem;
		font-weight: 700;
		color: #262626;
	}
	:global(.dark) .tl-title {
		color: #f5f5f5;
	}
	.tl-sub {
		font-size: 0.7rem;
		color: #737373;
	}

	.legend-n {
		font-variant-numeric: tabular-nums;
		font-weight: 700;
		opacity: 0.55;
		margin-left: 0.1rem;
	}

	.legend {
		display: flex;
		gap: 0.6rem;
		flex-wrap: wrap;
		font-size: 0.62rem;
		color: #525252;
	}
	:global(.dark) .legend {
		color: #a3a3a3;
	}
	.legend li {
		display: flex;
		align-items: center;
		gap: 0.25rem;
	}
	.swatch {
		width: 8px;
		height: 8px;
		border-radius: 2px;
		display: inline-block;
	}

	.key {
		display: flex;
		gap: 0.85rem;
		font-size: 0.6rem;
		color: #737373;
		margin-bottom: 0.5rem;
	}
	.key span {
		display: flex;
		align-items: center;
		gap: 0.25rem;
	}
	.key-note {
		font-style: italic;
		opacity: 0.85;
	}
	/* Neutral stand-in for the per-call route color. The overlays below are
	   the exact ones the bars use, so the key reads as a slice of a real bar. */
	.key-swatch {
		width: 14px;
		height: 8px;
		border-radius: 2px;
		display: inline-block;
		position: static;
		background: #8ba3c7;
	}
	.key-wait {
		background-image: repeating-linear-gradient(
			45deg,
			rgb(255 255 255 / 0.55) 0 2px,
			transparent 2px 4px
		);
	}
	.key-ttft {
		background-image: linear-gradient(rgb(0 0 0 / 0.28), rgb(0 0 0 / 0.28));
	}

	.tl-body {
		display: flex;
		gap: 0.4rem;
	}

	.gutter {
		width: 7.5rem;
		flex: none;
		display: flex;
		flex-direction: column;
	}
	.gutter-rows {
		display: flex;
		flex-direction: column;
	}

	/* One tier per lane, stated rather than encoded. Matches the badge on the
	   Newsroom's stations so the same agent reads the same in both views. */
	.gutter-effort {
		display: block;
		font-size: 0.48rem;
		font-weight: 700;
		letter-spacing: 0.05em;
		color: #a3a3a3;
		line-height: 1.2;
	}
	.gutter-effort[data-effort='xhigh'] {
		color: #7c3aed;
	}
	.gutter-effort[data-effort='max'] {
		color: #dc2626;
	}
	:global(.dark) .gutter-effort[data-effort='xhigh'] {
		color: #c4b5fd;
	}
	:global(.dark) .gutter-effort[data-effort='max'] {
		color: #fca5a5;
	}
	.gutter-label {
		display: flex;
		align-items: center;
		gap: 0.3rem;
		font-size: 0.62rem;
		color: #525252;
		border-top: 1px solid rgb(0 0 0 / 0.06);
		overflow: hidden;
	}
	:global(.dark) .gutter-label {
		color: #a3a3a3;
		border-color: rgb(255 255 255 / 0.07);
	}
	/* Agent identity, matching the Newsroom's cast colours so the same agent reads
	   the same in both views. It sits in the label gutter, not in the plot area, so
	   it never competes with the provider colour on the bars. */
	.gutter-tick {
		width: 3px;
		align-self: stretch;
		flex: none;
		border-radius: 2px;
		background: var(--accent);
		margin: 2px 0;
	}
	.gutter-text {
		min-width: 0;
		overflow: hidden;
		font-size: 0.6rem;
		line-height: 1.25;
		text-overflow: ellipsis;
	}

	.track-wrap {
		flex: 1;
		min-width: 0;
	}

	.track {
		position: relative;
		cursor: crosshair;
		background: rgb(0 0 0 / 0.025);
		border-radius: 4px;
	}
	:global(.dark) .track {
		background: rgb(255 255 255 / 0.03);
	}

	.grid-line {
		position: absolute;
		top: 0;
		bottom: 0;
		width: 1px;
		background: rgb(0 0 0 / 0.05);
	}
	:global(.dark) .grid-line {
		background: rgb(255 255 255 / 0.06);
	}

	.row {
		position: relative;
	}

	.bar {
		position: absolute;
		top: 2px;
		bottom: 2px;
		border-radius: 3px;
		overflow: hidden;
		background: var(--c);
		border: none;
		padding: 0;
		cursor: pointer;
		transition: opacity 150ms ease, filter 150ms ease;
	}
	.bar.future {
		opacity: 0.22;
	}
	.bar.done {
		opacity: 0.78;
	}
	.bar.live {
		opacity: 1;
		box-shadow: 0 0 0 1px var(--c), 0 0 10px -2px var(--c);
	}
	.bar:hover {
		filter: brightness(1.15);
		opacity: 1;
	}
	.bar.selected {
		box-shadow: 0 0 0 2px #fff, 0 0 0 3.5px #E63946;
		opacity: 1;
		z-index: 3;
	}
	:global(.dark) .bar.selected {
		box-shadow: 0 0 0 2px #171717, 0 0 0 3.5px #E63946;
	}

	.bar span {
		position: absolute;
		top: 0;
		bottom: 0;
	}
	.bar .seg-wait {
		left: 0;
		background: repeating-linear-gradient(
			45deg,
			rgb(255 255 255 / 0.55) 0 2px,
			transparent 2px 4px
		);
	}
	.bar .seg-ttft {
		background: rgb(0 0 0 / 0.28);
	}
	.bar .seg-stream {
		background: transparent;
	}

	.bar-flag {
		left: auto !important;
		right: 0;
		width: 3px;
		background: #f59e0b;
	}
	.bar-flag[data-outcome='failed'],
	.bar-flag[data-outcome='refused'] {
		background: #ef4444;
	}
	.bar-fallback {
		left: 0 !important;
		width: 3px;
		background: #a855f7;
	}

	.playhead-layer {
		position: absolute;
		inset: 0;
		overflow: hidden;
		pointer-events: none;
		z-index: 4;
	}

	/* Full-width wrapper so translateX(%) resolves against the track, with the visible
	   line hung off its leading edge. Transform-only movement keeps the 1.5px line on
	   the compositor; animating `left` re-lays-out every frame and the edge shimmers. */
	.playhead {
		position: absolute;
		left: 0;
		top: 0;
		bottom: 0;
		width: 100%;
		pointer-events: none;
		z-index: 4;
		will-change: transform;
	}
	.playhead-line {
		position: absolute;
		top: 0;
		bottom: 0;
		left: -0.75px;
		width: 1.5px;
		background: #E63946;
		box-shadow: 0 0 8px 0 rgb(230 57 70 / 0.8);
	}

	.conc {
		position: relative;
		height: 44px;
		margin-top: 4px;
		border-radius: 4px;
		overflow: hidden;
		background: rgb(0 0 0 / 0.03);
	}
	:global(.dark) .conc {
		background: rgb(255 255 255 / 0.04);
	}
	.conc svg {
		width: 100%;
		height: 100%;
		display: block;
	}
	.conc-total {
		fill: rgb(139 92 246 / 0.22);
	}
	.conc-active {
		fill: rgb(230 57 70 / 0.42);
	}
	.conc-playhead {
		position: absolute;
		left: 0;
		top: 0;
		bottom: 0;
		width: 100%;
		pointer-events: none;
		will-change: transform;
	}
	.conc-playhead .playhead-line {
		box-shadow: none;
	}
	.conc-label {
		position: absolute;
		top: 2px;
		left: 5px;
		font-size: 0.55rem;
		letter-spacing: 0.06em;
		text-transform: uppercase;
		color: #737373;
	}

	.axis {
		position: relative;
		height: 1.1rem;
		margin-top: 2px;
	}
	.axis-tick {
		position: absolute;
		transform: translateX(-50%);
		font-size: 0.58rem;
		font-variant-numeric: tabular-nums;
		color: #737373;
		white-space: nowrap;
	}

	@media (max-width: 700px) {
		.gutter {
			width: 4.5rem;
		}
		.gutter-text {
			font-size: 0.55rem;
		}
	}

	@media (prefers-reduced-motion: reduce) {
		.bar {
			transition: none;
		}
	}
</style>
