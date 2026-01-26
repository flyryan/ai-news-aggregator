<script lang="ts">
	import { createEventDispatcher } from 'svelte';

	export let day: Date;
	export let inMonth: boolean;
	export let today: boolean;
	export let selected: boolean;
	export let available: boolean;

	const dispatch = createEventDispatcher();

	$: dayNumber = day.getDate();

	function handleClick() {
		if (available) {
			dispatch('click');
		}
	}
</script>

<button
	on:click={handleClick}
	disabled={!available}
	class="
		relative aspect-square p-1 rounded-lg text-sm transition-all
		{inMonth ? 'text-trend-gray-700 dark:text-trend-gray-300' : 'text-trend-gray-400 dark:text-trend-gray-600'}
		{available
			? 'cursor-pointer hover:bg-trend-red/10'
			: 'cursor-default'}
		{selected
			? 'bg-trend-red text-white hover:bg-trend-red'
			: ''}
		{today && !selected
			? 'ring-2 ring-trend-red ring-inset'
			: ''}
	"
>
	<span class="relative z-10">{dayNumber}</span>

	<!-- Data indicator dot -->
	{#if available && !selected}
		<span
			class="absolute bottom-1 left-1/2 -translate-x-1/2 w-1.5 h-1.5 rounded-full bg-trend-red"
		></span>
	{/if}
</button>
