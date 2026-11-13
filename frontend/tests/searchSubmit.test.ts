import assert from 'node:assert/strict';
import { test } from 'node:test';
import { enterAction, searchKey, shouldOpenOnSettle } from '../src/lib/services/searchSubmit.ts';

test('the key separates query from category', () => {
	assert.notEqual(searchKey('agents', ''), searchKey('agents', 'news'));
	assert.notEqual(searchKey('news', ''), searchKey('', 'news'));
	assert.equal(searchKey('agents', 'news'), searchKey('agents', 'news'));
});

test('Enter opens settled results, defers an unanswered query, and otherwise does nothing', () => {
	assert.equal(enterAction({ hasQuery: true, settled: true, resultCount: 3 }), 'open');
	assert.equal(enterAction({ hasQuery: true, settled: true, resultCount: 0 }), 'none');
	assert.equal(enterAction({ hasQuery: true, settled: false, resultCount: 0 }), 'defer');
	// Results still on screen from an earlier query are not what Enter is for.
	assert.equal(enterAction({ hasQuery: true, settled: false, resultCount: 5 }), 'defer');
	assert.equal(enterAction({ hasQuery: false, settled: false, resultCount: 5 }), 'none');
	assert.equal(enterAction({ hasQuery: false, settled: true, resultCount: 0 }), 'none');
});

test('a deferred Enter opens the first result of the answer it was pressed for', () => {
	const key = searchKey('agents', '');
	assert.equal(
		shouldOpenOnSettle({ intentKey: key, responseKey: key, currentKey: key, resultCount: 4 }),
		true
	);
});

test('no pending Enter, no results, a stale response or changed text opens nothing', () => {
	const key = searchKey('agents', '');
	const other = searchKey('agentic', '');
	// Ordinary debounced search: nobody pressed Enter.
	assert.equal(
		shouldOpenOnSettle({ intentKey: null, responseKey: key, currentKey: key, resultCount: 4 }),
		false
	);
	// The answer came back empty.
	assert.equal(
		shouldOpenOnSettle({ intentKey: key, responseKey: key, currentKey: key, resultCount: 0 }),
		false
	);
	// A response to an earlier query arrives after Enter on the new text.
	assert.equal(
		shouldOpenOnSettle({ intentKey: other, responseKey: key, currentKey: other, resultCount: 4 }),
		false
	);
	// The text changed after Enter and the old answer arrives.
	assert.equal(
		shouldOpenOnSettle({ intentKey: key, responseKey: key, currentKey: other, resultCount: 4 }),
		false
	);
	// Same text, different category filter.
	assert.equal(
		shouldOpenOnSettle({
			intentKey: key,
			responseKey: key,
			currentKey: searchKey('agents', 'news'),
			resultCount: 4
		}),
		false
	);
});
