/**
 * Enter in the search field, decided without a DOM.
 *
 * Results arrive after a debounce and a worker round trip, so Enter can be
 * pressed before the answer to what is in the box exists. The intent to open
 * the best match is then kept, for that exact query and filter only, and is
 * honoured when their answer comes back.
 */

/** Identifies a query + category filter pair. */
export const searchKey = (query: string, category: string): string => `${category}\n${query}`;

export type EnterAction =
	/** The results on screen answer the box: open the selected (or best) one. */
	| 'open'
	/** No answer for the box yet: search now and remember the intent. */
	| 'defer'
	/** Nothing to open. */
	| 'none';

export function enterAction(state: {
	hasQuery: boolean;
	settled: boolean;
	resultCount: number;
}): EnterAction {
	if (!state.hasQuery) return 'none';
	if (!state.settled) return 'defer';
	return state.resultCount > 0 ? 'open' : 'none';
}

/**
 * Whether a response that just arrived should open its first result.
 *
 * Only when Enter was pressed for exactly this query + filter, the box still
 * holds them, and there is something to open. A stale response never opens
 * anything.
 */
export function shouldOpenOnSettle(state: {
	/** Key captured when Enter was pressed, or null when no submit is pending. */
	intentKey: string | null;
	/** Key the arriving response answers. */
	responseKey: string;
	/** Key for what is in the box now. */
	currentKey: string;
	resultCount: number;
}): boolean {
	return (
		state.intentKey !== null &&
		state.intentKey === state.responseKey &&
		state.responseKey === state.currentKey &&
		state.resultCount > 0
	);
}
