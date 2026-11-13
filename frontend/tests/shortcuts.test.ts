import assert from 'node:assert/strict';
import { test } from 'node:test';
import { get } from 'svelte/store';
import {
	characterShortcuts,
	isCharacterKey,
	isComposingKey,
	readCharacterShortcuts,
	setCharacterShortcuts,
	shortcutAllowed
} from '../src/lib/services/shortcuts.ts';

function memoryStorage(initial: Record<string, string> = {}) {
	const data = new Map(Object.entries(initial));
	return {
		data,
		getItem: (key: string) => data.get(key) ?? null,
		setItem: (key: string, value: string) => void data.set(key, value)
	};
}

const throwingStorage = {
	getItem: () => {
		throw new Error('storage blocked');
	},
	setItem: () => {
		throw new Error('storage blocked');
	}
};

/** A keydown as the guard sees it, with no DOM behind it. */
function keydown(key: string, extra: Partial<KeyboardEvent> = {}): KeyboardEvent {
	return {
		key,
		keyCode: 0,
		isComposing: false,
		defaultPrevented: false,
		ctrlKey: false,
		metaKey: false,
		altKey: false,
		target: null,
		...extra
	} as KeyboardEvent;
}

// The guard looks at the event target's element type and asks the document
// whether a dialog is open; neither exists in Node.
Object.assign(globalThis, {
	HTMLElement: class {},
	document: { querySelector: () => null }
});

test('single characters are character keys; named keys are not', () => {
	for (const key of ['/', 'j', 'k', 'J', '?', '1', ' ', 'é', '😀']) {
		assert.equal(isCharacterKey(key), true, key);
	}
	for (const key of ['ArrowLeft', 'ArrowRight', 'Escape', 'Enter', 'Tab', 'F1', 'Dead', '']) {
		assert.equal(isCharacterKey(key), false, key);
	}
});

test('composition is detected from isComposing and from Safari keyCode 229', () => {
	assert.equal(isComposingKey({ isComposing: true, keyCode: 27 }), true);
	assert.equal(isComposingKey({ isComposing: false, keyCode: 229 }), true);
	assert.equal(isComposingKey({ isComposing: false, keyCode: 27 }), false);
});

test('the preference defaults to on and is off only when "off" was stored', () => {
	assert.equal(readCharacterShortcuts(null), true);
	assert.equal(readCharacterShortcuts(memoryStorage()), true);
	assert.equal(readCharacterShortcuts(memoryStorage({ characterShortcuts: 'on' })), true);
	assert.equal(readCharacterShortcuts(memoryStorage({ characterShortcuts: 'garbage' })), true);
	assert.equal(readCharacterShortcuts(memoryStorage({ characterShortcuts: 'off' })), false);
	assert.equal(readCharacterShortcuts(throwingStorage), true);
});

test('setting the preference updates the store and persists it', () => {
	const storage = memoryStorage();
	setCharacterShortcuts(false, storage);
	assert.equal(get(characterShortcuts), false);
	assert.equal(storage.data.get('characterShortcuts'), 'off');
	assert.equal(readCharacterShortcuts(storage), false);

	setCharacterShortcuts(true, storage);
	assert.equal(get(characterShortcuts), true);
	assert.equal(storage.data.get('characterShortcuts'), 'on');
});

test('a storage that throws still changes the preference for this visit', () => {
	setCharacterShortcuts(false, throwingStorage);
	assert.equal(get(characterShortcuts), false);
	setCharacterShortcuts(true, throwingStorage);
	assert.equal(get(characterShortcuts), true);
});

test('with shortcuts off the guard refuses character keys and keeps arrows and Escape', () => {
	setCharacterShortcuts(true, null);
	for (const key of ['/', 'j', 'k', 'ArrowLeft', 'ArrowRight', 'Escape']) {
		assert.equal(shortcutAllowed(keydown(key)), true, `on: ${key}`);
	}

	setCharacterShortcuts(false, null);
	for (const key of ['/', 'j', 'k']) {
		assert.equal(shortcutAllowed(keydown(key)), false, `off: ${key}`);
	}
	for (const key of ['ArrowLeft', 'ArrowRight', 'Escape']) {
		assert.equal(shortcutAllowed(keydown(key)), true, `off: ${key}`);
	}
	setCharacterShortcuts(true, null);
});

test('the guard refuses keys that belong to an IME, a chord or an earlier handler', () => {
	assert.equal(shortcutAllowed(keydown('ArrowLeft', { isComposing: true })), false);
	assert.equal(shortcutAllowed(keydown('ArrowLeft', { keyCode: 229 })), false);
	assert.equal(shortcutAllowed(keydown('k', { metaKey: true })), false);
	assert.equal(shortcutAllowed(keydown('k', { ctrlKey: true })), false);
	assert.equal(shortcutAllowed(keydown('/', { altKey: true })), false);
	assert.equal(shortcutAllowed(keydown('j', { defaultPrevented: true })), false);
});
