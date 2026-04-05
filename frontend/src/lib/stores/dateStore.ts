/**
 * Date store for managing the current selected date
 * Uses URL query params for navigation (?date=YYYY-MM-DD&category=XXX)
 */

import { writable, derived, get } from 'svelte/store';
import { browser } from '$app/environment';
import { goto } from '$app/navigation';
import { getAvailableDates, getLatestDate } from '$lib/services/dataLoader';

// Current selected date (YYYY-MM-DD format)
export const currentDate = writable<string>('');

// Available dates with data
export const availableDates = writable<string[]>([]);

// Loading state
export const isLoading = writable<boolean>(false);

// Derived store for previous/next available dates
export const navigation = derived(
	[currentDate, availableDates],
	([$currentDate, $availableDates]) => {
		const idx = $availableDates.indexOf($currentDate);
		return {
			hasPrevious: idx < $availableDates.length - 1,
			hasNext: idx > 0,
			previousDate: idx < $availableDates.length - 1 ? $availableDates[idx + 1] : null,
			nextDate: idx > 0 ? $availableDates[idx - 1] : null
		};
	}
);

// Derived store for checking if a date has data
export const hasDataForDate = derived(availableDates, ($availableDates) => {
	return (date: string) => $availableDates.includes(date);
});

/**
 * Initialize the date store
 */
export async function initializeDateStore(): Promise<void> {
	if (!browser) return;

	isLoading.set(true);
	try {
		const dates = await syncAvailableDates();
		const latest = dates[0] || (await getLatestDate());
		currentDate.update((current) => current || latest || '');
	} finally {
		isLoading.set(false);
	}
}

async function syncAvailableDates(forceRefresh: boolean = false): Promise<string[]> {
	const dates = await getAvailableDates(forceRefresh);
	availableDates.set(dates);
	return dates;
}

/**
 * Resolve and store the latest available date.
 */
export async function resolveLatestDate(forceRefresh: boolean = false): Promise<string> {
	if (!browser) return '';

	isLoading.set(true);
	try {
		const dates = await syncAvailableDates(forceRefresh);
		const latest = dates[0] || '';
		currentDate.set(latest);
		return latest;
	} finally {
		isLoading.set(false);
	}
}

function buildUrl(date: string | null, category?: string): string {
	const params = new URLSearchParams();
	if (date) {
		params.set('date', date);
	}
	if (category) {
		params.set('category', category);
	}

	const query = params.toString();
	return query ? `/?${query}` : '/';
}

/**
 * Navigate to a specific date (and optionally category) using URL query params
 */
export function navigateToDate(date: string, category?: string): void {
	if (!browser) return;
	currentDate.set(date);
	goto(buildUrl(date, category));
}

/**
 * Navigate to the previous available date using URL query params
 */
export function goToPreviousDate(category?: string): void {
	if (!browser) return;
	const current = get(currentDate);
	const dates = get(availableDates);

	const idx = dates.indexOf(current);
	if (idx < dates.length - 1) {
		const previousDate = dates[idx + 1];
		currentDate.set(previousDate);
		goto(buildUrl(previousDate, category));
	}
}

/**
 * Navigate to the next available date using URL query params
 */
export function goToNextDate(category?: string): void {
	if (!browser) return;
	const current = get(currentDate);
	const dates = get(availableDates);

	const idx = dates.indexOf(current);
	if (idx > 0) {
		const nextDate = dates[idx - 1];
		currentDate.set(nextDate);
		goto(buildUrl(nextDate, category));
	}
}

/**
 * Navigate to the latest date using URL query params
 */
export function goToLatestDate(category?: string): void {
	if (!browser) return;
	const dates = get(availableDates);

	if (dates.length > 0) {
		const latestDate = dates[0];
		currentDate.set(latestDate);
		goto(buildUrl(null, category));
	}
}
