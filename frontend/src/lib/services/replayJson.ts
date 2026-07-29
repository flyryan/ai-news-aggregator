/**
 * Incremental JSON parsing for replayed model output.
 *
 * ~90% of what this pipeline's models emit is JSON — 44 of 59 captured streams on
 * 2026-07-28 opened with a ```json fence and 15 more were bare objects. Rendered as
 * flat prose that is a wall of braces and quotes; rendered structurally it is the
 * actual shape of the work: N items, each with a summary, a score, and themes.
 *
 * The hard requirement is that **the input is always a prefix**. At any frame the
 * text ends mid-token, mid-string, mid-object. A standard `JSON.parse` fails on all
 * of those, so this is a hand-written scanner that returns the largest well-formed
 * structure it can see and marks the rest as still-arriving. That "still arriving"
 * state is not a degradation — it is the thing worth watching, because it shows an
 * array growing by one item at a time exactly as the model produced it.
 *
 * What this is NOT: a validator. Malformed JSON is a display problem here, never a
 * correctness one — the pipeline already parsed this text for real, hours ago. When
 * the scanner cannot make sense of the input the caller falls back to raw text.
 */

/** A value that has been fully parsed, or is still being written. */
export type PartialValue =
	| { kind: 'object'; entries: PartialEntry[]; complete: boolean }
	| { kind: 'array'; items: PartialValue[]; complete: boolean }
	| { kind: 'string'; value: string; complete: boolean }
	| { kind: 'number'; value: string; complete: boolean }
	| { kind: 'boolean'; value: boolean }
	| { kind: 'null' };

export interface PartialEntry {
	key: string;
	/** Null while the key has been read but its value has not started arriving. */
	value: PartialValue | null;
}

export interface ParsedStream {
	/** The root value, or null when nothing parseable has arrived yet. */
	root: PartialValue | null;
	/** True once the root closed cleanly — the model finished its structure. */
	complete: boolean;
	/** Prose the model wrote before the JSON (or the fence). Usually empty. */
	preamble: string;
	/**
	 * Prose the model wrote *after* closing the JSON. Rare but real — one call on
	 * 2026-07-28 followed its `no_match` list with several paragraphs explaining the
	 * near-misses. Dropping it would mean the structured view silently showed less
	 * than the model actually said, which is exactly what this replay must not do.
	 */
	epilogue: string;
	/** True when the payload arrived inside a ```json fence. */
	fenced: boolean;
}

const WS = new Set([' ', '\t', '\n', '\r']);

/**
 * Strip a leading code fence and any prose before it.
 *
 * Returns the offset where the JSON payload starts. A fence that has opened but not
 * closed is normal mid-stream — we only ever need the opening.
 */
function findPayloadStart(text: string): { start: number; preamble: string; fenced: boolean } {
	const fence = /```(?:json|jsonc|json5)?[ \t]*\r?\n/i.exec(text);
	if (fence) {
		const before = text.slice(0, fence.index).trim();
		// Only treat it as *the* payload fence if JSON actually follows it; a fence
		// deep inside prose belongs to the prose.
		const after = text.slice(fence.index + fence[0].length);
		const firstReal = after.search(/\S/);
		if (firstReal >= 0 && (after[firstReal] === '{' || after[firstReal] === '[')) {
			return { start: fence.index + fence[0].length + firstReal, preamble: before, fenced: true };
		}
	}
	const brace = text.search(/[[{]/);
	if (brace < 0) return { start: -1, preamble: text, fenced: false };
	return { start: brace, preamble: text.slice(0, brace).trim(), fenced: false };
}

/**
 * Scanner over a prefix of JSON text.
 *
 * Every `read*` returns the value it managed to build and leaves `i` after it. When
 * input runs out mid-value the partial value is returned with `complete: false` —
 * that is the normal case for the last few values in a live stream, not an error.
 */
class PrefixScanner {
	private i: number;
	constructor(
		private readonly s: string,
		start: number
	) {
		this.i = start;
	}

	private skipWs() {
		while (this.i < this.s.length && WS.has(this.s[this.i])) this.i++;
	}

	/** Offset just past the last character consumed — where any epilogue begins. */
	get offset(): number {
		return this.i;
	}

	private atEnd(): boolean {
		return this.i >= this.s.length;
	}

	readValue(): PartialValue | null {
		this.skipWs();
		if (this.atEnd()) return null;
		const c = this.s[this.i];
		if (c === '{') return this.readObject();
		if (c === '[') return this.readArray();
		if (c === '"') return this.readString();
		if (c === '-' || (c >= '0' && c <= '9')) return this.readNumber();
		if (this.s.startsWith('true', this.i)) {
			this.i += 4;
			return { kind: 'boolean', value: true };
		}
		if (this.s.startsWith('false', this.i)) {
			this.i += 5;
			return { kind: 'boolean', value: false };
		}
		if (this.s.startsWith('null', this.i)) {
			this.i += 4;
			return { kind: 'null' };
		}
		// A literal that has only partly arrived ("tru", "nul"): treat as not-yet-here
		// rather than consuming it, so the next frame re-reads it whole.
		if ('truefalsn'.includes(c)) return null;
		// Anything else means the text is not JSON after all.
		throw new Error(`unexpected ${JSON.stringify(c)} at ${this.i}`);
	}

	/** Narrower than `PartialValue` so object keys can read `.value` directly. */
	private readString(): { kind: 'string'; value: string; complete: boolean } {
		this.i++; // opening quote
		let out = '';
		while (this.i < this.s.length) {
			const c = this.s[this.i];
			if (c === '\\') {
				// An escape split across the chunk boundary: stop here and let the next
				// frame see the whole pair, rather than emitting a stray backslash.
				if (this.i + 1 >= this.s.length) return { kind: 'string', value: out, complete: false };
				const e = this.s[this.i + 1];
				this.i += 2;
				if (e === 'n') out += '\n';
				else if (e === 't') out += '\t';
				else if (e === 'r') out += '\r';
				else if (e === 'b') out += '\b';
				else if (e === 'f') out += '\f';
				else if (e === 'u') {
					const hex = this.s.slice(this.i, this.i + 4);
					if (hex.length < 4) return { kind: 'string', value: out, complete: false };
					out += String.fromCharCode(parseInt(hex, 16));
					this.i += 4;
				} else out += e;
				continue;
			}
			if (c === '"') {
				this.i++;
				return { kind: 'string', value: out, complete: true };
			}
			out += c;
			this.i++;
		}
		return { kind: 'string', value: out, complete: false };
	}

	private readNumber(): PartialValue {
		const start = this.i;
		if (this.s[this.i] === '-') this.i++;
		while (this.i < this.s.length && /[0-9.eE+-]/.test(this.s[this.i])) this.i++;
		// A number is only known-terminated once a non-number char follows it.
		return { kind: 'number', value: this.s.slice(start, this.i), complete: !this.atEnd() };
	}

	private readArray(): PartialValue {
		this.i++; // [
		const items: PartialValue[] = [];
		for (;;) {
			this.skipWs();
			if (this.atEnd()) return { kind: 'array', items, complete: false };
			if (this.s[this.i] === ']') {
				this.i++;
				return { kind: 'array', items, complete: true };
			}
			if (this.s[this.i] === ',') {
				this.i++;
				continue;
			}
			const v = this.readValue();
			if (v === null) return { kind: 'array', items, complete: false };
			items.push(v);
			// A value that did not close means the stream ended inside it.
			if (isIncomplete(v)) return { kind: 'array', items, complete: false };
		}
	}

	private readObject(): PartialValue {
		this.i++; // {
		const entries: PartialEntry[] = [];
		for (;;) {
			this.skipWs();
			if (this.atEnd()) return { kind: 'object', entries, complete: false };
			if (this.s[this.i] === '}') {
				this.i++;
				return { kind: 'object', entries, complete: true };
			}
			if (this.s[this.i] === ',') {
				this.i++;
				continue;
			}
			if (this.s[this.i] !== '"') {
				// Key not yet started or malformed; stop cleanly.
				return { kind: 'object', entries, complete: false };
			}
			const keyVal = this.readString();
			if (!keyVal.complete) {
				// Half a key: show it as a pending row so the field name types in.
				entries.push({ key: keyVal.value, value: null });
				return { kind: 'object', entries, complete: false };
			}
			const key = keyVal.value;
			this.skipWs();
			if (this.atEnd() || this.s[this.i] !== ':') {
				entries.push({ key, value: null });
				return { kind: 'object', entries, complete: false };
			}
			this.i++; // :
			const v = this.readValue();
			entries.push({ key, value: v });
			if (v === null || isIncomplete(v)) return { kind: 'object', entries, complete: false };
		}
	}
}

function isIncomplete(v: PartialValue): boolean {
	switch (v.kind) {
		case 'object':
		case 'array':
		case 'string':
		case 'number':
			return !v.complete;
		default:
			return false;
	}
}

/**
 * Parse a prefix of streamed model output as JSON.
 *
 * Returns `null` when the text is not JSON at all (prose, markdown), which is the
 * caller's signal to render it as text instead.
 */
export function parsePrefix(text: string): ParsedStream | null {
	if (!text) return null;
	const { start, preamble, fenced } = findPayloadStart(text);
	if (start < 0) return null;
	try {
		const scanner = new PrefixScanner(text, start);
		const root = scanner.readValue();
		if (!root || (root.kind !== 'object' && root.kind !== 'array')) return null;
		// Whatever follows the closed structure, minus a closing fence, is prose the
		// model kept writing. Only meaningful once the root actually closed.
		const complete = !isIncomplete(root);
		const epilogue = complete
			? text
					.slice(scanner.offset)
					.replace(/^\s*```/, '')
					.trim()
			: '';
		return { root, complete, preamble, epilogue, fenced };
	} catch {
		return null;
	}
}

/** Count of leaf values under a node — used to size collapsed summaries. */
export function countItems(v: PartialValue | null): number {
	if (!v) return 0;
	if (v.kind === 'array') return v.items.length;
	if (v.kind === 'object') return v.entries.length;
	return 1;
}

/**
 * A one-line preview of a value, for collapsed rows.
 *
 * Prefers a human-meaningful field over the raw first key: an item's `summary` or
 * `title` says far more in a collapsed row than `{"id": "e5b5cb99aa82", …}`.
 */
const PREVIEW_KEYS = [
	'summary',
	'title',
	'name',
	'text',
	'description',
	'label',
	'theme',
	// The continuity agents' records are mostly ids; these are their prose fields.
	'reasoning',
	'reference_text'
];

/**
 * True for entries that identify rather than describe — item-id keys and bare
 * hash values. A collapsed row faced with `83712bd56768` tells the reader
 * nothing; these are skipped when choosing a preview so the row leads with
 * whatever prose the record carries instead.
 */
function isIdLike(key: string, value: string): boolean {
	return /(^|_)ids?$/i.test(key) || looksLikeItemId(value);
}

/** True for a bare item-id hash (this pipeline's ids are 12 hex chars). */
export function looksLikeItemId(value: string): boolean {
	return /^[0-9a-f]{6,}$/i.test(value.trim());
}

/** Resolves an item-id hash to something human (its title), or null. */
export type IdResolver = (id: string) => string | null;

export function previewOf(v: PartialValue | null, max = 120, resolve?: IdResolver): string {
	if (!v) return '';
	switch (v.kind) {
		case 'string': {
			// A bare id (the matcher's `no_match` array is exactly this) resolves to
			// its item's title when the caller can supply one.
			if (resolve) {
				const title = resolve(v.value.trim());
				if (title) return truncate(title, max);
			}
			return truncate(v.value, max);
		}
		case 'number':
			return v.value;
		case 'boolean':
			return String(v.value);
		case 'null':
			return 'null';
		case 'array':
			return `${v.items.length} item${v.items.length === 1 ? '' : 's'}`;
		case 'object': {
			// Suffix match as well as exact: the curator writes `original_title`, which
			// is exactly the story name a collapsed decision row should lead with.
			for (const key of PREVIEW_KEYS) {
				const hit = v.entries.find(
					(e) =>
						(e.key === key || e.key.endsWith(`_${key}`)) && e.value?.kind === 'string'
				);
				if (hit) return truncate((hit.value as { value: string }).value, max);
			}
			const strings = v.entries.filter((e) => e.value?.kind === 'string');
			const prose = strings.find(
				(e) => !isIdLike(e.key, (e.value as { value: string }).value)
			);
			if (prose) {
				const anchor = (prose.value as { value: string }).value;
				// A record whose only prose is a short label — the link enricher's
				// {phrase, item_id, category} — names its anchor text but not its
				// target. Resolve the id it carries and show where the link points.
				if (resolve) {
					for (const e of strings) {
						if (e === prose) continue;
						const title = resolve((e.value as { value: string }).value.trim());
						if (title && title !== anchor) return truncate(`${anchor} → ${title}`, max);
					}
				}
				return truncate(anchor, max);
			}
			// All ids: resolve one to its item title when possible.
			if (resolve) {
				for (const e of strings) {
					const title = resolve((e.value as { value: string }).value.trim());
					if (title) return truncate(title, max);
				}
			}
			// A record that is nothing but unresolvable ids still shows one — a hash
			// beats "3 fields".
			if (strings[0]) return truncate((strings[0].value as { value: string }).value, max);
			return v.entries.length === 0 ? '{}' : `${v.entries.length} fields`;
		}
	}
}

function truncate(s: string, max: number): string {
	const flat = s.replace(/\s+/g, ' ').trim();
	return flat.length > max ? `${flat.slice(0, max - 1)}…` : flat;
}

/**
 * The array a caller most likely wants to feature — the payload of the response.
 *
 * These models return `{"items": [...]}`, `{"topics": [...]}`, `{"top_10": [...]}`,
 * `{"matches": [...]}` and so on. Picking the longest array at the root gives the
 * headline collection without hardcoding this pipeline's field names.
 */
export function principalArray(
	root: PartialValue | null
): { key: string; value: PartialValue } | null {
	if (!root || root.kind !== 'object') return null;
	let best: { key: string; value: PartialValue } | null = null;
	for (const e of root.entries) {
		if (e.value?.kind !== 'array') continue;
		if (!best || e.value.items.length > (best.value as { items: unknown[] }).items.length) {
			best = { key: e.key, value: e.value };
		}
	}
	return best;
}

/**
 * Syntax-highlight a prefix of JSON as HTML.
 *
 * The raw view exists so the reader can check the structured view against the
 * literal bytes the model emitted — routing it through the markdown renderer
 * defeated that, because markdown reflows the whole payload into paragraphs. This
 * is a token-level pass that only ever emits `<span class="…">`, so the output is
 * safe to inject after escaping; no sanitiser round-trip is needed or wanted (the
 * shared allowlist has no `span`, and would strip every class).
 *
 * Like the parser, it must tolerate a truncated tail: an unterminated string is
 * highlighted as a string right up to the cut.
 */
export function highlightJson(text: string): string {
	const out: string[] = [];
	let i = 0;
	const n = text.length;
	const esc = (s: string) =>
		s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

	while (i < n) {
		const c = text[i];

		if (c === '"') {
			// Read the whole string, then decide if it was a key by what follows.
			const start = i;
			i++;
			while (i < n) {
				if (text[i] === '\\') {
					i += 2;
					continue;
				}
				if (text[i] === '"') {
					i++;
					break;
				}
				i++;
			}
			const raw = text.slice(start, i);
			let j = i;
			while (j < n && WS.has(text[j])) j++;
			const isKey = text[j] === ':';
			out.push(`<span class="${isKey ? 'j-key' : 'j-str'}">${esc(raw)}</span>`);
			continue;
		}

		if (c === '-' || (c >= '0' && c <= '9')) {
			const start = i;
			if (text[i] === '-') i++;
			while (i < n && /[0-9.eE+-]/.test(text[i])) i++;
			out.push(`<span class="j-num">${esc(text.slice(start, i))}</span>`);
			continue;
		}

		const lit = (['true', 'false', 'null'] as const).find((l) => text.startsWith(l, i));
		if (lit) {
			out.push(`<span class="j-lit">${lit}</span>`);
			i += lit.length;
			continue;
		}

		if ('{}[]'.includes(c)) {
			out.push(`<span class="j-brace">${c}</span>`);
			i++;
			continue;
		}
		if (',:'.includes(c)) {
			out.push(`<span class="j-punct">${c}</span>`);
			i++;
			continue;
		}

		out.push(esc(c));
		i++;
	}

	return out.join('');
}

/**
 * Split raw output into the fence label (if any) and the payload, so the raw view
 * can show the fence as chrome rather than as a line of literal backticks.
 */
export function splitFence(text: string): { lang: string | null; body: string } {
	const open = /^\s*```([a-z0-9]*)[ \t]*\r?\n/i.exec(text);
	if (!open) return { lang: null, body: text };
	let body = text.slice(open[0].length);
	body = body.replace(/\r?\n?\s*```\s*$/, '');
	return { lang: open[1] || 'json', body };
}

/** Humanise a JSON key for display: `importance_score` → `importance score`. */
export function humaniseKey(key: string): string {
	return key.replace(/[_-]+/g, ' ').trim();
}

/**
 * Numeric fields worth rendering as a bar rather than a bare number.
 * Scores in this pipeline are 0-100; anything outside that is shown as text.
 */
export function scoreFraction(key: string, v: PartialValue | null): number | null {
	if (!v || v.kind !== 'number') return null;
	if (!/score|rating|confidence|importance|rank/i.test(key)) return null;
	const n = Number(v.value);
	if (!Number.isFinite(n) || n < 0 || n > 100) return null;
	return n / 100;
}
