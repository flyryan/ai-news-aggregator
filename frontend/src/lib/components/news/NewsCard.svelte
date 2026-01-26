<script lang="ts">
	import type { NewsItem, Category } from '$lib/types';
	import { CATEGORY_CONFIG } from '$lib/types';
	import { formatRelativeTime } from '$lib/services/dateUtils';
	import { markdownToHtml } from '$lib/services/markdown';
	import { safeHtml } from '$lib/services/safeHtml';
	import CategoryBadge from './CategoryBadge.svelte';

	export let item: NewsItem;
	export let category: Category;
	export let date: string;
	export let showCategory: boolean = false;

	let expanded = false;
	let copied = false;

	function copyShareLink() {
		const url = `${window.location.origin}/?date=${date}&category=${category}#item-${item.id}`;
		navigator.clipboard.writeText(url);
		copied = true;
		setTimeout(() => (copied = false), 2000);
	}

	$: config = CATEGORY_CONFIG[category];
	$: hasContent = item.content && item.content.length > 0;
	$: truncatedContent = item.content?.slice(0, 300);
	$: needsTruncation = item.content?.length > 300;

	// Use pre-rendered HTML if available, otherwise convert client-side
	$: summaryHtml = item.summary_html || markdownToHtml(item.summary || '');
	$: contentHtml = item.content_html || markdownToHtml(item.content || '');

	// Determine importance tier class
	$: importanceTierClass =
		item.importance_score >= 80
			? 'card-importance-high'
			: item.importance_score >= 60
				? 'card-importance-medium'
				: item.importance_score >= 40
					? 'card-importance-standard'
					: 'card-importance-low';
</script>

<article id="item-{item.id}" class="card {importanceTierClass}" style="scroll-margin-top: 5rem;">
	<div class="flex items-start justify-between gap-4 mb-3">
		<div class="flex-1 min-w-0">
			{#if showCategory}
				<CategoryBadge {category} class="mb-2" />
			{/if}

			<h3 class="font-semibold text-trend-gray-800 dark:text-trend-gray-100 leading-snug">
				<a
					href={item.url}
					target="_blank"
					rel="noopener noreferrer"
					class="hover:text-trend-red transition-colors"
				>
					{item.title}
				</a>
			</h3>
		</div>

		<!-- Importance score -->
		<div
			class="flex-shrink-0 w-10 h-10 rounded-lg flex items-center justify-center text-sm font-bold
			       {item.importance_score >= 80
				? 'bg-trend-red/10 text-trend-red'
				: item.importance_score >= 60
					? 'bg-category-social/10 text-category-social'
					: 'bg-trend-gray-100 dark:bg-trend-gray-700 text-trend-gray-600 dark:text-trend-gray-400'}"
			title="Importance score: {item.importance_score}"
		>
			{Math.round(item.importance_score)}
		</div>
	</div>

	<!-- Metadata -->
	<div class="flex flex-wrap items-center gap-2 text-sm text-trend-gray-500 dark:text-trend-gray-400 mb-3">
		<span>{item.source}</span>
		{#if item.author}
			<span>&middot;</span>
			<span>{item.author}</span>
		{/if}
		{#if item.published}
			<span>&middot;</span>
			<span>{formatRelativeTime(item.published)}</span>
		{/if}
	</div>

	<!-- AI Analysis -->
	{#if item.summary}
		<div class="mb-3 pl-3 border-l-2 border-trend-red/30">
			<div class="flex items-center gap-1.5 text-xs font-bold text-trend-gray-500 dark:text-trend-gray-400 mb-1">
				<svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
					<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z" />
				</svg>
				<span>AI Analysis</span>
			</div>
			<div class="text-trend-gray-700 dark:text-trend-gray-300 leading-relaxed font-bold prose prose-sm dark:prose-invert max-w-none prose-p:my-1 prose-a:text-trend-red prose-a:no-underline hover:prose-a:underline">
				{@html safeHtml(summaryHtml)}
			</div>
		</div>
	{/if}

	<!-- Content (expandable) -->
	{#if hasContent}
		<div class="text-sm text-trend-gray-600 dark:text-trend-gray-400 mb-3">
			<div
				class="prose prose-sm dark:prose-invert max-w-none prose-p:my-1 prose-a:text-trend-red prose-a:no-underline hover:prose-a:underline"
				class:line-clamp-3={!expanded && needsTruncation}
			>
				{@html safeHtml(contentHtml)}
			</div>

			{#if needsTruncation}
				<button
					on:click={() => (expanded = !expanded)}
					class="text-trend-red hover:text-guardian-red mt-2 font-medium"
				>
					{expanded ? 'Show less' : 'Read more'}
				</button>
			{/if}
		</div>
	{/if}

	<!-- Themes -->
	{#if item.themes && item.themes.length > 0}
		<div class="flex flex-wrap gap-2 mb-3">
			{#each item.themes as theme}
				<span class="text-xs px-2 py-1 rounded-full bg-trend-gray-100 dark:bg-trend-gray-700 text-trend-gray-600 dark:text-trend-gray-400">
					{theme}
				</span>
			{/each}
		</div>
	{/if}

	<!-- Actions -->
	<div class="flex items-center justify-between pt-3 border-t border-trend-gray-100 dark:border-trend-gray-700">
		<a
			href={item.url}
			target="_blank"
			rel="noopener noreferrer"
			class="text-sm font-medium text-trend-red hover:text-guardian-red transition-colors"
		>
			{category === 'research' ? 'View Research' : category === 'reddit' ? 'View Discussion' : 'Read More'} &rarr;
		</a>
		<button
			on:click={copyShareLink}
			class="text-sm font-medium text-trend-red hover:text-guardian-red transition-colors"
		>
			{copied ? 'Copied!' : 'Share'}
		</button>
	</div>
</article>
