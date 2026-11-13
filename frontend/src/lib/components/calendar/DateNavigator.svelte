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
	import { formatDate, getPreviousDay } from '$lib/services/dateUtils';
	import { shortcutAllowed } from '$lib/services/shortcuts';

	// Optional coverage date prop from parent
	export let coverageDate: string | undefined = undefined;
	// On the overview the report date is the view's <h1> (it looks the same).
	// Category pages name the category in their own <h1>.
	export let heading: boolean = false;
	// While the report loads, hold the coverage line's space so its arrival
	// moves nothing. Coverage is the day before the report date.
	export let reserveCoverage: boolean = false;

	$: dateDisplay = $currentDate ? formatDate($currentDate, 'EEEE, MMMM d, yyyy') : '';
	$: coverageDisplay = coverageDate ? formatDate(coverageDate, 'EEEE, MMMM d, yyyy') : '';
	// Until the date list has loaded nothing is known about neighbours; saying
	// "Latest" then would flash on every older report.
	$: datesKnown = $availableDates.length > 0;
	$: isLatest = datesKnown && !$navigation.hasNext;

	$: expectedCoverage = $currentDate
		? formatDate(getPreviousDay($currentDate), 'EEEE, MMMM d, yyyy')
		: '';

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

<div class="flex items-center justify-between gap-4">
	<!-- Previous button -->
	<button
		type="button"
		on:click={() => goToPreviousDate(currentCategory || undefined)}
		disabled={!$navigation.hasPrevious}
		class="tap p-2 rounded-lg transition-colors
		       {$navigation.hasPrevious
			? 'hover:bg-trend-gray-200 dark:hover:bg-trend-gray-700 text-trend-gray-700 dark:text-trend-gray-300'
			: 'text-trend-gray-400 cursor-not-allowed'}"
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
	<div class="text-center flex-1">
		<div>
			<svelte:element
				this={heading ? 'h1' : 'span'}
				class="inline font-medium text-trend-gray-800 dark:text-trend-gray-200"
			>
				{dateDisplay}
			</svelte:element>
			{#if isLatest}
				<span class="ml-2 text-xs bg-trend-red-fill text-white px-2 py-0.5 rounded-full">
					Latest
				</span>
			{/if}
		</div>
		{#if coverageDisplay}
			<div class="text-sm text-trend-gray-500 dark:text-trend-gray-400 mt-1">
				Coverage: {coverageDisplay}, 00:00–23:59 ET
			</div>
		{:else if reserveCoverage && expectedCoverage}
			<div class="text-sm mt-1 invisible" aria-hidden="true">
				Coverage: {expectedCoverage}, 00:00–23:59 ET
			</div>
		{/if}
	</div>

	<!-- Next button -->
	<button
		type="button"
		on:click={() => goToNextDate(currentCategory || undefined)}
		disabled={!$navigation.hasNext}
		class="tap p-2 rounded-lg transition-colors
		       {$navigation.hasNext
			? 'hover:bg-trend-gray-200 dark:hover:bg-trend-gray-700 text-trend-gray-700 dark:text-trend-gray-300'
			: 'text-trend-gray-400 cursor-not-allowed'}"
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
	<div class="text-center mt-2">
		<button
			type="button"
			on:click={() => goToLatestDate(currentCategory || undefined)}
			class="tap text-sm text-trend-red-text hover:text-guardian-red transition-colors"
		>
			Jump to latest &rarr;
		</button>
	</div>
{/if}
