/**
 * Theme store for managing dark/light mode
 */

import { writable, derived } from 'svelte/store';
import { browser } from '$app/environment';

export type Theme = 'light' | 'dark' | 'system';

// Stored theme preference
const storedTheme = browser ? (localStorage.getItem('theme') as Theme) : null;
export const themePreference = writable<Theme>(storedTheme || 'system');

// Actual resolved theme based on preference and system
export const resolvedTheme = derived(themePreference, ($preference) => {
	if (!browser) return 'light';

	if ($preference === 'system') {
		return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
	}

	return $preference;
});

// Is dark mode active
export const isDark = derived(resolvedTheme, ($theme) => $theme === 'dark');

/**
 * Set the theme preference
 */
export function setTheme(theme: Theme): void {
	themePreference.set(theme);

	if (browser) {
		localStorage.setItem('theme', theme);
		applyTheme(theme);
	}
}

/**
 * Toggle between light and dark mode
 */
export function toggleTheme(): void {
	let current: Theme = 'system';
	themePreference.subscribe((v) => (current = v))();

	// If system, detect current and toggle to opposite
	if (current === 'system') {
		const systemDark = browser && window.matchMedia('(prefers-color-scheme: dark)').matches;
		setTheme(systemDark ? 'light' : 'dark');
	} else {
		setTheme(current === 'dark' ? 'light' : 'dark');
	}
}

/**
 * Apply theme to document
 */
function applyTheme(theme: Theme): void {
	if (!browser) return;

	const isDark =
		theme === 'dark' || (theme === 'system' && window.matchMedia('(prefers-color-scheme: dark)').matches);

	document.documentElement.classList.toggle('dark', isDark);
}

/**
 * Initialize theme on page load
 */
export function initializeTheme(): void {
	if (!browser) return;

	// Apply stored or default theme
	let theme: Theme = 'system';
	themePreference.subscribe((v) => (theme = v))();
	applyTheme(theme);

	// Listen for system theme changes
	window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => {
		let current: Theme = 'system';
		themePreference.subscribe((v) => (current = v))();

		if (current === 'system') {
			applyTheme('system');
		}
	});
}
