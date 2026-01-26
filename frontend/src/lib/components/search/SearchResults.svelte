<script lang="ts">
	import { createEventDispatcher } from 'svelte';
	import type { SearchResult, Category } from '$lib/types';
	import { CATEGORY_CONFIG } from '$lib/types';
	import { formatDate } from '$lib/services/dateUtils';

	export let results: SearchResult[];
	export let selectedIndex: number = -1;

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
	<ul class="py-2">
		{#each results as result, i (result.ref)}
			{@const doc = result.doc}
			{#if doc}
				{@const mappedCategory = getMappedCategory(doc.category)}
				{@const config = CATEGORY_CONFIG[mappedCategory]}
				<li>
					<button
						on:click={() => handleClick(result)}
						class="w-full px-4 py-3 text-left hover:bg-trend-gray-50 dark:hover:bg-trend-gray-700 transition-colors
						       {i === selectedIndex ? 'bg-trend-gray-50 dark:bg-trend-gray-700' : ''}"
					>
						<div class="flex items-start gap-3">
							<!-- Category indicator -->
							<span
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
										{doc.summary}
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
								title="Relevance score"
							>
								{Math.round(doc.importance)}
							</span>
						</div>
					</button>
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
