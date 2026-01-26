<script lang="ts">
	import type { NewsItem, Category } from '$lib/types';
	import NewsCard from './NewsCard.svelte';
	import EmptyState from '$lib/components/common/EmptyState.svelte';

	export let items: NewsItem[];
	export let category: Category;
	export let date: string;
	export let showCategory: boolean = false;
	export let limit: number | null = null;
	export let totalCount: number | null = null;

	$: displayItems = limit ? items.slice(0, limit) : items;
	$: hasMore = limit && items.length > limit;
</script>

{#if items.length === 0}
	<EmptyState
		title="No items found"
		message="There are no items in this category for the selected date."
	/>
{:else}
	<div class="space-y-4">
		{#each displayItems as item (item.id)}
			<NewsCard {item} {category} {date} {showCategory} />
		{/each}
	</div>

	{#if hasMore}
		<div class="mt-6 text-center">
			<slot name="view-all">
				<span class="text-sm text-trend-gray-500">
					Showing top {limit} of {totalCount ?? items.length} items
				</span>
			</slot>
		</div>
	{/if}
{/if}
