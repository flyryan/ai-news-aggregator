/**
 * Loaders for the LLM Replay artifacts.
 *
 * Mirrors the idiom in `dataLoader.ts`: module-level Map cache, absolute
 * `/data/...` paths, throw on !ok. The stream file is an optional enhancement —
 * every failure path here degrades to "no typewriter", never to a broken page.
 */

import { dataUrl } from './dataBase';
import type { ReplayIndex, ReplayPrompts, ReplayStream } from '$lib/types/replay';

const cache = new Map<string, unknown>();
const probeCache = new Map<string, Promise<boolean>>();
const inFlight = new Map<string, Promise<unknown>>();

export function replayIndexUrl(date: string): string {
	return dataUrl(`/data/${date}/replay-index.json`);
}

export function replayStreamUrl(date: string): string {
	return dataUrl(`/data/${date}/replay-stream.json.gz`);
}

export function replayPromptsUrl(date: string): string {
	return dataUrl(`/data/${date}/replay-prompts.json.gz`);
}

/** Load the replay index for a date. Throws when the date has no replay data. */
export async function loadReplayIndex(date: string): Promise<ReplayIndex> {
	const cacheKey = `replay-index-${date}`;
	if (cache.has(cacheKey)) {
		return cache.get(cacheKey) as ReplayIndex;
	}

	const response = await fetch(replayIndexUrl(date));
	if (!response.ok) {
		throw new Error(`No replay data for ${date} (${response.status})`);
	}

	const data = (await response.json()) as ReplayIndex;
	cache.set(cacheKey, data);
	return data;
}

/** True when this browser can inflate the gzipped stream file. */
export function supportsGzipDecompression(): boolean {
	return typeof globalThis !== 'undefined' && typeof globalThis.DecompressionStream === 'function';
}

async function inflateGzip(buffer: ArrayBuffer): Promise<string> {
	const stream = new Blob([buffer]).stream().pipeThrough(new DecompressionStream('gzip'));
	return await new Response(stream).text();
}

/**
 * Lazily load the per-call token deltas. Returns null (never throws) when the
 * file is absent, pruned, or the browser lacks `DecompressionStream`.
 */
export async function loadReplayStream(date: string): Promise<ReplayStream | null> {
	const cacheKey = `replay-stream-${date}`;
	if (cache.has(cacheKey)) {
		return cache.get(cacheKey) as ReplayStream | null;
	}

	let result: ReplayStream | null = null;
	try {
		if (!supportsGzipDecompression()) {
			throw new Error('DecompressionStream unavailable');
		}
		const response = await fetch(replayStreamUrl(date));
		if (!response.ok) {
			throw new Error(`stream ${response.status}`);
		}
		const buffer = await response.arrayBuffer();
		// Some dev servers helpfully decode Content-Encoding for us; detect the
		// gzip magic bytes and only inflate when the payload is still compressed.
		const head = new Uint8Array(buffer.slice(0, 2));
		const text =
			head[0] === 0x1f && head[1] === 0x8b
				? await inflateGzip(buffer)
				: new TextDecoder().decode(buffer);
		result = JSON.parse(text) as ReplayStream;
	} catch {
		result = null;
	}

	cache.set(cacheKey, result);
	return result;
}

/**
 * Lazily load the per-call prompts. Returns null (never throws) when the file is
 * absent — days published before prompt capture existed, and days whose prompts
 * have aged out of the retention window, both land here and must degrade to
 * "not retained" rather than to an error.
 *
 * This is the largest replay artifact (~600 KB gzipped), which is exactly why it
 * is not in the index: `/replay` renders without it and only pays for it when a
 * detail pane is actually opened. The in-flight map dedupes the burst of calls
 * that happens when a user clicks through several panes quickly.
 */
export async function loadReplayPrompts(date: string): Promise<ReplayPrompts | null> {
	const cacheKey = `replay-prompts-${date}`;
	if (cache.has(cacheKey)) {
		return cache.get(cacheKey) as ReplayPrompts | null;
	}
	const pending = inFlight.get(cacheKey);
	if (pending) return pending as Promise<ReplayPrompts | null>;

	const task = (async () => {
		let result: ReplayPrompts | null = null;
		try {
			if (!supportsGzipDecompression()) {
				throw new Error('DecompressionStream unavailable');
			}
			const response = await fetch(replayPromptsUrl(date));
			if (!response.ok) {
				throw new Error(`prompts ${response.status}`);
			}
			const buffer = await response.arrayBuffer();
			// Same magic-byte check as the stream: some dev servers decode
			// Content-Encoding for us, and double-inflating throws.
			const head = new Uint8Array(buffer.slice(0, 2));
			const text =
				head[0] === 0x1f && head[1] === 0x8b
					? await inflateGzip(buffer)
					: new TextDecoder().decode(buffer);
			result = JSON.parse(text) as ReplayPrompts;
		} catch {
			result = null;
		}
		cache.set(cacheKey, result);
		inFlight.delete(cacheKey);
		return result;
	})();

	inFlight.set(cacheKey, task);
	return task;
}

/**
 * Cheap existence check used to decide whether to surface the replay entry
 * link. Never throws, and caches the in-flight promise so repeated renders
 * don't refetch.
 */
export function hasReplayData(date: string): Promise<boolean> {
	const existing = probeCache.get(date);
	if (existing) return existing;

	const probe = (async () => {
		try {
			const response = await fetch(replayIndexUrl(date), { method: 'HEAD' });
			if (response.ok) return true;
			// Some static hosts answer HEAD with 405; fall back to a ranged GET.
			if (response.status === 405 || response.status === 501) {
				const get = await fetch(replayIndexUrl(date), { headers: { Range: 'bytes=0-0' } });
				return get.ok;
			}
			return false;
		} catch {
			return false;
		}
	})();

	probeCache.set(date, probe);
	return probe;
}

export function clearReplayCache(): void {
	cache.clear();
	probeCache.clear();
	inFlight.clear();
}
