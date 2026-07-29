<script lang="ts">
	import { onMount } from 'svelte';
	import { getHealth, getSourceDay } from '$lib/services/adminApi';
	import type { HealthSeries, SourceDayDetail } from '$lib/types/admin';

	let data = $state<HealthSeries | null>(null);
	let error = $state<string | null>(null);
	let days = $state(90);

	let selected = $state<{ source: string; date: string } | null>(null);
	let detail = $state<SourceDayDetail | null>(null);
	let detailError = $state<string | null>(null);
	let detailLoading = $state(false);

	async function load() {
		error = null;
		try {
			data = await getHealth(days);
		} catch (e) {
			error = e instanceof Error ? e.message : 'Could not load source health.';
		}
	}

	onMount(load);

	async function select(source: string, date: string) {
		if (selected?.source === source && selected?.date === date) {
			selected = null;
			detail = null;
			return;
		}
		selected = { source, date };
		detail = null;
		detailError = null;
		detailLoading = true;
		try {
			detail = await getSourceDay(source, date);
		} catch (e) {
			detailError = e instanceof Error ? e.message : 'Could not load that day.';
		} finally {
			detailLoading = false;
		}
	}

	// Per-source scaling: research runs ~500/day and news ~45, so one shared
	// scale would flatten news into a single tone and hide exactly the drops
	// this view exists to show.
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

	/** Largest feed in the detail, for scaling the breakdown bars. */
	const feedPeak = $derived(Math.max(...(detail?.feeds ?? []).map((f) => f.count), 1));
</script>

<div class="card">
	<div class="flex items-baseline justify-between gap-3 flex-wrap mb-3">
		<div>
			<h2 class="text-lg font-semibold text-trend-gray-800 dark:text-trend-gray-100">
				Source health
			</h2>
			<p class="text-sm text-trend-gray-600 dark:text-trend-gray-400">
				Items collected per source, compared against the same weekday — arXiv skips weekends
				and Monday runs a three-day catch-up. Select any day for its feed breakdown.
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
					Items collected per source per day. Select a day for its feed breakdown.
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
										{@const isSel =
											selected?.source === source && selected?.date === date}
										<button
											type="button"
											class="cell"
											class:missing={value === null}
											class:flagged
											class:selected={isSel}
											style="--i: {intensity(source, value)}"
											aria-pressed={isSel}
											onclick={() => select(source, date)}
											title="{source} · {date} · {value === null
												? 'no report published'
												: `${value} items`}{flagged ? ' · below baseline' : ''}"
										>
											<span class="sr-only">
												{source} on {date}: {value === null
													? 'no report published'
													: `${value} items`}
											</span>
										</button>
									{/each}
								</div>
							</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>

		<p class="legend">
			<span class="swatch" style="--i: 0.15"></span>
			<span class="swatch" style="--i: 0.5"></span>
			<span class="swatch" style="--i: 1"></span>
			<span>volume</span>
			<span class="swatch flagged" style="--i: 0.2"></span>
			<span>below baseline</span>
			<span class="swatch missing"></span>
			<span>no report</span>
		</p>

		{#if selected}
			<div class="detail">
				{#if detailLoading}
					<p class="text-sm text-trend-gray-500">Loading {selected.source} · {selected.date}…</p>
				{:else if detailError}
					<p class="text-sm text-trend-red">{detailError}</p>
				{:else if detail}
					<div class="flex items-baseline justify-between gap-3 flex-wrap">
						<h3 class="text-base font-semibold text-trend-gray-800 dark:text-trend-gray-100">
							{detail.display_name} · {detail.date}
							{#if detail.weekday}<span class="dow">{detail.weekday}</span>{/if}
						</h3>
						<button class="close" onclick={() => ((selected = null), (detail = null))}>
							Close
						</button>
					</div>

					{#if !detail.published}
						<p class="mt-2 text-sm text-trend-gray-600 dark:text-trend-gray-400">
							{detail.note}
						</p>
					{:else}
						<div class="run-stats mt-3">
							<div>
								<span class="rs-label">Collected</span>
								<span class="rs-value" class:bad={detail.anomalous}>{detail.count ?? '—'}</span>
							</div>
							<div>
								<span class="rs-label">Same-weekday norm</span>
								<span class="rs-value">{detail.baseline ?? '—'}</span>
							</div>
							<div>
								<span class="rs-label">Of normal</span>
								<span class="rs-value" class:bad={detail.anomalous}>
									{detail.ratio !== null ? `${Math.round(detail.ratio * 100)}%` : '—'}
								</span>
							</div>
							<div>
								<span class="rs-label">Reported</span>
								<span class="rs-value">{detail.status ?? '—'}</span>
							</div>
						</div>

						{#if detail.note}
							<p class="note" class:bad={detail.anomalous}>{detail.note}</p>
						{/if}
						{#if detail.error}
							<p class="note bad">Collector error: {detail.error}</p>
						{/if}

						<h4 class="sub">Feeds that contributed</h4>
						{#if detail.feeds.length === 0}
							<p class="text-sm text-trend-gray-600 dark:text-trend-gray-400">
								Nothing arrived from this source on this day.
							</p>
						{:else}
							<ul class="feeds">
								{#each detail.feeds as feed (feed.name)}
									<li>
										<span class="fname">{feed.name}</span>
										<span class="fbar" style="--w: {(feed.count / feedPeak) * 100}%"></span>
										<span class="fcount">{feed.count}</span>
									</li>
								{/each}
							</ul>
							{#if detail.anomalous}
								<p class="hint">
									Compare this list with a healthy {detail.weekday}. A feed that is missing
									here, rather than merely smaller, is the one that broke.
								</p>
							{/if}
						{/if}

						{#if detail.sample_titles.length}
							<h4 class="sub">Top items</h4>
							<ul class="titles">
								{#each detail.sample_titles as title (title)}
									<li>{title}</li>
								{/each}
							</ul>
						{/if}

						<div class="links">
							{#if detail.report_url}
								<a href={detail.report_url} target="_blank" rel="noopener noreferrer">
									Open the report
								</a>
							{/if}
							{#if detail.replay_url}
								<a href={detail.replay_url} target="_blank" rel="noopener noreferrer">
									Watch the replay
								</a>
							{/if}
						</div>
					{/if}
				{/if}
			</div>
		{/if}

		{#if recentAnomalies.length}
			<div class="mt-4 pt-4 border-t border-trend-gray-200 dark:border-trend-gray-700">
				<h3 class="text-sm font-semibold text-trend-gray-800 dark:text-trend-gray-100 mb-2">
					Flagged
				</h3>
				<ul class="space-y-1">
					{#each recentAnomalies as a (a.date + a.source)}
						<li class="text-sm text-trend-gray-700 dark:text-trend-gray-300">
							<button class="flaglink" onclick={() => select(a.source, a.date)}>
								<span class="font-mono text-xs text-trend-gray-500">{a.date}</span>
								{a.detail}
							</button>
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
	.cell,
	.swatch {
		flex: 1 1 auto;
		min-width: 3px;
		height: 16px;
		border-radius: 1px;
		padding: 0;
		border: 0;
		/* Volume is a neutral blue ramp, deliberately NOT the category colours:
		   category-research is the same green as `success` and category-reddit the
		   same red as `failed`, so a red Reddit lane would read as broken. */
		background: color-mix(in srgb, #3b82f6 calc(var(--i) * 100%), transparent);
	}
	.cell {
		cursor: pointer;
		transition: transform 120ms ease;
	}
	.cell:hover {
		transform: scaleY(1.35);
	}
	.cell:focus-visible {
		outline: 2px solid #e63946;
		outline-offset: 1px;
		position: relative;
		z-index: 2;
	}
	.cell.selected {
		box-shadow:
			0 0 0 2px #e63946,
			0 0 0 3px rgb(255 255 255 / 0.6);
		position: relative;
		z-index: 1;
	}
	.cell.missing,
	.swatch.missing {
		background: repeating-linear-gradient(
			45deg,
			rgb(0 0 0 / 0.06),
			rgb(0 0 0 / 0.06) 2px,
			transparent 2px,
			transparent 4px
		);
	}
	:global(.dark) .cell.missing,
	:global(.dark) .swatch.missing {
		background: repeating-linear-gradient(
			45deg,
			rgb(255 255 255 / 0.08),
			rgb(255 255 255 / 0.08) 2px,
			transparent 2px,
			transparent 4px
		);
	}
	.cell.flagged,
	.swatch.flagged {
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
	.legend .swatch {
		flex: 0 0 12px;
		width: 12px;
		height: 12px;
	}

	.detail {
		margin-top: 1rem;
		padding: 0.9rem 1rem;
		border-radius: 0.6rem;
		background: rgb(0 0 0 / 0.03);
		border: 1px solid rgb(0 0 0 / 0.09);
	}
	:global(.dark) .detail {
		background: rgb(255 255 255 / 0.04);
		border-color: rgb(255 255 255 / 0.1);
	}
	.dow {
		margin-left: 0.4rem;
		font-size: 0.65rem;
		font-weight: 600;
		text-transform: uppercase;
		letter-spacing: 0.06em;
		color: #737373;
	}
	.close {
		font-size: 0.7rem;
		color: #737373;
		text-decoration: underline;
	}
	.close:hover {
		color: #e63946;
	}

	.run-stats {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(7rem, 1fr));
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
	.run-stats .rs-label {
		font-size: 0.6rem;
		text-transform: uppercase;
		letter-spacing: 0.08em;
		color: #737373;
	}
	.run-stats .rs-value {
		font-size: 0.95rem;
		font-weight: 650;
		font-variant-numeric: tabular-nums;
	}
	.rs-value.bad {
		color: #ef4444;
	}

	.note {
		margin-top: 0.6rem;
		font-size: 0.78rem;
		color: #525252;
	}
	:global(.dark) .note {
		color: #a3a3a3;
	}
	.note.bad {
		color: #ef4444;
	}

	.sub {
		margin-top: 0.9rem;
		margin-bottom: 0.35rem;
		font-size: 0.72rem;
		font-weight: 700;
		text-transform: uppercase;
		letter-spacing: 0.07em;
		color: #737373;
	}

	.feeds {
		display: flex;
		flex-direction: column;
		gap: 3px;
	}
	.feeds li {
		display: grid;
		grid-template-columns: minmax(8rem, 14rem) 1fr auto;
		align-items: center;
		gap: 0.5rem;
		font-size: 0.78rem;
	}
	.fname {
		white-space: nowrap;
		overflow: hidden;
		text-overflow: ellipsis;
	}
	.fbar {
		height: 8px;
		width: var(--w);
		min-width: 2px;
		border-radius: 2px;
		background: #3b82f6;
	}
	.fcount {
		font-variant-numeric: tabular-nums;
		color: #737373;
		font-size: 0.72rem;
	}
	.hint {
		margin-top: 0.5rem;
		font-size: 0.72rem;
		color: #737373;
	}

	.titles {
		display: flex;
		flex-direction: column;
		gap: 2px;
		font-size: 0.78rem;
		color: #525252;
	}
	:global(.dark) .titles {
		color: #a3a3a3;
	}

	.links {
		margin-top: 0.9rem;
		display: flex;
		gap: 0.9rem;
		font-size: 0.78rem;
	}
	.links a {
		color: #e63946;
	}
	.links a:hover {
		color: #c1272d;
	}

	.flaglink {
		text-align: left;
		cursor: pointer;
	}
	.flaglink:hover {
		color: #e63946;
	}
	.flaglink:focus-visible {
		outline: 2px solid #e63946;
		outline-offset: 2px;
	}

	@media (prefers-reduced-motion: reduce) {
		.cell {
			transition: none;
		}
		.cell:hover {
			transform: none;
		}
	}
</style>
