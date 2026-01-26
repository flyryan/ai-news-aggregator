/**
 * Search index service using Lunr.js
 */

import type { SearchDocument, SearchResult, Category } from '$lib/types';
import lunr from 'lunr';

let index: lunr.Index | null = null;
let documents: Map<string, SearchDocument> = new Map();
let initialized = false;

/**
 * Initialize the search index
 */
export async function initializeSearch(): Promise<boolean> {
	if (initialized) return true;

	try {
		// Try to load pre-built index
		const [indexResponse, docsResponse] = await Promise.all([
			fetch('/data/search-index.json'),
			fetch('/data/search-documents.json')
		]);

		if (indexResponse.ok && docsResponse.ok) {
			// Load pre-built lunr index
			const indexData = await indexResponse.json();
			index = lunr.Index.load(indexData);

			// Load document lookup
			const docsData = await docsResponse.json();
			documents = new Map(Object.entries(docsData));

			initialized = true;
			return true;
		}

		// Fall back to simple search if lunr index not available
		return await initializeSimpleSearch();
	} catch (e) {
		console.warn('Failed to load search index:', e);
		return await initializeSimpleSearch();
	}
}

/**
 * Initialize simple search as fallback
 */
async function initializeSimpleSearch(): Promise<boolean> {
	try {
		const response = await fetch('/data/search-documents.json');
		if (!response.ok) return false;

		const docsData = await response.json();
		documents = new Map(Object.entries(docsData));

		initialized = true;
		return true;
	} catch {
		return false;
	}
}

/**
 * Search for documents
 */
export function search(query: string, category?: Category, limit: number = 50): SearchResult[] {
	if (!initialized || !query.trim()) return [];

	const results: SearchResult[] = [];

	if (index) {
		// Use lunr index
		try {
			const lunrResults = index.search(query);

			for (const result of lunrResults) {
				const doc = documents.get(result.ref);
				if (doc && (!category || doc.category === category)) {
					results.push({
						ref: result.ref,
						score: result.score,
						doc
					});
				}
			}
		} catch {
			// Fall back to simple search on lunr error
			return simpleSearch(query, category, limit);
		}
	} else {
		// Simple search fallback
		return simpleSearch(query, category, limit);
	}

	// Sort by score descending, then by importance
	results.sort((a, b) => {
		const scoreDiff = b.score - a.score;
		if (Math.abs(scoreDiff) > 0.1) return scoreDiff;
		return (b.doc?.importance || 0) - (a.doc?.importance || 0);
	});

	return results.slice(0, limit);
}

/**
 * Simple string matching search
 */
function simpleSearch(query: string, category?: Category, limit: number = 50): SearchResult[] {
	const queryLower = query.toLowerCase();
	const results: SearchResult[] = [];

	for (const [id, doc] of documents) {
		if (category && doc.category !== category) continue;

		const titleMatch = doc.title?.toLowerCase().includes(queryLower);
		const summaryMatch = doc.summary?.toLowerCase().includes(queryLower);
		const sourceMatch = doc.source?.toLowerCase().includes(queryLower);

		if (titleMatch || summaryMatch || sourceMatch) {
			// Calculate a simple relevance score
			let score = 0;
			if (titleMatch) score += 10;
			if (summaryMatch) score += 5;
			if (sourceMatch) score += 2;

			results.push({
				ref: id,
				score,
				doc
			});
		}
	}

	// Sort by score and importance
	results.sort((a, b) => {
		const scoreDiff = b.score - a.score;
		if (scoreDiff !== 0) return scoreDiff;
		return (b.doc?.importance || 0) - (a.doc?.importance || 0);
	});

	return results.slice(0, limit);
}

/**
 * Get search suggestions based on partial query
 */
export function getSuggestions(query: string, limit: number = 5): string[] {
	if (!initialized || query.length < 2) return [];

	const queryLower = query.toLowerCase();
	const suggestions = new Set<string>();

	for (const doc of documents.values()) {
		// Extract potential suggestions from titles
		const words = doc.title?.toLowerCase().split(/\s+/) || [];
		for (const word of words) {
			if (word.startsWith(queryLower) && word.length > query.length) {
				suggestions.add(word);
				if (suggestions.size >= limit) break;
			}
		}
		if (suggestions.size >= limit) break;
	}

	return Array.from(suggestions);
}

/**
 * Check if search is initialized
 */
export function isSearchInitialized(): boolean {
	return initialized;
}

/**
 * Get total document count
 */
export function getDocumentCount(): number {
	return documents.size;
}
