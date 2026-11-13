/**
 * Display-only helpers for item cards. Nothing here changes the published
 * data: each function turns a raw field into what a reader should see.
 */

/** Importance bands. The thresholds are the tiers the cards have always used. */
export type ImportanceBand = 'high' | 'medium' | 'standard' | 'low';

export const IMPORTANCE_BANDS: { band: ImportanceBand; min: number; label: string }[] = [
	{ band: 'high', min: 80, label: 'Importance 80 and above' },
	{ band: 'medium', min: 60, label: 'Importance 60 to 79' },
	{ band: 'standard', min: 40, label: 'Importance 40 to 59' },
	{ band: 'low', min: -Infinity, label: 'Importance below 40' }
];

export function importanceBand(score: number): ImportanceBand {
	return (IMPORTANCE_BANDS.find((b) => score >= b.min) ?? IMPORTANCE_BANDS[3]).band;
}

/** Items scoring below this render as compact rows on category pages. */
export const COMPACT_BELOW = 60;

/** "1,984", the same everywhere regardless of the reader's locale. */
export function formatCount(n: number | null | undefined): string {
	return (n ?? 0).toLocaleString('en-US');
}

// Hosts whose public name differs from their hostname. Anything else is shown
// as the bare host ("venturebeat.com"), which still names where the link goes.
const HOST_NAMES: Record<string, string> = {
	'arxiv.org': 'arXiv',
	'arstechnica.com': 'Ars Technica',
	'techcrunch.com': 'TechCrunch',
	'theverge.com': 'The Verge',
	'wired.com': 'WIRED',
	'theguardian.com': 'The Guardian',
	'the-decoder.com': 'The Decoder',
	'openai.com': 'OpenAI',
	'marktechpost.com': 'MarkTechPost',
	'latent.space': 'Latent Space',
	'deepmind.google': 'Google DeepMind',
	'technologyreview.com': 'MIT Technology Review',
	'huggingface.co': 'Hugging Face',
	'newscientist.com': 'New Scientist',
	'github.blog': 'The GitHub Blog',
	'github.com': 'GitHub',
	'news.mit.edu': 'MIT News',
	'lesswrong.com': 'LessWrong',
	'alignmentforum.org': 'AI Alignment Forum',
	'reddit.com': 'Reddit',
	'old.reddit.com': 'Reddit',
	'x.com': 'X',
	'twitter.com': 'X',
	'bsky.app': 'Bluesky',
	'youtube.com': 'YouTube',
	'youtu.be': 'YouTube'
};

/**
 * Where an item's external link goes, named for the link text ("Read at Ars
 * Technica"). Taken from the URL's host, not from `item.source`: feed names
 * such as "Artificial Intelligence" or "Feed: Artificial Intelligence Latest"
 * do not name a destination. Null when the URL has no usable host.
 */
export function destinationName(url: string | undefined | null): string | null {
	if (!url) return null;
	let host: string;
	try {
		host = new URL(url).hostname.toLowerCase();
	} catch {
		return null;
	}
	host = host.replace(/^www\./, '');
	if (!host) return null;
	if (HOST_NAMES[host]) return HOST_NAMES[host];
	// A subdomain of a known host (export.arxiv.org, blog.x.com).
	const parent = host.split('.').slice(-2).join('.');
	return HOST_NAMES[parent] ?? host;
}

/**
 * arXiv's feed prefixes every abstract with "arXiv:2611.17146v1 Announce Type:
 * new  Abstract: ". It is feed plumbing, so it is dropped from the excerpt at
 * render time, in plain text and in pre-rendered HTML alike.
 */
const ARXIV_PREFIX =
	/^(\s*(?:<p>)?\s*)arXiv:\d{4}\.\d{4,5}(?:v\d+)?\s+Announce Type:\s*[\w-]+\s+Abstract:\s*/i;

export function stripArxivBoilerplate(text: string | undefined | null): string {
	if (!text) return '';
	return text.replace(ARXIV_PREFIX, '$1');
}

/**
 * An author line for a card. "Unknown" (the gatherers' placeholder) is not an
 * author and yields null. Long lists shorten to the first name plus "et al.";
 * `full` always holds the whole list.
 */
export function authorDisplay(
	author: string | undefined | null
): { short: string; full: string; truncated: boolean } | null {
	const full = (author ?? '').trim();
	if (!full || /^unknown$/i.test(full)) return null;
	const names = full
		.split(/,\s*/)
		.map((n) => n.trim())
		.filter(Boolean);
	if (names.length <= 3) return { short: full, full, truncated: false };
	return { short: `${names[0]} et al.`, full, truncated: true };
}
