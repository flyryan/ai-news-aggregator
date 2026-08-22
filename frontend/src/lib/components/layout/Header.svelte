<script lang="ts">
	import { currentDate, currentModel } from '$lib/stores/dateStore';
	import { formatDate, isNewModelBadge } from '$lib/services/dateUtils';
	import ThemeToggle from './ThemeToggle.svelte';
	import SearchBar from '$lib/components/search/SearchBar.svelte';

	$: dateDisplay = $currentDate ? formatDate($currentDate) : '';
	$: modelName = $currentModel?.display_name ?? '';
	$: showNewBadge = isNewModelBadge($currentModel?.since);

	let showSearch = false;
</script>

<header class="bg-gradient-to-r from-trend-red to-guardian-red text-white shadow-lg">
	<div class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-5">
		<!--
			Mobile geometry, the short version: four 36px icon buttons plus their gaps
			claim ~190px of a 393px viewport. Whatever the brand block is given, it must
			fit in the rest — so it needs `min-w-0` (a flex item defaults to
			`min-width:auto` and refuses to shrink below its content) and a title small
			enough that its longest word fits. At `text-2xl`, "Aggregator" alone is 121px
			against ~109px of space, which is exactly how the title came to sit under the
			GitHub icon.
		-->
		<div class="flex items-center justify-between gap-2 sm:gap-4">
			<div class="flex items-center min-w-0">
				<a href="/" class="flex items-center gap-2 sm:gap-3 min-w-0 hover:opacity-90 transition-opacity">
					<img
						src="/assets/logo.webp"
						alt="AATF Logo"
						class="w-10 h-10 sm:w-12 sm:h-12 flex-none rounded-full bg-white p-0.5"
					/>
					<div class="min-w-0">
						<h1 class="text-lg sm:text-2xl font-bold tracking-tight leading-tight">
							AATF AI News Aggregator
						</h1>
						<p class="text-xs sm:text-sm text-white/80 leading-snug">
							{#if modelName}
								Powered by {modelName}
							{:else}
								Daily AI/ML news from the AATF
							{/if}
							{#if showNewBadge}
								<span class="new-badge">NEW</span>
							{/if}
						</p>
					</div>
				</a>
			</div>

			<div class="flex items-center gap-1.5 sm:gap-4 flex-none">
				{#if dateDisplay}
					<a
						href="/archive"
						class="hidden sm:block text-sm text-white/90 bg-white/10 px-3 py-1.5 rounded-lg hover:bg-white/20 transition-colors"
					>
						{dateDisplay}
					</a>
				{/if}

				<!-- GitHub link -->
				<a
					href="https://github.com/flyryan/ai-news-aggregator"
					target="_blank"
					rel="noopener noreferrer"
					class="p-1.5 sm:p-2 rounded-lg bg-white/10 hover:bg-white/20 transition-colors flex-none"
					aria-label="GitHub Repository"
					title="View source on GitHub"
				>
					<svg
						xmlns="http://www.w3.org/2000/svg"
						class="w-5 h-5"
						fill="currentColor"
						viewBox="0 0 24 24"
					>
						<path fill-rule="evenodd" d="M12 2C6.477 2 2 6.484 2 12.017c0 4.425 2.865 8.18 6.839 9.504.5.092.682-.217.682-.483 0-.237-.008-.868-.013-1.703-2.782.605-3.369-1.343-3.369-1.343-.454-1.158-1.11-1.466-1.11-1.466-.908-.62.069-.608.069-.608 1.003.07 1.531 1.032 1.531 1.032.892 1.53 2.341 1.088 2.91.832.092-.647.35-1.088.636-1.338-2.22-.253-4.555-1.113-4.555-4.951 0-1.093.39-1.988 1.029-2.688-.103-.253-.446-1.272.098-2.65 0 0 .84-.27 2.75 1.026A9.564 9.564 0 0112 6.844c.85.004 1.705.115 2.504.337 1.909-1.296 2.747-1.027 2.747-1.027.546 1.379.202 2.398.1 2.651.64.7 1.028 1.595 1.028 2.688 0 3.848-2.339 4.695-4.566 4.943.359.309.678.92.678 1.855 0 1.338-.012 2.419-.012 2.747 0 .268.18.58.688.482A10.019 10.019 0 0022 12.017C22 6.484 17.522 2 12 2z" clip-rule="evenodd" />
					</svg>
				</a>

				<!-- RSS Feed link -->
				<a
					href="/feeds"
					class="p-1.5 sm:p-2 rounded-lg bg-white/10 hover:bg-white/20 transition-colors flex-none"
					aria-label="RSS Feeds"
					title="Subscribe to RSS feeds"
				>
					<svg
						xmlns="http://www.w3.org/2000/svg"
						class="w-5 h-5"
						fill="currentColor"
						viewBox="0 0 24 24"
					>
						<circle cx="6.18" cy="17.82" r="2.18"/>
						<path d="M4 4.44v2.83c7.03 0 12.73 5.7 12.73 12.73h2.83c0-8.59-6.97-15.56-15.56-15.56zm0 5.66v2.83c3.9 0 7.07 3.17 7.07 7.07h2.83c0-5.47-4.43-9.9-9.9-9.9z"/>
					</svg>
				</a>

				<!-- Search toggle button -->
				<button
					on:click={() => showSearch = !showSearch}
					class="p-1.5 sm:p-2 rounded-lg bg-white/10 hover:bg-white/20 transition-colors flex-none"
					aria-label="Toggle search"
				>
					<svg
						xmlns="http://www.w3.org/2000/svg"
						class="w-5 h-5"
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
			<div class="mt-4 pt-4 border-t border-white/20">
				<SearchBar on:select={() => showSearch = false} />
			</div>
		{/if}
	</div>
</header>

<style>
	.new-badge {
		display: inline-block;
		font-size: 0.625rem;
		font-weight: 700;
		letter-spacing: 0.05em;
		line-height: 1;
		padding: 2px 6px;
		margin-left: 6px;
		border-radius: 9999px;
		background: rgba(255, 255, 255, 0.25);
		color: white;
		vertical-align: middle;
		animation: badge-pulse 2s ease-in-out infinite;
	}
	@keyframes badge-pulse {
		0%, 100% { opacity: 1; }
		50% { opacity: 0.6; }
	}

	/* The badge is decorative; it must not pulse for anyone who asked motion to stop. */
	@media (prefers-reduced-motion: reduce) {
		.new-badge {
			animation: none;
		}
	}
</style>
