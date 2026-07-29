/**
 * Where this page's data lives.
 *
 * Live browsing uses `/data`. A preview sets `data-aatf-data-base` on <body>,
 * and every data fetch is prefixed with it, so the same built bundle renders
 * draft content without a rebuild.
 *
 * The attribute carries the base rather than an injected inline script because
 * each built page has its own CSP hash (`script-src 'self' 'sha256-...'`) --
 * an added inline script is blocked, and a global it was meant to set would
 * never appear.
 *
 * A malformed base THROWS. The predecessor to this file fell back to `/data`,
 * which meant a preview quietly rendered the live report under a draft banner;
 * an operator could approve something they had never seen. Refusing to render
 * is the safe failure.
 */

const ATTRIBUTE = 'aatfDataBase'; // <body data-aatf-data-base="...">
const LABEL_ATTRIBUTE = 'aatfPreviewLabel';

let cached: string | null = null;

function readAttribute(name: string): string | undefined {
	if (typeof document === 'undefined') return undefined;
	return document.body?.dataset?.[name];
}

/** The data root for this page. `''` means live. Never has a trailing slash. */
export function dataBase(): string {
	if (cached !== null) return cached;

	const raw = readAttribute(ATTRIBUTE);
	if (raw === undefined || raw === '') {
		cached = '';
		return cached;
	}

	const value = raw.trim();

	// Same-origin absolute paths only. A preview base is a path on this origin;
	// anything else is either a mistake or an attempt to point the page at
	// someone else's data.
	if (!value.startsWith('/') || value.startsWith('//')) {
		throw new Error(
			`Invalid data base ${JSON.stringify(raw)}: expected an absolute same-origin ` +
				`path like "/preview/abc123". Refusing to load data rather than falling ` +
				`back to live content, which would show the live report inside a preview.`
		);
	}

	cached = value.replace(/\/+$/, '');
	return cached;
}

/** Absolute URL for a data path. Pass paths like `/data/2026-07-28/summary.json`. */
export function dataUrl(path: string): string {
	const base = dataBase();
	if (!base) return path;
	return `${base}${path.startsWith('/') ? path : `/${path}`}`;
}

export function isPreview(): boolean {
	return dataBase() !== '';
}

export function previewLabel(): string | null {
	return readAttribute(LABEL_ATTRIBUTE) ?? null;
}

/** Testing only: clear the memoised value. */
export function resetDataBaseCache(): void {
	cached = null;
}
