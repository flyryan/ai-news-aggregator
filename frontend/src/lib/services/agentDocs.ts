/**
 * What each agent in the pipeline actually does.
 *
 * These descriptions are static — they describe the pipeline, not any one run —
 * so they live here rather than in every day's `replay-index.json`. That keeps
 * the index lean (it is fetched on every replay load), lets the copy be corrected
 * without regenerating published artifacts, and means historical days
 * automatically show current documentation.
 *
 * Everything here was verified against the code it describes. Where a number is
 * a default that an environment variable can change, it says so; where a count
 * varies per run, it is described as a shape rather than a figure. If you change
 * the pipeline, change this too — a confidently wrong explanation is worse than
 * none.
 */

export interface AgentRoleDoc {
	/** The `role` on the call, matching what the replay renders on each chip. */
	role: string;
	/** Short label for the kind of work, e.g. "Per-batch analysis". */
	label: string;
	/** What the model is asked to decide or produce. Never the prompt itself. */
	asks: string;
	/** Effort tier this role runs at, and why it is worth that much. */
	effort: string;
}

export interface AgentDoc {
	/** One or two sentences a non-engineer would understand. */
	summary: string;
	/** Where its input comes from. */
	input: string;
	/** What it hands downstream. */
	output: string;
	/** What the published report would lose without it. Concrete, not abstract. */
	matters: string;
	/** Per-call-type detail, for agents that make more than one kind of call. */
	roles?: AgentRoleDoc[];
	/** Anything surprising that a reader would otherwise misread. */
	note?: string;
}

const CATEGORY_LABEL: Record<string, string> = {
	news: 'News',
	research: 'Research',
	social: 'Social',
	reddit: 'Reddit'
};

// ---------------------------------------------------------------- scouts

const SCOUT_DOCS: Record<string, AgentDoc> = {
	news_gatherer: {
		summary:
			'Pulls the RSS wire — company blogs, product announcements, trade press — and can follow links found in social posts to read the underlying article.',
		input:
			'The feeds listed in config/rss_feeds.txt, plus any followable URLs the social scout turned up.',
		output: 'Articles with title, source, publication time and extracted body text.',
		matters:
			'This is where product launches and company news enter the pipeline. Without it the report is community chatter with no primary sources.',
		roles: [
			{
				role: 'filter',
				label: 'Link triage',
				asks: 'Shown a URL and the post it appeared in — never the page itself — decide whether it looks like a real article worth fetching.',
				effort: 'high — a yes/no per URL, run before anything is downloaded'
			}
		],
		note: 'The only scout that calls a model, and in practice it rarely gets the chance: Twitter wraps every outbound link in t.co, which is skipped as a redirector, so almost nothing reaches the triage step. Runs after the other three, because it needs their links.'
	},
	research_gatherer: {
		summary:
			"Reads the day's new arXiv preprints across seven AI categories, and pulls research blogs and alignment forums.",
		input:
			'arXiv announcement feeds for cs.AI, cs.LG, cs.CL, cs.CV, cs.NE, cs.RO and stat.ML, plus the feeds in config/research_feeds.txt.',
		output: 'Papers and posts with authors, abstract and category — typically the largest haul of the day.',
		matters: 'The only route by which new research reaches the report.',
		note: 'Only new and cross-listed submissions count; revisions of existing papers are skipped. A weekend report has no arXiv papers at all, and Monday collects a three-day catch-up. LessWrong needs a different route than the other blogs and is the most failure-prone source here.'
	},
	social_gatherer: {
		summary:
			'Follows practitioner accounts across Twitter, Bluesky and Mastodon and collects what they posted during the coverage window.',
		input:
			'Handles listed in config/twitter_accounts.txt, config/bluesky_accounts.txt and config/mastodon_accounts.txt.',
		output: 'Posts with author, platform and engagement figures — plus any links they contain, handed to the news scout.',
		matters:
			'Catches reaction and context that never becomes an article. Twitter supplies the overwhelming majority of what lands here.',
		note: 'The only scout that reports per-platform status, so one platform failing degrades that platform rather than the whole category. Makes no model calls — collection only. Twitter is the one paid source in this pipeline besides Reddit.'
	},
	reddit_gatherer: {
		summary:
			'Reads the subreddits where practitioners argue, and pulls the discussion underneath the headline as well as the post itself.',
		input: 'Subreddits listed in config/reddit_subreddits.txt, fetched through the ScrapeCreators API.',
		output: 'Posts with score and comment count; the highest-scoring ones also carry a digest of their top comments.',
		matters:
			'Community reaction is often the first place a claim gets checked. Without it the report has announcements and no scrutiny of them.',
		note: "Reddit's free endpoint no longer works for servers, so this runs through a paid API with a hard per-run call budget. Only the top posts per subreddit are worth a second call for their comments. Makes no model calls — the comment digest is ranking and truncation, not summarization."
	}
};

// -------------------------------------------------------------- analysts

function analystDoc(category: string): AgentDoc {
	const label = CATEGORY_LABEL[category] ?? category;
	const lens: Record<string, string> = {
		news: 'how much it actually moves the frontier, as opposed to how loudly it was announced',
		research: 'novelty, methodology quality, and whether the result holds up',
		social: 'who is saying it and whether the discussion is substantive',
		reddit: 'the quality of the argument, not the size of the thread'
	};
	return {
		summary: `Reads every ${label.toLowerCase()} item collected today, writes a short summary of each, and scores how much it matters.`,
		input: `Everything the ${label} Scout collected, split into batches of 75 items (configurable) so several can be read in parallel.`,
		output:
			'A summary, an importance score from 0 to 100, brief reasoning, and theme tags for each item — plus the themes running across the batch.',
		matters: `Nothing from ${label.toLowerCase()} reaches the report unranked. This is the judgement that decides what leads.`,
		roles: [
			{
				role: 'map',
				label: 'Read a batch',
				asks: `Summarize each item, score its importance, and tag its themes — weighing ${lens[category] ?? 'significance'}.`,
				effort: 'xhigh — runs once per batch, so it is the bulk of the run\'s spend'
			},
			{
				role: 'reduce',
				label: 'Rank and select',
				asks: 'Read the scored candidates side by side, pick the ten that lead the category, and write the category briefing.',
				effort: 'max — runs once and decides what readers actually see'
			},
			...(category === 'news'
				? [
						{
							role: 'filter',
							label: 'Pre-filter',
							asks: 'Decide which collected articles are about frontier AI at all, before anything is spent scoring them.',
							effort: 'high — a relevance gate, not a quality judgement'
						}
					]
				: [])
		],
		note: 'Every analyst is given the current list of model release dates, so it cannot describe a six-week-old model as newly launched. If a batch fails or comes back malformed it is split in half and retried.'
	};
}

// ------------------------------------------------------------------ desk

const DESK_DOCS: Record<string, AgentDoc> = {
	continuity: {
		summary:
			"Compares today's stories against the previous two days and works out which are genuinely new and which are a rerun of something already covered.",
		input: "Today's top items per category, and the items published on the two days before.",
		output:
			'Matches between today and earlier coverage, each classified as a rehash, a follow-up, a mainstream pickup, or a new development.',
		matters:
			'Without it the same story headlines three days running. Stories judged a rehash have their score capped, which changes the ranking, the topics, the summary and the hero image.',
		roles: [
			{
				role: 'match',
				label: 'Match against prior days',
				asks: 'For each of today\'s items, decide whether it covers the same underlying event as anything published recently. Deliberately conservative.',
				effort: 'high — a comparison, run once per category'
			},
			{
				role: 'curate',
				label: 'Curate storylines',
				asks: 'For each match, decide what kind of continuation it is, whether it should be demoted, and how to reference the earlier coverage in prose.',
				effort: 'max — the judgement that removes a story from the front page'
			}
		],
		note: 'Curation only runs if the matchers actually found something.'
	},
	freshness: {
		summary:
			'Catches articles that present old news as new — a write-up of a model released six weeks ago, or a recap whose only real claim was already covered.',
		input:
			"Today's items and up to 45 days of prior coverage, plus the tracked list of model release dates.",
		output: 'A freshness verdict per item, with flags that keep stale items out of the top stories and the summaries.',
		matters:
			'Without it, weeks-old launches resurface as "just released" and recycled coverage outranks new reporting.',
		roles: [
			{
				role: 'check',
				label: 'Check a borderline case',
				asks: 'Is this article mostly commentary on something already covered, or does it carry a concrete new development?',
				effort: 'high — a narrow judgement, capped at a few calls per category'
			}
		],
		note: 'Most of this agent\'s work needs no model at all — obvious cases are decided by comparing dates. Only genuinely ambiguous ones are escalated.'
	},
	orchestrator: {
		summary:
			'Reads all four category reports together, names the threads running across them, and writes the briefing that opens the report.',
		input: 'Every category summary and its top stories, plus the executive summaries from the previous three days.',
		output: "The day's cross-category topics, and the executive summary itself.",
		matters:
			'These topics are the report\'s spine — they drive the top-topics section, the summary, and the hero image. The summary is the most-read thing on the site.',
		roles: [
			{
				role: 'synthesize',
				label: 'Detect cross-category topics',
				asks: 'Find the stories that show up in more than one category and name the six that define the day.',
				effort: 'max — the hardest reasoning in the pipeline, and everything downstream depends on it'
			},
			{
				role: 'synthesize',
				label: 'Write the executive summary',
				asks: 'Write the day\'s briefing to a fixed structure, without re-announcing anything that already led in the past three days.',
				effort: 'max — this is the paragraph most readers will read'
			}
		]
	},
	link_enricher: {
		summary:
			'Takes the finished prose and turns the claims in it into links to the specific items that back them.',
		input: 'The executive summary, each category summary, each topic description, and the ranked items available to link to.',
		output: 'The same text with inline links, plus a record of which phrase points at which item.',
		matters:
			'Without it the summaries are dead text — factual claims with no path to the source.',
		roles: [
			{
				role: 'enrich',
				label: 'Link one piece of prose',
				asks: 'Insert short links on the action or event being described, pointing at the highest-ranked item covering it.',
				effort: 'xhigh — one call per summary and per topic, so a dozen or so per run'
			}
		],
		note: 'Designed to fail safe: if enrichment fails, the original unlinked text is published rather than nothing.'
	},
	ecosystem: {
		summary:
			"Reads the day's news for genuinely new model launches and records them in the project's release-date file.",
		input: 'The list of already-tracked models and the top news items of the day.',
		output: 'New releases with provider, model name, release date and a confidence rating.',
		matters:
			'That file is what stops every other agent, tomorrow and after, from describing an old model as newly released. Low-confidence detections are discarded rather than recorded.',
		roles: [
			{
				role: 'enrich',
				label: 'Detect model releases',
				asks: 'Identify model releases in today\'s news that are not already tracked — conservatively, excluding updates and features.',
				effort: 'xhigh — an extraction task with a high cost of being wrong'
			}
		]
	},
	hero: {
		summary:
			"Paints the day's banner: the AATF skunk in a scene built out of the day's top stories.",
		input: 'The detected topics, a set of keyword-to-visual mappings, and the skunk reference image.',
		output: 'A 21:9 banner image, and the prompt that produced it.',
		matters:
			'The hero is the visual identity of each day and the image RSS readers show. It is also the one output in the whole replay you can simply look at.',
		note: 'Not a language model: this runs on an image model, billed per image rather than per token. Its prompt is published alongside the image. If topic detection produced nothing, it falls back to the top themes from each category.'
	}
};

// --------------------------------------------------------------- lookup

const DOCS: Record<string, AgentDoc> = {
	...SCOUT_DOCS,
	...DESK_DOCS,
	news_analyzer: analystDoc('news'),
	research_analyzer: analystDoc('research'),
	social_analyzer: analystDoc('social'),
	reddit_analyzer: analystDoc('reddit')
};

/**
 * Documentation for an agent, or null when we have none.
 *
 * Returns null rather than a generic placeholder: an empty info button is better
 * than one that opens onto nothing useful. Unknown agents (a caller the taxonomy
 * guessed at, or a new agent added before this file was updated) simply have no
 * info affordance.
 */
export function agentDoc(agentId: string): AgentDoc | null {
	return DOCS[agentId] ?? null;
}
