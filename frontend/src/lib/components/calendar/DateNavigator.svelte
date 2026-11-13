<script lang="ts">
	import { page } from '$app/stores';
	import { browser } from '$app/environment';
	import {
		currentDate,
		navigation,
		availableDates,
		goToPreviousDate,
		goToNextDate,
		goToLatestDate
	} from '$lib/stores/dateStore';
	import { formatDate } from '$lib/services/dateUtils';
	import { shortcutAllowed } from '$lib/services/shortcuts';

	// Optional coverage date prop from parent
	export let coverageDate: string | undefined = undefined;
	// On the overview the report date is the view's <h1>. Category pages name
	// the category in their own <h1>, so there the date is plain text.
	export let heading: boolean = false;

	$: dateDisplay = $currentDate ? formatDate($currentDate, 'EEEE, MMMM d, yyyy') : '';
	$: coverageDisplay = coverageDate ? formatDate(coverageDate, 'EEEE, MMMM d, yyyy') : '';
	// Until the date list has loaded nothing is known about neighbours; saying
	// "Latest" then would flash on every older report.
	$: datesKnown = $availableDates.length > 0;
	$: isLatest = datesKnown && !$navigation.hasNext;

	// Read current category from URL to preserve it during navigation
	$: currentCategory = browser ? $page.url.searchParams.get('category') : null;

	// Left/right arrows do what the two buttons do, and nothing when the button
	// is disabled. Shift is left alone (it extends a text selection), and a held
	// key does not run through the archive.
	function handleWindowKeydown(event: KeyboardEvent) {
		if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return;
		if (event.shiftKey || event.repeat || !shortcutAllowed(event)) return;
		if (event.key === 'ArrowLeft' && $navigation.hasPrevious) {
			event.preventDefault();
			goToPreviousDate(currentCategory || undefined);
		} else if (event.key === 'ArrowRight' && $navigation.hasNext) {
			event.preventDefault();
			goToNextDate(currentCategory || undefined);
		}
	}
</script>

<svelte:window on:keydown={handleWindowKeydown} />

<div class="flex items-center justify-between gap-2 sm:gap-4">
	<!-- Previous button -->
	<button
		type="button"
		on:click={() => goToPreviousDate(currentCategory || undefined)}
		disabled={!$navigation.hasPrevious}
		class="date-arrow {$navigation.hasPrevious ? 'is-enabled' : ''}"
		aria-label="Previous day"
		aria-keyshortcuts="ArrowLeft"
		title="Previous day (left arrow)"
	>
		<svg
			xmlns="http://www.w3.org/2000/svg"
			class="w-5 h-5"
			fill="none"
			viewBox="0 0 24 24"
			stroke="currentColor"
			stroke-width="2"
			aria-hidden="true"
		>
			<path stroke-linecap="round" stroke-linejoin="round" d="M15 19l-7-7 7-7" />
		</svg>
	</button>

	<!-- Date display -->
	<div class="text-center flex-1 min-w-0">
		<div class="flex flex-wrap items-center justify-center gap-x-2 gap-y-1 min-h-[1.75rem]">
			<svelte:element
				this={heading ? 'h1' : 'p'}
				class="text-xl sm:text-2xl font-bold leading-tight text-trend-gray-900 dark:text-white"
			>
				{dateDisplay}
			</svelte:element>
			{#if isLatest}
				<span class="text-xs font-medium bg-brand-strong text-white px-2 py-0.5 rounded-full">
					Latest
				</span>
			{/if}
		</div>
		<!-- Always rendered, so the line arriving with the data moves nothing. -->
		<p class="meta mt-1 min-h-[1.25rem]">
			{#if coverageDisplay}
				Coverage: {coverageDisplay}, 00:00–23:59 ET
			{/if}
		</p>
	</div>

	<!-- Next button -->
	<button
		type="button"
		on:click={() => goToNextDate(currentCategory || undefined)}
		disabled={!$navigation.hasNext}
		class="date-arrow {$navigation.hasNext ? 'is-enabled' : ''}"
		aria-label="Next day"
		aria-keyshortcuts="ArrowRight"
		title="Next day (right arrow)"
	>
		<svg
			xmlns="http://www.w3.org/2000/svg"
			class="w-5 h-5"
			fill="none"
			viewBox="0 0 24 24"
			stroke="currentColor"
			stroke-width="2"
			aria-hidden="true"
		>
			<path stroke-linecap="round" stroke-linejoin="round" d="M9 5l7 7-7 7" />
		</svg>
	</button>
</div>

{#if datesKnown && $navigation.hasNext}
	<div class="text-center mt-1">
		<button
			type="button"
			on:click={() => goToLatestDate(currentCategory || undefined)}
			class="tap text-sm font-medium text-link hover:text-link-hover transition-colors"
		>
			Jump to latest <span aria-hidden="true">&rarr;</span>
		</button>
	</div>
{/if}

<style>
	.date-arrow {
		flex: none;
		display: grid;
		place-items: center;
		width: 44px;
		height: 44px;
		border-radius: 0.5rem;
		color: #737373;
		cursor: not-allowed;
		transition: background-color 200ms;
	}
	.date-arrow.is-enabled {
		color: #404040;
		cursor: pointer;
	}
	.date-arrow.is-enabled:hover {
		background: #e5e5e5;
	}
	:global(.dark) .date-arrow {
		color: #737373;
	}
	:global(.dark) .date-arrow.is-enabled {
		color: #d4d4d4;
	}
	:global(.dark) .date-arrow.is-enabled:hover {
		background: #404040;
	}
</style>
