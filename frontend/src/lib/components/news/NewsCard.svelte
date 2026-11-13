<script lang="ts">
	import { tick } from 'svelte';
	import type { NewsItem, Category } from '$lib/types';
	import { formatRelativeTime } from '$lib/services/dateUtils';
	import { markdownToHtml } from '$lib/services/markdown';
	import { safeHtml } from '$lib/services/safeHtml';
	import { isSafeUrl } from '$lib/services/sanitize';
	import {
		authorDisplay,
		destinationName,
		importanceBand,
		stripArxivBoilerplate
	} from '$lib/services/itemDisplay';
	import { announce } from '$lib/stores/announcer';
	import CategoryBadge from './CategoryBadge.svelte';
	import ScoreLabel from './ScoreLabel.svelte';

	export let item: NewsItem;
	export let category: Category;
	export let date: string;
	export let showCategory: boolean = false;
	// The `item-{id}` anchor must exist exactly once per page: it is what
	// scrollToHashTarget() in +page.svelte resolves with getElementById. A preview
	// copy of a card already on the page would shadow the real one, so it opts out.
	// Items are programmatically focusable (tabindex -1, so not a Tab stop):
	// landing on an anchored one from search or a shared link, and the j/k
	// shortcuts, move focus to the item so Tab continues from inside it.
	export let anchor: boolean = true;
	// The link preview pins its own read/share actions outside the scroll area, so
	// the card's footer would be a second copy the reader has to scroll to reach.
	export let showActions: boolean = true;
	// Category pages list lower-scoring items as one-line rows that open in place
	// into this same card. The <article> and its id are the same element in both
	// states, so anchors, the landing ring and j/k need no second code path.
	export let compact: boolean = false;

	let open = false;
	let expanded = false;
	let copied = false;
	let rowButton: HTMLButtonElement | undefined;
	let collapseButton: HTMLButtonElement | undefined;

	function copyShareLink() {
		const url = `${window.location.origin}/?date=${date}&category=${category}#item-${item.id}`;
		navigator.clipboard.writeText(url);
		copied = true;
		announce('Link copied to clipboard');
		setTimeout(() => (copied = false), 2000);
	}

	// The row's own button: open or close, and keep focus on the control that
	// replaces the one just activated.
	async function toggleRow() {
		open = !open;
		await tick();
		(open ? collapseButton : rowButton)?.focus();
	}

	// The article itself only takes focus from code: a `#item-` landing (search,
	// shared link, summary link) or j/k. Either way the reader asked for this
	// item, so a row opens. Focus stays on the article.
	function revealOnFocus(event: FocusEvent) {
		if (compact && event.target === event.currentTarget) open = true;
	}

	$: isRow = compact && !open;
	$: safeUrl = isSafeUrl(item.url) ? item.url : undefined;
	$: destination = safeUrl ? destinationName(safeUrl) : null;
	$: fallbackReadLabel =
		category === 'research'
			? 'View Research'
			: category === 'reddit'
				? 'View Discussion'
				: 'Read More';
	$: readLabel = destination ? `Read at ${destination}` : fallbackReadLabel;
	$: author = authorDisplay(item.author);

	// Excerpts drop arXiv's feed prefix at render time; the data is untouched.
	$: content = stripArxivBoilerplate(item.content);
	$: hasContent = content.length > 0;
	$: needsTruncation = content.length > 300;
	$: freshness = item.freshness;

	// Use pre-rendered HTML if available, otherwise convert client-side
	$: summaryHtml = item.summary_html || markdownToHtml(item.summary || '');
	$: contentHtml = item.content_html
		? stripArxivBoilerplate(item.content_html)
		: markdownToHtml(content);

	$: importanceTierClass = `card-importance-${importanceBand(item.importance_score)}`;
	$: bodyId = `item-body-${item.id}`;
</script>

<article
	id={anchor ? `item-${item.id}` : undefined}
	tabindex="-1"
	class={compact ? 'item-row' : `card ${importanceTierClass}`}
	class:is-open={compact && open}
	style="scroll-margin-top: 5rem;"
	on:focus={revealOnFocus}
>
	{#if isRow}
		<button
			bind:this={rowButton}
			type="button"
			class="row-toggle"
			aria-expanded="false"
			on:click={toggleRow}
		>
			<ScoreLabel score={item.importance_score} compact />
			<span class="min-w-0">
				<span class="block truncate text-sm font-medium text-trend-gray-800 dark:text-trend-gray-100">
					{item.title}
				</span>
				<span class="meta block truncate sm:hidden">{item.source}</span>
			</span>
			<span class="meta hidden sm:block truncate text-right">{item.source}</span>
			<svg
				class="w-4 h-4 flex-none text-trend-gray-600 dark:text-trend-gray-400"
				fill="none"
				viewBox="0 0 24 24"
				stroke="currentColor"
				stroke-width="2"
				aria-hidden="true"
			>
				<path stroke-linecap="round" stroke-linejoin="round" d="M19 9l-7 7-7-7" />
			</svg>
		</button>
	{:else}
		<div id={bodyId}>
			<div class="flex items-start justify-between gap-3 mb-2">
				<div class="flex-1 min-w-0">
					{#if showCategory}
						<CategoryBadge {category} class="mb-2" />
					{/if}

					<h3 class="text-lg font-semibold text-trend-gray-900 dark:text-white leading-snug break-words">
						<a
							href={safeUrl}
							target="_blank"
							rel="noopener noreferrer"
							class="tap block hover:text-link transition-colors"
						>
							{item.title}
						</a>
					</h3>
				</div>

				{#if compact}
					<button
						bind:this={collapseButton}
						type="button"
						class="collapse-toggle"
						aria-expanded="true"
						aria-controls={bodyId}
						aria-label="Collapse: {item.title}"
						title="Collapse"
						on:click={toggleRow}
					>
						<svg
							class="w-4 h-4"
							fill="none"
							viewBox="0 0 24 24"
							stroke="currentColor"
							stroke-width="2"
							aria-hidden="true"
						>
							<path stroke-linecap="round" stroke-linejoin="round" d="M5 15l7-7 7 7" />
						</svg>
					</button>
				{/if}
			</div>

			<!-- Metadata: the labelled score first, then where and who and when. -->
			<div class="meta dot-list mb-3">
				<ScoreLabel score={item.importance_score} />
				<span>{item.source}</span>
				{#if freshness?.label}
					<span
						class="freshness text-xs leading-none px-1.5 py-1 rounded border border-trend-gray-300 dark:border-trend-gray-600 text-trend-gray-600 dark:text-trend-gray-300"
						title={freshness.reason || freshness.label}
					>
						{freshness.label}
					</span>
				{/if}
				{#if author}
					<!-- A long author list shortens to "First et al."; the full list is in
					     the title and is shown once the excerpt is opened. -->
					<span title={author.truncated ? author.full : undefined}>
						{expanded ? author.full : author.short}
					</span>
				{/if}
				{#if item.published}
					<span>{formatRelativeTime(item.published)}</span>
				{/if}
			</div>

			<!-- AI Analysis -->
			{#if item.summary}
				<div class="mb-3">
					<div class="flex items-center gap-1.5 text-xs font-semibold text-trend-gray-600 dark:text-trend-gray-400 mb-1">
						<svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
							<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z" />
						</svg>
						<span>AI Analysis</span>
					</div>
					<div class="prose-summary max-w-[75ch] leading-relaxed">
						{@html safeHtml(summaryHtml)}
					</div>
				</div>
			{/if}

			<!-- Content (expandable) -->
			{#if hasContent}
				<div class="mb-3">
					<div
						id="excerpt-{item.id}"
						class="prose-summary prose-quiet text-sm leading-relaxed max-w-[80ch]"
						class:line-clamp-3={!expanded && needsTruncation}
					>
						{@html safeHtml(contentHtml)}
					</div>

					{#if needsTruncation}
						<button
							type="button"
							on:click={() => (expanded = !expanded)}
							aria-expanded={expanded}
							aria-controls="excerpt-{item.id}"
							class="tap mt-1.5 text-sm font-medium text-link hover:text-link-hover"
						>
							{expanded ? 'Shorten excerpt' : 'Show full excerpt'}
						</button>
					{/if}
				</div>
			{/if}

			<!-- Themes: labels, not controls -->
			{#if item.themes && item.themes.length > 0}
				<ul class="flex flex-wrap gap-x-1.5 gap-y-1.5 mb-3" aria-label="Themes">
					{#each item.themes as theme}
						<li
							class="text-xs px-1.5 py-0.5 rounded border border-trend-gray-200 dark:border-trend-gray-600 text-trend-gray-600 dark:text-trend-gray-300"
						>
							{theme}
						</li>
					{/each}
				</ul>
			{/if}

			<!-- Actions -->
			{#if showActions}
				<div
					class="flex items-center justify-between gap-4 pt-3 border-t border-trend-gray-200 dark:border-trend-gray-700"
				>
					<a
						href={safeUrl}
						target="_blank"
						rel="noopener noreferrer"
						aria-label="{readLabel}: {item.title}"
						class="tap text-sm font-medium text-link hover:text-link-hover transition-colors"
					>
						{readLabel} <span aria-hidden="true">{destination ? '↗' : '→'}</span>
					</a>
					<button
						type="button"
						on:click={copyShareLink}
						aria-label="Share: {item.title}"
						class="tap text-sm font-medium text-link hover:text-link-hover transition-colors"
					>
						{copied ? 'Copied!' : 'Share'}
					</button>
				</div>
			{/if}
		</div>
	{/if}
</article>

<style>
	/* The freshness label qualifies the source before it: no dot between them. */
	.freshness::before {
		content: none !important;
	}
	.freshness {
		margin-left: -0.625rem;
	}

	/* A compact row: one button, at least 44px tall, that opens the card. */
	.row-toggle {
		display: grid;
		grid-template-columns: 2.75rem minmax(0, 1fr) auto;
		align-items: center;
		column-gap: 0.75rem;
		width: 100%;
		min-height: 44px;
		padding: 0.5rem 1rem;
		text-align: left;
	}
	.row-toggle:hover {
		background: rgb(0 0 0 / 0.03);
	}
	:global(.dark) .row-toggle:hover {
		background: rgb(255 255 255 / 0.04);
	}
	.row-toggle:focus-visible {
		outline-offset: -2px;
	}
	@media (min-width: 640px) {
		.row-toggle {
			grid-template-columns: 2.75rem minmax(0, 1fr) minmax(0, 14rem) auto;
		}
	}

	/* An opened row is the full card, set flush in the list. */
	.item-row.is-open {
		padding: 1rem;
	}
	@media (min-width: 640px) {
		.item-row.is-open {
			padding: 1.25rem 1.5rem;
		}
	}

	.collapse-toggle {
		flex: none;
		display: grid;
		place-items: center;
		width: 44px;
		height: 44px;
		margin: -0.625rem -0.625rem -0.625rem 0;
		border-radius: 0.5rem;
		color: #525252;
	}
	.collapse-toggle:hover {
		background: rgb(0 0 0 / 0.05);
	}
	:global(.dark) .collapse-toggle {
		color: #a3a3a3;
	}
	:global(.dark) .collapse-toggle:hover {
		background: rgb(255 255 255 / 0.08);
	}
</style>
