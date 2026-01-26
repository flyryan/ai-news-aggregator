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
	import '../app.css';

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
	<Header />
	<Navigation />

	<main class="flex-1">
		<slot />
	</main>

	<Footer />
</div>
