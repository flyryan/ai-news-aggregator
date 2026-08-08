/**
 * Item lookup by id, for resolving internal `#item-{id}` links to their card.
 *
 * Kept separate from dataLoader: that module owns fetching and the preview-origin
 * URL rules, and shouldn't also own a per-item index.
 *
 * Most lookups are synchronous hits. `summary.json` ships full NewsItem objects
 * in every category's `top_items`, which covers the large majority of the links
 * the enricher writes — those resolve from memory with no fetch and no skeleton.
 * The remainder are long-tail items (mostly Reddit and Social, whose categories
 * run to hundreds of items) and need the category file.
 */

import type { Category, NewsItem } from '$lib/types';
import { loadCategoryData } from './dataLoader';

const items = new Map<string, NewsItem>();

const keyFor = (date: string, id: string) => `${date}:${id}`;

export function registerItems(date: string, list: NewsItem[] | undefined | null): void {
	if (!list) return;
	for (const item of list) {
		if (item?.id) items.set(keyFor(date, item.id), item);
	}
}

/** Synchronous lookup. Null means "not indexed yet", not "does not exist". */
export function peekItem(date: string, id: string): NewsItem | null {
	return items.get(keyFor(date, id)) ?? null;
}

/**
 * Resolve an item, fetching its category file if it isn't indexed yet.
 * Null means the id isn't in that category at all — the enricher can outrun the
 * data, so callers must handle it.
 */
export async function resolveItem(
	date: string,
	category: Category,
	id: string
): Promise<NewsItem | null> {
	const cached = peekItem(date, id);
	if (cached) return cached;

	try {
		const data = await loadCategoryData(date, category);
		registerItems(date, data.items);
	} catch {
		return null;
	}

	return peekItem(date, id);
}
