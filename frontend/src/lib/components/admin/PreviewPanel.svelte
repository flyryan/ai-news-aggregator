<script lang="ts">
	import { onMount } from 'svelte';
	import {
		createPreview,
		discardPreview,
		getPreviews,
		promotePreview,
		runAction
	} from '$lib/services/adminApi';
	import type { PreviewJob } from '$lib/types/admin';

	let previews = $state<PreviewJob[]>([]);
	let error = $state<string | null>(null);
	let notice = $state<string | null>(null);
	let busy = $state<string | null>(null);
	let newDate = $state('');
	let confirmingPromote = $state<PreviewJob | null>(null);

	async function refresh() {
		try {
			previews = (await getPreviews()).previews;
		} catch (e) {
			error = e instanceof Error ? e.message : 'Could not load previews.';
		}
	}

	onMount(refresh);

	async function startHero() {
		error = null;
		notice = null;
		busy = 'create';
		try {
			await createPreview('hero', newDate);
			// Generation runs as a host unit and writes into the preview tree.
			await runAction('hero-regen', newDate);
			notice = `Generating a hero for ${newDate}. Refresh in a minute to view it.`;
			await refresh();
		} catch (e) {
			error = e instanceof Error ? e.message : 'Could not start hero generation.';
			await refresh();
		} finally {
			busy = null;
		}
	}

	async function promote(job: PreviewJob) {
		confirmingPromote = null;
		error = null;
		notice = null;
		busy = job.job_id;
		try {
			const result = await promotePreview(job.job_id);
			notice = `Published ${result.files.length} file(s) for ${job.date}. The site updates on the next deploy.`;
			await refresh();
		} catch (e) {
			error = e instanceof Error ? e.message : 'Could not publish this preview.';
		} finally {
			busy = null;
		}
	}

	async function discard(job: PreviewJob) {
		busy = job.job_id;
		try {
			await discardPreview(job.job_id);
			await refresh();
		} catch (e) {
			error = e instanceof Error ? e.message : 'Could not discard this preview.';
		} finally {
			busy = null;
		}
	}

	function size(bytes: number): string {
		if (bytes < 1024) return `${bytes} B`;
		if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
		return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
	}
</script>

<div class="card">
	<h2 class="text-lg font-semibold text-trend-gray-800 dark:text-trend-gray-100">Previews</h2>
	<p class="text-sm text-trend-gray-600 dark:text-trend-gray-400">
		Draft content, viewable exactly as readers would see it. Nothing here is public until you
		publish it.
	</p>

	{#if error}
		<p class="mt-3 text-sm text-trend-red" role="alert">{error}</p>
	{/if}
	{#if notice}
		<p class="mt-3 text-sm text-trend-gray-700 dark:text-trend-gray-300" role="status">{notice}</p>
	{/if}

	<div class="mt-3 flex items-end gap-2 flex-wrap">
		<label class="text-xs text-trend-gray-600 dark:text-trend-gray-400">
			Report date
			<input
				type="date"
				bind:value={newDate}
				class="ml-1 rounded border border-trend-gray-300 dark:border-trend-gray-600 bg-transparent px-1 py-0.5"
			/>
		</label>
		<button class="btn-primary text-sm" disabled={!newDate || busy === 'create'} onclick={startHero}>
			{busy === 'create' ? 'Starting…' : 'Regenerate hero'}
		</button>
	</div>

	{#if previews.length === 0}
		<p class="mt-4 text-sm text-trend-gray-500">
			No previews yet. Regenerate a hero to create one.
		</p>
	{:else}
		<ul class="mt-4 divide-y divide-trend-gray-200 dark:divide-trend-gray-700">
			{#each previews as job (job.job_id)}
				<li class="py-3">
					<div class="flex items-center gap-3 flex-wrap">
						<span class="kind">{job.kind}</span>
						<span class="text-sm font-medium text-trend-gray-800 dark:text-trend-gray-100">
							{job.date}
						</span>
						<span class="text-xs text-trend-gray-500">
							{job.created_at.slice(0, 16).replace('T', ' ')} · {size(job.size_bytes)}
						</span>
						<div class="ml-auto flex gap-2">
							<a
								href={job.url}
								target="_blank"
								rel="noopener noreferrer"
								class="btn-secondary text-xs"
							>
								View
							</a>
							<button
								class="btn-primary text-xs"
								disabled={busy === job.job_id}
								onclick={() => (confirmingPromote = job)}
							>
								Publish
							</button>
							<button
								class="btn-secondary text-xs"
								disabled={busy === job.job_id}
								onclick={() => discard(job)}
							>
								Discard
							</button>
						</div>
					</div>

					{#if confirmingPromote?.job_id === job.job_id}
						<div class="confirm">
							<p class="text-sm text-trend-gray-800 dark:text-trend-gray-100">
								Publish the {job.kind} for {job.date}?
							</p>
							<p class="text-xs text-trend-gray-600 dark:text-trend-gray-400 mt-1">
								This copies the draft into the site, commits it signed, and pushes. View it
								first if you have not.
							</p>
							<div class="mt-2 flex gap-2">
								<button class="btn-primary text-xs" onclick={() => promote(job)}>
									Publish {job.date}
								</button>
								<button
									class="btn-secondary text-xs"
									onclick={() => (confirmingPromote = null)}
								>
									Cancel
								</button>
							</div>
						</div>
					{/if}
				</li>
			{/each}
		</ul>
	{/if}
</div>

<style>
	.kind {
		display: inline-block;
		padding: 1px 7px;
		border-radius: 999px;
		font-size: 0.65rem;
		font-weight: 650;
		text-transform: uppercase;
		letter-spacing: 0.05em;
		background: rgb(0 0 0 / 0.07);
		color: #525252;
	}
	:global(.dark) .kind {
		background: rgb(255 255 255 / 0.09);
		color: #a3a3a3;
	}
	.confirm {
		margin-top: 0.6rem;
		padding: 0.7rem;
		border-radius: 0.5rem;
		background: rgb(230 57 70 / 0.06);
		border: 1px solid rgb(230 57 70 / 0.25);
	}
</style>
