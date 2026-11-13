import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
	displayAuthor,
	formatCount,
	plainSnippet,
	stripArxivBoilerplate
} from '../src/lib/services/itemDisplay.ts';

test('counts get thousands separators from 1,000 up', () => {
	assert.equal(formatCount(1984), '1,984');
	assert.equal(formatCount(881), '881');
	assert.equal(formatCount(1000), '1,000');
	assert.equal(formatCount(0), '0');
	assert.equal(formatCount(undefined), '0');
});

test('strips the arXiv feed prefix from text and from pre-rendered HTML', () => {
	assert.equal(
		stripArxivBoilerplate('arXiv:2611.17146v1 Announce Type: new  Abstract: The proliferation'),
		'The proliferation'
	);
	assert.equal(
		stripArxivBoilerplate('arXiv:2611.15094v1 Announce Type: cross  Abstract: Frontier agents'),
		'Frontier agents'
	);
	assert.equal(
		stripArxivBoilerplate(
			'<p>arXiv:2611.15094v2 Announce Type: replace-cross  Abstract: Medical imaging</p>'
		),
		'<p>Medical imaging</p>'
	);
	// Only a leading prefix is plumbing; a citation in the body is content.
	const cited = 'We cite arXiv:2611.17146v1 Announce Type: new';
	assert.equal(stripArxivBoilerplate(cited), cited);
	assert.equal(stripArxivBoilerplate('A plain news excerpt.'), 'A plain news excerpt.');
	assert.equal(stripArxivBoilerplate(undefined), '');
});

test('"Unknown" and blank are not authors', () => {
	assert.equal(displayAuthor('Unknown'), null);
	assert.equal(displayAuthor(' unknown '), null);
	assert.equal(displayAuthor(''), null);
	assert.equal(displayAuthor(undefined), null);
	assert.equal(displayAuthor('Caroline Haskins, Lauren Goode'), 'Caroline Haskins, Lauren Goode');
	assert.equal(displayAuthor('u/anonymousStrang3r'), 'u/anonymousStrang3r');
});

test('search snippets read as plain text', () => {
	assert.equal(
		plainSnippet(
			"Building on yesterday's [Reddit](/?date=2026-11-12&category=reddit#item-aa51422c3a6e) discussion — Nvidia's chief resigned o..."
		),
		"Building on yesterday's Reddit discussion — Nvidia's chief resigned o..."
	);
	assert.equal(
		plainSnippet('**TechCrunch** broke the story; see [the **memo**](https://example.com/a_b).'),
		'TechCrunch broke the story; see the memo.'
	);
	// The 200-character cut can land inside a link or a bold pair.
	assert.equal(plainSnippet('Later confirmed by [The Verge](/?date=2026-11-12&cat...'), 'Later confirmed by The Verge…');
	assert.equal(plainSnippet('Later confirmed by [The Ver...'), 'Later confirmed by The Ver…');
	assert.equal(plainSnippet('First spotted on the **AI Alignment For...'), 'First spotted on the AI Alignment For...');
	// Single asterisks and brackets that are not links are content.
	assert.equal(plainSnippet('compares Branch-and-Bound and A* within c*n/log n'), 'compares Branch-and-Bound and A* within c*n/log n');
	assert.equal(plainSnippet('scores [0, 1] on |<q|k>|^4'), 'scores [0, 1] on |<q|k>|^4');
	assert.equal(plainSnippet('arXiv:2611.17146v1 Announce Type: new  Abstract: We show'), 'We show');
	assert.equal(plainSnippet(undefined), '');
});
