<script lang="ts">
	/** Importance score, 0 to 100. */
	export let score: number;
	/** Rows show the bare number beside a visually hidden label. */
	export let compact: boolean = false;

	$: rounded = Math.round(score);
</script>

<!-- The number is always labelled. No colour code: the old red/amber/grey
     badges needed a legend and red also means Reddit on this site. -->
<span
	class="score inline-flex items-baseline gap-1 whitespace-nowrap text-sm tabular-nums text-trend-gray-600 dark:text-trend-gray-400"
	title="Importance score, 0 to 100"
>
	{#if compact}
		<span class="sr-only">Importance</span>
	{:else}
		Importance
	{/if}
	<span class="font-semibold text-trend-gray-900 dark:text-white">{rounded}</span>
</span>
