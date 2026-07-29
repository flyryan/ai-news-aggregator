<script lang="ts">
	import { onMount } from 'svelte';
	import { AdminApiError, getLatest } from '$lib/services/adminApi';
	import type { LatestReport } from '$lib/types/admin';
	import HealthTimeline from '$lib/components/admin/HealthTimeline.svelte';
	import RunHistory from '$lib/components/admin/RunHistory.svelte';
	import CostTrend from '$lib/components/admin/CostTrend.svelte';
	import BalanceCard from '$lib/components/admin/BalanceCard.svelte';
	import ActionPanel from '$lib/components/admin/ActionPanel.svelte';
	import PreviewPanel from '$lib/components/admin/PreviewPanel.svelte';

	type View = 'health' | 'runs' | 'cost' | 'preview' | 'actions';

	let view = $state<View>('health');
	let latest = $state<LatestReport | null>(null);
	let error = $state<string | null>(null);
	let loading = $state(true);

	onMount(async () => {
		try {
			latest = (await getLatest()).latest;
		} catch (e) {
			error = e instanceof AdminApiError ? e.message : 'Could not reach the admin service.';
		} finally {
			loading = false;
		}
	});

	const views: { id: View; label: string }[] = [
		{ id: 'health', label: 'Health' },
		{ id: 'runs', label: 'Runs' },
		{ id: 'cost', label: 'Cost' },
		{ id: 'preview', label: 'Preview' },
		{ id: 'actions', label: 'Actions' }
	];
</script>

<svelte:head>
	<title>Operations · AATF</title>
	<meta name="robots" content="noindex, nofollow" />
</svelte:head>

<div class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
	<header class="mb-6">
		<p class="eyebrow">Operations</p>
		<h1 class="text-2xl sm:text-3xl font-bold text-trend-gray-800 dark:text-trend-gray-100">
			Pipeline control
		</h1>
		<p class="mt-1 text-sm text-trend-gray-600 dark:text-trend-gray-400 max-w-2xl">
			{#if latest}
				Last published
				<strong class="text-trend-gray-800 dark:text-trend-gray-200">{latest.date}</strong>
				— {latest.total_items.toLocaleString()} items, {latest.topics} topics.
			{:else if loading}
				Loading the current state…
			{:else}
				No published report found.
			{/if}
		</p>
	</header>

	{#if error}
		<div class="card border-l-4 border-l-trend-red" role="alert">
			<h2 class="font-semibold text-trend-gray-800 dark:text-trend-gray-100">
				The admin service is not responding
			</h2>
			<p class="mt-1 text-sm text-trend-gray-600 dark:text-trend-gray-400">{error}</p>
			<p class="mt-3 text-sm text-trend-gray-600 dark:text-trend-gray-400">
				In development, start it with:
				<code class="block mt-1 text-xs bg-black/5 dark:bg-white/5 rounded px-2 py-1"
					>./scripts/admin_dev.sh</code
				>
			</p>
		</div>
	{:else}
		{#if latest}
			<div class="run-stats mb-5">
				<div>
					<span class="rs-label">Items</span>
					<span class="rs-value">{latest.total_items.toLocaleString()}</span>
				</div>
				<div><span class="rs-label">Topics</span><span class="rs-value">{latest.topics}</span></div>
				{#each Object.entries(latest.categories) as [name, info] (name)}
					<div>
						<span class="rs-label">{name}</span><span class="rs-value">{info.count}</span>
					</div>
				{/each}
				<div>
					<span class="rs-label">Replay</span>
					<span class="rs-value">{latest.has_replay ? 'yes' : 'none'}</span>
				</div>
			</div>
		{/if}

		<div class="viewswitch mb-4" role="tablist" aria-label="Dashboard view">
			{#each views as v (v.id)}
				<button
					role="tab"
					aria-selected={view === v.id}
					class:on={view === v.id}
					onclick={() => (view = v.id)}
				>
					{v.label}
				</button>
			{/each}
		</div>

		{#if view === 'health'}
			<HealthTimeline />
		{:else if view === 'runs'}
			<RunHistory />
		{:else if view === 'cost'}
			<CostTrend />
			<div class="grid gap-4 sm:grid-cols-2 mt-4">
				<BalanceCard />
			</div>
		{:else if view === 'preview'}
			<PreviewPanel />
		{:else if view === 'actions'}
			<ActionPanel />
		{/if}
	{/if}
</div>

<style>
	.eyebrow {
		font-size: 0.62rem;
		font-weight: 700;
		letter-spacing: 0.14em;
		text-transform: uppercase;
		color: #e63946;
		margin-bottom: 2px;
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
		transition:
			background 140ms ease,
			color 140ms ease;
	}
	:global(.dark) .viewswitch button {
		color: #a3a3a3;
	}
	.viewswitch button.on {
		background: #e63946;
		color: #fff;
	}
	.viewswitch button:focus-visible {
		outline: 2px solid #e63946;
		outline-offset: -2px;
	}

	@media (prefers-reduced-motion: reduce) {
		.viewswitch button {
			transition: none;
		}
	}
</style>
