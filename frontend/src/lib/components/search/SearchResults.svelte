<script lang="ts">
	import { createEventDispatcher } from 'svelte';
	import type { SearchResult, Category } from '$lib/types';
	import { CATEGORY_CONFIG } from '$lib/types';
	import { formatDate } from '$lib/services/dateUtils';
	import { plainSnippet } from '$lib/services/itemDisplay';

	export let results: SearchResult[];
	export let selectedIndex: number = -1;
	// The search field is the combobox that owns this listbox: it keeps DOM
	// focus and points at the active option by id.
	export let listboxId: string;
	export let optionId: (index: number) => string;

	let listElement: HTMLUListElement;

	// Keep the arrow-selected option visible inside the scrolling panel.
	$: if (listElement && selectedIndex >= 0) {
		listElement
			.querySelector(`#${CSS.escape(optionId(selectedIndex))}`)
			?.scrollIntoView({ block: 'nearest' });
	}

	const dispatch = createEventDispatcher();

	// Map old category names for backwards compatibility
	const categoryMapping: Record<string, Category> = {
		papers: 'research'
	};

	function getMappedCategory(cat: string): Category {
		return (categoryMapping[cat] || cat) as Category;
	}

	function handleClick(result: SearchResult) {
		dispatch('select', result);
	}
</script>

<div
	class="absolute z-[60] w-full mt-2 bg-white dark:bg-trend-gray-800 rounded-xl shadow-lg border border-trend-gray-200 dark:border-trend-gray-700 max-h-96 overflow-y-auto"
>
	<ul bind:this={listElement} id={listboxId} role="listbox" aria-label="Search results" class="py-2">
		{#each results as result, i (result.ref)}
			{@const doc = result.doc}
			{#if doc}
				{@const mappedCategory = getMappedCategory(doc.category)}
				{@const config = CATEGORY_CONFIG[mappedCategory]}
				<!-- Options are driven from the search field (arrows + Enter), which
				     keeps focus; mousedown is cancelled so a click does not blur it. -->
				<!-- svelte-ignore a11y_click_events_have_key_events -->
				<li
					id={optionId(i)}
					role="option"
					aria-selected={i === selectedIndex}
					tabindex="-1"
					on:mousedown|preventDefault
					on:click={() => handleClick(result)}
					class="w-full px-4 py-3 text-left cursor-pointer hover:bg-trend-gray-50 dark:hover:bg-trend-gray-700 transition-colors
					       {i === selectedIndex ? 'bg-trend-gray-50 dark:bg-trend-gray-700' : ''}"
				>
					<div class="flex items-start gap-3">
						<!-- Category indicator -->
						<span
							aria-hidden="true"
							class="mt-1 w-2 h-2 rounded-full flex-shrink-0"
							style="background-color: {config.color}"
						></span>

						<div class="flex-1 min-w-0">
							<!-- Title -->
							<p class="font-medium text-trend-gray-800 dark:text-trend-gray-100 line-clamp-1">
								{doc.title}
							</p>

							<!-- Summary preview -->
							{#if doc.summary}
								<p class="text-sm text-trend-gray-500 dark:text-trend-gray-400 line-clamp-2 mt-1">
									{plainSnippet(doc.summary)}
								</p>
							{/if}

							<!-- Metadata -->
							<div class="flex items-center gap-2 mt-2 text-xs text-trend-gray-400">
								<span class="badge {config.badgeClass}">
									{config.title}
								</span>
								<span>{doc.source}</span>
								<span>&middot;</span>
								<span>{formatDate(doc.date, 'MMM d')}</span>
							</div>
						</div>

						<!-- Score indicator -->
						<span
							class="text-xs px-1.5 py-0.5 rounded bg-trend-gray-100 dark:bg-trend-gray-700 text-trend-gray-500"
							title="Importance score: {Math.round(doc.importance)}"
						>
							{Math.round(doc.importance)}
						</span>
					</div>
				</li>
			{/if}
		{/each}
	</ul>

	{#if results.length > 0}
		<div class="px-4 py-2 border-t border-trend-gray-100 dark:border-trend-gray-700">
			<p class="text-xs text-trend-gray-400 text-center">
				{results.length} result{results.length === 1 ? '' : 's'} found
			</p>
		</div>
	{/if}
</div>
