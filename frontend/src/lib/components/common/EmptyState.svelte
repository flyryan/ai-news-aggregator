<script lang="ts">
	export let title: string = 'No data available';
	export let message: string = 'Check back later for updates.';
	/** Optional way forward, e.g. a link to the latest report. */
	export let actionHref: string | null = null;
	export let actionLabel: string = '';
</script>

<div class="text-center py-12">
	<svg
		class="w-16 h-16 mx-auto text-trend-gray-400 mb-4"
		xmlns="http://www.w3.org/2000/svg"
		fill="none"
		viewBox="0 0 24 24"
		stroke="currentColor"
		stroke-width="1.5"
		aria-hidden="true"
	>
		<path
			stroke-linecap="round"
			stroke-linejoin="round"
			d="M20 13V6a2 2 0 00-2-2H6a2 2 0 00-2 2v7m16 0v5a2 2 0 01-2 2H6a2 2 0 01-2-2v-5m16 0h-2.586a1 1 0 00-.707.293l-2.414 2.414a1 1 0 01-.707.293h-3.172a1 1 0 01-.707-.293l-2.414-2.414A1 1 0 006.586 13H4"
		/>
	</svg>
	<h2 class="text-lg font-medium text-trend-gray-700 dark:text-trend-gray-300 mb-2">
		{title}
	</h2>
	<p class="text-trend-gray-500 dark:text-trend-gray-400">
		{message}
	</p>
	{#if actionHref && actionLabel}
		<a href={actionHref} class="btn-primary inline-flex items-center min-h-[44px] mt-5">
			{actionLabel}
		</a>
	{/if}
</div>
