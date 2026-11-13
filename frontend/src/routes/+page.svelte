<script lang="ts">
	import { page } from '$app/stores';
	import { browser } from '$app/environment';
	import { goto, afterNavigate } from '$app/navigation';
	import { tick } from 'svelte';
	import {
		currentDate,
		availableDates,
		navigation,
		isLoading as storeLoading,
		resolveLatestDate
	} from '$lib/stores/dateStore';
	import { loadDaySummary, loadCategoryData, preloadAdjacentDates } from '$lib/services/dataLoader';
	import { parseDate, formatDate } from '$lib/services/dateUtils';
	import { formatCount } from '$lib/services/itemDisplay';
	import type { DaySummary, CategoryData, Category } from '$lib/types';
	import { CATEGORY_CONFIG } from '$lib/types';
	import DateNavigator from '$lib/components/calendar/DateNavigator.svelte';
	import HeroSection from '$lib/components/layout/HeroSection.svelte';
	import TopicCard from '$lib/components/news/TopicCard.svelte';
	import NewsList from '$lib/components/news/NewsList.svelte';
	import LinkPreview from '$lib/components/news/LinkPreview.svelte';
	import { registerItems } from '$lib/services/itemIndex';
	import LoadingSpinner from '$lib/components/common/LoadingSpinner.svelte';
	import ErrorMessage from '$lib/components/common/ErrorMessage.svelte';
	import EmptyState from '$lib/components/common/EmptyState.svelte';
	import { safeHtml } from '$lib/services/safeHtml';
	import { hasReplayData } from '$lib/services/replayLoader';
	import { dataUrl } from '$lib/services/dataBase';
	import { shortcutAllowed } from '$lib/services/shortcuts';

	// Data state
	let summary: DaySummary | null = null;
	let categoryData: CategoryData | null = null;
	let dataLoading = false;
	let error: string | null = null;
	let activeLoadId = 0;
	let lastHandledRouteKey = '';

	const validCategories: Category[] = ['news', 'research', 'social', 'reddit'];

	// Read query params
	$: dateParam = $page.url.searchParams.get('date');
	$: rawCategoryParam = $page.url.searchParams.get('category');
	$: categoryParam =
		rawCategoryParam && validCategories.includes(rawCategoryParam as Category)
			? (rawCategoryParam as Category)
			: null;
	$: hasExplicitDate = dateParam !== null;
	$: routeKey = `${dateParam ?? 'latest'}|${rawCategoryParam ?? ''}`;

	// Validate params
	$: isValidDate = !hasExplicitDate || parseDate(dateParam ?? '') !== null;
	$: effectiveDate = hasExplicitDate ? (isValidDate ? dateParam : null) : ($currentDate || null);
	$: overviewHref = hasExplicitDate && effectiveDate ? `/?date=${effectiveDate}` : '/';
	$: categoryHref = (category: Category) =>
		hasExplicitDate && effectiveDate
			? `/?date=${effectiveDate}&category=${category}`
			: `/?category=${category}`;

	$: if (!$storeLoading && routeKey !== lastHandledRouteKey) {
		lastHandledRouteKey = routeKey;
		void handleRouteChange(dateParam, rawCategoryParam);
	}

	// Surface the replay CTA only for dates that actually have a replay index —
	// most historical dates predate the recorder, and a dead link is worse than none.
	let replayUrl: string | null = null;
	// True until the probe answers: the hero holds the button's space meanwhile.
	let replayPending = false;
	$: {
		const forDate = effectiveDate;
		replayUrl = null;
		replayPending = !!forDate;
		if (forDate) {
			void hasReplayData(forDate)
				.then((exists) => {
					// Ignore a probe that resolved after the user moved to another date.
					if (forDate !== effectiveDate) return;
					if (exists) replayUrl = `/replay?date=${forDate}`;
					replayPending = false;
				})
				.catch(() => {
					if (forDate === effectiveDate) replayPending = false;
				});
		}
	}

	// Show loading when store is initializing OR when data is loading
	$: loading = $storeLoading || dataLoading;

	// Get category config for category view
	$: config = categoryParam ? CATEGORY_CONFIG[categoryParam] : null;

	// Scroll to the hash target once it exists. The target card isn't in the DOM
	// until the route's data fetch resolves and the list renders, so a fixed delay
	// can't work — retry per frame (bounded) until the element appears, then jump
	// instantly: a smooth animation over a 300+ card page gets cancelled by
	// Firefox when late content reflows mid-scroll.
	let scrollAttemptToken = 0; // bumping cancels any in-flight retry loop
	let userScrolledDuringNav = false; // once the user scrolls, stop chasing the anchor until the next navigation

	function scrollToHashTarget(hash: string): void {
		if (!browser || !hash || userScrolledDuringNav) return;
		const id = decodeURIComponent(hash.slice(1));
		const token = ++scrollAttemptToken;
		let framesLeft = 120; // ~2s at 60fps

		const cancel = () => {
			if (token === scrollAttemptToken) scrollAttemptToken++;
			userScrolledDuringNav = true;
			cleanup();
		};
		const cleanup = () => {
			window.removeEventListener('wheel', cancel);
			window.removeEventListener('touchstart', cancel);
		};
		window.addEventListener('wheel', cancel, { passive: true });
		window.addEventListener('touchstart', cancel, { passive: true });

		const attempt = () => {
			if (token !== scrollAttemptToken) {
				cleanup();
				return;
			}
			const el = document.getElementById(id);
			if (el) {
				requestAnimationFrame(() => {
					cleanup();
					if (token !== scrollAttemptToken) return;
					el.scrollIntoView({ behavior: 'instant', block: 'start' });
					markLanded(el);
				});
				return;
			}
			if (--framesLeft > 0) requestAnimationFrame(attempt);
			else cleanup();
		};
		requestAnimationFrame(attempt);
	}

	// Show the reader which item they landed on: a ring that fades (app.css) and
	// focus on the item, so Tab and j/k carry on from it. Focus is also what opens
	// a compact row (NewsCard). Only items: other hash targets are left alone.
	let landedTimer: ReturnType<typeof setTimeout> | undefined;

	function markLanded(el: HTMLElement): void {
		if (!el.matches('article[id^="item-"]')) return;
		clearTimeout(landedTimer);
		document.querySelectorAll('.item-landed').forEach((n) => n.classList.remove('item-landed'));
		el.classList.add('item-landed');
		el.focus({ preventScroll: true });
		landedTimer = setTimeout(() => el.classList.remove('item-landed'), 2600);
	}

	afterNavigate((nav) => {
		if (nav.type === 'popstate') return; // let SvelteKit restore scroll on back/forward
		userScrolledDuringNav = false;
		const to = nav.to?.url;
		if (!to?.hash) {
			scrollAttemptToken++;
			return;
		}
		// Same page, new hash (a search result on the category already open): the
		// card is in the DOM, and the same instant jump serves as for a fresh load.
		scrollToHashTarget(to.hash);
	});

	// j / k: step through the item cards. From a focused card they move to its
	// neighbour; otherwise j takes the first card still in view and k the last
	// one above it.
	function handleWindowKeydown(event: KeyboardEvent) {
		const key = event.key;
		if ((key !== 'j' && key !== 'k') || !shortcutAllowed(event)) return;
		const cards = Array.from(document.querySelectorAll<HTMLElement>('article[id^="item-"]'));
		if (cards.length === 0) return;

		const current = (document.activeElement as HTMLElement | null)?.closest<HTMLElement>(
			'article[id^="item-"]'
		);
		let index = current ? cards.indexOf(current) : -1;
		if (index >= 0) {
			index += key === 'j' ? 1 : -1;
		} else if (key === 'j') {
			index = cards.findIndex((card) => card.getBoundingClientRect().top >= 0);
		} else {
			index = cards.findLastIndex((card) => card.getBoundingClientRect().top < 0);
		}
		const next = cards[index];
		if (!next) return; // already at the first or last card

		event.preventDefault();
		scrollAttemptToken++; // a pending hash scroll must not pull the page back
		next.focus({ preventScroll: true });
		next.scrollIntoView({ behavior: 'instant', block: 'start' });
	}

	async function handleRouteChange(rawDate: string | null, rawCategory: string | null) {
		if (rawCategory && !validCategories.includes(rawCategory as Category)) {
			const fallbackUrl = rawDate && parseDate(rawDate) ? `/?date=${rawDate}` : '/';
			goto(fallbackUrl, { replaceState: true });
			return;
		}

		if (rawDate && parseDate(rawDate) === null) {
			const fallbackUrl = rawCategory ? `/?category=${rawCategory}` : '/';
			goto(fallbackUrl, { replaceState: true });
			return;
		}

		const loadId = ++activeLoadId;
		error = null;

		const resolvedCategory = rawCategory as Category | null;
		const resolvedDate = rawDate ?? (await resolveLatestDate(true));
		if (loadId !== activeLoadId) {
			return;
		}

		if (!resolvedDate) {
			summary = null;
			categoryData = null;
			error = 'No reports available yet.';
			return;
		}

		currentDate.set(resolvedDate);
		await loadRouteData(resolvedDate, resolvedCategory, loadId);
	}

	// Feed the link-preview index from data we already hold. Every category's
	// top_items ships inside summary.json, which covers most of the internal links
	// the enricher writes — those previews then need no fetch of their own.
	function indexItems(date: string, day: DaySummary, cat: CategoryData | null) {
		for (const entry of Object.values(day.categories ?? {})) {
			registerItems(date, entry?.top_items);
		}
		if (cat) registerItems(date, cat.items);
	}

	async function loadRouteData(date: string, category: Category | null, loadId: number) {
		dataLoading = true;
		error = null;
		summary = null;
		categoryData = null;

		try {
			if (category) {
				const [summaryData, catData] = await Promise.all([
					loadDaySummary(date),
					loadCategoryData(date, category)
				]);

				if (loadId !== activeLoadId) {
					return;
				}

				summary = summaryData;
				categoryData = catData;
				indexItems(date, summaryData, catData);
			} else {
				const summaryData = await loadDaySummary(date);
				if (loadId !== activeLoadId) {
					return;
				}

				summary = summaryData;
				indexItems(date, summaryData, null);
				preloadAdjacentDates(date);
			}
		} catch (e) {
			if (loadId !== activeLoadId) {
				return;
			}

			error = e instanceof Error ? e.message : 'Failed to load data';
			summary = null;
			categoryData = null;
		} finally {
			if (loadId === activeLoadId) {
				dataLoading = false;
				if (!error) {
					await tick();
					scrollToHashTarget($page.url.hash);
				}
			}
		}
	}

	function retry() {
		lastHandledRouteKey = '';
		void handleRouteChange(dateParam, rawCategoryParam);
	}

	// Format executive summary for display
	function formatExecutiveSummary(text: string, html?: string): string {
		if (html) {
			return html;
		}
		let formatted = text.replace(/^##\s*Executive Summary[^\n]*\n+/i, '');
		formatted = formatted.split(/\n\n+/).map(p => `<p class="mb-4 last:mb-0">${p.trim()}</p>`).join('');
		return formatted;
	}

	// Summaries arrive with <h4> (sometimes <h2>) sub-headings and sit under an
	// <h2> on the page. Re-level them to <h3> so the outline has no gaps; the
	// styles for h3 and h4 are the same, so nothing changes to the eye.
	function sequenceHeadings(html: string): string {
		return html.replace(/<(\/?)h[2-4](\s[^>]*)?>/gi, '<$1h3>');
	}

	// Give each summary sub-heading an id so "On this page" can link to it, and
	// read the section titles for that list. Done on the rendered nodes: the
	// sanitizer strips id attributes from the HTML, and the DOM has the titles
	// as text ("Safety & Regulation", not "&amp;").
	let summarySections: string[] = [];
	function headingIds(node: HTMLElement, _html: string) {
		const apply = () => {
			const headings = Array.from(node.querySelectorAll('h3'));
			headings.forEach((h, i) => {
				h.id = `summary-section-${i}`;
			});
			summarySections = headings.map((h) => h.textContent?.trim() ?? '');
		};
		apply();
		return { update: () => void tick().then(apply) };
	}

	$: executiveSummaryHtml = summary
		? sequenceHeadings(
				safeHtml(formatExecutiveSummary(summary.executive_summary, summary.executive_summary_html))
			)
		: '';
	$: categorySummaryHtml = categoryData
		? sequenceHeadings(
				safeHtml(categoryData.category_summary_html || categoryData.category_summary)
			)
		: '';

	$: longDate = effectiveDate ? formatDate(effectiveDate, 'EEEE, MMMM d, yyyy') : '';
	// A date the site has never published: say so, rather than "failed to load".
	$: unknownDate =
		!!effectiveDate && $availableDates.length > 0 && !$availableDates.includes(effectiveDate);
	$: overviewSections = summary
		? validCategories.filter((c) => (summary?.categories[c]?.top_items.length ?? 0) > 0)
		: [];
</script>

<svelte:head>
	{#if categoryParam && config}
		<title>{config.title}, {effectiveDate ? formatDate(effectiveDate) : 'Latest'} | AI News Daily</title>
	{:else}
		<title>AI News Daily | AATF</title>
	{/if}
</svelte:head>

<svelte:window on:keydown={handleWindowKeydown} />

<!-- Previews for the enricher's internal links. Listens at the document level,
     so mounting it here scopes it to this route. -->
<LinkPreview fallbackDate={effectiveDate} />

<!-- A screen tall while the report loads, so the footer is not in view and
     then pushed away when the report arrives (the largest measured layout shift). -->
<div
	class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 pt-4 pb-10 sm:pt-6 sm:pb-12"
	class:min-h-screen={loading}
>
	<!-- Date Navigator with coverage info. On the overview its date is the <h1>. -->
	<div class="mb-5 sm:mb-6">
		<DateNavigator coverageDate={summary?.coverage_date} heading={!categoryParam} />
	</div>

	{#if categoryParam && config}
		<!-- Category View Header -->
		<header class="mb-8">
			<a
				href={overviewHref}
				class="tap inline-flex items-center gap-1 text-sm font-medium text-link hover:text-link-hover transition-colors"
			>
				<svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2" aria-hidden="true">
					<path stroke-linecap="round" stroke-linejoin="round" d="M15 19l-7-7 7-7" />
				</svg>
				Back to the overview
			</a>
			<div class="flex items-center gap-3 mt-3">
				<span
					class="w-3 h-3 rounded-full flex-none"
					style="background-color: {config.color}"
					aria-hidden="true"
				></span>
				<h1 class="text-2xl font-bold leading-tight text-trend-gray-900 dark:text-white">
					{config.title}
				</h1>
			</div>
			<!-- Always rendered, so the count arriving with the data moves nothing. -->
			<p class="meta mt-1 min-h-[1.25rem]">
				{#if categoryData}
					{formatCount(categoryData.total_items)}
					{categoryData.total_items === 1 ? 'item' : 'items'} in the report for {longDate}
				{/if}
			</p>
		</header>
	{/if}

	{#if loading}
		<div class="py-20">
			<LoadingSpinner size="lg" />
		</div>
	{:else if error && unknownDate}
		<EmptyState
			title="No report for this date"
			message="Nothing was published for {longDate}."
			actionHref="/"
			actionLabel="Go to the latest report"
		/>
	{:else if error}
		<ErrorMessage title="Failed to load data" message={error} onRetry={retry} />
	{:else if categoryParam && categoryData}
		<!-- Category View -->
		<!-- Notice Banner (e.g., weekend arXiv notice) -->
		{#if categoryData.notice}
			<div
				class="mb-6 p-4 rounded-lg border {categoryData.notice.type === 'info'
					? 'bg-blue-50 border-blue-200 text-blue-900 dark:bg-blue-950 dark:border-blue-800 dark:text-blue-100'
					: 'bg-amber-50 border-amber-300 text-amber-900 dark:bg-amber-950 dark:border-amber-800 dark:text-amber-100'}"
				role="note"
			>
				<div class="flex items-start gap-3">
					<svg
						class="w-5 h-5 flex-shrink-0 mt-0.5"
						fill="currentColor"
						viewBox="0 0 20 20"
						aria-hidden="true"
					>
						{#if categoryData.notice.type === 'info'}
							<path
								fill-rule="evenodd"
								d="M18 10a8 8 0 11-16 0 8 8 0 0116 0zm-7-4a1 1 0 11-2 0 1 1 0 012 0zM9 9a1 1 0 000 2v3a1 1 0 001 1h1a1 1 0 100-2v-3a1 1 0 00-1-1H9z"
								clip-rule="evenodd"
							/>
						{:else}
							<path
								fill-rule="evenodd"
								d="M8.257 3.099c.765-1.36 2.722-1.36 3.486 0l5.58 9.92c.75 1.334-.213 2.98-1.742 2.98H4.42c-1.53 0-2.493-1.646-1.743-2.98l5.58-9.92zM11 13a1 1 0 11-2 0 1 1 0 012 0zm-1-8a1 1 0 00-1 1v3a1 1 0 002 0V6a1 1 0 00-1-1z"
								clip-rule="evenodd"
							/>
						{/if}
					</svg>
					<div>
						<p class="font-semibold">{categoryData.notice.title}</p>
						<p class="mt-1 text-sm">{categoryData.notice.message}</p>
					</div>
				</div>
			</div>
		{/if}

		{#if categoryData.items.length === 0}
			<EmptyState
				title="No {config?.title.toLowerCase()} found"
				message="No items in this category for {longDate}."
			/>
		{:else}
			<!-- Category Summary -->
			{#if categoryData.category_summary}
				<section class="mb-10">
					<div class="card">
						<h2 class="text-xl font-bold text-trend-gray-900 dark:text-white mb-3">
							{config?.title} Summary
						</h2>
						<div class="prose-summary max-w-[66ch] leading-7">
							{@html categorySummaryHtml}
						</div>
					</div>
				</section>
			{/if}

			<!-- Themes: a plain list. They are labels, not filters. -->
			{#if categoryData.themes && categoryData.themes.length > 0}
				<section class="mb-10">
					<h2 class="text-xl font-bold text-trend-gray-900 dark:text-white mb-3">
						Key Themes
					</h2>
					<ul class="flex flex-wrap gap-x-6 gap-y-2 text-trend-gray-800 dark:text-trend-gray-200">
						{#each categoryData.themes as theme}
							<li>
								{theme.name}
								<span class="meta tabular-nums">
									{formatCount(theme.item_count)}<span class="sr-only">
										{theme.item_count === 1 ? 'item' : 'items'}</span>
								</span>
							</li>
						{/each}
					</ul>
				</section>
			{/if}

			<!-- All Items -->
			<section>
				<h2 class="text-xl font-bold text-trend-gray-900 dark:text-white mb-5">
					All Items
					<span class="meta font-normal ml-1">{formatCount(categoryData.items.length)}</span>
				</h2>
				<NewsList
					items={categoryData.items}
					category={categoryParam}
					date={effectiveDate || ''}
					banded
				/>
			</section>
		{/if}
	{:else if summary}
		<!-- Overview View -->
		<!-- Hero Section -->
		<div class="mb-8">
			<HeroSection
				date={summary.date}
				totalItems={summary.total_items_analyzed}
				heroImageUrl={summary.hero_image_url ? dataUrl(summary.hero_image_url) : null}
				collectionStatus={summary.collection_status?.overall || 'success'}
				modelDisplayName={summary.llm_model_display || summary.llm_model || null}
				imageModelDisplayName={summary.image_model_display || summary.image_model
					|| summary.hero_image_usage?.model || null}
				{replayUrl}
				{replayPending}
			/>
		</div>

		<!-- Executive Summary: a reading column, with the page's contents beside
		     it on desktop. -->
		<section id="executive-summary" class="mb-12 scroll-mt-20">
			<div class="card lg:grid lg:grid-cols-[minmax(0,1fr)_15rem] lg:gap-10">
				<div class="min-w-0">
					<h2 class="text-xl font-bold text-trend-gray-900 dark:text-white mb-4">
						Executive Summary
					</h2>
					<div
						class="prose-summary max-w-[58ch] leading-7 lg:text-lg lg:leading-8"
						use:headingIds={executiveSummaryHtml}
					>
						{@html executiveSummaryHtml}
					</div>
				</div>

				<nav
					aria-label="On this page"
					class="hidden lg:block border-l border-trend-gray-200 dark:border-trend-gray-700 pl-8"
				>
					<div class="sticky top-20">
						<p class="text-sm font-semibold text-trend-gray-900 dark:text-white mb-2">
							On this page
						</p>
						<ul class="toc text-sm">
							<li>
								<a href="#executive-summary">Executive Summary</a>
								{#if summarySections.length > 0}
									<ul>
										{#each summarySections as title, i}
											<li><a href="#summary-section-{i}">{title}</a></li>
										{/each}
									</ul>
								{/if}
							</li>
							{#if summary.top_topics && summary.top_topics.length > 0}
								<li><a href="#top-topics">Top Topics Today</a></li>
							{/if}
							{#each overviewSections as category}
								<li><a href="#section-{category}">{CATEGORY_CONFIG[category].title}</a></li>
							{/each}
						</ul>
					</div>
				</nav>
			</div>
		</section>

		<!-- Top Topics -->
		{#if summary.top_topics && summary.top_topics.length > 0}
			<section id="top-topics" class="mb-12 scroll-mt-20">
				<h2 class="text-xl font-bold text-trend-gray-900 dark:text-white mb-5">
					Top Topics Today
				</h2>
				<div class="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
					{#each summary.top_topics as topic}
						<TopicCard {topic} />
					{/each}
				</div>
			</section>
		{/if}

		<!-- Category Sections -->
		{#each validCategories as category}
			{@const catSummary = summary.categories[category]}
			{#if catSummary && catSummary.top_items.length > 0}
				<section id="section-{category}" class="mb-12 scroll-mt-20">
					<div class="flex items-center justify-between gap-4 mb-5">
						<div class="flex flex-wrap items-baseline gap-x-3 gap-y-1 min-w-0">
							<h2 class="flex items-center gap-2.5 text-xl font-bold text-trend-gray-900 dark:text-white">
								<span
									class="w-3 h-3 rounded-full flex-none"
									style="background-color: {CATEGORY_CONFIG[category].color}"
									aria-hidden="true"
								></span>
								{CATEGORY_CONFIG[category].title}
							</h2>
							<span class="meta">
								{formatCount(catSummary.count)} {catSummary.count === 1 ? 'item' : 'items'}
							</span>
						</div>
						<a
							href={categoryHref(category)}
							aria-label="View all {CATEGORY_CONFIG[category].title}"
							class="tap flex-none text-sm font-medium text-link hover:text-link-hover transition-colors"
						>
							View All <span aria-hidden="true">&rarr;</span>
						</a>
					</div>

					<NewsList items={catSummary.top_items} {category} date={effectiveDate || ''} limit={5} totalCount={catSummary.count} />
				</section>
			{/if}
		{/each}

		<!-- Page end: where to go from here. Real destinations only. -->
		<nav
			aria-label="Continue"
			class="mt-16 pt-6 border-t border-trend-gray-300 dark:border-trend-gray-700"
		>
			<p class="meta mb-3">End of the report for {longDate}.</p>
			<ul class="page-end">
				{#if $navigation.previousDate}
					<li>
						<a href="/?date={$navigation.previousDate}">
							<span class="page-end-title">Previous report</span>
							<span class="meta">{formatDate($navigation.previousDate, 'EEEE, MMMM d, yyyy')}</span>
						</a>
					</li>
				{/if}
				{#if replayUrl}
					<li>
						<a href={replayUrl}>
							<span class="page-end-title">Watch the replay</span>
							<span class="meta">The pipeline run that produced this report</span>
						</a>
					</li>
				{/if}
				<li>
					<a href="/feeds">
						<span class="page-end-title">Subscribe by feed</span>
						<span class="meta">Atom feeds of the daily report</span>
					</a>
				</li>
				<li>
					<a href="/archive">
						<span class="page-end-title">Browse the archive</span>
						<span class="meta">
							{#if $availableDates.length > 0}
								{formatCount($availableDates.length)} daily reports
							{:else}
								Every daily report
							{/if}
						</span>
					</a>
				</li>
			</ul>
		</nav>
	{:else}
		<EmptyState
			title="No report for this date"
			message={longDate ? `Nothing was published for ${longDate}.` : 'No report is available yet.'}
			actionHref="/"
			actionLabel="Go to the latest report"
		/>
	{/if}
</div>

<style>
	/* "On this page": quiet links, with the summary's own sections nested. */
	.toc a {
		display: block;
		padding: 0.3125rem 0;
		color: #404040;
	}
	.toc ul {
		margin: 0 0 0.25rem 0.75rem;
	}
	.toc ul a {
		color: #525252;
	}
	.toc a:hover {
		color: var(--color-link);
		text-decoration: underline;
		text-underline-offset: 0.18em;
	}
	:global(.dark) .toc a {
		color: #d4d4d4;
	}
	:global(.dark) .toc ul a {
		color: #a3a3a3;
	}
	:global(.dark) .toc a:hover {
		color: var(--color-link);
	}

	/* Summary sub-headings and sections are link targets under a sticky nav. */
	:global(.prose-summary h3[id]) {
		scroll-margin-top: 5rem;
	}

	.page-end {
		display: grid;
		gap: 0.25rem 2rem;
		grid-template-columns: repeat(auto-fit, minmax(14rem, 1fr));
	}
	.page-end a {
		display: flex;
		flex-direction: column;
		justify-content: center;
		min-height: 44px;
		padding: 0.375rem 0;
	}
	.page-end-title {
		font-weight: 600;
		color: var(--color-link);
	}
	.page-end a:hover .page-end-title {
		color: var(--color-link-hover);
		text-decoration: underline;
		text-underline-offset: 0.18em;
	}
</style>
