/**
 * HTML Sanitization Module
 *
 * Uses DOMPurify to sanitize HTML and prevent XSS attacks.
 * This is a defense-in-depth measure - the backend also sanitizes,
 * but we sanitize again on the client to protect against any
 * data that bypasses the backend (e.g., cached data, direct API calls).
 */

import DOMPurify from 'dompurify';

// Allowlist of safe HTML tags (matches backend ALLOWED_TAGS)
const ALLOWED_TAGS = ['a', 'strong', 'em', 'p', 'ul', 'li', 'h2', 'h3', 'h4', 'br'];

// Allowlist of safe HTML attributes (matches backend ALLOWED_ATTRIBUTES)
const ALLOWED_ATTR = ['href', 'class', 'target', 'rel'];

// Safe URL protocols (blocks javascript:, data:, etc.)
// Also allows relative URLs starting with /, #, or ?
const ALLOWED_URI_REGEXP = /^(?:https?:|mailto:|\/|#|\?)/i;

/**
 * Sanitize HTML string to prevent XSS attacks.
 *
 * Uses an allowlist approach - only explicitly permitted tags and attributes
 * are allowed. Blocks dangerous URL protocols like javascript: and data:.
 *
 * @param html - The HTML string to sanitize
 * @returns Sanitized HTML string safe for rendering with {@html}
 */
export function sanitizeHtml(html: string): string {
	if (!html) return '';

	return DOMPurify.sanitize(html, {
		ALLOWED_TAGS,
		ALLOWED_ATTR,
		ALLOW_DATA_ATTR: false,
		ALLOWED_URI_REGEXP
	});
}
