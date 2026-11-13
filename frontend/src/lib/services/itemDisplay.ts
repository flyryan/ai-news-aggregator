/**
 * Display-only helpers for item cards and search results. Nothing here changes
 * the published data: each function turns a raw field into what a reader
 * should see. No DOM access, so the unit tests run under plain node.
 */

/** "1,984", the same everywhere regardless of the reader's locale. */
export function formatCount(n: number | null | undefined): string {
	return (n ?? 0).toLocaleString('en-US');
}

/**
 * arXiv's feed prefixes every abstract with "arXiv:2611.17146v1 Announce Type:
 * new  Abstract: ". It is feed plumbing, so it is dropped from the excerpt at
 * render time, in plain text and in pre-rendered HTML alike.
 */
const ARXIV_PREFIX =
	/^(\s*(?:<p>\s*)?)arXiv:\d{4}\.\d{4,5}(?:v\d+)?\s+Announce Type:\s*[\w-]+\s+Abstract:\s*/i;

export function stripArxivBoilerplate(text: string | undefined | null): string {
	if (typeof text !== 'string' || !text) return '';
	return text.replace(ARXIV_PREFIX, '$1');
}

/**
 * The author to show on a card, or null when there is none: "Unknown" is the
 * gatherers' placeholder, not a name.
 */
export function displayAuthor(author: string | undefined | null): string | null {
	const name = typeof author === 'string' ? author.trim() : '';
	if (!name || /^unknown$/i.test(name)) return null;
	return name;
}

/**
 * A search snippet as plain text. The corpus stores the first 200 characters
 * of the item's markdown summary, so a snippet can carry `[text](/?date=…)`
 * links and `**bold**` markers, and the cut can land inside either one.
 * Single asterisks are left alone: in this corpus they are content ("A*",
 * "c*n/log n"), not emphasis.
 */
export function plainSnippet(text: string | undefined | null): string {
	if (!text) return '';
	return (
		stripArxivBoilerplate(text)
			// [text](url) -> text
			.replace(/\[([^\]]*)\]\([^)\s]*\)/g, '$1')
			// a link cut off inside its URL by the 200-character limit
			.replace(/\[([^\]]*)\]\([^)\s]*?(?:\.{3}|…)?$/, '$1…')
			// a link cut off inside its text
			.replace(/\[([^\]]*?)(?:\.{3}|…)$/, '$1…')
			// **bold**, including a pair the cut left unclosed
			.replace(/\*\*/g, '')
	);
}
