<script lang="ts">
	import { CATEGORY_CONFIG, type Category } from '$lib/types';

	const feeds = {
		main: {
			name: 'Main Feed',
			description: 'Top stories from across all categories, plus top 5 items from each category. Mirrors the homepage.',
			url: '/data/feeds/main.xml',
			recommended: true
		},
		news: {
			name: 'News',
			description: 'All news items from RSS feeds and linked articles.',
			variants: [
				{ name: 'All Items', url: '/data/feeds/news.xml' }
			]
		},
		research: {
			name: 'Research',
			description: 'arXiv papers and research blog posts.',
			variants: [
				{ name: 'Top 25', url: '/data/feeds/research-25.xml' },
				{ name: 'Top 50', url: '/data/feeds/research-50.xml', default: true },
				{ name: 'Top 100', url: '/data/feeds/research-100.xml' },
				{ name: 'All Items', url: '/data/feeds/research-full.xml' }
			]
		},
		social: {
			name: 'Social',
			description: 'Posts from Twitter, Bluesky, and Mastodon.',
			variants: [
				{ name: 'Top 25', url: '/data/feeds/social-25.xml' },
				{ name: 'Top 50', url: '/data/feeds/social-50.xml', default: true },
				{ name: 'Top 100', url: '/data/feeds/social-100.xml' },
				{ name: 'All Items', url: '/data/feeds/social-full.xml' }
			]
		},
		reddit: {
			name: 'Reddit',
			description: 'Discussions from AI-related subreddits.',
			variants: [
				{ name: 'Top 25', url: '/data/feeds/reddit-25.xml' },
				{ name: 'Top 50', url: '/data/feeds/reddit-50.xml', default: true },
				{ name: 'Top 100', url: '/data/feeds/reddit-100.xml' },
				{ name: 'All Items', url: '/data/feeds/reddit-full.xml' }
			]
		}
	};

	const summaryFeeds = {
		executive: {
			name: 'Daily Briefing',
			description: 'Just the daily executive summary with hero image. One entry per day.',
			url: '/data/feeds/summaries-executive.xml',
			recommended: true
		},
		all: {
			name: 'All Summaries',
			description: 'Executive summary plus all category summaries. No individual items.',
			url: '/data/feeds/summaries.xml'
		},
		news: {
			name: 'News Summaries',
			description: 'Daily news category summaries only.',
			url: '/data/feeds/summaries-news.xml'
		},
		research: {
			name: 'Research Summaries',
			description: 'Daily research category summaries only.',
			url: '/data/feeds/summaries-research.xml'
		},
		social: {
			name: 'Social Summaries',
			description: 'Daily social category summaries only.',
			url: '/data/feeds/summaries-social.xml'
		},
		reddit: {
			name: 'Reddit Summaries',
			description: 'Daily reddit category summaries only.',
			url: '/data/feeds/summaries-reddit.xml'
		}
	};

	function getCategoryColor(category: string): string {
		const config = CATEGORY_CONFIG[category as Category];
		return config?.color || '#6b7280';
	}
</script>

<svelte:head>
	<title>RSS Feeds | AI News Daily</title>
</svelte:head>

<div class="max-w-4xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
	<h1 class="text-2xl font-bold text-trend-gray-800 dark:text-trend-gray-100 mb-2">
		RSS Feeds
	</h1>
	<p class="text-trend-gray-600 dark:text-trend-gray-400 mb-8">
		Subscribe to AI news in your favorite RSS reader. All feeds use Atom 1.0 format and include the last 7 days of content.
	</p>

	<!-- Main Feed (Recommended) -->
	<div class="card mb-8 border-2 border-trend-red">
		<div class="flex items-start justify-between gap-4">
			<div>
				<div class="flex items-center gap-2 mb-1">
					<h2 class="text-lg font-semibold text-trend-gray-800 dark:text-trend-gray-100">
						{feeds.main.name}
					</h2>
					<span class="text-xs px-2 py-0.5 bg-trend-red text-white rounded-full">Recommended</span>
				</div>
				<p class="text-sm text-trend-gray-600 dark:text-trend-gray-400">
					{feeds.main.description}
				</p>
			</div>
			<a
				href={feeds.main.url}
				class="shrink-0 inline-flex items-center gap-2 px-4 py-2 bg-trend-red text-white rounded-lg hover:bg-guardian-red transition-colors"
			>
				<svg xmlns="http://www.w3.org/2000/svg" class="w-4 h-4" fill="currentColor" viewBox="0 0 24 24">
					<circle cx="6.18" cy="17.82" r="2.18"/>
					<path d="M4 4.44v2.83c7.03 0 12.73 5.7 12.73 12.73h2.83c0-8.59-6.97-15.56-15.56-15.56zm0 5.66v2.83c3.9 0 7.07 3.17 7.07 7.07h2.83c0-5.47-4.43-9.9-9.9-9.9z"/>
				</svg>
				Subscribe
			</a>
		</div>
	</div>

	<!-- Summary Feeds -->
	<h2 class="text-lg font-semibold text-trend-gray-800 dark:text-trend-gray-100 mb-4">
		Summary Feeds
	</h2>
	<p class="text-sm text-trend-gray-600 dark:text-trend-gray-400 mb-6">
		Just the summaries, no individual items. Great for a quick daily overview.
	</p>

	<!-- Daily Briefing (Recommended Summary Feed) -->
	<div class="card mb-4 border-2 border-trend-red/50">
		<div class="flex items-start justify-between gap-4">
			<div>
				<div class="flex items-center gap-2 mb-1">
					<h3 class="font-semibold text-trend-gray-800 dark:text-trend-gray-100">
						{summaryFeeds.executive.name}
					</h3>
					<span class="text-xs px-2 py-0.5 bg-trend-red/80 text-white rounded-full">Most Popular</span>
				</div>
				<p class="text-sm text-trend-gray-600 dark:text-trend-gray-400">
					{summaryFeeds.executive.description}
				</p>
			</div>
			<a
				href={summaryFeeds.executive.url}
				class="shrink-0 inline-flex items-center gap-2 px-4 py-2 bg-trend-red/80 text-white rounded-lg hover:bg-trend-red transition-colors"
			>
				<svg xmlns="http://www.w3.org/2000/svg" class="w-4 h-4" fill="currentColor" viewBox="0 0 24 24">
					<circle cx="6.18" cy="17.82" r="2.18"/>
					<path d="M4 4.44v2.83c7.03 0 12.73 5.7 12.73 12.73h2.83c0-8.59-6.97-15.56-15.56-15.56zm0 5.66v2.83c3.9 0 7.07 3.17 7.07 7.07h2.83c0-5.47-4.43-9.9-9.9-9.9z"/>
				</svg>
				Subscribe
			</a>
		</div>
	</div>

	<!-- All Summaries (Full Width) -->
	<a
		href={summaryFeeds.all.url}
		class="block card p-3 hover:border-trend-red/50 transition-colors group mb-3"
		style="border-left: 3px solid #6b7280"
	>
		<div class="flex items-center justify-between gap-2">
			<div>
				<h4 class="font-medium text-trend-gray-800 dark:text-trend-gray-100 text-sm">
					{summaryFeeds.all.name}
				</h4>
				<p class="text-xs text-trend-gray-500 dark:text-trend-gray-400 mt-0.5">
					{summaryFeeds.all.description}
				</p>
			</div>
			<svg xmlns="http://www.w3.org/2000/svg" class="w-4 h-4 text-trend-gray-400 group-hover:text-trend-red transition-colors shrink-0" fill="currentColor" viewBox="0 0 24 24">
				<circle cx="6.18" cy="17.82" r="2.18"/>
				<path d="M4 4.44v2.83c7.03 0 12.73 5.7 12.73 12.73h2.83c0-8.59-6.97-15.56-15.56-15.56zm0 5.66v2.83c3.9 0 7.07 3.17 7.07 7.07h2.83c0-5.47-4.43-9.9-9.9-9.9z"/>
			</svg>
		</div>
	</a>

	<!-- Category Summary Feeds -->
	<div class="grid grid-cols-1 sm:grid-cols-2 gap-3 mb-8">
		{#each ['news', 'research', 'social', 'reddit'] as key}
			{@const feed = summaryFeeds[key as keyof typeof summaryFeeds]}
			{@const color = getCategoryColor(key)}
			<a
				href={feed.url}
				class="block card p-3 hover:border-trend-red/50 transition-colors group"
				style="border-left: 3px solid {color}"
			>
				<div class="flex items-center justify-between gap-2">
					<div>
						<h4 class="font-medium text-trend-gray-800 dark:text-trend-gray-100 text-sm">
							{feed.name}
						</h4>
						<p class="text-xs text-trend-gray-500 dark:text-trend-gray-400 mt-0.5">
							{feed.description}
						</p>
					</div>
					<svg xmlns="http://www.w3.org/2000/svg" class="w-4 h-4 text-trend-gray-400 group-hover:text-trend-red transition-colors shrink-0" fill="currentColor" viewBox="0 0 24 24">
						<circle cx="6.18" cy="17.82" r="2.18"/>
						<path d="M4 4.44v2.83c7.03 0 12.73 5.7 12.73 12.73h2.83c0-8.59-6.97-15.56-15.56-15.56zm0 5.66v2.83c3.9 0 7.07 3.17 7.07 7.07h2.83c0-5.47-4.43-9.9-9.9-9.9z"/>
					</svg>
				</div>
			</a>
		{/each}
	</div>

	<!-- Category Feeds -->
	<h2 class="text-lg font-semibold text-trend-gray-800 dark:text-trend-gray-100 mb-4">
		Category Feeds
	</h2>
	<p class="text-sm text-trend-gray-600 dark:text-trend-gray-400 mb-6">
		Subscribe to specific categories. Choose how many items you want in your feed.
	</p>

	<div class="space-y-4">
		{#each ['news', 'research', 'social', 'reddit'] as category}
			{@const feed = feeds[category as keyof typeof feeds]}
			{@const color = getCategoryColor(category)}
			{#if 'variants' in feed}
				<div class="card" style="border-left: 4px solid {color}">
					<div class="mb-3">
						<h3 class="font-semibold text-trend-gray-800 dark:text-trend-gray-100">
							{feed.name}
						</h3>
						<p class="text-sm text-trend-gray-600 dark:text-trend-gray-400">
							{feed.description}
						</p>
					</div>
					<div class="flex flex-wrap gap-2">
						{#each feed.variants as variant}
							<a
								href={variant.url}
								class="inline-flex items-center gap-1.5 px-3 py-1.5 text-sm rounded-lg border transition-colors
									{variant.default
										? 'bg-trend-gray-100 dark:bg-trend-gray-700 border-trend-gray-300 dark:border-trend-gray-600 text-trend-gray-800 dark:text-trend-gray-100 hover:border-trend-red'
										: 'border-trend-gray-200 dark:border-trend-gray-700 text-trend-gray-600 dark:text-trend-gray-400 hover:border-trend-gray-400 dark:hover:border-trend-gray-500'
									}"
							>
								<svg xmlns="http://www.w3.org/2000/svg" class="w-3.5 h-3.5" fill="currentColor" viewBox="0 0 24 24">
									<circle cx="6.18" cy="17.82" r="2.18"/>
									<path d="M4 4.44v2.83c7.03 0 12.73 5.7 12.73 12.73h2.83c0-8.59-6.97-15.56-15.56-15.56zm0 5.66v2.83c3.9 0 7.07 3.17 7.07 7.07h2.83c0-5.47-4.43-9.9-9.9-9.9z"/>
								</svg>
								{variant.name}
								{#if variant.default}
									<span class="text-xs text-trend-gray-500">(default)</span>
								{/if}
							</a>
						{/each}
					</div>
				</div>
			{/if}
		{/each}
	</div>

	<!-- Help Section -->
	<div class="mt-8 p-4 bg-trend-gray-50 dark:bg-trend-gray-800 rounded-lg">
		<h3 class="font-semibold text-trend-gray-800 dark:text-trend-gray-100 mb-2">
			How to Subscribe
		</h3>
		<ol class="list-decimal list-inside text-sm text-trend-gray-600 dark:text-trend-gray-400 space-y-1">
			<li>Click a feed link above to open it</li>
			<li>Copy the URL from your browser's address bar</li>
			<li>Paste the URL into your RSS reader (Feedly, Inoreader, NetNewsWire, etc.)</li>
		</ol>
		<p class="text-sm text-trend-gray-500 mt-3">
			Most browsers will show raw XML when you click a feed link. That's normal! Just copy the URL and add it to your reader.
		</p>
	</div>
</div>
