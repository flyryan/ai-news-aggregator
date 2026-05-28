/**
 * Search service.
 *
 * Loads a compact corpus (web/data/search-corpus.json) and builds a MiniSearch
 * index inside a Web Worker so the ~1-2s build never blocks the main thread.
 * If the worker or corpus is unavailable, falls back to a main-thread substring
 * search over the same corpus.
 */

import type { SearchDocument, SearchResult, Category } from '$lib/types';

interface CorpusDoc extends SearchDocument {
	ref: string;
}

let worker: Worker | null = null;
let initialized = false;
let docCount = 0;

// Fallback state (only populated if the worker path fails).
let fallbackDocs: CorpusDoc[] | null = null;

let nextSearchId = 0;
const pending = new Map<number, (results: SearchResult[]) => void>();

function spawnWorker(): Promise<boolean> {
	return new Promise((resolve) => {
		try {
			worker = new Worker(new URL('./searchWorker.ts', import.meta.url), { type: 'module' });
		} catch {
			resolve(false);
			return;
		}

		const onMessage = (event: MessageEvent) => {
			const msg = event.data;
			if (msg.type === 'ready') {
				docCount = msg.count;
				initialized = true;
				resolve(true);
			} else if (msg.type === 'error') {
				console.warn('Search worker error:', msg.message);
				resolve(false);
			} else if (msg.type === 'results') {
				const cb = pending.get(msg.id);
				if (cb) {
					pending.delete(msg.id);
					cb(msg.results as SearchResult[]);
				}
			}
		};

		worker.addEventListener('message', onMessage);
		worker.addEventListener('error', () => resolve(false));
		worker.postMessage({ type: 'init' });
	});
}

async function initializeFallback(): Promise<boolean> {
	try {
		const response = await fetch('/data/search-corpus.json');
		if (!response.ok) return false;
		fallbackDocs = await response.json();
		docCount = fallbackDocs?.length ?? 0;
		initialized = true;
		return true;
	} catch {
		return false;
	}
}

export async function initializeSearch(): Promise<boolean> {
	if (initialized) return true;

	if (await spawnWorker()) return true;

	// Worker failed — tear it down and fall back to main-thread search.
	worker?.terminate();
	worker = null;
	return await initializeFallback();
}

export function search(
	query: string,
	category?: Category,
	limit: number = 50
): Promise<SearchResult[]> {
	if (!initialized || !query.trim()) return Promise.resolve([]);

	if (worker) {
		return new Promise((resolve) => {
			const id = nextSearchId++;
			pending.set(id, resolve);
			worker!.postMessage({ type: 'search', id, query, category, limit });
		});
	}

	return Promise.resolve(simpleSearch(query, category, limit));
}

function simpleSearch(query: string, category?: Category, limit: number = 50): SearchResult[] {
	if (!fallbackDocs) return [];

	const queryLower = query.toLowerCase();
	const results: SearchResult[] = [];

	for (const doc of fallbackDocs) {
		if (category && doc.category !== category) continue;

		const titleMatch = doc.title?.toLowerCase().includes(queryLower);
		const summaryMatch = doc.summary?.toLowerCase().includes(queryLower);
		const sourceMatch = doc.source?.toLowerCase().includes(queryLower);

		if (titleMatch || summaryMatch || sourceMatch) {
			let score = 0;
			if (titleMatch) score += 10;
			if (summaryMatch) score += 5;
			if (sourceMatch) score += 2;

			results.push({ ref: doc.ref, score, doc });
		}
	}

	results.sort((a, b) => {
		const scoreDiff = b.score - a.score;
		if (scoreDiff !== 0) return scoreDiff;
		return (b.doc?.importance || 0) - (a.doc?.importance || 0);
	});

	return results.slice(0, limit);
}

export function getSuggestions(query: string, limit: number = 5): string[] {
	if (!initialized || query.length < 2 || !fallbackDocs) return [];

	const queryLower = query.toLowerCase();
	const suggestions = new Set<string>();

	for (const doc of fallbackDocs) {
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

export function isSearchInitialized(): boolean {
	return initialized;
}

export function getDocumentCount(): number {
	return docCount;
}
