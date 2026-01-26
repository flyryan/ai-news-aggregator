/**
 * Client-side markdown to HTML conversion
 * Mirrors the backend json_generator._markdown_to_html() function
 *
 * Output is sanitized to prevent XSS attacks.
 */

import { sanitizeHtml } from './sanitize';

/**
 * Convert markdown formatting to HTML.
 * Handles:
 * - [text](url) -> <a> (internal vs external links)
 * - **bold** -> <strong>
 * - #### headers -> <h4>
 * - - bullet lists -> <ul><li>
 * - Raw URLs -> clickable links
 *
 * Output is sanitized to prevent XSS attacks.
 */
export function markdownToHtml(text: string): string {
	if (!text) return '';

	// Convert raw URLs to markdown links first (before other processing)
	// Match URLs that aren't already in markdown link format
	text = text.replace(
		/(?<!\]\()(?<!\()(https?:\/\/[^\s<>\[\]()]+)/g,
		(url) => `<a href="${url}" target="_blank" rel="noopener noreferrer">${truncateUrl(url)}</a>`
	);

	// Convert markdown links [text](url)
	text = text.replace(/\[([^\]]+)\]\(([^)]+)\)/g, (_match, linkText, url) => {
		if (url.startsWith('/') || url.startsWith('#')) {
			// Internal link
			return `<a href="${url}" class="internal-link">${linkText}</a>`;
		} else {
			// External link
			return `<a href="${url}" target="_blank" rel="noopener noreferrer">${linkText}</a>`;
		}
	});

	// Convert **bold** to <strong>
	text = text.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');

	// Convert #### headers to <h4>
	text = text.replace(/^####\s+(.+)$/gm, '<h4>$1</h4>');

	// Convert bullet lists to <ul><li>
	const lines = text.split('\n');
	let inList = false;
	const result: string[] = [];

	for (const line of lines) {
		const stripped = line.trim();
		if (stripped.startsWith('- ')) {
			if (!inList) {
				result.push('<ul>');
				inList = true;
			}
			const bulletContent = stripped.slice(2);
			result.push(`<li>${bulletContent}</li>`);
		} else {
			if (inList) {
				result.push('</ul>');
				inList = false;
			}
			if (stripped) {
				// Check if already HTML tag
				if (stripped.startsWith('<h4>') || stripped.startsWith('<ul>')) {
					result.push(stripped);
				} else {
					result.push(stripped);
				}
			}
		}
	}

	// Close any open list
	if (inList) {
		result.push('</ul>');
	}

	const html = result.join('\n');

	// Sanitize before returning to prevent XSS
	return sanitizeHtml(html);
}

/**
 * Truncate long URLs for display
 */
function truncateUrl(url: string): string {
	try {
		const parsed = new URL(url);
		const path = parsed.pathname + parsed.search;
		if (path.length > 40) {
			return parsed.hostname + path.slice(0, 30) + '...';
		}
		return parsed.hostname + path;
	} catch {
		return url.length > 50 ? url.slice(0, 47) + '...' : url;
	}
}

/**
 * Strip markdown syntax to get plain text (for truncated previews)
 */
export function stripMarkdown(text: string): string {
	if (!text) return '';

	// Remove markdown links [text](url) -> text
	text = text.replace(/\[([^\]]+)\]\([^)]+\)/g, '$1');

	// Remove **bold** -> bold
	text = text.replace(/\*\*([^*]+)\*\*/g, '$1');

	// Remove #### headers -> text
	text = text.replace(/^#{1,6}\s+/gm, '');

	// Remove bullet points
	text = text.replace(/^-\s+/gm, '');

	// Clean up multiple spaces/newlines
	text = text.replace(/\n{2,}/g, ' ').replace(/\s{2,}/g, ' ');

	return text.trim();
}
