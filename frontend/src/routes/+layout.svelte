<script lang="ts">
	import { onMount } from 'svelte';
	import { page } from '$app/stores';
	import { goto } from '$app/navigation';
	import { browser } from '$app/environment';
	import { initializeTheme } from '$lib/stores/themeStore';
	import { initializeDateStore, currentModel } from '$lib/stores/dateStore';
	import { get } from 'svelte/store';
	import Header from '$lib/components/layout/Header.svelte';
	import Navigation from '$lib/components/layout/Navigation.svelte';
	import Footer from '$lib/components/layout/Footer.svelte';
	import { isPreview, previewLabel } from '$lib/services/dataBase';
	import { announcement } from '$lib/stores/announcer';
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
		updateModelMeta();
	});

	// Skip link: move focus into <main> without leaving a #hash in the URL (the
	// report page treats a hash as an item to scroll to).
	let mainElement: HTMLElement;
	function skipToMain() {
		mainElement?.focus({ preventScroll: true });
		mainElement?.scrollIntoView({ block: 'start' });
	}

	// Keep the static social/description meta tags (app.html) in step with the
	// current model. Prerendered pages bake a neutral fallback; once index.json
	// loads we rewrite the three tags client-side so they always name whatever
	// model powers today's reports.
	function updateModelMeta() {
		const model = get(currentModel);
		if (!model?.display_name) return;
		const text = `Your daily digest of AI developments powered by the AATF and ${model.display_name}`;
		for (const sel of [
			'meta[name="description"]',
			'meta[property="og:description"]',
			'meta[name="twitter:description"]'
		]) {
			document.querySelector(sel)?.setAttribute('content', text);
		}
	}

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
	<a href="#main" class="skip-link" on:click|preventDefault={skipToMain}>Skip to main content</a>
	{#if showPreviewBanner}
		<div class="preview-banner" role="status">
			<strong>Draft.</strong>
			{previewBannerLabel ?? 'This is unpublished content'} — not visible to readers.
		</div>
	{/if}
	<Header />
	<Navigation />

	<main id="main" tabindex="-1" bind:this={mainElement} class="flex-1">
		<slot />
	</main>

	<Footer />

	<!-- One polite live region for the app; see $lib/stores/announcer. -->
	<p class="sr-only" role="status" aria-live="polite">{$announcement}</p>
</div>

<style>
	/* Off screen until it takes keyboard focus. */
	.skip-link {
		position: absolute;
		left: 0.5rem;
		top: 0.5rem;
		z-index: 70;
		padding: 0.625rem 1rem;
		border-radius: 0.5rem;
		background: #fff;
		color: #171717;
		font-weight: 600;
		box-shadow: 0 2px 10px rgb(0 0 0 / 0.3);
		transform: translateY(-200%);
	}
	.skip-link:focus {
		transform: none;
	}

	/* <main> takes focus from the skip link; it shows no ring of its own. Only
	   while focused: a standing transparent outline changed how edges below it
	   were anti-aliased. */
	main:focus,
	main:focus-visible {
		outline: none;
	}

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
