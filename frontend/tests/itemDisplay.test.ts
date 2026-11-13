import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
	authorDisplay,
	destinationName,
	importanceBand,
	stripArxivBoilerplate
} from '../src/lib/services/itemDisplay.ts';

test('strips the arXiv feed prefix from text and from pre-rendered HTML', () => {
	assert.equal(
		stripArxivBoilerplate('arXiv:2611.17146v1 Announce Type: new  Abstract: The proliferation'),
		'The proliferation'
	);
	assert.equal(
		stripArxivBoilerplate('<p>arXiv:2611.15094v2 Announce Type: replace-cross  Abstract: Medical imaging</p>'),
		'<p>Medical imaging</p>'
	);
	assert.equal(stripArxivBoilerplate('We cite arXiv:2611.17146v1 Announce Type: new'), 'We cite arXiv:2611.17146v1 Announce Type: new');
	assert.equal(stripArxivBoilerplate(undefined), '');
});

test('author lists: no "Unknown", first author plus et al. past three names', () => {
	assert.equal(authorDisplay('Unknown'), null);
	assert.equal(authorDisplay(''), null);
	assert.deepEqual(authorDisplay('Caroline Haskins, Lauren Goode'), {
		short: 'Caroline Haskins, Lauren Goode',
		full: 'Caroline Haskins, Lauren Goode',
		truncated: false
	});
	const long = authorDisplay('A One, B Two, C Three, D Four');
	assert.equal(long?.short, 'A One et al.');
	assert.equal(long?.full, 'A One, B Two, C Three, D Four');
	assert.equal(long?.truncated, true);
});

test('destination comes from the link host', () => {
	assert.equal(destinationName('https://arstechnica.com/ai/2026/11/x'), 'Ars Technica');
	assert.equal(destinationName('http://export.arxiv.org/abs/2611.17146'), 'arXiv');
	assert.equal(destinationName('https://www.example.org/post'), 'example.org');
	assert.equal(destinationName('not a url'), null);
	assert.equal(destinationName(undefined), null);
});

test('bands match the card tiers', () => {
	assert.equal(importanceBand(93), 'high');
	assert.equal(importanceBand(80), 'high');
	assert.equal(importanceBand(79.9), 'medium');
	assert.equal(importanceBand(40), 'standard');
	assert.equal(importanceBand(12), 'low');
});
