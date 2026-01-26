<script lang="ts">
	export let title: string = 'Something went wrong';
	export let message: string = 'Please try again later.';
	export let onRetry: (() => void) | null = null;
</script>

<div class="text-center py-12">
	<svg
		class="w-16 h-16 mx-auto text-trend-red mb-4"
		xmlns="http://www.w3.org/2000/svg"
		fill="none"
		viewBox="0 0 24 24"
		stroke="currentColor"
		stroke-width="1.5"
	>
		<path
			stroke-linecap="round"
			stroke-linejoin="round"
			d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"
		/>
	</svg>
	<h3 class="text-lg font-medium text-trend-gray-700 dark:text-trend-gray-300 mb-2">
		{title}
	</h3>
	<p class="text-trend-gray-500 dark:text-trend-gray-400 mb-4">
		{message}
	</p>
	{#if onRetry}
		<button class="btn-primary" on:click={onRetry}>
			Try Again
		</button>
	{/if}
</div>
