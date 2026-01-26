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

	let query = '';
	let category: Category | '' = '';
	let results: SearchResult[] = [];
	let isOpen = false;
	let isLoading = false;
	let selectedIndex = -1;
	let inputElement: HTMLInputElement;

	const categories: (Category | '')[] = ['', 'news', 'research', 'social', 'reddit'];

	// Initialize search on mount
	onMount(async () => {
		await initializeSearch();
		if (autofocus && inputElement) {
			inputElement.focus();
		}
	});

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
		}
	}

	function performSearch() {
		if (!isSearchInitialized()) return;
		results = search(query, category || undefined);
		selectedIndex = -1;
	}

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
				selectedIndex = Math.min(selectedIndex + 1, results.length - 1);
				break;
			case 'ArrowUp':
				event.preventDefault();
				selectedIndex = Math.max(selectedIndex - 1, -1);
				break;
			case 'Enter':
				event.preventDefault();
				if (selectedIndex >= 0 && results[selectedIndex]) {
					selectResult(results[selectedIndex]);
				}
				break;
			case 'Escape':
				isOpen = false;
				inputElement?.blur();
				break;
		}
	}

	function selectResult(result: SearchResult) {
		if (result.doc) {
			goto(`/${result.doc.date}/${result.doc.category}`);
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
				type="search"
				{placeholder}
				class="input pr-10"
			/>

			{#if query}
				<button
					on:click={clearSearch}
					class="absolute right-3 top-1/2 -translate-y-1/2 text-trend-gray-400 hover:text-trend-gray-600"
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
				<span class="absolute right-3 top-1/2 -translate-y-1/2 text-trend-gray-400">
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
	{#if isOpen && results.length > 0}
		<SearchResults
			{results}
			{selectedIndex}
			on:select={(e) => selectResult(e.detail)}
		/>
	{:else if isOpen && query.length >= 2 && results.length === 0}
		<div class="absolute z-[60] w-full mt-2 p-4 bg-white dark:bg-trend-gray-800 rounded-xl shadow-lg border border-trend-gray-200 dark:border-trend-gray-700">
			<p class="text-sm text-trend-gray-500 text-center">
				No results found for "{query}"
			</p>
		</div>
	{/if}
</div>
