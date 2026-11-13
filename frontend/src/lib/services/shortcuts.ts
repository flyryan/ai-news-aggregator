/**
 * Guards shared by the site's single-key shortcuts (`/`, arrows, `j`/`k`).
 *
 * A bare-key shortcut must never take a keystroke the reader meant for
 * something else: text they are typing, a widget that already uses the key,
 * a browser or OS chord, or a dialog that is open on top of the page.
 */

const WIDGET_ROLES = new Set([
	'textbox',
	'searchbox',
	'combobox',
	'listbox',
	'option',
	'slider',
	'spinbutton',
	'menu',
	'menuitem',
	'tablist',
	'tab',
	'radio',
	'radiogroup',
	'grid',
	'tree'
]);

/** True when the event target is somewhere the key already has a meaning. */
export function isTypingTarget(target: EventTarget | null): boolean {
	if (!(target instanceof HTMLElement)) return false;
	const tag = target.tagName;
	if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return true;
	if (target.isContentEditable) return true;
	const role = target.getAttribute('role');
	return role !== null && WIDGET_ROLES.has(role);
}

/**
 * True when a bare-key shortcut may act on this event: no Ctrl/Meta/Alt chord,
 * nobody handled it already, the reader is not typing, and no dialog is open.
 */
export function shortcutAllowed(event: KeyboardEvent): boolean {
	if (event.defaultPrevented || event.isComposing) return false;
	if (event.ctrlKey || event.metaKey || event.altKey) return false;
	if (isTypingTarget(event.target)) return false;
	if (document.querySelector('[role="dialog"], dialog[open]')) return false;
	return true;
}
