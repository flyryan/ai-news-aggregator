<script lang="ts">
	import { onMount, createEventDispatcher } from 'svelte';
	import { goto } from '$app/navigation';
	import { search, initializeSearch, isSearchInitialized } from '$lib/services/searchIndex';
	import type { SearchResult, Category } from '$lib/types';
	import { CATEGORY_CONFIG } from '$lib/types';
	import SearchResults from './SearchResults.svelte';

	const dispatch = createEventDispatcher();

	export let placeholder = 'Search articles, research, discussions...';
	export let autofocus = false;

	const listboxId = 'site-search-results';
	const optionId = (index: number) => `${listboxId}-option-${index}`;

	let query = '';
	let category: Category | '' = '';
	let results: SearchResult[] = [];
	let isOpen = false;
	let selectedIndex = -1;
	let inputElement: HTMLInputElement;

	// The index is built in a worker from a ~50k-document corpus, which takes a
	// moment on first open. Until it is ready a query has no answer yet — that is
	// not the same as "no results", and the panel says which one it is.
	let indexState: 'loading' | 'ready' | 'error' = isSearchInitialized() ? 'ready' : 'loading';

	// Which query+category the current `results` answer. "No results" is only
	// true once the search for what is in the box has actually come back.
	let settledKey: string | null = null;
	const keyFor = (q: string, c: string) => `${c}\n${q}`;

	const categories: (Category | '')[] = ['', 'news', 'research', 'social', 'reddit'];

	// Older corpora used `papers` for what is now the research category.
	const categoryMapping: Record<string, Category> = { papers: 'research' };

	async function loadIndex() {
		indexState = isSearchInitialized() ? 'ready' : 'loading';
		const ok = await initializeSearch();
		indexState = ok ? 'ready' : 'error';
		// Answer whatever was typed while the index was building.
		if (ok && query.length >= 2) void performSearch();
	}

	onMount(() => {
		// Focus first: the reader can type while the index builds.
		if (autofocus) inputElement?.focus();
		void loadIndex();
	});

	/** Lets the header put the caret in the field when `/` is pressed again. */
	export function focusInput() {
		inputElement?.focus();
	}

	// Debounced search
	let searchTimeout: ReturnType<typeof setTimeout>;
	$: {
		clearTimeout(searchTimeout);
		if (query.length >= 2) {
			searchTimeout = setTimeout(() => {
				performSearch();
			}, 150);
		} else {
			results = [];
			settledKey = null;
		}
	}

	async function performSearch() {
		if (indexState !== 'ready') return; // loadIndex() re-runs the query once ready
		const key = keyFor(query, category);
		const found = await search(query, category || undefined);
		// Ignore stale responses if the query or filter changed while awaiting.
		if (key !== keyFor(query, category)) return;
		results = found;
		settledKey = key;
		selectedIndex = -1;
	}

	$: hasQuery = query.length >= 2;
	$: settled = settledKey === keyFor(query, category);
	$: showList = isOpen && results.length > 0;
	$: panel = !isOpen || !hasQuery || results.length > 0
		? null
		: indexState === 'loading'
			? 'loading'
			: indexState === 'error'
				? 'error'
				: settled
					? 'empty'
					: null;

	// One polite announcement per state change, for readers who cannot see the panel.
	$: announcement = !hasQuery
		? ''
		: indexState === 'loading'
			? 'Building search index…'
			: indexState === 'error'
				? 'Search is unavailable. The search index could not be loaded.'
				: !settled
					? ''
					: results.length === 0
						? `No results found for ${query}`
						: `${results.length} result${results.length === 1 ? '' : 's'}. Use the up and down arrow keys to review, Enter to open.`;

	function handleFocus() {
		isOpen = true;
	}

	function handleBlur(event: FocusEvent) {
		// Delay closing to allow click on results
		setTimeout(() => {
			const target = event.relatedTarget as HTMLElement;
			if (!target?.closest('.search-container')) {
				isOpen = false;
			}
		}, 200);
	}

	function handleKeydown(event: KeyboardEvent) {
		switch (event.key) {
			case 'ArrowDown':
				event.preventDefault();
				isOpen = true;
				selectedIndex = Math.min(selectedIndex + 1, results.length - 1);
				break;
			case 'ArrowUp':
				event.preventDefault();
				selectedIndex = Math.max(selectedIndex - 1, -1);
				break;
			case 'Enter':
				event.preventDefault();
				if (!settled) {
					// Results on screen (if any) answer an earlier query; get the real
					// ones now instead of opening something the reader did not ask for.
					clearTimeout(searchTimeout);
					if (hasQuery) void performSearch();
				} else if (results.length > 0) {
					// With nothing arrow-selected, Enter opens the best match.
					selectResult(results[Math.max(selectedIndex, 0)]);
				}
				break;
			case 'Escape':
				isOpen = false;
				dispatch('close');
				break;
		}
	}

	function selectResult(result: SearchResult) {
		const doc = result.doc;
		if (doc) {
			const cat = categoryMapping[doc.category] || doc.category;
			// Land on the item itself, not the top of its category page.
			goto(`/?date=${doc.date}&category=${cat}#item-${encodeURIComponent(doc.id)}`);
			isOpen = false;
			query = '';
			dispatch('select', result);
		}
	}

	function handleCategoryChange() {
		if (query.length >= 2) {
			performSearch();
		}
	}

	function clearSearch() {
		query = '';
		results = [];
		inputElement?.focus();
	}
</script>

<div class="search-container relative">
	<div class="flex gap-2">
		<!-- Category filter -->
		<select
			bind:value={category}
			on:change={handleCategoryChange}
			aria-label="Filter search by category"
			class="input w-auto min-w-[120px] text-sm"
		>
			<option value="">All Categories</option>
			{#each categories.filter(c => c !== '') as cat}
				<option value={cat}>{CATEGORY_CONFIG[cat].title}</option>
			{/each}
		</select>

		<!-- Search input -->
		<div class="relative flex-1">
			<input
				bind:this={inputElement}
				bind:value={query}
				on:focus={handleFocus}
				on:blur={handleBlur}
				on:keydown={handleKeydown}
				type="text"
				inputmode="search"
				enterkeyhint="search"
				autocomplete="off"
				autocapitalize="off"
				spellcheck="false"
				role="combobox"
				aria-label="Search"
				aria-autocomplete="list"
				aria-expanded={showList}
				aria-controls={listboxId}
				aria-activedescendant={showList && selectedIndex >= 0 ? optionId(selectedIndex) : undefined}
				{placeholder}
				class="input pr-10"
			/>

			{#if query}
				<button
					type="button"
					on:click={clearSearch}
					class="tap absolute right-3 top-1/2 -translate-y-1/2 text-trend-gray-400 hover:text-trend-gray-600"
					aria-label="Clear search"
				>
					<svg
						xmlns="http://www.w3.org/2000/svg"
						class="w-5 h-5"
						fill="none"
						viewBox="0 0 24 24"
						stroke="currentColor"
						stroke-width="2"
					>
						<path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12" />
					</svg>
				</button>
			{:else}
				<span class="absolute right-3 top-1/2 -translate-y-1/2 text-trend-gray-400" aria-hidden="true">
					<svg
						xmlns="http://www.w3.org/2000/svg"
						class="w-5 h-5"
						fill="none"
						viewBox="0 0 24 24"
						stroke="currentColor"
						stroke-width="2"
					>
						<path
							stroke-linecap="round"
							stroke-linejoin="round"
							d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"
						/>
					</svg>
				</span>
			{/if}
		</div>
	</div>

	<!-- Results dropdown -->
	{#if showList}
		<SearchResults
			{results}
			{selectedIndex}
			{listboxId}
			{optionId}
			on:select={(e) => selectResult(e.detail)}
		/>
	{:else if panel}
		<div class="absolute z-[60] w-full mt-2 p-4 bg-white dark:bg-trend-gray-800 rounded-xl shadow-lg border border-trend-gray-200 dark:border-trend-gray-700">
			{#if panel === 'loading'}
				<p class="text-sm text-trend-gray-500 dark:text-trend-gray-400 text-center">
					Building search index…
				</p>
			{:else if panel === 'error'}
				<p class="text-sm text-trend-gray-600 dark:text-trend-gray-300 text-center">
					Search is unavailable: the search index could not be loaded.
					<button
						type="button"
						on:click={loadIndex}
						class="font-medium text-trend-red hover:text-guardian-red dark:text-red-300 dark:hover:text-red-200 underline"
					>
						Try again
					</button>
				</p>
			{:else}
				<p class="text-sm text-trend-gray-500 dark:text-trend-gray-400 text-center">
					No results found for "{query}"
				</p>
			{/if}
		</div>
	{/if}

	<!-- Always in the DOM so screen readers pick up each change. -->
	<p class="sr-only" role="status" aria-live="polite">{announcement}</p>
</div>
