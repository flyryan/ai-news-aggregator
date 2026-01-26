<script lang="ts">
	import type { TopTopic, Category } from '$lib/types';
	import { CATEGORY_CONFIG } from '$lib/types';
	import { safeHtml } from '$lib/services/safeHtml';

	export let topic: TopTopic;

	$: categories = Object.entries(topic.category_breakdown || {})
		.filter(([_, count]) => count > 0)
		.map(([cat, count]) => [cat as Category, count] as [Category, number])
		.filter(([cat]) => CATEGORY_CONFIG[cat] !== undefined);
</script>

<div class="card">
	<h3 class="font-semibold text-lg text-trend-gray-800 dark:text-trend-gray-100 mb-2">
		{topic.name}
	</h3>

	<!-- Description (with HTML links if available) -->
	{#if topic.description_html}
		<div class="text-trend-gray-700 dark:text-trend-gray-300 leading-relaxed mb-4 prose prose-sm dark:prose-invert max-w-none">
			{@html safeHtml(topic.description_html)}
		</div>
	{:else if topic.description}
		<p class="text-trend-gray-700 dark:text-trend-gray-300 leading-relaxed mb-4">
			{topic.description}
		</p>
	{/if}

	<!-- Category breakdown -->
	{#if categories.length > 0}
		<div class="flex flex-wrap items-center gap-2">
			{#each categories as [category, count]}
				{@const config = CATEGORY_CONFIG[category]}
				<span
					class="inline-flex items-center gap-1.5 text-xs px-2 py-1 rounded-full"
					style="background-color: {config.color}20; color: {config.color}"
				>
					<span
						class="w-2 h-2 rounded-full"
						style="background-color: {config.color}"
					></span>
					{count} {config.title.toLowerCase()}
				</span>
			{/each}
		</div>
	{/if}

	<!-- Importance indicator -->
	{#if topic.importance}
		<div class="mt-3 pt-3 border-t border-trend-gray-100 dark:border-trend-gray-700">
			<div class="flex items-center gap-2">
				<div class="flex-1 h-1.5 bg-trend-gray-100 dark:bg-trend-gray-700 rounded-full overflow-hidden">
					<div
						class="h-full bg-trend-red rounded-full transition-all"
						style="width: {topic.importance}%"
					></div>
				</div>
				<span class="text-xs text-trend-gray-500 font-medium">
					{Math.round(topic.importance)}%
				</span>
			</div>
		</div>
	{/if}
</div>
