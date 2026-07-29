<script lang="ts">
	import { onMount } from 'svelte';
	import { getRuns } from '$lib/services/adminApi';
	import type { WorkflowRun } from '$lib/types/admin';

	let runs = $state<WorkflowRun[]>([]);
	let error = $state<string | null>(null);
	let apiNote = $state<string | null>(null);
	let loading = $state(true);
	let hideNoops = $state(true);

	onMount(async () => {
		try {
			const payload = await getRuns(50);
			runs = payload.runs;
			apiNote = payload.error ?? null;
		} catch (e) {
			error = e instanceof Error ? e.message : 'Could not load run history.';
		} finally {
			loading = false;
		}
	});

	const shown = $derived(hideNoops ? runs.filter((r) => r.did_real_work) : runs);
	const noopCount = $derived(runs.filter((r) => !r.did_real_work).length);
	const failures = $derived(
		runs.filter((r) => r.did_real_work && r.conclusion && r.conclusion !== 'success').length
	);

	function statusColor(run: WorkflowRun): string {
		if (run.status !== 'completed') return '#3b82f6';
		if (run.conclusion === 'success') return '#10b981';
		if (run.conclusion === 'cancelled') return '#a3a3a3';
		return '#ef4444';
	}

	function duration(seconds: number): string {
		if (seconds < 60) return `${seconds}s`;
		return `${Math.round(seconds / 60)}m`;
	}
</script>

<div class="card">
	<div class="flex items-baseline justify-between gap-3 flex-wrap mb-3">
		<div>
			<h2 class="text-lg font-semibold text-trend-gray-800 dark:text-trend-gray-100">
				Pipeline runs
			</h2>
			<p class="text-sm text-trend-gray-600 dark:text-trend-gray-400">
				From the Actions API. Published data only ever contains successful runs, so failures
				and cancellations appear nowhere else.
			</p>
		</div>
		{#if noopCount}
			<label class="text-xs text-trend-gray-600 dark:text-trend-gray-400">
				<input type="checkbox" bind:checked={hideNoops} />
				Hide {noopCount} schedule-gate no-op{noopCount === 1 ? '' : 's'}
			</label>
		{/if}
	</div>

	{#if error}
		<p class="text-sm text-trend-red">{error}</p>
	{:else if apiNote}
		<p class="text-sm text-trend-gray-600 dark:text-trend-gray-400">{apiNote}</p>
	{:else if loading}
		<p class="text-sm text-trend-gray-500">Loading…</p>
	{:else if !runs.length}
		<p class="text-sm text-trend-gray-500">No runs found.</p>
	{:else}
		<div class="run-stats mb-3">
			<div><span class="rs-label">Shown</span><span class="rs-value">{shown.length}</span></div>
			<div><span class="rs-label">No-ops</span><span class="rs-value">{noopCount}</span></div>
			<div><span class="rs-label">Not OK</span><span class="rs-value">{failures}</span></div>
		</div>

		<ul class="divide-y divide-trend-gray-200 dark:divide-trend-gray-700">
			{#each shown as run (run.id)}
				<li class="py-2 flex items-center gap-3 text-sm">
					<span class="dot" style="--c: {statusColor(run)}" aria-hidden="true"></span>
					<a
						href={run.html_url}
						target="_blank"
						rel="noopener noreferrer"
						class="font-mono text-xs text-trend-red hover:text-guardian-red"
					>
						{run.created_at.slice(0, 10)}
					</a>
					<span class="text-trend-gray-700 dark:text-trend-gray-300">
						{run.status === 'completed' ? (run.conclusion ?? 'unknown') : run.status}
					</span>
					<span class="text-trend-gray-500 text-xs">{run.event}</span>
					<span class="ml-auto text-trend-gray-500 text-xs tabular-nums">
						{duration(run.duration_seconds)}
					</span>
				</li>
			{/each}
		</ul>
	{/if}
</div>

<style>
	.dot {
		width: 8px;
		height: 8px;
		border-radius: 50%;
		background: var(--c);
		flex: 0 0 8px;
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
