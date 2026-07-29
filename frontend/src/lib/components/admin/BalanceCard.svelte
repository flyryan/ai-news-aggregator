<script lang="ts">
	import { onMount } from 'svelte';
	import { getBalances } from '$lib/services/adminApi';
	import type { Balance } from '$lib/types/admin';

	let balances = $state<Balance[]>([]);
	let error = $state<string | null>(null);
	let loading = $state(true);

	onMount(async () => {
		try {
			balances = (await getBalances()).balances;
		} catch (e) {
			error = e instanceof Error ? e.message : 'Could not read vendor balances.';
		} finally {
			loading = false;
		}
	});
</script>

{#if error}
	<div class="card"><p class="text-sm text-trend-red">{error}</p></div>
{:else if loading}
	<div class="card"><p class="text-sm text-trend-gray-500">Reading balances…</p></div>
{:else}
	{#each balances as b (b.vendor)}
		<div class="card" data-urgent={b.urgent}>
			<h3 class="text-sm font-semibold text-trend-gray-800 dark:text-trend-gray-100">{b.label}</h3>

			{#if b.balance === null}
				<p class="mt-2 text-sm text-trend-gray-600 dark:text-trend-gray-400">
					{b.error || 'No balance available.'}
				</p>
			{:else}
				<p class="balance">
					{b.balance.toLocaleString()}<span class="unit">{b.unit}</span>
				</p>
				{#if b.balance_usd !== null}
					<p class="text-xs text-trend-gray-500">${b.balance_usd.toFixed(2)}</p>
				{/if}

				{#if b.days_remaining !== null && b.burn_per_day !== null}
					<p class="mt-2 text-sm" class:urgent={b.urgent}>
						~{Math.round(b.days_remaining)} days left at {b.burn_per_day}/day
					</p>
					{#if b.urgent}
						<p class="mt-1 text-xs text-trend-gray-600 dark:text-trend-gray-400">
							When this reaches zero the source stops collecting, and the published report
							will still say success.
						</p>
					{/if}
				{:else}
					<p class="mt-2 text-xs text-trend-gray-500">
						Trend needs a second reading before it can project.
					</p>
				{/if}
			{/if}
		</div>
	{/each}
{/if}

<style>
	.balance {
		margin-top: 0.35rem;
		font-size: 1.6rem;
		font-weight: 700;
		font-variant-numeric: tabular-nums;
		line-height: 1.1;
	}
	.unit {
		font-size: 0.7rem;
		font-weight: 500;
		color: #737373;
		margin-left: 0.3rem;
	}
	.urgent {
		color: #ef4444;
		font-weight: 600;
	}
	.card[data-urgent='true'] {
		border-left: 4px solid #ef4444;
	}
</style>
