<script lang="ts">
	import type { ReplayAgent } from '$lib/types/replay';
	import type { AgentFrameState } from '$lib/services/replayEngine';
	import {
		agentColor,
		providerColor,
		formatTokens,
		formatCost,
		formatDuration,
		isImageCall
	} from '$lib/services/replayViz';

	export let agent: ReplayAgent;
	export let state: AgentFrameState | undefined = undefined;
	export let sources: { name: string; items: number; progress: number; active: boolean; done: boolean; status: string }[] = [];
	export let reduced = false;
	export let onSelectCall: (callId: string) => void = () => {};

	$: color = agentColor(agent);
	$: status = state?.status ?? 'idle';
	$: activeCalls = state?.active ?? [];
	$: flash = reduced ? 0 : (state?.flash ?? 0);
	$: completed = state?.completed ?? 0;
	$: total = state?.total ?? agent.call_count ?? 0;
	$: barPct = total > 0 ? Math.min(100, (completed / total) * 100) : status === 'idle' ? 0 : 100;
	$: isLive = status === 'active';

	// The Illustrator runs on an image client, not an LLM route: its tokens and cost
	// are structurally zero, never measured. Printing "0 tok · $0.000" here would read
	// as "we measured nothing happening" when in fact ~57s of real work happened.
	$: isImageAgent = agent.kind === 'imagegen';
	// The one call whose output is a picture — surface it on the station as a thumbnail
	// once it lands, so the station reports in with the artefact rather than a number.
	$: imageResult = isImageAgent && status === 'done' ? (state?.last_done?.image_url ?? null) : null;
	// The image call itself, whichever state it is in — the footer reads cost off it
	// rather than off the agent rollup, which the image client never contributes to.
	$: imageCall = isImageAgent
		? (state?.last_done ?? activeCalls[0]?.call ?? null)
		: null;
	let thumbFailed = false;
	// A new day's replay reuses this component instance; retry the new URL.
	$: if (imageResult) thumbFailed = false;

	// Finished work stays on the station for the rest of the run. A call that has
	// already landed is the *most* inspectable thing in the replay — its transcript
	// is complete — so hiding it the moment the flash decays threw away the reason
	// to click. Newest first: that is where attention already is.
	$: doneCalls = [...(state?.done ?? [])].reverse();
	// Which of those chips are already shown as live chips, so nothing double-renders.
	$: activeIds = new Set(activeCalls.map((a) => a.call.id));
	$: restingCalls = doneCalls.filter((c) => !activeIds.has(c.id));

	// The Research Analyst runs 13 batches; the tallest column would otherwise set
	// the height of the whole floor. Show a generous window and fold the rest.
	const DONE_VISIBLE = 8;
	let showAllDone = false;
	$: if (agent.id) showAllDone = false;
	$: visibleDone = showAllDone ? restingCalls : restingCalls.slice(0, DONE_VISIBLE);
	$: hiddenDone = restingCalls.length - visibleDone.length;
</script>

<div
	class="station"
	class:is-idle={status === 'idle'}
	class:is-live={isLive}
	class:is-done={status === 'done'}
	style="--accent: {color}; --flash: {flash};"
>
	<!-- Completion flash: fires the instant a call ends, decays over ~2.2s of run time -->
	<div class="flash-layer" aria-hidden="true"></div>

	<div class="station-head">
		<span class="dot" aria-hidden="true">
			{#if isLive && !reduced}
				<span class="dot-ring"></span>
			{/if}
		</span>
		<div class="min-w-0 flex-1">
			<div class="label">{agent.label}</div>
			<div class="kind">{agent.kind}</div>
		</div>
		{#if total > 0}
			<div class="counter" title="{completed} of {total} LLM calls complete">
				<span class="counter-now">{completed}</span><span class="counter-sep">/</span><span>{total}</span>
			</div>
		{/if}
	</div>

	{#if total > 0}
		<div class="progress" role="presentation">
			<div class="progress-fill" style="width: {barPct}%"></div>
		</div>
	{/if}

	<!-- Non-LLM work: the scouts pulling from their sources -->
	{#if sources.length > 0}
		<ul class="sources">
			{#each sources as src (src.name)}
				<li class="source" class:src-active={src.active} class:src-done={src.done}>
					<span class="source-bar" style="width: {Math.round(src.progress * 100)}%"></span>
					<span class="source-name">{src.name}</span>
					<span class="source-count">
						{#if src.done}
							{src.items.toLocaleString()}
							{#if src.status === 'partial'}<span class="warn" title="Partial collection">!</span>{/if}
						{:else if src.active}
							<span class="working">pulling…</span>
						{:else}
							<span class="muted">—</span>
						{/if}
					</span>
				</li>
			{/each}
		</ul>
	{/if}

	<!-- In-flight calls: one chip per parallel worker, so the fan-out is legible -->
	{#if activeCalls.length > 0}
		<ul class="calls">
			{#each activeCalls as ac (ac.call.id)}
				<li>
					<button
						type="button"
						class="call-chip"
						class:waiting={ac.state === 'waiting'}
						class:queued={ac.state === 'queued'}
						style="--route: {providerColor(ac.call.provider_id)}"
						on:click={() => onSelectCall(ac.call.id)}
						title={isImageCall(ac.call)
							? `${ac.call.task} — ${ac.call.model} — image, no token metering`
							: `${ac.call.task} — ${ac.call.provider_id} — ${ac.call.profile}`}
					>
						<span class="chip-fill" style="width: {Math.round(ac.progress * 100)}%"></span>
						<span class="chip-body">
							<span class="chip-task">{ac.call.task}</span>
							<span class="chip-meta">
								<!-- Effort belongs to the call, not the station: an agent can mix
								     tiers, so the badge rides the chip. -->
								{#if ac.call.effort}
									<span class="effort" data-effort={ac.call.effort}>{ac.call.effort}</span>
								{/if}
								<span class="route-tag">{ac.call.provider_id}</span>
								{#if isImageCall(ac.call)}
									<!-- No token stream to count: an image arrives whole. Say what it is doing. -->
									<span class="state-tag painting">
										{#if !reduced}<span class="paint-dots" aria-hidden="true"
												><i></i><i></i><i></i></span
											>{/if}
										painting
									</span>
								{:else if ac.state === 'queued'}
									<span class="state-tag">queued {formatDuration(ac.call.wait_ms)}</span>
								{:else if ac.state === 'waiting'}
									<span class="state-tag thinking">thinking{reduced ? '' : '…'}</span>
								{:else}
									<span
										class="state-tag writing"
										title={ac.tokensApprox
											? 'Estimated from the call span — this run has no recorded first-token time'
											: undefined}>{ac.tokensApprox ? '≈' : ''}{formatTokens(ac.tokens)} tok</span
									>
								{/if}
							</span>
						</span>
					</button>
				</li>
			{/each}
		</ul>
	{/if}

	<!-- Finished calls, newest first. Persist for the whole replay: a landed call is
	     the most inspectable thing here, since its transcript is complete. -->
	{#if restingCalls.length > 0}
		<ul class="calls done-calls">
			{#each visibleDone as dc (dc.id)}
				<li>
					<button
						type="button"
						class="call-chip is-complete"
						class:failed={dc.outcome === 'failed' || dc.outcome === 'refused'}
						style="--route: {providerColor(dc.provider_id)}"
						on:click={() => onSelectCall(dc.id)}
						title={isImageCall(dc)
							? `${dc.task} — ${dc.model} — image, no token metering`
							: `${dc.task} — ${dc.provider_id} — ${dc.profile} — ${formatTokens(
									dc.output_tokens
								)} tok — ${formatCost(dc.cost_usd)}`}
					>
						<span class="chip-body">
							<span class="chip-task">
								<span class="chip-check" aria-hidden="true">✓</span>{dc.task}
							</span>
							<span class="chip-meta">
								{#if dc.effort}
									<span class="effort" data-effort={dc.effort}>{dc.effort}</span>
								{/if}
								<span class="route-tag">{dc.provider_id}</span>
								{#if isImageCall(dc)}
									<span class="state-tag done-tag">image</span>
								{:else if dc.outcome !== 'ok'}
									<span class="state-tag bad-tag">{dc.outcome}</span>
								{:else}
									<span class="state-tag done-tag">{formatTokens(dc.output_tokens)} tok</span>
								{/if}
							</span>
						</span>
					</button>
				</li>
			{/each}
		</ul>
		{#if hiddenDone > 0}
			<button type="button" class="more-btn" on:click={() => (showAllDone = true)}>
				+{hiddenDone} more
			</button>
		{:else if showAllDone && restingCalls.length > DONE_VISIBLE}
			<button type="button" class="more-btn" on:click={() => (showAllDone = false)}>
				Show fewer
			</button>
		{/if}
	{/if}

	{#if imageResult && !thumbFailed}
		<!-- The finished artwork, clickable straight through to the full result. -->
		<button
			type="button"
			class="thumb"
			on:click={() => state?.last_done && onSelectCall(state.last_done.id)}
			title="Open the hero image and the prompt that produced it"
		>
			<img
				src={imageResult}
				alt="The day's hero illustration, as generated by this run"
				loading="lazy"
				decoding="async"
				on:error={() => (thumbFailed = true)}
			/>
			<span class="thumb-cta">View result</span>
		</button>
	{/if}

	{#if status === 'idle' && activeCalls.length === 0 && restingCalls.length === 0}
		<div class="resting muted-text">
			{#if agent.blurb}{agent.blurb}{:else}Standing by{/if}
		</div>
	{/if}

	{#if isImageAgent && status !== 'idle'}
		<!-- The image client bypasses the cost tracker, so `state.cost_usd` is always
		     zero here; the figure comes off the call itself, and only when the
		     provider actually reported usage. -->
		<div class="foot">
			{#if imageCall?.usage_measured}
				<span>1 image</span>
				<span class="foot-dot">·</span>
				<span>{formatCost(imageCall.cost_usd)}</span>
			{:else}
				<span>{status === 'done' ? '1 image · ' : ''}cost not reported by provider</span>
			{/if}
		</div>
	{:else if completed > 0 && (state?.output_tokens ?? 0) > 0}
		<div class="foot">
			<span>{formatTokens(state?.output_tokens ?? 0)} out</span>
			<span class="foot-dot">·</span>
			<span>{formatCost(state?.cost_usd ?? 0)}</span>
		</div>
	{:else if agent.items_out != null && (status === 'done' || status === 'active')}
		<div class="foot">
			<span>{agent.items_out.toLocaleString()} items collected</span>
		</div>
	{/if}
</div>

<style>
	.station {
		position: relative;
		border-radius: 0.75rem;
		padding: 0.7rem 0.75rem 0.6rem;
		background: rgb(255 255 255 / 0.72);
		border: 1px solid rgb(0 0 0 / 0.08);
		overflow: hidden;
		transition:
			border-color 220ms ease,
			box-shadow 220ms ease,
			opacity 220ms ease,
			transform 220ms ease;
	}
	:global(.dark) .station {
		background: rgb(23 23 23 / 0.72);
		border-color: rgb(255 255 255 / 0.09);
	}

	.station.is-idle {
		opacity: 0.44;
	}
	.station.is-done {
		opacity: 0.92;
		border-color: color-mix(in srgb, var(--accent) 34%, transparent);
	}
	.station.is-live {
		opacity: 1;
		border-color: color-mix(in srgb, var(--accent) 72%, transparent);
		box-shadow:
			0 0 0 1px color-mix(in srgb, var(--accent) 30%, transparent),
			0 6px 22px -8px color-mix(in srgb, var(--accent) 60%, transparent);
		transform: translateY(-1px);
	}

	.flash-layer {
		position: absolute;
		inset: 0;
		pointer-events: none;
		background: radial-gradient(
			120% 90% at 50% 0%,
			color-mix(in srgb, var(--accent) 42%, transparent) 0%,
			transparent 70%
		);
		opacity: calc(var(--flash) * 0.75);
	}

	.station-head {
		display: flex;
		align-items: center;
		gap: 0.5rem;
		position: relative;
	}

	.dot {
		position: relative;
		width: 0.55rem;
		height: 0.55rem;
		flex: none;
		border-radius: 999px;
		background: var(--accent);
	}
	.is-idle .dot {
		background: #a3a3a3;
	}
	.dot-ring {
		position: absolute;
		inset: -0.28rem;
		border-radius: 999px;
		border: 1.5px solid var(--accent);
		animation: ring 1.5s ease-out infinite;
	}
	@keyframes ring {
		0% {
			transform: scale(0.7);
			opacity: 0.9;
		}
		100% {
			transform: scale(2.1);
			opacity: 0;
		}
	}

	.label {
		font-size: 0.8rem;
		font-weight: 650;
		line-height: 1.15;
		color: #262626;
		white-space: nowrap;
		overflow: hidden;
		text-overflow: ellipsis;
	}
	:global(.dark) .label {
		color: #f5f5f5;
	}

	.kind {
		font-size: 0.6rem;
		letter-spacing: 0.06em;
		text-transform: uppercase;
		color: #737373;
	}
	:global(.dark) .kind {
		color: #a3a3a3;
	}

	/* Effort tier, per call. Colour rises with the tier so the expensive calls stand
	   out in a scan of the floor; `high` stays neutral because most calls are cheap
	   and tinting them all would flatten the signal. */
	.effort {
		display: inline-block;
		padding: 0 4px;
		border-radius: 3px;
		font-size: 0.5rem;
		font-weight: 700;
		letter-spacing: 0.04em;
		background: rgb(0 0 0 / 0.05);
		color: #737373;
		flex: none;
	}
	:global(.dark) .effort {
		background: rgb(255 255 255 / 0.08);
	}
	.effort[data-effort='xhigh'] {
		background: rgb(139 92 246 / 0.14);
		color: #6d28d9;
	}
	:global(.dark) .effort[data-effort='xhigh'] {
		color: #c4b5fd;
	}
	.effort[data-effort='max'] {
		background: rgb(230 57 70 / 0.14);
		color: #b91c1c;
	}
	:global(.dark) .effort[data-effort='max'] {
		color: #fca5a5;
	}

	.counter {
		font-variant-numeric: tabular-nums;
		font-size: 0.68rem;
		color: #737373;
		flex: none;
	}
	:global(.dark) .counter {
		color: #a3a3a3;
	}
	.counter-now {
		color: var(--accent);
		font-weight: 700;
	}
	.counter-sep {
		opacity: 0.5;
		margin: 0 1px;
	}

	.progress {
		margin-top: 0.45rem;
		height: 2px;
		border-radius: 999px;
		background: rgb(0 0 0 / 0.09);
		overflow: hidden;
	}
	:global(.dark) .progress {
		background: rgb(255 255 255 / 0.12);
	}
	.progress-fill {
		height: 100%;
		background: var(--accent);
		transition: width 120ms linear;
	}

	.sources {
		margin-top: 0.5rem;
		display: flex;
		flex-direction: column;
		gap: 2px;
	}
	.source {
		position: relative;
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 0.4rem;
		font-size: 0.65rem;
		padding: 2px 5px;
		border-radius: 4px;
		background: rgb(0 0 0 / 0.04);
		color: #525252;
		overflow: hidden;
	}
	:global(.dark) .source {
		background: rgb(255 255 255 / 0.05);
		color: #d4d4d4;
	}
	.source-bar {
		position: absolute;
		left: 0;
		top: 0;
		bottom: 0;
		background: color-mix(in srgb, var(--accent) 20%, transparent);
		transition: width 120ms linear;
	}
	.source-name,
	.source-count {
		position: relative;
		white-space: nowrap;
	}
	.source-name {
		overflow: hidden;
		text-overflow: ellipsis;
	}
	.source-count {
		font-variant-numeric: tabular-nums;
		flex: none;
	}
	.src-done .source-count {
		color: var(--accent);
		font-weight: 600;
	}
	.working {
		color: var(--accent);
	}
	.muted {
		opacity: 0.4;
	}
	.warn {
		color: #f59e0b;
		font-weight: 700;
		margin-left: 2px;
	}

	.calls {
		margin-top: 0.5rem;
		display: flex;
		flex-direction: column;
		gap: 3px;
	}

	.call-chip {
		position: relative;
		display: block;
		width: 100%;
		text-align: left;
		border-radius: 5px;
		overflow: hidden;
		background: rgb(0 0 0 / 0.05);
		border: 1px solid color-mix(in srgb, var(--route) 40%, transparent);
		padding: 3px 6px;
		cursor: pointer;
		transition: border-color 150ms ease;
	}
	:global(.dark) .call-chip {
		background: rgb(255 255 255 / 0.06);
	}
	.call-chip:focus-visible {
		border-color: var(--route);
		outline: none;
	}
	@media (hover: hover) and (pointer: fine) {
		.call-chip:hover {
			border-color: var(--route);
			outline: none;
		}
	}
	.call-chip.queued {
		opacity: 0.62;
		border-style: dashed;
	}

	/* Landed work: recessive enough that the live chips above still read as "now",
	   but fully interactive — these are the calls with complete transcripts. */
	.done-calls {
		margin-top: 3px;
	}
	.call-chip.is-complete {
		background: transparent;
		border-color: color-mix(in srgb, var(--route) 22%, transparent);
	}
	:global(.dark) .call-chip.is-complete {
		background: transparent;
	}
	.call-chip.is-complete .chip-task {
		color: #737373;
		font-weight: 500;
	}
	:global(.dark) .call-chip.is-complete .chip-task {
		color: #a3a3a3;
	}
	.call-chip.is-complete:focus-visible .chip-task {
		color: #262626;
	}
	:global(.dark) .call-chip.is-complete:focus-visible .chip-task {
		color: #e5e5e5;
	}
	@media (hover: hover) and (pointer: fine) {
		.call-chip.is-complete:hover .chip-task {
			color: #262626;
		}
		:global(.dark) .call-chip.is-complete:hover .chip-task {
			color: #e5e5e5;
		}
	}
	.call-chip.is-complete.failed {
		border-color: color-mix(in srgb, #ef4444 45%, transparent);
	}

	.chip-check {
		color: #10b981;
		margin-right: 0.25rem;
		font-size: 0.6rem;
	}
	.call-chip.is-complete.failed .chip-check {
		color: #ef4444;
	}
	.state-tag.done-tag {
		opacity: 0.7;
	}
	.state-tag.bad-tag {
		color: #ef4444;
		font-weight: 700;
	}

	.more-btn {
		display: block;
		width: 100%;
		margin-top: 3px;
		padding: 2px 0;
		font-size: 0.58rem;
		font-weight: 600;
		letter-spacing: 0.04em;
		color: #737373;
		background: none;
		border: none;
		border-radius: 4px;
		cursor: pointer;
	}
	@media (hover: hover) and (pointer: fine) {
		.more-btn:hover {
			color: var(--accent);
			background: rgb(0 0 0 / 0.03);
		}
	}
	@media (hover: hover) and (pointer: fine) {
		:global(.dark) .more-btn:hover {
			background: rgb(255 255 255 / 0.05);
		}
	}

	.chip-fill {
		position: absolute;
		left: 0;
		top: 0;
		bottom: 0;
		background: color-mix(in srgb, var(--route) 22%, transparent);
		transition: width 120ms linear;
	}

	.chip-body {
		position: relative;
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 0.4rem;
	}

	.chip-task {
		font-size: 0.66rem;
		font-weight: 550;
		color: #262626;
		white-space: nowrap;
		overflow: hidden;
		text-overflow: ellipsis;
	}
	:global(.dark) .chip-task {
		color: #e5e5e5;
	}

	.chip-meta {
		display: flex;
		align-items: center;
		gap: 0.3rem;
		flex: none;
	}

	.route-tag {
		font-size: 0.55rem;
		font-weight: 700;
		letter-spacing: 0.04em;
		text-transform: uppercase;
		padding: 0 4px;
		border-radius: 3px;
		color: #fff;
		background: var(--route);
	}

	.state-tag {
		font-size: 0.58rem;
		font-variant-numeric: tabular-nums;
		color: #737373;
	}
	:global(.dark) .state-tag {
		color: #a3a3a3;
	}
	.state-tag.thinking {
		font-style: italic;
		color: #8b5cf6;
	}
	:global(.dark) .state-tag.thinking {
		color: #c4b5fd;
	}
	.state-tag.writing {
		color: var(--route);
		font-weight: 600;
	}
	.state-tag.painting {
		display: inline-flex;
		align-items: center;
		gap: 0.25rem;
		color: var(--route);
		font-weight: 600;
		font-style: italic;
	}
	/* Three dots cycling: an image has no token stream to count, so this is the one
	   honest "work is happening" signal — it encodes duration, not throughput. */
	.paint-dots {
		display: inline-flex;
		gap: 1.5px;
	}
	.paint-dots i {
		width: 3px;
		height: 3px;
		border-radius: 999px;
		background: currentColor;
		opacity: 0.3;
		animation: paint 1.2s ease-in-out infinite;
	}
	.paint-dots i:nth-child(2) {
		animation-delay: 0.2s;
	}
	.paint-dots i:nth-child(3) {
		animation-delay: 0.4s;
	}
	@keyframes paint {
		0%,
		100% {
			opacity: 0.25;
		}
		50% {
			opacity: 1;
		}
	}

	.thumb {
		position: relative;
		display: block;
		width: 100%;
		margin-top: 0.4rem;
		border-radius: 6px;
		overflow: hidden;
		border: 1px solid color-mix(in srgb, var(--accent) 45%, transparent);
		cursor: pointer;
		line-height: 0;
		transition: border-color 150ms ease, box-shadow 150ms ease;
	}
	.thumb:focus-visible {
		border-color: var(--accent);
		box-shadow: 0 0 0 2px color-mix(in srgb, var(--accent) 30%, transparent);
		outline: none;
	}
	@media (hover: hover) and (pointer: fine) {
		.thumb:hover {
			border-color: var(--accent);
			box-shadow: 0 0 0 2px color-mix(in srgb, var(--accent) 30%, transparent);
			outline: none;
		}
	}
	.thumb img {
		width: 100%;
		aspect-ratio: 21 / 9;
		object-fit: cover;
		display: block;
	}
	.thumb-cta {
		position: absolute;
		right: 3px;
		bottom: 3px;
		font-size: 0.52rem;
		font-weight: 700;
		letter-spacing: 0.04em;
		text-transform: uppercase;
		color: #fff;
		background: rgb(0 0 0 / 0.62);
		padding: 1px 5px;
		border-radius: 3px;
		line-height: 1.5;
	}

	.resting {
		margin-top: 0.45rem;
		display: flex;
		align-items: center;
		gap: 0.3rem;
		font-size: 0.65rem;
		color: #525252;
		min-height: 1rem;
	}
	:global(.dark) .resting {
		color: #a3a3a3;
	}
	.muted-text {
		font-style: italic;
		opacity: 0.85;
		line-height: 1.3;
	}

	.foot {
		margin-top: 0.4rem;
		font-size: 0.6rem;
		font-variant-numeric: tabular-nums;
		color: #737373;
		display: flex;
		gap: 0.3rem;
	}
	:global(.dark) .foot {
		color: #8f8f8f;
	}
	.foot-dot {
		opacity: 0.5;
	}

	@media (prefers-reduced-motion: reduce) {
		.dot-ring {
			animation: none;
			opacity: 0;
		}
		.paint-dots i {
			animation: none;
			opacity: 0.7;
		}
		.station,
		.progress-fill,
		.chip-fill,
		.source-bar,
		.thumb {
			transition: none;
		}
	}
</style>
