/**
 * Data loader service for fetching JSON data from the backend
 */

import type { DataIndex, DaySummary, CategoryData, Category } from '$lib/types';

const cache = new Map<string, unknown>();

/**
 * Load the main data index
 */
export async function loadIndex(): Promise<DataIndex> {
	const cacheKey = 'index';
	if (cache.has(cacheKey)) {
		return cache.get(cacheKey) as DataIndex;
	}

	const response = await fetch('/data/index.json');
	if (!response.ok) {
		throw new Error(`Failed to load data index: ${response.status}`);
	}

	const data = await response.json();
	cache.set(cacheKey, data);
	return data;
}

/**
 * Load summary data for a specific date
 */
export async function loadDaySummary(date: string): Promise<DaySummary> {
	const cacheKey = `summary-${date}`;
	if (cache.has(cacheKey)) {
		return cache.get(cacheKey) as DaySummary;
	}

	const response = await fetch(`/data/${date}/summary.json`);
	if (!response.ok) {
		throw new Error(`Failed to load summary for ${date}: ${response.status}`);
	}

	const data = await response.json();
	cache.set(cacheKey, data);
	return data;
}

/**
 * Load category data for a specific date
 */
export async function loadCategoryData(date: string, category: Category): Promise<CategoryData> {
	const cacheKey = `category-${date}-${category}`;
	if (cache.has(cacheKey)) {
		return cache.get(cacheKey) as CategoryData;
	}

	const response = await fetch(`/data/${date}/${category}.json`);
	if (!response.ok) {
		throw new Error(`Failed to load ${category} data for ${date}: ${response.status}`);
	}

	const data = await response.json();
	cache.set(cacheKey, data);
	return data;
}

/**
 * Get the latest available date
 */
export async function getLatestDate(): Promise<string | null> {
	try {
		const index = await loadIndex();
		return index.latestDate;
	} catch {
		return null;
	}
}

/**
 * Get all available dates
 */
export async function getAvailableDates(): Promise<string[]> {
	try {
		const index = await loadIndex();
		return index.dates.map((d) => d.date);
	} catch {
		return [];
	}
}

/**
 * Check if a date has data
 */
export async function hasDataForDate(date: string): Promise<boolean> {
	try {
		const dates = await getAvailableDates();
		return dates.includes(date);
	} catch {
		return false;
	}
}

/**
 * Preload adjacent dates for faster navigation
 */
export async function preloadAdjacentDates(currentDate: string): Promise<void> {
	try {
		const dates = await getAvailableDates();
		const idx = dates.indexOf(currentDate);

		// Preload previous and next dates (don't await, let them load in background)
		if (idx > 0) {
			loadDaySummary(dates[idx - 1]).catch(() => {});
		}
		if (idx < dates.length - 1) {
			loadDaySummary(dates[idx + 1]).catch(() => {});
		}
	} catch {
		// Ignore errors in preloading
	}
}

/**
 * Clear the cache
 */
export function clearCache(): void {
	cache.clear();
}
