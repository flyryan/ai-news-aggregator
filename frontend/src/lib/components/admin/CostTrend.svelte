<script lang="ts">
	import { onMount } from 'svelte';
	import { getCost } from '$lib/services/adminApi';
	import type { CostRun } from '$lib/types/admin';

	let runs = $state<CostRun[]>([]);
	let error = $state<string | null>(null);
	let loading = $state(true);

	onMount(async () => {
		try {
			runs = (await getCost(400)).runs;
		} catch (e) {
			error = e instanceof Error ? e.message : 'Could not load cost history.';
		} finally {
			loading = false;
		}
	});

	const peak = $derived(Math.max(...runs.map((r) => r.cost_usd), 0.01));
	const total = $derived(runs.reduce((sum, r) => sum + r.cost_usd, 0));
	const mean = $derived(runs.length ? total / runs.length : 0);
</script>

<div class="card">
	<h2 class="text-lg font-semibold text-trend-gray-800 dark:text-trend-gray-100">Cost per run</h2>
	<p class="text-sm text-trend-gray-600 dark:text-trend-gray-400">
		From each day's published replay index. No ingest required.
	</p>

	{#if error}
		<p class="mt-3 text-sm text-trend-red">{error}</p>
	{:else if loading}
		<p class="mt-3 text-sm text-trend-gray-500">Loading…</p>
	{:else if !runs.length}
		<p class="mt-3 text-sm text-trend-gray-500">
			No replay indexes published yet. This fills in as runs complete.
		</p>
	{:else}
		<div class="run-stats mt-3 mb-3">
			<div><span class="rs-label">Runs</span><span class="rs-value">{runs.length}</span></div>
			<div><span class="rs-label">Mean</span><span class="rs-value">${mean.toFixed(2)}</span></div>
			<div><span class="rs-label">Peak</span><span class="rs-value">${peak.toFixed(2)}</span></div>
			<div><span class="rs-label">Total</span><span class="rs-value">${total.toFixed(2)}</span></div>
		</div>

		<div class="bars">
			{#each runs as run (run.date)}
				<span
					class="bar"
					class:reconstructed={!run.timings_measured}
					style="--h: {(run.cost_usd / peak) * 100}%"
					title="{run.date} · ${run.cost_usd.toFixed(2)} · {run.llm_calls} calls{run.timings_measured
						? ''
						: ' · reconstructed offline'}"
				></span>
			{/each}
		</div>

		{#if runs.some((r) => !r.timings_measured)}
			<p class="mt-2 text-xs text-trend-gray-500">
				Hatched bars are days rebuilt from stored data. Their cost is real; their timings are
				not measurements.
			</p>
		{/if}
	{/if}
</div>

<style>
	.bars {
		display: flex;
		align-items: flex-end;
		gap: 2px;
		height: 120px;
	}
	.bar {
		flex: 1 1 auto;
		min-width: 4px;
		height: var(--h);
		background: #e63946;
		border-radius: 1px 1px 0 0;
	}
	.bar.reconstructed {
		background: repeating-linear-gradient(
			45deg,
			#e63946,
			#e63946 3px,
			rgb(230 57 70 / 0.35) 3px,
			rgb(230 57 70 / 0.35) 6px
		);
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
	.run-stats :global(.rs-label) {
		font-size: 0.6rem;
		text-transform: uppercase;
		letter-spacing: 0.08em;
		color: #737373;
	}
	.run-stats :global(.rs-value) {
		font-size: 0.95rem;
		font-weight: 650;
		font-variant-numeric: tabular-nums;
	}
</style>
