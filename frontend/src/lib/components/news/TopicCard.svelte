<script lang="ts">
	import type { TopTopic, Category } from '$lib/types';
	import { CATEGORY_CONFIG } from '$lib/types';
	import { safeHtml } from '$lib/services/safeHtml';
	import { formatCount } from '$lib/services/itemDisplay';
	import ScoreLabel from './ScoreLabel.svelte';

	export let topic: TopTopic;

	$: categories = Object.entries(topic.category_breakdown || {})
		.filter(([_, count]) => count > 0)
		.map(([cat, count]) => [cat as Category, count] as [Category, number])
		.filter(([cat]) => CATEGORY_CONFIG[cat] !== undefined);
</script>

<div class="card flex flex-col min-w-0">
	<h3 class="font-semibold text-lg leading-snug text-trend-gray-900 dark:text-white mb-2 break-words">
		{topic.name}
	</h3>

	<!-- Description (with HTML links if available) -->
	{#if topic.description_html}
		<div class="prose-summary leading-relaxed mb-4">
			{@html safeHtml(topic.description_html)}
		</div>
	{:else if topic.description}
		<p class="text-trend-gray-700 dark:text-trend-gray-300 leading-relaxed mb-4">
			{topic.description}
		</p>
	{/if}

	<!-- Where the topic's items came from, and how it scored -->
	{#if categories.length > 0 || topic.importance}
		<div
			class="meta mt-auto pt-3 border-t border-trend-gray-200 dark:border-trend-gray-700 flex flex-wrap items-center gap-x-4 gap-y-1"
		>
			{#if topic.importance}
				<ScoreLabel score={topic.importance} />
			{/if}
			{#if categories.length > 0}
				<ul class="flex flex-wrap items-center gap-x-3 gap-y-1" aria-label="Sources">
					{#each categories as [category, count]}
						{@const config = CATEGORY_CONFIG[category]}
						<li class="inline-flex items-center gap-1.5 whitespace-nowrap">
							<span
								class="w-2 h-2 rounded-full flex-none"
								style="background-color: {config.color}"
								aria-hidden="true"
							></span>
							{formatCount(count)} {config.shortTitle.toLowerCase()}
						</li>
					{/each}
				</ul>
			{/if}
		</div>
	{/if}
</div>
