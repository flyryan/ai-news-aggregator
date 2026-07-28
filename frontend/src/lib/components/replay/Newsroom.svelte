<script lang="ts">
	import type { ReplayIndex } from '$lib/types/replay';
	import type { ReplayFrame } from '$lib/services/replayEngine';
	import {
		buildStage,
		downstreamOf,
		agentColor,
		providerColor,
		formatTokens,
		formatCost
	} from '$lib/services/replayViz';
	import AgentStation from './AgentStation.svelte';

	export let index: ReplayIndex;
	export let frame: ReplayFrame;
	export let reduced = false;
	export let onSelectCall: (callId: string) => void = () => {};

	$: columns = buildStage(index.agents ?? []);

	/**
	 * Sub-group a column's agents by category, preserving the taxonomy's order.
	 *
	 * The analysis column holds a whole pipeline per category — triage, reader, editor
	 * — and those three hand work to each other. Banding them keeps that relationship
	 * visible instead of presenting twelve peers. Agents with no category (the desk)
	 * each stand alone.
	 */
	function groupColumn(col: { agents: ReplayIndex['agents'] }) {
		const groups: { key: string; agents: ReplayIndex['agents'] }[] = [];
		for (const agent of col.agents) {
			const key = agent.category ?? agent.id;
			const last = groups[groups.length - 1];
			if (last && last.key === key) last.agents.push(agent);
			else groups.push({ key, agents: [agent] });
		}
		return groups;
	}
	$: agentById = new Map((index.agents ?? []).map((a) => [a.id, a]));

	// Sources grouped per gatherer, so a Scout station can show its own feeds.
	$: sourcesByAgent = frame.sources.reduce((acc, s) => {
		const list = acc.get(s.agent_id) ?? [];
		list.push(s);
		acc.set(s.agent_id, list);
		return acc;
	}, new Map<string, ReplayFrame['sources']>());

	// "Reporting in": each finished call throws a packet at the next column.
	// Only calls whose owning agent has a downstream get one — the desk is terminal.
	$: packets = frame.pulses
		.map((p) => {
			const agent = agentById.get(p.call.agent_id);
			const target = agent ? downstreamOf(agent.kind) : null;
			return target ? { pulse: p, agent, target } : null;
		})
		.filter((x): x is NonNullable<typeof x> => x !== null)
		.slice(0, 14);

	// Packets in flight per conveyor, keyed by the column they are heading toward, so
	// adding a column does not need this to be rewritten.
	$: laneCounts = packets.reduce(
		(acc, p) => acc.set(p.target, (acc.get(p.target) ?? 0) + 1),
		new Map<string, number>()
	);

	$: inFlight = frame.active.filter((a) => a.state !== 'queued').length;
	$: queuedNow = frame.active.filter((a) => a.state === 'queued').length;

	// Which routes are carrying traffic right now — the load-balancing readout.
	$: routeLoad = (() => {
		const counts = new Map<string, number>();
		for (const a of frame.active) {
			if (a.state === 'queued') continue;
			counts.set(a.call.provider_id, (counts.get(a.call.provider_id) ?? 0) + 1);
		}
		const known = index.calls.reduce((set, c) => set.add(c.provider_id), new Set<string>());
		return [...known].sort().map((id) => ({ id, count: counts.get(id) ?? 0 }));
	})();
</script>

<div class="stage" class:reduced>
	<!-- Backdrop shifts with the phase; the gradient is the only ambient motion -->
	<div class="backdrop" aria-hidden="true"></div>

	<div class="stage-header">
		<div class="phase-block">
			<span class="phase-ord">Phase {frame.phase?.ordinal ?? '—'}</span>
			<h3 class="phase-label">{frame.phase?.label ?? 'Idle'}</h3>
			{#if frame.phase?.detail}
				<p class="phase-detail">{frame.phase.detail}</p>
			{/if}
		</div>

		<dl class="live-stats">
			<div><dt>In flight</dt><dd class="stat-hot">{inFlight}</dd></div>
			<div><dt>Queued</dt><dd>{queuedNow}</dd></div>
			<div><dt>Done</dt><dd>{frame.completedCalls}<span class="of">/{index.calls.length}</span></dd></div>
			<div><dt>Output</dt><dd>{formatTokens(frame.output_tokens)}</dd></div>
			<div><dt>Spend</dt><dd>{formatCost(frame.cost_usd)}</dd></div>
		</dl>
	</div>

	<!-- Route load: three providers taking work in real time -->
	<div class="routes" aria-label="Provider route load">
		<span class="routes-label">Routes</span>
		{#each routeLoad as r (r.id)}
			<span class="route" class:hot={r.count > 0} style="--route: {providerColor(r.id)}">
				<span class="route-name">{r.id}</span>
				<span class="route-count">{r.count}</span>
			</span>
		{/each}
	</div>

	<div class="floor" style="--floor-cols: {columns.map(() => '1fr').join(' auto ')}">
		{#each columns as col, ci (col.id)}
			<section class="column" aria-label={col.title}>
				<header class="col-head">
					<h4>{col.title}</h4>
					<span>{col.caption}</span>
				</header>

				<div class="stations">
					{#each groupColumn(col) as group, gi (group.key)}
						<!-- A category's agents hand work to each other, so they are banded
						     together with a hairline rather than left as a flat list. -->
						<div class="group" class:banded={group.agents.length > 1} class:first={gi === 0}>
							{#each group.agents as agent (agent.id)}
								<AgentStation
									{agent}
									state={frame.agents.get(agent.id)}
									sources={sourcesByAgent.get(agent.id) ?? []}
									{reduced}
									{onSelectCall}
								/>
							{/each}
						</div>
					{/each}
				</div>
			</section>

			{#if ci < columns.length - 1}
				{@const nextId = columns[ci + 1].id}
				<!-- The conveyor between columns. Each packet is one real completed call. -->
				<div class="lane" aria-hidden="true">
					<span class="lane-rail"></span>
					{#if !reduced}
						{#each packets.filter((p) => p.target === nextId) as p (p.pulse.call.id)}
							<span
								class="packet"
								style="
									--x: {p.pulse.age * 100}%;
									--fade: {1 - Math.pow(p.pulse.age, 3)};
									--accent: {agentColor(p.agent)};
									--drift: {((p.pulse.call.id.charCodeAt(p.pulse.call.id.length - 1) % 7) - 3) * 9}px;
								"
							></span>
						{/each}
					{/if}
					{#if (laneCounts.get(nextId) ?? 0) > 0}
						<span class="lane-badge">+{laneCounts.get(nextId)}</span>
					{/if}
				</div>
			{/if}
		{/each}
	</div>
</div>

<style>
	.stage {
		position: relative;
		border-radius: 1rem;
		overflow: hidden;
		border: 1px solid rgb(0 0 0 / 0.08);
		background: linear-gradient(180deg, #fafafa 0%, #f1f1f4 100%);
		padding: 1rem;
	}
	:global(.dark) .stage {
		border-color: rgb(255 255 255 / 0.08);
		background: linear-gradient(180deg, #14141a 0%, #0e0e13 100%);
	}

	.backdrop {
		position: absolute;
		inset: 0;
		pointer-events: none;
		background:
			radial-gradient(70% 55% at 12% 0%, rgb(102 126 234 / 0.1), transparent 60%),
			radial-gradient(60% 50% at 50% 100%, rgb(139 92 246 / 0.08), transparent 60%),
			radial-gradient(60% 60% at 92% 10%, rgb(230 57 70 / 0.09), transparent 62%);
	}
	:global(.dark) .backdrop {
		background:
			radial-gradient(70% 55% at 12% 0%, rgb(102 126 234 / 0.18), transparent 60%),
			radial-gradient(60% 50% at 50% 100%, rgb(139 92 246 / 0.14), transparent 60%),
			radial-gradient(60% 60% at 92% 10%, rgb(230 57 70 / 0.16), transparent 62%);
	}

	.stage-header {
		position: relative;
		display: flex;
		flex-wrap: wrap;
		align-items: flex-start;
		justify-content: space-between;
		gap: 0.75rem;
		margin-bottom: 0.6rem;
	}

	.phase-ord {
		font-size: 0.6rem;
		font-weight: 700;
		letter-spacing: 0.12em;
		text-transform: uppercase;
		color: #E63946;
	}
	.phase-label {
		font-size: 1.05rem;
		font-weight: 700;
		line-height: 1.2;
		color: #171717;
	}
	:global(.dark) .phase-label {
		color: #fafafa;
	}
	.phase-detail {
		font-size: 0.72rem;
		color: #525252;
		margin-top: 1px;
	}
	:global(.dark) .phase-detail {
		color: #a3a3a3;
	}

	.live-stats {
		display: flex;
		gap: 0.9rem;
		flex-wrap: wrap;
	}
	.live-stats dt {
		font-size: 0.55rem;
		letter-spacing: 0.08em;
		text-transform: uppercase;
		color: #737373;
	}
	.live-stats dd {
		font-size: 0.95rem;
		font-weight: 700;
		font-variant-numeric: tabular-nums;
		color: #262626;
		line-height: 1.1;
	}
	:global(.dark) .live-stats dd {
		color: #f5f5f5;
	}
	.stat-hot {
		color: #E63946 !important;
	}
	.of {
		font-size: 0.65rem;
		font-weight: 500;
		opacity: 0.5;
	}

	.routes {
		position: relative;
		display: flex;
		align-items: center;
		gap: 0.4rem;
		margin-bottom: 0.75rem;
		flex-wrap: wrap;
	}
	.routes-label {
		font-size: 0.55rem;
		letter-spacing: 0.1em;
		text-transform: uppercase;
		color: #737373;
		margin-right: 0.1rem;
	}
	.route {
		display: inline-flex;
		align-items: center;
		gap: 0.3rem;
		padding: 1px 7px 1px 5px;
		border-radius: 999px;
		font-size: 0.62rem;
		font-weight: 600;
		border: 1px solid color-mix(in srgb, var(--route) 35%, transparent);
		color: #737373;
		transition: all 180ms ease;
	}
	.route.hot {
		color: #fff;
		background: var(--route);
		border-color: var(--route);
		box-shadow: 0 0 12px -2px color-mix(in srgb, var(--route) 70%, transparent);
	}
	.route-count {
		font-variant-numeric: tabular-nums;
		opacity: 0.85;
	}

	/* Columns and conveyors alternate, so the template is generated from the column
	   count rather than hardcoded — the cast gained a column and this should not
	   have to change again. */
	.floor {
		position: relative;
		display: grid;
		grid-template-columns: var(--floor-cols);
		gap: 0.3rem;
		align-items: stretch;
	}

	.column {
		min-width: 0;
	}

	.col-head {
		margin-bottom: 0.45rem;
		padding-bottom: 0.3rem;
		border-bottom: 1px solid rgb(0 0 0 / 0.08);
	}
	:global(.dark) .col-head {
		border-color: rgb(255 255 255 / 0.1);
	}
	.col-head h4 {
		font-size: 0.68rem;
		font-weight: 700;
		letter-spacing: 0.1em;
		text-transform: uppercase;
		color: #404040;
	}
	:global(.dark) .col-head h4 {
		color: #d4d4d4;
	}
	.col-head span {
		font-size: 0.6rem;
		color: #737373;
	}

	.stations {
		display: flex;
		flex-direction: column;
		gap: 0.4rem;
	}

	/* Agents in the same category sit tight together; the gap between groups is what
	   separates one category's pipeline from the next. */
	.group {
		display: flex;
		flex-direction: column;
		gap: 0.2rem;
	}
	.group.banded + :global(.group) {
		margin-top: 0.35rem;
	}
	.group.banded:not(.first) {
		padding-top: 0.55rem;
		border-top: 1px solid rgb(0 0 0 / 0.07);
	}
	:global(.dark) .group.banded:not(.first) {
		border-top-color: rgb(255 255 255 / 0.09);
	}

	/* --- the conveyor ------------------------------------------------------ */
	.lane {
		position: relative;
		width: 2.75rem;
		align-self: stretch;
		margin-top: 2.1rem;
	}
	.lane-rail {
		position: absolute;
		left: 0;
		right: 0;
		top: 50%;
		height: 1px;
		background: linear-gradient(
			90deg,
			transparent,
			rgb(0 0 0 / 0.16) 20%,
			rgb(0 0 0 / 0.16) 80%,
			transparent
		);
	}
	:global(.dark) .lane-rail {
		background: linear-gradient(
			90deg,
			transparent,
			rgb(255 255 255 / 0.18) 20%,
			rgb(255 255 255 / 0.18) 80%,
			transparent
		);
	}

	.packet {
		position: absolute;
		top: calc(50% + var(--drift, 0px));
		left: var(--x);
		width: 7px;
		height: 7px;
		margin-top: -3.5px;
		margin-left: -3.5px;
		border-radius: 999px;
		background: var(--accent);
		opacity: var(--fade);
		box-shadow:
			0 0 10px 1px color-mix(in srgb, var(--accent) 85%, transparent),
			-9px 0 8px -5px color-mix(in srgb, var(--accent) 60%, transparent);
	}

	.lane-badge {
		position: absolute;
		top: calc(50% - 1.5rem);
		left: 50%;
		transform: translateX(-50%);
		font-size: 0.58rem;
		font-weight: 700;
		font-variant-numeric: tabular-nums;
		color: #E63946;
	}

	/* --- responsive: stack the floor on small screens ---------------------- */
	@media (max-width: 900px) {
		.floor {
			grid-template-columns: 1fr;
			gap: 0.9rem;
		}
		.lane {
			width: 100%;
			height: 1.4rem;
			margin-top: 0;
			align-self: auto;
		}
		.lane-rail {
			left: 50%;
			right: auto;
			top: 0;
			bottom: 0;
			height: auto;
			width: 1px;
			background: linear-gradient(180deg, transparent, rgb(0 0 0 / 0.16), transparent);
		}
		:global(.dark) .lane-rail {
			background: linear-gradient(180deg, transparent, rgb(255 255 255 / 0.18), transparent);
		}
		.packet {
			top: var(--x);
			left: 50%;
		}
		.lane-badge {
			top: 50%;
			left: calc(50% + 1.2rem);
			transform: translateY(-50%);
		}
	}

	@media (prefers-reduced-motion: reduce) {
		.route {
			transition: none;
		}
	}
</style>
