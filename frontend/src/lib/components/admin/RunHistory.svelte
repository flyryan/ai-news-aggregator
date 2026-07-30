<script lang="ts">
	import { onMount } from 'svelte';
	import { getRunJobs, getRunLogs, getRuns } from '$lib/services/adminApi';
	import type { RunJob, WorkflowRun } from '$lib/types/admin';

	let runs = $state<WorkflowRun[]>([]);
	let error = $state<string | null>(null);
	let apiNote = $state<string | null>(null);
	let loading = $state(true);
	let hideNoops = $state(true);
	let expanded = $state<number | null>(null);

	// Lazy per-run detail, fetched once on first expand.
	let jobs = $state<Record<number, RunJob[] | 'loading' | 'error'>>({});
	let logs = $state<Record<number, string[] | 'loading' | 'error'>>({});
	let logsOpen = $state<Record<number, boolean>>({});

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
	const totalCost = $derived(
		runs.reduce((sum, r) => sum + (r.did_real_work ? (r.cost_usd ?? 0) : 0), 0)
	);

	async function toggle(run: WorkflowRun) {
		expanded = expanded === run.id ? null : run.id;
		if (expanded === run.id && !jobs[run.id]) {
			jobs[run.id] = 'loading';
			try {
				jobs[run.id] = (await getRunJobs(run.id)).jobs;
			} catch {
				jobs[run.id] = 'error';
			}
		}
	}

	async function toggleLogs(run: WorkflowRun) {
		logsOpen[run.id] = !logsOpen[run.id];
		if (logsOpen[run.id] && !logs[run.id]) {
			logs[run.id] = 'loading';
			try {
				logs[run.id] = (await getRunLogs(run.id)).lines;
			} catch (e) {
				logs[run.id] = 'error';
			}
		}
	}

	function statusColor(status: string, conclusion: string | null): string {
		if (status !== 'completed') return '#3b82f6';
		if (conclusion === 'success') return '#10b981';
		if (conclusion === 'cancelled' || conclusion === 'skipped') return '#a3a3a3';
		return '#ef4444';
	}

	function duration(seconds: number | null): string {
		if (seconds == null) return '—';
		if (seconds < 60) return `${seconds}s`;
		if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
		return `${Math.floor(seconds / 3600)}h ${Math.round((seconds % 3600) / 60)}m`;
	}

	const timeFmt = new Intl.DateTimeFormat('en-US', {
		timeZone: 'America/New_York',
		hour: 'numeric',
		minute: '2-digit'
	});
	const fullFmt = new Intl.DateTimeFormat('en-US', {
		timeZone: 'America/New_York',
		month: 'short',
		day: 'numeric',
		hour: 'numeric',
		minute: '2-digit',
		timeZoneName: 'short'
	});

	function etTime(iso: string): string {
		return timeFmt.format(new Date(iso));
	}
	function etFull(iso: string): string {
		return fullFmt.format(new Date(iso));
	}
	function cost(value: number | null): string {
		return value == null ? '—' : `$${value.toFixed(2)}`;
	}
</script>

<div class="card">
	<div class="flex items-baseline justify-between gap-3 flex-wrap mb-3">
		<div>
			<h2 class="text-lg font-semibold text-trend-gray-800 dark:text-trend-gray-100">
				Pipeline runs
			</h2>
			<p class="text-sm text-trend-gray-600 dark:text-trend-gray-400">
				From the Actions API. Published data only ever contains successful runs, so failures and
				cancellations appear nowhere else. Select a run for its jobs, cost, and logs.
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
			<div>
				<span class="rs-label">LLM spend</span><span class="rs-value">${totalCost.toFixed(0)}</span>
			</div>
		</div>

		<ul class="divide-y divide-trend-gray-200 dark:divide-trend-gray-700">
			{#each shown as run (run.id)}
				<li>
					<button
						class="row"
						onclick={() => toggle(run)}
						aria-expanded={expanded === run.id}
					>
						<span class="dot" style="--c: {statusColor(run.status, run.conclusion)}"
							aria-hidden="true"></span>
						<span class="font-mono text-xs text-trend-red">{run.report_date ?? run.created_at.slice(0, 10)}</span>
						<span class="text-xs text-trend-gray-500 tabular-nums">{etTime(run.created_at)}</span>
						<span class="text-trend-gray-700 dark:text-trend-gray-300">
							{run.status === 'completed' ? (run.conclusion ?? 'unknown') : run.status}
						</span>
						<span class="text-trend-gray-500 text-xs">{run.event}</span>
						{#if run.actor && run.event === 'workflow_dispatch'}
							<span class="text-trend-gray-500 text-xs">by {run.actor}</span>
						{/if}
						{#if run.run_attempt > 1}
							<span class="chip warn">attempt {run.run_attempt}</span>
						{/if}
						{#if run.published}
							<span class="chip ok">published</span>
						{/if}
						<span class="ml-auto flex items-center gap-3">
							{#if run.cost_usd != null}
								<span class="text-trend-gray-500 text-xs tabular-nums">{cost(run.cost_usd)}</span>
							{/if}
							<span class="text-trend-gray-500 text-xs tabular-nums">
								{duration(run.duration_seconds)}
							</span>
							<span class="chev" class:open={expanded === run.id} aria-hidden="true">▾</span>
						</span>
					</button>

					{#if expanded === run.id}
						<div class="detail">
							<div class="detail-grid">
								<div><span class="rs-label">Started</span><span>{etFull(run.created_at)}</span></div>
								<div><span class="rs-label">Duration</span><span>{duration(run.duration_seconds)}</span></div>
								<div><span class="rs-label">Run</span><span>#{run.run_number}</span></div>
								<div><span class="rs-label">Trigger</span><span>{run.event}{run.actor ? ` · ${run.actor}` : ''}</span></div>
								<div><span class="rs-label">LLM cost</span><span>{cost(run.cost_usd)}</span></div>
								<div>
									<span class="rs-label">Commit</span>
									<span>
										{#if run.head_sha}
											<a
												class="link font-mono"
												href={`https://github.com/flyryan/ai-news-aggregator/commit/${run.head_sha}`}
												target="_blank"
												rel="noopener noreferrer">{run.head_sha.slice(0, 7)}</a
											>
										{:else}—{/if}
									</span>
								</div>
							</div>

							<div class="mt-2 flex gap-3 flex-wrap text-xs">
								<a class="link" href={run.html_url} target="_blank" rel="noopener noreferrer">
									GitHub run ↗
								</a>
								{#if run.published && run.report_date}
									<a class="link" href={`/?date=${run.report_date}`} target="_blank" rel="noopener noreferrer">
										Report →
									</a>
									<a class="link" href={`/replay?date=${run.report_date}`} target="_blank" rel="noopener noreferrer">
										Replay →
									</a>
								{/if}
								<button class="link" onclick={() => toggleLogs(run)}>
									{logsOpen[run.id] ? 'Hide log tail' : 'Log tail'}
								</button>
							</div>

							{#if jobs[run.id] === 'loading'}
								<p class="mt-2 text-xs text-trend-gray-500">Loading jobs…</p>
							{:else if jobs[run.id] === 'error'}
								<p class="mt-2 text-xs text-trend-red">Could not load jobs from GitHub.</p>
							{:else if Array.isArray(jobs[run.id])}
								{#each jobs[run.id] as RunJob[] as job (job.id)}
									<div class="job">
										<div class="flex items-center gap-2 text-sm">
											<span class="dot" style="--c: {statusColor(job.status, job.conclusion)}"
												aria-hidden="true"></span>
											<a class="link" href={job.html_url} target="_blank" rel="noopener noreferrer">
												{job.name}
											</a>
											<span class="ml-auto text-xs text-trend-gray-500 tabular-nums">
												{duration(job.duration_seconds)}
											</span>
										</div>
										<ol class="steps">
											{#each job.steps as step, i (i)}
												<li class:skipped={step.conclusion === 'skipped'}>
													<span class="dot sm" style="--c: {statusColor(step.status, step.conclusion)}"
														aria-hidden="true"></span>
													<span class="step-name">{step.name}</span>
													<span class="ml-auto tabular-nums">
														{step.conclusion === 'skipped' ? 'skipped' : duration(step.duration_seconds)}
													</span>
												</li>
											{/each}
										</ol>
									</div>
								{/each}
							{/if}

							{#if logsOpen[run.id]}
								{#if logs[run.id] === 'loading'}
									<p class="mt-2 text-xs text-trend-gray-500">Fetching logs…</p>
								{:else if logs[run.id] === 'error'}
									<p class="mt-2 text-xs text-trend-red">
										Could not fetch logs — the GitHub token may lack access.
									</p>
								{:else if Array.isArray(logs[run.id])}
									<pre class="logbox">{(logs[run.id] as string[]).join('\n')}</pre>
								{/if}
							{/if}
						</div>
					{/if}
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
	.dot.sm {
		width: 6px;
		height: 6px;
		flex-basis: 6px;
	}
	.row {
		display: flex;
		align-items: center;
		gap: 0.75rem;
		width: 100%;
		padding: 0.5rem 0.25rem;
		font-size: 0.875rem;
		text-align: left;
		border-radius: 0.375rem;
		transition: background 140ms ease;
	}
	.row:hover {
		background: rgb(0 0 0 / 0.03);
	}
	:global(.dark) .row:hover {
		background: rgb(255 255 255 / 0.04);
	}
	.chev {
		font-size: 0.65rem;
		color: #737373;
		transition: transform 140ms ease;
	}
	.chev.open {
		transform: rotate(180deg);
	}
	.chip {
		font-size: 0.6rem;
		font-weight: 650;
		text-transform: uppercase;
		letter-spacing: 0.06em;
		padding: 1px 6px;
		border-radius: 999px;
	}
	.chip.ok {
		background: rgb(16 185 129 / 0.12);
		color: #10b981;
	}
	.chip.warn {
		background: rgb(245 158 11 / 0.14);
		color: #d97706;
	}
	.detail {
		margin: 0 0.25rem 0.6rem;
		padding: 0.7rem 0.8rem;
		border-radius: 0.5rem;
		background: rgb(0 0 0 / 0.035);
	}
	:global(.dark) .detail {
		background: rgb(255 255 255 / 0.045);
	}
	.detail-grid {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(9rem, 1fr));
		gap: 0.5rem;
		font-size: 0.8rem;
	}
	.detail-grid > div {
		display: flex;
		flex-direction: column;
	}
	.link {
		color: #e63946;
		font-size: inherit;
	}
	.link:hover {
		text-decoration: underline;
	}
	.job {
		margin-top: 0.7rem;
		padding-top: 0.55rem;
		border-top: 1px solid rgb(0 0 0 / 0.06);
	}
	:global(.dark) .job {
		border-top-color: rgb(255 255 255 / 0.08);
	}
	.steps {
		margin: 0.35rem 0 0 0.35rem;
		display: flex;
		flex-direction: column;
		gap: 0.15rem;
		font-size: 0.72rem;
		color: #737373;
	}
	.steps li {
		display: flex;
		align-items: center;
		gap: 0.5rem;
	}
	.steps li.skipped {
		opacity: 0.55;
	}
	.step-name {
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
	.logbox {
		margin-top: 0.6rem;
		max-height: 20rem;
		overflow: auto;
		padding: 0.6rem;
		border-radius: 0.4rem;
		background: rgb(0 0 0 / 0.5);
		color: #d4d4d4;
		font-size: 0.68rem;
		line-height: 1.5;
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
	.run-stats :global(.rs-label),
	.detail-grid :global(.rs-label) {
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
