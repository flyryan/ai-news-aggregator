<script lang="ts">
	import { currentDate, currentModel } from '$lib/stores/dateStore';
	import { formatDate, isNewModelBadge } from '$lib/services/dateUtils';
	import ThemeToggle from './ThemeToggle.svelte';
	import SearchBar from '$lib/components/search/SearchBar.svelte';
	import { tick } from 'svelte';
	import { shortcutAllowed } from '$lib/services/shortcuts';

	$: dateDisplay = $currentDate ? formatDate($currentDate) : '';
	$: modelName = $currentModel?.display_name ?? '';
	$: showNewBadge = isNewModelBadge($currentModel?.since);

	let showSearch = false;
	let searchBar: SearchBar | undefined;
	let searchToggle: HTMLButtonElement;

	function closeSearch(restoreFocus = false) {
		if (!showSearch) return;
		// Only pull focus back to the button when it would otherwise be lost with
		// the field; a reader who has moved on elsewhere keeps their place.
		const hadFocus = document.activeElement?.closest('#site-search') != null;
		showSearch = false;
		if (restoreFocus && hadFocus) searchToggle?.focus();
	}

	// `/` opens search from anywhere on the site; Escape closes it.
	async function handleWindowKeydown(event: KeyboardEvent) {
		if (event.key === 'Escape') {
			if (showSearch && !event.defaultPrevented) closeSearch(true);
			return;
		}
		if (event.key !== '/' || event.repeat || !shortcutAllowed(event)) return;
		event.preventDefault(); // otherwise Firefox opens its quick find
		showSearch = true;
		await tick();
		searchBar?.focusInput();
	}
</script>

<svelte:window on:keydown={handleWindowKeydown} />

<header class="site-header bg-brand-strong dark:bg-header-dark text-white">
	<div class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-2.5 sm:py-4">
		<!--
			Mobile geometry, the short version: four icon buttons drawn at 36px on a
			44px pitch (each button's hit area is 44px; see .icon-button) claim 168px
			of a 390px viewport. Whatever the brand block is given, it must fit in the
			rest, so it needs `min-w-0` (a flex item defaults to `min-width:auto` and
			refuses to shrink below its content) and a title that fits on one line:
			"AI News Daily" at 18px is about 115px against about 130px of space.
		-->
		<div class="flex items-center justify-between gap-2 sm:gap-4">
			<div class="flex items-center min-w-0">
				<!-- The site name is not a heading: each view's own <h1> names the view. -->
				<a
					href="/"
					class="flex items-center gap-2 sm:gap-3 min-w-0 min-h-[44px] rounded-lg hover:opacity-90 transition-opacity"
				>
					<img
						src="/assets/logo.webp"
						alt=""
						width="48"
						height="48"
						class="w-9 h-9 sm:w-12 sm:h-12 flex-none rounded-full bg-white p-0.5"
					/>
					<span class="block min-w-0">
						<span class="block text-lg sm:text-2xl font-bold tracking-tight leading-tight whitespace-nowrap">
							AI News Daily
						</span>
						<span class="block text-xs sm:text-sm text-white/90 leading-snug">
							by AATF<span class="hidden sm:inline">{#if modelName}<span aria-hidden="true">&nbsp;&middot;&nbsp;</span><span class="sr-only">, </span>Powered by {modelName}{/if}{#if showNewBadge}
									<span class="new-badge">NEW</span>{/if}</span>
						</span>
					</span>
				</a>
			</div>

			<div class="flex items-center gap-2 sm:gap-3 flex-none">
				{#if dateDisplay}
					<a
						href="/archive"
						class="hidden md:inline-flex items-center min-h-[44px] text-sm text-white bg-white/15 px-3 rounded-lg hover:bg-white/25 transition-colors"
					>
						{dateDisplay}
					</a>
				{/if}

				<!-- GitHub link -->
				<a
					href="https://github.com/flyryan/ai-news-aggregator"
					target="_blank"
					rel="noopener noreferrer"
					class="icon-button"
					aria-label="GitHub Repository"
					title="View source on GitHub"
				>
					<svg
						xmlns="http://www.w3.org/2000/svg"
						class="w-5 h-5"
						aria-hidden="true"
						fill="currentColor"
						viewBox="0 0 24 24"
					>
						<path fill-rule="evenodd" d="M12 2C6.477 2 2 6.484 2 12.017c0 4.425 2.865 8.18 6.839 9.504.5.092.682-.217.682-.483 0-.237-.008-.868-.013-1.703-2.782.605-3.369-1.343-3.369-1.343-.454-1.158-1.11-1.466-1.11-1.466-.908-.62.069-.608.069-.608 1.003.07 1.531 1.032 1.531 1.032.892 1.53 2.341 1.088 2.91.832.092-.647.35-1.088.636-1.338-2.22-.253-4.555-1.113-4.555-4.951 0-1.093.39-1.988 1.029-2.688-.103-.253-.446-1.272.098-2.65 0 0 .84-.27 2.75 1.026A9.564 9.564 0 0112 6.844c.85.004 1.705.115 2.504.337 1.909-1.296 2.747-1.027 2.747-1.027.546 1.379.202 2.398.1 2.651.64.7 1.028 1.595 1.028 2.688 0 3.848-2.339 4.695-4.566 4.943.359.309.678.92.678 1.855 0 1.338-.012 2.419-.012 2.747 0 .268.18.58.688.482A10.019 10.019 0 0022 12.017C22 6.484 17.522 2 12 2z" clip-rule="evenodd" />
					</svg>
				</a>

				<!-- RSS Feed link -->
				<a
					href="/feeds"
					class="icon-button"
					aria-label="RSS Feeds"
					title="Subscribe to RSS feeds"
				>
					<svg
						xmlns="http://www.w3.org/2000/svg"
						class="w-5 h-5"
						aria-hidden="true"
						fill="currentColor"
						viewBox="0 0 24 24"
					>
						<circle cx="6.18" cy="17.82" r="2.18"/>
						<path d="M4 4.44v2.83c7.03 0 12.73 5.7 12.73 12.73h2.83c0-8.59-6.97-15.56-15.56-15.56zm0 5.66v2.83c3.9 0 7.07 3.17 7.07 7.07h2.83c0-5.47-4.43-9.9-9.9-9.9z"/>
					</svg>
				</a>

				<!-- Search toggle button -->
				<button
					bind:this={searchToggle}
					type="button"
					on:click={() => showSearch = !showSearch}
					class="icon-button"
					aria-label="Toggle search"
					aria-expanded={showSearch}
					aria-controls="site-search"
					aria-keyshortcuts="/"
					title="Search (press /)"
				>
					<svg
						xmlns="http://www.w3.org/2000/svg"
						class="w-5 h-5"
						aria-hidden="true"
						fill="none"
						viewBox="0 0 24 24"
						stroke="currentColor"
						stroke-width="2"
					>
						<path
							stroke-linecap="round"
							stroke-linejoin="round"
							d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"
						/>
					</svg>
				</button>

				<ThemeToggle />
			</div>
		</div>

		<!-- Expandable search bar -->
		{#if showSearch}
			<div id="site-search" class="mt-2.5 pt-2.5 sm:mt-4 sm:pt-4 border-t border-white/25">
				<SearchBar
					bind:this={searchBar}
					autofocus
					on:select={() => showSearch = false}
					on:close={() => closeSearch(true)}
				/>
			</div>
		{/if}
	</div>
</header>

<style>
	/* Focus rings on the red header are white: the page's red ring would vanish. */
	.site-header :global(:focus-visible) {
		outline-color: #fff;
	}
	/* ...except inside the search panel, whose fields are white. */
	.site-header :global(#site-search :focus-visible) {
		outline-color: #fff;
		outline-offset: 3px;
	}

	/* Header icon buttons: drawn at 36px on a phone and 40px from `sm`, with a
	   44px hit area either way (the pseudo-element), so four of them still leave
	   room for the title at 390px. Shared with ThemeToggle. */
	.site-header :global(.icon-button) {
		position: relative;
		flex: none;
		display: grid;
		place-items: center;
		width: 36px;
		height: 36px;
		border-radius: 0.5rem;
		background: rgb(255 255 255 / 0.15);
		transition: background-color 200ms;
	}
	.site-header :global(.icon-button:hover) {
		background: rgb(255 255 255 / 0.25);
	}
	.site-header :global(.icon-button::after) {
		content: '';
		position: absolute;
		inset: -4px;
	}
	@media (min-width: 640px) {
		.site-header :global(.icon-button) {
			width: 44px;
			height: 44px;
		}
		.site-header :global(.icon-button::after) {
			content: none;
		}
	}

	.new-badge {
		display: inline-block;
		font-size: 0.75rem;
		font-weight: 700;
		letter-spacing: 0.04em;
		line-height: 1;
		padding: 3px 7px;
		margin-left: 6px;
		border-radius: 9999px;
		background: #fff;
		color: #8f1a20;
		vertical-align: middle;
	}

	/* A single short entrance, then still. Nothing for readers who asked motion
	   to stop. */
	@media (prefers-reduced-motion: no-preference) {
		.new-badge {
			animation: badge-in 480ms cubic-bezier(0.16, 1, 0.3, 1) both;
		}
	}
	@keyframes badge-in {
		from {
			opacity: 0;
			transform: translateY(2px);
		}
	}
</style>
