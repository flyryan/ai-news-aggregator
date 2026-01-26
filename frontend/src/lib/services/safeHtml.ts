/**
 * Safe HTML Rendering Helper
 *
 * Provides a consistent way to safely render HTML content in Svelte components.
 * Always use safeHtml() instead of raw {@html} to ensure XSS protection.
 */

import { sanitizeHtml } from './sanitize';

/**
 * Safely render HTML content.
 *
 * Always use this function when rendering HTML with Svelte's {@html} directive.
 * Applies DOMPurify sanitization as defense-in-depth against XSS.
 *
 * @param content - The HTML content to sanitize (can be undefined/null)
 * @returns Sanitized HTML string safe for rendering
 *
 * @example
 * ```svelte
 * <script>
 *   import { safeHtml } from '$lib/services/safeHtml';
 * </script>
 *
 * {@html safeHtml(item.content_html)}
 * ```
 */
export function safeHtml(content: string | undefined | null): string {
	if (!content) return '';
	return sanitizeHtml(content);
}
