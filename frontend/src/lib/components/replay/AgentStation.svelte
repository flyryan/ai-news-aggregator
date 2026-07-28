<script lang="ts">
	import type { ReplayAgent } from '$lib/types/replay';
	import type { AgentFrameState } from '$lib/services/replayEngine';
	import {
		agentColor,
		providerColor,
		formatTokens,
		formatCost,
		formatDuration
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
						title="{ac.call.task} — {ac.call.provider_id} — {ac.call.profile}"
					>
						<span class="chip-fill" style="width: {Math.round(ac.progress * 100)}%"></span>
						<span class="chip-body">
							<span class="chip-task">{ac.call.task}</span>
							<span class="chip-meta">
								<span class="route-tag">{ac.call.provider_id}</span>
								{#if ac.state === 'queued'}
									<span class="state-tag">queued {formatDuration(ac.call.wait_ms)}</span>
								{:else if ac.state === 'waiting'}
									<span class="state-tag thinking">thinking{reduced ? '' : '…'}</span>
								{:else}
									<span class="state-tag writing">{formatTokens(ac.tokens)} tok</span>
								{/if}
							</span>
						</span>
					</button>
				</li>
			{/each}
		</ul>
	{:else if status === 'done'}
		<div class="resting">
			{#if state?.last_done}
				<span class="resting-check" aria-hidden="true">✓</span>
				<span class="truncate">{state.last_done.task}</span>
			{/if}
		</div>
	{:else if status === 'idle'}
		<div class="resting muted-text">
			{#if agent.blurb}{agent.blurb}{:else}Standing by{/if}
		</div>
	{/if}

	{#if completed > 0 && (state?.output_tokens ?? 0) > 0}
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
	.call-chip:hover,
	.call-chip:focus-visible {
		border-color: var(--route);
		outline: none;
	}
	.call-chip.queued {
		opacity: 0.62;
		border-style: dashed;
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
	.resting-check {
		color: var(--accent);
		font-weight: 700;
		flex: none;
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
		.station,
		.progress-fill,
		.chip-fill,
		.source-bar {
			transition: none;
		}
	}
</style>
