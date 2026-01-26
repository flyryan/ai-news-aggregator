<script lang="ts">
	import { goto } from '$app/navigation';
	import { availableDates } from '$lib/stores/dateStore';
	import { loadIndex } from '$lib/services/dataLoader';
	import { formatDate } from '$lib/services/dateUtils';
	import { CATEGORY_CONFIG, type Category, type DataIndex, type DateEntry } from '$lib/types';
	import Calendar from '$lib/components/calendar/Calendar.svelte';
	import LoadingSpinner from '$lib/components/common/LoadingSpinner.svelte';
	import EmptyState from '$lib/components/common/EmptyState.svelte';

	let index: DataIndex | null = null;
	let loading = true;

	// Load index on mount
	loadIndex()
		.then((data) => {
			index = data;
			loading = false;
		})
		.catch(() => {
			loading = false;
		});

	function handleDateSelect(event: CustomEvent<{ date: string }>) {
		goto(`/?date=${event.detail.date}`);
	}
</script>

<svelte:head>
	<title>Archive | AI News Daily</title>
</svelte:head>

<div class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
	<h1 class="text-2xl font-bold text-trend-gray-800 dark:text-trend-gray-100 mb-8">
		News Archive
	</h1>

	{#if loading}
		<div class="py-20">
			<LoadingSpinner size="lg" />
		</div>
	{:else}
		<div class="grid gap-8 lg:grid-cols-3">
			<!-- Calendar -->
			<div class="lg:col-span-1">
				<Calendar on:select={handleDateSelect} />
			</div>

			<!-- Date List -->
			<div class="lg:col-span-2">
				<h2 class="font-semibold text-trend-gray-800 dark:text-trend-gray-100 mb-4">
					Available Reports ({$availableDates.length})
				</h2>

				{#if $availableDates.length === 0}
					<EmptyState
						title="No reports available"
						message="Run the pipeline to generate news reports."
					/>
				{:else}
					<div class="max-h-[480px] overflow-y-auto pr-2">
						<div class="space-y-3">
						{#each index?.dates || [] as dateEntry}
							<a
								href="/?date={dateEntry.date}"
								class="card block hover:border-trend-red transition-colors"
							>
								<div class="flex items-center justify-between">
									<div>
										<h3 class="font-medium text-trend-gray-800 dark:text-trend-gray-100">
											{formatDate(dateEntry.date, 'EEEE, MMMM d, yyyy')}
										</h3>
										<p class="text-sm text-trend-gray-500 mt-1">
											{dateEntry.total_items} items analyzed
										</p>
									</div>

									<!-- Category breakdown -->
									<div class="flex items-center gap-2">
										{#each Object.entries(dateEntry.categories) as [category, info]}
											{@const config = CATEGORY_CONFIG[category as Category]}
											{#if config}
												<span
													class="text-xs px-2 py-1 rounded-full bg-trend-gray-100 dark:bg-trend-gray-700 text-trend-gray-600 dark:text-trend-gray-400 border"
													style="border-color: {config.color}"
												>
													{info.count}
												</span>
											{/if}
										{/each}
									</div>
								</div>
							</a>
						{/each}
						</div>
					</div>
				{/if}
			</div>
		</div>
	{/if}
</div>
