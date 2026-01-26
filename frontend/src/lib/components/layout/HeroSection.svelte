<script lang="ts">
	import { formatDate, formatDayOfWeek } from '$lib/services/dateUtils';

	export let date: string;
	export let coverageDate: string | undefined = undefined;
	export let totalItems: number = 0;
	export let heroImageUrl: string | null = null;
	export let collectionStatus: string = 'success';

	$: formattedDate = date ? `${formatDayOfWeek(date)}, ${formatDate(date)}` : '';
	$: formattedCoverage = coverageDate ? formatDate(coverageDate) : '';

	// Status indicator color
	$: statusColor =
		collectionStatus === 'success'
			? 'bg-green-500'
			: collectionStatus === 'partial'
				? 'bg-yellow-500'
				: 'bg-red-500';
</script>

<section class="hero-section mb-8">
	{#if heroImageUrl}
		<img src={heroImageUrl} alt="Daily AI scene featuring AATF mascot" class="hero-image" />
	{:else}
		<div
			class="hero-fallback bg-gradient-to-br from-trend-gray-700 to-trend-gray-900 flex items-center justify-center"
		>
			<img src="/assets/logo.webp" alt="AATF Logo" class="w-24 h-24 opacity-40" />
		</div>
	{/if}

	<div class="hero-overlay">
		<div class="flex items-end justify-between">
			<div>
				<h2 class="hero-date">{formattedDate}</h2>
				{#if formattedCoverage}
					<p class="hero-meta">
						Coverage: {formattedCoverage}
						{#if totalItems > 0}
							<span class="mx-2">•</span>
							{totalItems.toLocaleString()} items analyzed
						{/if}
					</p>
				{/if}
			</div>

			{#if collectionStatus !== 'success'}
				<div class="flex items-center gap-2 text-sm text-white/80">
					<span class={`w-2 h-2 rounded-full ${statusColor}`}></span>
					<span class="capitalize">{collectionStatus}</span>
				</div>
			{/if}
		</div>
	</div>
</section>

<style>
	.hero-section {
		position: relative;
		width: 100%;
		aspect-ratio: 21 / 9;
		overflow: hidden;
		border-radius: 1rem;
		background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
	}

	.hero-image {
		width: 100%;
		height: 100%;
		object-fit: cover;
	}

	.hero-fallback {
		width: 100%;
		height: 100%;
	}

	.hero-overlay {
		position: absolute;
		bottom: 0;
		left: 0;
		right: 0;
		background: linear-gradient(to top, rgba(0, 0, 0, 0.75) 0%, rgba(0, 0, 0, 0.3) 50%, transparent 100%);
		padding: 4rem 1.5rem 1.5rem 1.5rem;
	}

	.hero-date {
		color: white;
		font-size: 1.5rem;
		font-weight: 700;
		line-height: 1.2;
	}

	.hero-meta {
		color: rgba(255, 255, 255, 0.8);
		font-size: 0.875rem;
		margin-top: 0.25rem;
	}

	@media (min-width: 640px) {
		.hero-date {
			font-size: 1.75rem;
		}

		.hero-overlay {
			padding: 4rem 2rem 1.5rem 2rem;
		}
	}
</style>
