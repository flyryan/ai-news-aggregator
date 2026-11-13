<script lang="ts">
	import type { NewsItem, Category } from '$lib/types';
	import NewsCard from './NewsCard.svelte';
	import EmptyState from '$lib/components/common/EmptyState.svelte';
	import {
		COMPACT_BELOW,
		IMPORTANCE_BANDS,
		formatCount,
		importanceBand
	} from '$lib/services/itemDisplay';

	export let items: NewsItem[];
	export let category: Category;
	export let date: string;
	export let showCategory: boolean = false;
	export let limit: number | null = null;
	export let totalCount: number | null = null;
	// Category pages: split the ranked list into its importance bands, each under
	// a heading, with the bands below COMPACT_BELOW set as compact rows.
	export let banded: boolean = false;

	$: displayItems = limit ? items.slice(0, limit) : items;
	$: hasMore = limit && items.length > limit;

	// Items arrive ranked by score, so each band is one contiguous run and
	// grouping keeps the published order.
	$: bands = banded
		? IMPORTANCE_BANDS.map((def) => ({
				...def,
				compact: def.min < COMPACT_BELOW,
				items: displayItems.filter((item) => importanceBand(item.importance_score) === def.band)
			})).filter((group) => group.items.length > 0)
		: [];
</script>

{#if items.length === 0}
	<EmptyState
		title="No items found"
		message="There are no items in this category for the selected date."
	/>
{:else if banded}
	{#each bands as group (group.band)}
		<section class="band" aria-labelledby="band-{group.band}">
			<h3 id="band-{group.band}" class="band-heading">
				<span>{group.label}</span>
				<span class="meta font-normal">
					{formatCount(group.items.length)}
					{group.items.length === 1 ? 'item' : 'items'}
				</span>
			</h3>

			{#if group.compact}
				<div
					class="rounded-xl border border-trend-gray-200 dark:border-trend-gray-700 bg-white dark:bg-trend-gray-800 divide-y divide-trend-gray-200 dark:divide-trend-gray-700"
				>
					{#each group.items as item (item.id)}
						<NewsCard {item} {category} {date} {showCategory} compact />
					{/each}
				</div>
			{:else}
				<div class="space-y-4">
					{#each group.items as item (item.id)}
						<NewsCard {item} {category} {date} {showCategory} />
					{/each}
				</div>
			{/if}
		</section>
	{/each}
{:else}
	<div class="space-y-4">
		{#each displayItems as item (item.id)}
			<NewsCard {item} {category} {date} {showCategory} />
		{/each}
	</div>

	{#if hasMore}
		<div class="mt-6 text-center">
			<slot name="view-all">
				<span class="meta">
					Showing top {limit} of {formatCount(totalCount ?? items.length)} items
				</span>
			</slot>
		</div>
	{/if}
{/if}

<style>
	.band + .band {
		margin-top: 2.5rem;
	}

	/* A landmark in a long list: the band's name, its count, and a rule that
	   runs to the edge. */
	.band-heading {
		display: flex;
		align-items: baseline;
		gap: 0.75rem;
		margin-bottom: 0.75rem;
		font-size: 1rem;
		line-height: 1.5rem;
		font-weight: 600;
		color: #262626;
	}
	:global(.dark) .band-heading {
		color: #f5f5f5;
	}
	.band-heading::after {
		content: '';
		flex: 1;
		align-self: center;
		height: 1px;
		background: #d4d4d4;
	}
	:global(.dark) .band-heading::after {
		background: #404040;
	}
</style>
