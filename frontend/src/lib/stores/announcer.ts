/**
 * One polite live region for the whole app (rendered in +layout.svelte).
 * Components call announce() for changes a screen reader would otherwise miss,
 * such as "Link copied" after Share.
 */
import { writable } from 'svelte/store';

export const announcement = writable('');

let clearTimer: ReturnType<typeof setTimeout> | undefined;

export function announce(message: string): void {
	// Clear first so repeating the same message is announced again.
	announcement.set('');
	clearTimeout(clearTimer);
	setTimeout(() => announcement.set(message), 30);
	clearTimer = setTimeout(() => announcement.set(''), 4000);
}
