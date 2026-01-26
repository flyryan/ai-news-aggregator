<script lang="ts">
	import { page } from '$app/stores';
	import { browser } from '$app/environment';
	import { currentDate } from '$lib/stores/dateStore';
	import { CATEGORY_CONFIG, type Category } from '$lib/types';

	const categories: Category[] = ['news', 'research', 'social', 'reddit'];

	// Guard searchParams access for prerendering compatibility
	$: pathname = $page.url.pathname;
	$: dateParam = browser ? $page.url.searchParams.get('date') : null;
	$: categoryParam = browser ? $page.url.searchParams.get('category') : null;
	$: date = dateParam || $currentDate;

	// Explicit active state computations
	$: homeActive = pathname === '/' && !!dateParam && !categoryParam;
	$: archiveActive = pathname === '/archive';
	$: aboutActive = pathname === '/about';

	function isCategoryActive(cat: string): boolean {
		return categoryParam === cat;
	}
</script>

<nav class="bg-white dark:bg-trend-gray-800 border-b border-gray-200 dark:border-trend-gray-700 sticky top-0 z-50">
	<div class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
		<div class="flex items-center h-14">
			<!-- Main navigation -->
			<ul class="flex w-full items-center justify-evenly sm:justify-start sm:w-auto sm:gap-1">
				<!-- Home: icon on mobile (no bg), text+bg on desktop -->
				<li>
					<a
						href={date ? `/?date=${date}` : '/'}
						class="nav-link whitespace-nowrap hover:bg-transparent sm:hover:bg-trend-gray-200 {homeActive ? 'text-trend-red sm:bg-trend-red/10' : ''}"
						aria-label="Home"
					>
						<svg
							class="w-5 h-5 sm:hidden"
							fill="none"
							viewBox="0 0 24 24"
							stroke="currentColor"
							stroke-width="2"
						>
							<path stroke-linecap="round" stroke-linejoin="round" d="M3 12l2-2m0 0l7-7 7 7M5 10v10a1 1 0 001 1h3m10-11l2 2m-2-2v10a1 1 0 01-1 1h-3m-6 0a1 1 0 001-1v-4a1 1 0 011-1h2a1 1 0 011 1v4a1 1 0 001 1m-6 0h6" />
						</svg>
						<span class="hidden sm:inline">Home</span>
					</a>
				</li>

				<!-- Categories: text on all sizes, standard active styling -->
				{#each categories as category}
					<li>
						<a
							href={date ? `/?date=${date}&category=${category}` : '#'}
							class="nav-link whitespace-nowrap {isCategoryActive(category) ? 'nav-link-active' : ''} {!date ? 'opacity-50 pointer-events-none' : ''}"
							aria-disabled={!date}
						>
							<span
								class="w-2 h-2 rounded-full inline-block mr-1"
								style="background-color: {CATEGORY_CONFIG[category].color}"
							></span>
							<span class="sm:hidden">{CATEGORY_CONFIG[category].shortTitle}</span>
							<span class="hidden sm:inline">{CATEGORY_CONFIG[category].title}</span>
						</a>
					</li>
				{/each}

				<!-- Archive: icon on mobile (no bg), text+bg on desktop -->
				<li>
					<a
						href="/archive"
						class="nav-link whitespace-nowrap hover:bg-transparent sm:hover:bg-trend-gray-200 {archiveActive ? 'text-trend-red sm:bg-trend-red/10' : ''}"
						aria-label="Archive"
					>
						<svg
							class="w-5 h-5 sm:hidden"
							fill="none"
							viewBox="0 0 24 24"
							stroke="currentColor"
							stroke-width="2"
						>
							<path stroke-linecap="round" stroke-linejoin="round" d="M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z" />
						</svg>
						<span class="hidden sm:inline">Archive</span>
					</a>
				</li>

				<!-- About: desktop only (mobile users access via footer) -->
				<li class="hidden sm:block">
					<a
						href="/about"
						class="nav-link whitespace-nowrap {aboutActive ? 'text-trend-red bg-trend-red/10' : ''}"
						aria-label="About"
					>
						About
					</a>
				</li>
			</ul>
		</div>
	</div>
</nav>
