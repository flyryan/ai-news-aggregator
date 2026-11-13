/**
 * Guards shared by the site's single-key shortcuts (`/`, arrows, `j`/`k`).
 *
 * A bare-key shortcut must never take a keystroke the reader meant for
 * something else: text they are typing, a widget that already uses the key,
 * a browser or OS chord, or a dialog that is open on top of the page.
 *
 * Shortcuts that are a single character (`/`, `j`, `k`) can also be switched
 * off by the reader (WCAG 2.1.4): speech input types characters, and a stray
 * one must not be able to move the page. Arrow keys and Escape are not
 * character keys and are not covered by the switch.
 */

import { writable, get } from 'svelte/store';

const STORAGE_KEY = 'characterShortcuts';

type ReadableStorage = Pick<Storage, 'getItem'>;
type WritableStorage = Pick<Storage, 'setItem'>;

/** Whether the single-character shortcuts are on. On until the reader says otherwise. */
export const characterShortcuts = writable(true);

function browserStorage(): Storage | null {
	try {
		return typeof localStorage === 'undefined' ? null : localStorage;
	} catch {
		return null; // storage blocked by the browser
	}
}

/** The saved preference: off only when "off" was stored; anything else is on. */
export function readCharacterShortcuts(storage: ReadableStorage | null = browserStorage()): boolean {
	try {
		return storage?.getItem(STORAGE_KEY) !== 'off';
	} catch {
		return true;
	}
}

/** Load the saved preference. Called once on mount, so the prerendered markup still matches. */
export function initializeCharacterShortcuts(): void {
	characterShortcuts.set(readCharacterShortcuts());
}

/** Turn the single-character shortcuts on or off and remember the choice. */
export function setCharacterShortcuts(
	enabled: boolean,
	storage: WritableStorage | null = browserStorage()
): void {
	characterShortcuts.set(enabled);
	try {
		storage?.setItem(STORAGE_KEY, enabled ? 'on' : 'off');
	} catch {
		// Not saved (private mode, storage full): the choice still holds for this visit.
	}
}

/**
 * True for a key that types one character (a letter, digit, punctuation or
 * symbol). Named keys such as "ArrowLeft" and "Escape" are longer than one
 * code point.
 */
export function isCharacterKey(key: string): boolean {
	return [...key].length === 1;
}

/**
 * True while an IME is composing text: the key belongs to the IME (Enter
 * commits, Escape cancels a candidate, arrows pick one). 229 covers Safari,
 * which sends the keydown after compositionend with isComposing already false.
 */
export function isComposingKey(event: Pick<KeyboardEvent, 'isComposing' | 'keyCode'>): boolean {
	return event.isComposing || event.keyCode === 229;
}

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
 * nobody handled it already, the reader is not typing or composing, no dialog
 * is open, and, for a single-character key, the reader has not switched
 * those shortcuts off.
 */
export function shortcutAllowed(event: KeyboardEvent): boolean {
	if (event.defaultPrevented || isComposingKey(event)) return false;
	if (event.ctrlKey || event.metaKey || event.altKey) return false;
	if (isCharacterKey(event.key) && !get(characterShortcuts)) return false;
	if (isTypingTarget(event.target)) return false;
	if (document.querySelector('[role="dialog"], dialog[open]')) return false;
	return true;
}
