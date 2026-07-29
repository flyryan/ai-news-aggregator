<script lang="ts">
	import { onDestroy, onMount } from 'svelte';
	import {
		getActionLogs,
		getActionStatus,
		getActions,
		getAudit,
		runAction
	} from '$lib/services/adminApi';
	import type { ActionSpec, ActionStatus, AuditEntry } from '$lib/types/admin';

	let actions = $state<ActionSpec[]>([]);
	let audit = $state<AuditEntry[]>([]);
	let error = $state<string | null>(null);

	let running = $state<string | null>(null);
	let status = $state<ActionStatus | null>(null);
	let logs = $state<string[]>([]);
	let confirming = $state<ActionSpec | null>(null);
	let dateArg = $state('');

	let poller: ReturnType<typeof setInterval> | null = null;

	onMount(async () => {
		try {
			actions = (await getActions()).actions;
			audit = (await getAudit(20)).actions;
		} catch (e) {
			error = e instanceof Error ? e.message : 'Could not load actions.';
		}
	});

	onDestroy(() => {
		if (poller) clearInterval(poller);
	});

	async function start(spec: ActionSpec) {
		confirming = null;
		error = null;
		logs = [];
		status = null;
		try {
			const result = await runAction(spec.name, spec.needs_arg ? dateArg : undefined);
			running = result.unit;
			poll();
		} catch (e) {
			error = e instanceof Error ? e.message : `Could not start ${spec.name}.`;
		}
	}

	function poll() {
		if (poller) clearInterval(poller);
		poller = setInterval(async () => {
			if (!running) return;
			try {
				status = await getActionStatus(running);
				logs = (await getActionLogs(running, 120)).lines;
				// Stop on the unit's own terminal state, never on log silence:
				// a killed build and a slow build look identical in a log tail.
				if (status.finished && poller) {
					clearInterval(poller);
					poller = null;
					audit = (await getAudit(20)).actions;
				}
			} catch {
				// Transient poll failures are expected while a unit restarts.
			}
		}, 2000);
	}
</script>

<div class="card">
	<h2 class="text-lg font-semibold text-trend-gray-800 dark:text-trend-gray-100">Actions</h2>
	<p class="text-sm text-trend-gray-600 dark:text-trend-gray-400">
		Each action runs as a systemd unit on the host. Output is live.
	</p>

	{#if error}
		<p class="mt-3 text-sm text-trend-red" role="alert">{error}</p>
	{/if}

	<div class="mt-3 grid gap-2 sm:grid-cols-2">
		{#each actions as spec (spec.name)}
			<button
				class="action"
				data-danger={spec.danger}
				disabled={!!running && !status?.finished}
				onclick={() => (confirming = spec)}
			>
				<span class="name">{spec.name}</span>
				<span class="desc">{spec.description}</span>
			</button>
		{/each}
	</div>

	{#if confirming}
		<div class="confirm" role="dialog" aria-label="Confirm action">
			<p class="text-sm text-trend-gray-800 dark:text-trend-gray-100">
				Run <strong>{confirming.name}</strong>?
			</p>
			<p class="text-xs text-trend-gray-600 dark:text-trend-gray-400 mt-1">
				{confirming.description}.
				{#if confirming.danger === 'high'}
					This replaces the container serving news.aatf.ai.
				{/if}
			</p>
			{#if confirming.needs_arg}
				<label class="block mt-2 text-xs">
					Report date
					<input
						type="date"
						bind:value={dateArg}
						class="ml-1 rounded border border-trend-gray-300 dark:border-trend-gray-600 bg-transparent px-1 py-0.5"
					/>
				</label>
			{/if}
			<div class="mt-3 flex gap-2">
				<button
					class="btn-primary text-sm"
					disabled={confirming.needs_arg && !dateArg}
					onclick={() => confirming && start(confirming)}
				>
					Run {confirming.name}
				</button>
				<button class="btn-secondary text-sm" onclick={() => (confirming = null)}>Cancel</button>
			</div>
		</div>
	{/if}

	{#if running}
		<div class="mt-4 pt-4 border-t border-trend-gray-200 dark:border-trend-gray-700">
			<p class="text-sm">
				<span class="font-mono text-xs">{running}</span>
				{#if status}
					<span
						class="ml-2"
						data-state={status.finished ? (status.succeeded ? 'ok' : 'bad') : 'run'}
					>
						{status.finished
							? status.succeeded
								? 'succeeded'
								: `failed (${status.result})`
							: 'running…'}
					</span>
				{/if}
			</p>
			{#if logs.length}
				<pre class="logs">{logs.join('\n')}</pre>
			{/if}
		</div>
	{/if}

	{#if audit.length}
		<div class="mt-4 pt-4 border-t border-trend-gray-200 dark:border-trend-gray-700">
			<h3 class="text-sm font-semibold text-trend-gray-800 dark:text-trend-gray-100 mb-2">
				Recent activity
			</h3>
			<ul class="space-y-1 text-xs text-trend-gray-600 dark:text-trend-gray-400">
				{#each audit as entry (entry.id)}
					<li>
						<span class="font-mono">{entry.ts.slice(0, 16).replace('T', ' ')}</span>
						· {entry.principal} · <strong>{entry.action}</strong> · {entry.outcome}
					</li>
				{/each}
			</ul>
		</div>
	{/if}
</div>

<style>
	.action {
		display: flex;
		flex-direction: column;
		align-items: flex-start;
		gap: 2px;
		padding: 0.6rem 0.75rem;
		border-radius: 0.5rem;
		border: 1px solid rgb(0 0 0 / 0.12);
		text-align: left;
		transition:
			border-color 140ms ease,
			background 140ms ease;
	}
	:global(.dark) .action {
		border-color: rgb(255 255 255 / 0.13);
	}
	.action:hover:not(:disabled) {
		border-color: #e63946;
	}
	.action:disabled {
		opacity: 0.5;
		cursor: not-allowed;
	}
	.action[data-danger='high'] {
		border-left: 3px solid #e63946;
	}
	.action .name {
		font-weight: 650;
		font-size: 0.85rem;
	}
	.action .desc {
		font-size: 0.72rem;
		color: #737373;
	}
	.confirm {
		margin-top: 0.75rem;
		padding: 0.75rem;
		border-radius: 0.5rem;
		background: rgb(230 57 70 / 0.06);
		border: 1px solid rgb(230 57 70 / 0.25);
	}
	.logs {
		margin-top: 0.5rem;
		max-height: 18rem;
		overflow: auto;
		font-size: 0.7rem;
		line-height: 1.45;
		padding: 0.6rem;
		border-radius: 0.4rem;
		background: rgb(0 0 0 / 0.04);
		white-space: pre-wrap;
		word-break: break-word;
	}
	:global(.dark) .logs {
		background: rgb(255 255 255 / 0.05);
	}
	[data-state='ok'] {
		color: #10b981;
		font-weight: 600;
	}
	[data-state='bad'] {
		color: #ef4444;
		font-weight: 600;
	}
	[data-state='run'] {
		color: #3b82f6;
	}
	@media (prefers-reduced-motion: reduce) {
		.action {
			transition: none;
		}
	}
</style>
