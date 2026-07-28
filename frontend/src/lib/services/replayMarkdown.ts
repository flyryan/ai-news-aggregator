/**
 * Markdown rendering for replayed model output.
 *
 * The site already has `services/markdown.ts`, but it is built for finished
 * backend prose: it trims every line and drops blank ones, which is exactly the
 * information a transcript needs — the model's paragraph breaks. This renderer
 * keeps block structure instead.
 *
 * Two things make this different from a general-purpose markdown parser:
 *
 *  1. **The input is always partial.** We are rendering a prefix of a stream, so
 *     the last line is routinely mid-word, mid-`**bold**`, or mid-heading. An
 *     unterminated inline marker must render as literal text rather than swallow
 *     the rest of the document — so inline rules only fire on *closed* pairs.
 *  2. **It runs every animation frame.** Output is memoised on the exact input
 *     string, so a paused or finished transcript re-renders for free, and a live
 *     one does one linear pass over text that is already in memory.
 *
 * Output is sanitised through the shared allowlist before it reaches `{@html}`.
 * That allowlist (a, strong, em, p, ul, li, h2-h4, br) is deliberately narrow, so
 * this renderer only ever emits tags from it — no `ol`, no `code`. Ordered lists
 * keep their marker as text inside a `ul`; code spans render as emphasis.
 */

import { sanitizeHtml } from './sanitize';

/**
 * Escapes both quote characters, not just `"`.
 *
 * Every attribute this module emits is double-quoted, so `'` is not strictly
 * required today — but the cost is one line and the failure mode if someone later
 * writes a single-quoted attribute is an attribute breakout. The companion
 * invariant: replacement strings in `inline()` must never contain `"` or `'`,
 * which is what keeps a later bold/em rule from escaping an attribute it happens
 * to match inside.
 */
function escapeHtml(s: string): string {
	return s
		.replace(/&/g, '&amp;')
		.replace(/</g, '&lt;')
		.replace(/>/g, '&gt;')
		.replace(/"/g, '&quot;')
		.replace(/'/g, '&#39;');
}

/**
 * Inline spans. Every pattern requires its closing delimiter, so a half-typed
 * `**infer` stays literal until the model finishes the pair — no flicker of the
 * remaining paragraph turning bold and back.
 */
function inline(raw: string): string {
	let s = escapeHtml(raw);

	// `code` is not in the sanitiser allowlist; emphasis is the closest thing that
	// survives, and it keeps monospaced snippets visually distinct.
	s = s.replace(/`([^`\n]+)`/g, '<em class="md-code">$1</em>');

	// Links: [label](url). Only http(s), mailto and site-relative targets — the
	// sanitiser enforces this too, but failing here keeps the label readable.
	s = s.replace(/\[([^\]\n]+)\]\(([^)\s]+)\)/g, (m, label: string, url: string) => {
		if (/^(?:https?:|mailto:)/i.test(url)) {
			return `<a href="${url}" target="_blank" rel="noopener noreferrer">${label}</a>`;
		}
		if (url.startsWith('/') || url.startsWith('#')) {
			return `<a href="${url}" class="internal-link">${label}</a>`;
		}
		return m;
	});

	s = s.replace(/\*\*([^\n]+?)\*\*/g, '<strong>$1</strong>');
	// Single-asterisk emphasis, but not the leftover half of a `**` pair.
	s = s.replace(/(^|[^*])\*([^*\n]+?)\*(?!\*)/g, '$1<em>$2</em>');

	return s;
}

const BULLET = /^\s*[-*•]\s+(.*)$/;
const ORDERED = /^\s*(\d{1,3})[.)]\s+(.*)$/;
const HEADING = /^\s*(#{1,6})\s+(.*)$/;

/** Render a prefix of streamed markdown to sanitised HTML. */
function render(text: string): string {
	if (!text) return '';

	const out: string[] = [];
	let listOpen = false;
	let para: string[] = [];

	const closeList = () => {
		if (listOpen) {
			out.push('</ul>');
			listOpen = false;
		}
	};
	const flushPara = () => {
		if (para.length === 0) return;
		// Soft line breaks inside a paragraph are real breaks in model output —
		// it writes lists and stanzas that way — so keep them as <br>.
		out.push(`<p>${para.join('<br>')}</p>`);
		para = [];
	};

	for (const line of text.split('\n')) {
		if (line.trim() === '') {
			flushPara();
			closeList();
			continue;
		}

		const heading = HEADING.exec(line);
		if (heading) {
			flushPara();
			closeList();
			// h1 is the page title; demote everything by one so a document that opens
			// with `#` cannot outrank the route's own heading.
			const level = Math.min(4, Math.max(2, heading[1].length + 1));
			out.push(`<h${level}>${inline(heading[2])}</h${level}>`);
			continue;
		}

		const bullet = BULLET.exec(line);
		const ordered = bullet ? null : ORDERED.exec(line);
		if (bullet || ordered) {
			flushPara();
			if (!listOpen) {
				out.push('<ul>');
				listOpen = true;
			}
			if (bullet) {
				out.push(`<li>${inline(bullet[1])}</li>`);
			} else if (ordered) {
				// Neither `ol` nor `span` is in the sanitiser allowlist, so the model's own
				// numbering is kept as plain text and the bullet marker is suppressed via
				// `.md-ord`. Wrapping the number in a span would just be stripped.
				out.push(`<li class="md-ord">${ordered[1]}. ${inline(ordered[2])}</li>`);
			}
			continue;
		}

		closeList();
		para.push(inline(line));
	}

	flushPara();
	closeList();

	return sanitizeHtml(out.join(''));
}

/**
 * A renderer with its own one-entry cache.
 *
 * Each pane gets its own instance: the reasoning and answer panes re-render on
 * the same frames, so a single shared cache would be invalidated by the other
 * caller every time and never hit.
 */
export function createStreamRenderer(): (text: string) => string {
	let lastInput: string | null = null;
	let lastOutput = '';
	return (text: string) => {
		if (text === lastInput) return lastOutput;
		lastInput = text;
		lastOutput = render(text);
		return lastOutput;
	};
}

/**
 * Append the typewriter caret inside the final block element, so it sits at the
 * end of the last word rather than dropping to its own line. The markup is ours,
 * not model output, so it is added after sanitising.
 */
export function withCaret(html: string, caret: string): string {
	if (!caret) return html;
	if (!html) return caret;
	// Skip any trailing container close tags (`</ul>`) to find the innermost block
	// that actually holds text — otherwise the caret lands after the list instead of
	// after its final item, and drops onto its own line.
	const match = /<\/(p|li|h2|h3|h4)>(?:\s*<\/ul>)*\s*$/.exec(html);
	if (!match) return html + caret;
	return html.slice(0, match.index) + caret + match[0];
}
