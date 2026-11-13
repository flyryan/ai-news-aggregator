<script lang="ts">
	import { page } from '$app/stores';
	import { browser } from '$app/environment';
	import { goto, afterNavigate } from '$app/navigation';
	import { tick } from 'svelte';
	import {
		currentDate,
		availableDates,
		isLoading as storeLoading,
		resolveLatestDate
	} from '$lib/stores/dateStore';
	import { loadDaySummary, loadCategoryData, preloadAdjacentDates } from '$lib/services/dataLoader';
	import { parseDate, formatDate } from '$lib/services/dateUtils';
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
	import { formatCount } from '$lib/services/itemDisplay';

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

	// Show the reader which card they landed on: a ring that fades (app.css) and
	// focus on the card, so Tab and j/k carry on from it. Only item cards — other
	// hash targets are left alone.
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
	// <h2> on the page. Re-level them to <h3> so the outline has no gaps; each
	// keeps a class that pins the look of the level it came from (app.css).
	function sequenceHeadings(html: string): string {
		return html.replace(/<(\/?)h([2-4])(\s[^>]*)?>/gi, (_m, close, level) =>
			close ? '</h3>' : `<h3 class="was-h${level}">`
		);
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

	// A date the site has never published: say so, rather than "failed to load".
	$: unknownDate =
		!!effectiveDate && $availableDates.length > 0 && !$availableDates.includes(effectiveDate);
	$: longDate = effectiveDate ? formatDate(effectiveDate, 'EEEE, MMMM d, yyyy') : '';
</script>

<svelte:head>
	{#if categoryParam && config}
		<title>{config.title} - {effectiveDate || 'Latest'} | AATF AI News Aggregator</title>
	{:else}
		<title>AATF AI News Aggregator</title>
	{/if}
</svelte:head>

<svelte:window on:keydown={handleWindowKeydown} />

<!-- Previews for the enricher's internal links. Listens at the document level,
     so mounting it here scopes it to this route. -->
<LinkPreview fallbackDate={effectiveDate} />

<!-- A screen tall while the report loads, so the footer is not shown and then
     pushed away when the report arrives. -->
<div class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8" class:min-h-screen={loading}>
	{#if categoryParam && config}
		<!-- Category View Header -->
		<div
			class="rounded-xl p-6 mb-8 text-white"
			style="background: linear-gradient(135deg, var(--category-{categoryParam}-banner, {config.color}) 0%, color-mix(in srgb, var(--category-{categoryParam}-banner, {config.color}) 86.7%, transparent) 100%)"
		>
			<div class="flex items-center gap-3 mb-2">
				<a
					href={overviewHref}
					class="tap text-white hover:underline transition-colors"
				>
					&larr; Back
				</a>
			</div>
			<h1 class="text-2xl font-bold">{config.title}</h1>
			{#if categoryData}
				<p class="text-white mt-1">{formatCount(categoryData.total_items)} items for {effectiveDate}</p>
			{:else if loading}
				<!-- Holds the line's space so the count arriving moves nothing. -->
				<p class="mt-1 invisible" aria-hidden="true">&nbsp;</p>
			{/if}
		</div>
	{/if}

	<!-- Date Navigator with coverage info -->
	<div class="mb-8">
		<DateNavigator
			coverageDate={summary?.coverage_date}
			heading={!categoryParam}
			reserveCoverage={loading}
		/>
	</div>

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
				class="mb-6 p-4 rounded-lg border-l-4 {categoryData.notice.type === 'info'
					? 'bg-blue-50 border-blue-400 text-blue-800 dark:bg-blue-900/30 dark:border-blue-500 dark:text-blue-200'
					: 'bg-amber-50 border-amber-400 text-amber-800 dark:bg-amber-900/30 dark:border-amber-500 dark:text-amber-200'}"
			>
				<div class="flex items-start gap-3">
					<svg
						class="w-5 h-5 flex-shrink-0 mt-0.5"
						aria-hidden="true"
						fill="currentColor"
						viewBox="0 0 20 20"
					>
						<path
							fill-rule="evenodd"
							d="M18 10a8 8 0 11-16 0 8 8 0 0116 0zm-7-4a1 1 0 11-2 0 1 1 0 012 0zM9 9a1 1 0 000 2v3a1 1 0 001 1h1a1 1 0 100-2v-3a1 1 0 00-1-1H9z"
							clip-rule="evenodd"
						/>
					</svg>
					<div>
						<p class="font-semibold">{categoryData.notice.title}</p>
						<p class="mt-1 text-sm opacity-90">{categoryData.notice.message}</p>
					</div>
				</div>
			</div>
		{/if}

		{#if categoryData.items.length === 0}
			<EmptyState
				title="No {config?.title.toLowerCase()} found"
				message="No items in this category for {effectiveDate}."
			/>
		{:else}
			<!-- Category Summary -->
			{#if categoryData.category_summary}
				<section class="mb-8">
					<div class="card border-l-4" style="border-left-color: {config?.color}">
						<h2 class="font-semibold text-trend-gray-800 dark:text-trend-gray-100 mb-3">
							{config?.title} Summary
						</h2>
						<div class="prose-summary max-w-none">
							{@html categorySummaryHtml}
						</div>
					</div>
				</section>
			{/if}

			<!-- Themes -->
			{#if categoryData.themes && categoryData.themes.length > 0}
				<section class="mb-8">
					<h2 class="font-semibold text-trend-gray-800 dark:text-trend-gray-100 mb-4">
						Key Themes
					</h2>
					<div class="flex flex-wrap gap-2">
						{#each categoryData.themes as theme}
							<span
								class="px-3 py-1.5 rounded-full text-sm font-medium"
								style="background-color: {config?.color}20; color: var(--category-{categoryParam}-text, {config?.color})"
							>
								{theme.name} ({formatCount(theme.item_count)})
							</span>
						{/each}
					</div>
				</section>
			{/if}

			<!-- All Items -->
			<section>
				<h2 class="font-semibold text-trend-gray-800 dark:text-trend-gray-100 mb-6">
					All Items ({formatCount(categoryData.items.length)})
				</h2>
				<NewsList items={categoryData.items} category={categoryParam} date={effectiveDate || ''} />
			</section>
		{/if}
	{:else if summary}
		<!-- Overview View -->
		<!-- Hero Section -->
		<section class="mb-8">
			<HeroSection
				date={summary.date}
				coverageDate={summary.coverage_date}
				totalItems={summary.total_items_analyzed}
				heroImageUrl={summary.hero_image_url ? dataUrl(summary.hero_image_url) : null}
				collectionStatus={summary.collection_status?.overall || 'success'}
				modelDisplayName={summary.llm_model_display || summary.llm_model || null}
				imageModelDisplayName={summary.image_model_display || summary.image_model
					|| summary.hero_image_usage?.model || null}
				{replayUrl}
				{replayPending}
			/>
		</section>

		<!-- Executive Summary -->
		<section class="mb-12">
			<div class="card border-l-4 border-trend-red dark:border-trend-red">
				<div class="flex items-center justify-between mb-4">
					<h2 class="text-xl font-bold text-trend-gray-800 dark:text-trend-gray-100">
						Executive Summary
					</h2>
					<span class="text-sm text-trend-gray-500 dark:text-trend-gray-400">
						{formatCount(summary.total_items_analyzed)} items analyzed
					</span>
				</div>
				<div class="prose-summary max-w-none">
					{@html executiveSummaryHtml}
				</div>
			</div>
		</section>

		<!-- Top Topics -->
		{#if summary.top_topics && summary.top_topics.length > 0}
			<section class="mb-12">
				<h2 class="text-xl font-bold text-trend-gray-800 dark:text-trend-gray-100 mb-6">
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
				<section class="mb-12">
					<div class="flex items-center justify-between mb-6">
						<div class="flex items-center gap-3">
							<span
								aria-hidden="true"
								class="w-3 h-3 rounded-full"
								style="background-color: {CATEGORY_CONFIG[category].color}"
							></span>
							<h2 class="text-xl font-bold text-trend-gray-800 dark:text-trend-gray-100">
								{CATEGORY_CONFIG[category].title}
							</h2>
							<span class="text-sm text-trend-gray-500 dark:text-[#8d8d8d]">
								({formatCount(catSummary.count)} items)
							</span>
						</div>
						<a
							href={categoryHref(category)}
							aria-label="View all {CATEGORY_CONFIG[category].title}"
							class="tap text-sm font-medium text-trend-red-text hover:text-guardian-red transition-colors"
						>
							View All &rarr;
						</a>
					</div>

					<NewsList items={catSummary.top_items} {category} date={effectiveDate || ''} limit={5} totalCount={catSummary.count} />
				</section>
			{/if}
		{/each}
	{:else}
		<EmptyState
			title="No data available"
			message="Run the pipeline to generate news data."
		/>
	{/if}
</div>
