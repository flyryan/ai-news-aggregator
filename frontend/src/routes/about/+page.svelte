<svelte:head>
	<title>About | AI News Daily</title>
</svelte:head>

<div class="max-w-4xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
	<h1 class="text-2xl font-bold text-trend-gray-800 dark:text-trend-gray-100 mb-8">
		About AI News Daily
	</h1>

	<!-- About This Project -->
	<section class="card mb-8">
		<h2 class="text-lg font-semibold text-trend-gray-800 dark:text-trend-gray-100 mb-4">
			About This Project
		</h2>
		<div class="prose dark:prose-invert max-w-none text-trend-gray-600 dark:text-trend-gray-400">
			<p>
				<strong>AATF AI News Aggregator</strong> delivers daily briefings on AI/ML developments,
				helping you stay current with the fast-moving world of artificial intelligence.
			</p>
			<p>
				Built by the <a href="https://www.trendmicro.com/" target="_blank" rel="noopener noreferrer" class="text-trend-red hover:underline font-semibold">TrendAI</a> <strong>AI Acceleration Task Force (AATF)</strong>, this project was created
				to make sense of the overwhelming volume of AI news published daily across research papers,
				news outlets, and social media.
			</p>
			<p>
				The aggregator uses a multi-agent pipeline powered by Claude Opus 4.5 with extended thinking
				to gather, analyze, and synthesize content from diverse sources into coherent daily reports.
			</p>
			<p>
				Originally developed as an internal tool to keep our team informed, we've open-sourced
				it so others can run their own instances or contribute improvements.
			</p>
		</div>
	</section>

	<!-- How It Works -->
	<section class="card mb-8">
		<h2 class="text-lg font-semibold text-trend-gray-800 dark:text-trend-gray-100 mb-4">
			How It Works
		</h2>
		<div class="prose dark:prose-invert max-w-none text-trend-gray-600 dark:text-trend-gray-400">
			<figure class="my-6">
				<img
					src="/assets/agent-architecture.webp"
					alt="Agent architecture diagram showing data sources flowing through gatherers and analyzers to the orchestrator"
					class="w-full rounded-lg shadow-md"
				/>
				<figcaption class="text-center text-sm text-trend-gray-500 dark:text-trend-gray-500 mt-2">
					Multi-agent pipeline architecture
				</figcaption>
			</figure>
			<p>Each day, the pipeline runs through several phases:</p>
			<ol>
				<li>
					<strong>Parallel Gathering</strong> - Four specialized gatherers collect content from
					different source types: News (RSS feeds and linked articles), Research (arXiv papers
					and research blogs), Social (Twitter, Bluesky, Mastodon), and Reddit.
				</li>
				<li>
					<strong>Category Analysis</strong> - Four analyzers process each category using
					extended thinking to identify key developments, assess importance, and generate summaries.
				</li>
				<li>
					<strong>Cross-Category Topic Detection</strong> - The system identifies themes that
					span multiple categories, revealing broader narratives in the day's news.
				</li>
				<li>
					<strong>Executive Summary Generation</strong> - A high-level summary is created that
					captures the most important developments across all categories.
				</li>
				<li>
					<strong>Link Enrichment</strong> - Internal links are added to summaries so you can
					easily navigate to referenced items.
				</li>
			</ol>
		</div>
	</section>

	<!-- AI-Generated Content Disclaimer -->
	<section class="card mb-8 border-2 border-amber-500 dark:border-amber-600">
		<div class="flex items-start gap-3">
			<div class="shrink-0 mt-1">
				<svg class="w-6 h-6 text-amber-500" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
					<path stroke-linecap="round" stroke-linejoin="round" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
				</svg>
			</div>
			<div>
				<h2 class="text-lg font-semibold text-trend-gray-800 dark:text-trend-gray-100 mb-4">
					AI-Generated Content Disclaimer
				</h2>
				<div class="prose dark:prose-invert max-w-none text-trend-gray-600 dark:text-trend-gray-400">
					<p>
						<strong>All summaries and analysis on this site are AI-generated</strong> using
						Claude Opus 4.5 from Anthropic. The content is produced entirely by automated processes
						without human editorial review.
					</p>
					<p>
						The pipeline uses <strong>extended thinking</strong> (up to 32,000 tokens of reasoning)
						for complex analysis tasks like cross-category topic detection and executive summaries.
						This helps the AI produce more thoughtful, well-reasoned content.
					</p>
					<p>
						<strong>While we strive for accuracy, AI can and does make errors.</strong> These may include:
					</p>
					<ul>
						<li>Misattributing quotes or claims to the wrong source</li>
						<li>Misinterpreting technical details in research papers</li>
						<li>Missing important context or nuance</li>
						<li>Hallucinating details that weren't in the original sources</li>
					</ul>
					<p>
						<strong>Source links are provided throughout</strong> so you can verify information
						by reading the original content. We strongly recommend checking primary sources for
						any information you plan to act on or share.
					</p>
					<p class="font-medium">
						Always verify important information from primary sources.
					</p>
				</div>
			</div>
		</div>
	</section>

	<!-- Open Source -->
	<section class="card">
		<h2 class="text-lg font-semibold text-trend-gray-800 dark:text-trend-gray-100 mb-4">
			Open Source
		</h2>
		<div class="prose dark:prose-invert max-w-none text-trend-gray-600 dark:text-trend-gray-400">
			<p>
				This project is open source under the <strong>Apache 2.0 License</strong>, which allows
				you to use, modify, and distribute the code freely.
			</p>
			<p>
				The source code is available on GitHub:
			</p>
			<p>
				<a
					href="https://github.com/flyryan/ai-news-aggregator"
					target="_blank"
					rel="noopener noreferrer"
					class="inline-flex items-center gap-2 text-trend-red hover:underline"
				>
					<svg class="w-5 h-5" fill="currentColor" viewBox="0 0 24 24">
						<path fill-rule="evenodd" d="M12 2C6.477 2 2 6.484 2 12.017c0 4.425 2.865 8.18 6.839 9.504.5.092.682-.217.682-.483 0-.237-.008-.868-.013-1.703-2.782.605-3.369-1.343-3.369-1.343-.454-1.158-1.11-1.466-1.11-1.466-.908-.62.069-.608.069-.608 1.003.07 1.531 1.032 1.531 1.032.892 1.53 2.341 1.088 2.91.832.092-.647.35-1.088.636-1.338-2.22-.253-4.555-1.113-4.555-4.951 0-1.093.39-1.988 1.029-2.688-.103-.253-.446-1.272.098-2.65 0 0 .84-.27 2.75 1.026A9.564 9.564 0 0112 6.844c.85.004 1.705.115 2.504.337 1.909-1.296 2.747-1.027 2.747-1.027.546 1.379.202 2.398.1 2.651.64.7 1.028 1.595 1.028 2.688 0 3.848-2.339 4.695-4.566 4.943.359.309.678.92.678 1.855 0 1.338-.012 2.419-.012 2.747 0 .268.18.58.688.482A10.019 10.019 0 0022 12.017C22 6.484 17.522 2 12 2z" clip-rule="evenodd" />
					</svg>
					flyryan/ai-news-aggregator
				</a>
			</p>
			<p>
				Contributions are welcome! Whether you want to add new data sources, improve the analysis
				prompts, enhance the frontend, or fix bugs, we'd love to see your pull requests.
			</p>
		</div>
	</section>
</div>
