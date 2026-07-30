<script lang="ts">
	import { onMount } from 'svelte';
	import { page } from '$app/stores';
	import { goto } from '$app/navigation';
	import { browser } from '$app/environment';
	import { initializeTheme } from '$lib/stores/themeStore';
	import { initializeDateStore } from '$lib/stores/dateStore';
	import Header from '$lib/components/layout/Header.svelte';
	import Navigation from '$lib/components/layout/Navigation.svelte';
	import Footer from '$lib/components/layout/Footer.svelte';
	import { isPreview, previewLabel } from '$lib/services/dataBase';
	import '../app.css';

	// Draft banner. Read on mount: the attribute lives on <body>, which is not
	// available during SSR/prerender.
	let showPreviewBanner = false;
	let previewBannerLabel: string | null = null;
	onMount(() => {
		try {
			showPreviewBanner = isPreview();
			previewBannerLabel = previewLabel();
			// The URL claiming ?preview= while the body attribute is absent means
			// this page was NOT served by the admin origin's injection path -- a
			// dev server or a misroute. Every fetch would silently read LIVE data
			// under a preview URL, which is the exact failure the data-base design
			// exists to prevent. Same loud banner as a malformed base.
			if (!showPreviewBanner && $page.url.searchParams.has('preview')) {
				showPreviewBanner = true;
				previewBannerLabel =
					'Preview misconfigured: this origin did not inject the preview data base, so ' +
					'you are looking at LIVE data. Open the preview from the admin origin';
			}
		} catch (e) {
			// A malformed base throws by design rather than falling back to live
			// data. Surface it loudly instead of rendering something misleading.
			console.error(e);
			showPreviewBanner = true;
			previewBannerLabel = 'Preview misconfigured — this page may not be showing draft data';
		}
	});

	onMount(async () => {
		initializeTheme();
		await initializeDateStore();
	});

	// Redirect legacy path-based URLs to query param format
	// e.g., /2026-01-05 -> /?date=2026-01-05
	// e.g., /2026-01-05/research -> /?date=2026-01-05&category=research
	$: if (browser) {
		const path = $page.url.pathname;
		const dateMatch = path.match(/^\/(\d{4}-\d{2}-\d{2})(?:\/(\w+))?$/);
		if (dateMatch) {
			const [, date, category] = dateMatch;
			const hash = $page.url.hash;
			const newUrl = category
				? `/?date=${date}&category=${category}${hash}`
				: `/?date=${date}${hash}`;
			goto(newUrl, { replaceState: true });
		}
	}
</script>

<svelte:head>
	<link rel="alternate" type="application/atom+xml" title="AATF AI News" href="/data/feeds/main.xml"/>
</svelte:head>

<div class="min-h-screen flex flex-col">
	{#if showPreviewBanner}
		<div class="preview-banner" role="status">
			<strong>Draft.</strong>
			{previewBannerLabel ?? 'This is unpublished content'} — not visible to readers.
		</div>
	{/if}
	<Header />
	<Navigation />

	<main class="flex-1">
		<slot />
	</main>

	<Footer />
</div>

<style>
	.preview-banner {
		position: sticky;
		top: 0;
		z-index: 60;
		padding: 0.45rem 1rem;
		text-align: center;
		font-size: 0.8rem;
		color: #fff;
		background: #e63946;
		/* Deliberately loud and sticky. The failure this prevents is an operator
		   reading a draft, believing it is live, and "fixing" something that was
		   never broken -- or approving a page they never actually looked at. */
	}
</style>
