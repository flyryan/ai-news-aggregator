<script lang="ts">
	import { onMount } from 'svelte';
	import { getHealth } from '$lib/services/adminApi';
	import type { HealthSeries } from '$lib/types/admin';

	let data = $state<HealthSeries | null>(null);
	let error = $state<string | null>(null);
	let days = $state(90);

	async function load() {
		error = null;
		try {
			data = await getHealth(days);
		} catch (e) {
			error = e instanceof Error ? e.message : 'Could not load source health.';
		}
	}

	onMount(load);

	// Per-source scaling: research runs ~500/day and news ~45, so one shared
	// scale would flatten news into a single tone and hide exactly the drops
	// this view exists to show. The tooltip carries the absolute number.
	function intensity(source: string, value: number | null): number {
		if (value === null || !data) return 0;
		const series = data.series[source].filter((v): v is number => v !== null);
		const peak = Math.max(...series, 1);
		return Math.min(1, value / peak);
	}

	function isAnomalous(date: string, source: string): boolean {
		return !!data?.anomalies.some((a) => a.date === date && a.source === source);
	}

	const recentAnomalies = $derived(
		[...(data?.anomalies ?? [])].sort((a, b) => b.date.localeCompare(a.date)).slice(0, 8)
	);
</script>

<div class="card">
	<div class="flex items-baseline justify-between gap-3 flex-wrap mb-3">
		<div>
			<h2 class="text-lg font-semibold text-trend-gray-800 dark:text-trend-gray-100">
				Source health
			</h2>
			<p class="text-sm text-trend-gray-600 dark:text-trend-gray-400">
				Items collected per source, compared against the same weekday — arXiv skips weekends
				and Monday runs a three-day catch-up.
			</p>
		</div>
		<label class="text-xs text-trend-gray-600 dark:text-trend-gray-400">
			Window
			<select
				bind:value={days}
				onchange={load}
				class="ml-1 rounded border border-trend-gray-300 dark:border-trend-gray-600 bg-transparent px-1 py-0.5"
			>
				<option value={30}>30 days</option>
				<option value={90}>90 days</option>
				<option value={180}>180 days</option>
				<option value={365}>365 days</option>
			</select>
		</label>
	</div>

	{#if error}
		<p class="text-sm text-trend-red">{error}</p>
	{:else if !data}
		<p class="text-sm text-trend-gray-500">Loading…</p>
	{:else if data.dates.length === 0}
		<p class="text-sm text-trend-gray-500">No published reports in this window.</p>
	{:else}
		<div class="overflow-x-auto">
			<table class="heatmap">
				<caption class="sr-only">
					Items collected per source per day, with anomalies marked
				</caption>
				<tbody>
					{#each data.sources as source (source)}
						<tr>
							<th scope="row">{source}</th>
							<td>
								<div class="cells">
									{#each data.dates as date, i (date)}
										{@const value = data.series[source][i]}
										{@const flagged = isAnomalous(date, source)}
										<span
											class="cell"
											class:missing={value === null}
											class:flagged
											style="--i: {intensity(source, value)}"
											title="{source} · {date} · {value === null
												? 'no report published'
												: `${value} items`}{flagged ? ' · below baseline' : ''}"
										></span>
									{/each}
								</div>
							</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>

		<p class="legend">
			<span class="cell" style="--i: 0.15"></span>
			<span class="cell" style="--i: 0.5"></span>
			<span class="cell" style="--i: 1"></span>
			<span>volume</span>
			<span class="cell flagged" style="--i: 0.2"></span>
			<span>below baseline</span>
			<span class="cell missing"></span>
			<span>no report</span>
		</p>

		{#if recentAnomalies.length}
			<div class="mt-4 pt-4 border-t border-trend-gray-200 dark:border-trend-gray-700">
				<h3 class="text-sm font-semibold text-trend-gray-800 dark:text-trend-gray-100 mb-2">
					Flagged
				</h3>
				<ul class="space-y-1">
					{#each recentAnomalies as a (a.date + a.source)}
						<li class="text-sm text-trend-gray-700 dark:text-trend-gray-300">
							<span class="font-mono text-xs text-trend-gray-500">{a.date}</span>
							{a.detail}
						</li>
					{/each}
				</ul>
			</div>
		{:else}
			<p
				class="mt-4 pt-4 border-t border-trend-gray-200 dark:border-trend-gray-700 text-sm text-trend-gray-600 dark:text-trend-gray-400"
			>
				No sources fell below their baseline in this window.
			</p>
		{/if}
	{/if}
</div>

<style>
	.heatmap {
		border-collapse: collapse;
		width: 100%;
	}
	.heatmap th {
		text-align: right;
		padding-right: 0.6rem;
		font-size: 0.7rem;
		font-weight: 600;
		color: #525252;
		white-space: nowrap;
		vertical-align: middle;
	}
	:global(.dark) .heatmap th {
		color: #a3a3a3;
	}
	.heatmap td {
		width: 100%;
	}
	.cells {
		display: flex;
		gap: 1px;
		padding: 2px 0;
	}
	.cell {
		flex: 1 1 auto;
		min-width: 3px;
		height: 16px;
		border-radius: 1px;
		/* Volume is a neutral blue ramp, deliberately NOT the category colours:
		   category-research is the same green as `success` and category-reddit the
		   same red as `failed`, so a red Reddit lane would read as broken. */
		background: color-mix(in srgb, #3b82f6 calc(var(--i) * 100%), transparent);
	}
	.cell.missing {
		background: repeating-linear-gradient(
			45deg,
			rgb(0 0 0 / 0.06),
			rgb(0 0 0 / 0.06) 2px,
			transparent 2px,
			transparent 4px
		);
	}
	:global(.dark) .cell.missing {
		background: repeating-linear-gradient(
			45deg,
			rgb(255 255 255 / 0.08),
			rgb(255 255 255 / 0.08) 2px,
			transparent 2px,
			transparent 4px
		);
	}
	.cell.flagged {
		background: #ef4444;
		box-shadow: 0 0 0 1px #ef4444;
	}
	.legend {
		display: flex;
		align-items: center;
		gap: 0.4rem;
		margin-top: 0.6rem;
		font-size: 0.7rem;
		color: #737373;
	}
	.legend .cell {
		flex: 0 0 12px;
		width: 12px;
		height: 12px;
	}
</style>
