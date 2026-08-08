<script lang="ts">
	/**
	 * Preview the story behind an internal `#item-` link, in place.
	 *
	 * Those links used to be a detour: click, swap to the category view, wait for a
	 * 300-item list, scroll to the card, then click again to reach the article. The
	 * preview collapses that to a hover — and, since hover doesn't exist on touch
	 * and isn't discoverable to everyone, to a click and a keyboard focus as well.
	 *
	 * It renders the real NewsCard rather than a lookalike, so "Read more", Share,
	 * and the external link all work exactly as they do on the destination page and
	 * there is no second card to keep in sync.
	 *
	 * Modified clicks (⌘/ctrl/shift/alt, middle) still navigate natively, as does
	 * "Open in …" in the footer. Shared and direct `#item-` URLs are untouched.
	 *
	 * Mounted once, from the page route — the document-level delegation below is
	 * scoped by that lifetime, so /archive, /replay and /about never see it.
	 */
	import { onMount, tick } from 'svelte';
	import { goto } from '$app/navigation';
	import type { Category, NewsItem } from '$lib/types';
	import { peekItem, resolveItem } from '$lib/services/itemIndex';
	import { isSafeUrl } from '$lib/services/sanitize';
	import { portal } from '$lib/actions/portal';
	import NewsCard from './NewsCard.svelte';

	/** Used when a link omits `?date=` — it inherits the page's date. */
	export let fallbackDate: string | null = null;

	const OPEN_DELAY = 350; // long enough that a pointer crossing a link doesn't fire
	const CLOSE_DELAY = 200; // covers the diagonal from link to panel
	const IDEAL_WIDTH = 416;
	const IDEAL_HEIGHT = 560;
	const MIN_USABLE = 320; // below this a side is too cramped to read the card in
	const MARGIN = 8;
	const GAP = 6;

	const VALID_CATEGORIES: Category[] = ['news', 'research', 'social', 'reddit'];

	type Target = { date: string; category: Category; id: string; href: string };

	let target: Target | null = null;
	let item: NewsItem | null = null;
	let loading = false;

	let panel: HTMLDivElement | null = null;
	/** The link the panel is positioned against. Not updated for nested links. */
	let placementAnchor: HTMLAnchorElement | null = null;
	/** The link currently previewed, including nested ones inside the panel. */
	let activeAnchor: HTMLAnchorElement | null = null;
	/** Where Escape returns focus. */
	let triggerAnchor: HTMLAnchorElement | null = null;

	let openToken = 0;
	let openTimer: ReturnType<typeof setTimeout> | null = null;
	let closeTimer: ReturnType<typeof setTimeout> | null = null;

	let canHover = false;
	let isSheet = false;

	let top = 0;
	let left = 0;
	let width = IDEAL_WIDTH;
	let maxHeight = IDEAL_HEIGHT;
	let placeAbove = false;

	$: sourceUrl = item && isSafeUrl(item.url) ? item.url : null;
	// Source names run long ("AI News & Artificial Intelligence | TechCrunch"), so
	// the button says what the action is; the card above already names the source.
	$: readLabel =
		target?.category === 'research'
			? 'Read the paper'
			: target?.category === 'reddit'
				? 'Open the discussion'
				: target?.category === 'social'
					? 'Open the post'
					: 'Read the article';

	/**
	 * The anchor's own href is the whole contract — `ALLOW_DATA_ATTR: false` in
	 * sanitize.ts strips any attribute we might otherwise have added upstream.
	 * Read the DOM property, not the attribute: the stored HTML is `&amp;`-escaped
	 * and only the property is decoded.
	 */
	function parseTarget(a: HTMLAnchorElement): Target | null {
		let url: URL;
		try {
			url = new URL(a.href, window.location.href);
		} catch {
			return null;
		}
		if (url.origin !== window.location.origin) return null;

		const id = url.hash.match(/^#item-([A-Za-z0-9_-]+)$/)?.[1];
		if (!id) return null;

		const category = url.searchParams.get('category');
		if (!category || !VALID_CATEGORIES.includes(category as Category)) return null;

		const date = url.searchParams.get('date') ?? fallbackDate;
		if (!date) return null;

		return {
			date,
			category: category as Category,
			id,
			href: `${url.pathname}${url.search}${url.hash}`
		};
	}

	let copied = false;
	let copiedTimer: ReturnType<typeof setTimeout> | null = null;

	function copyLink() {
		if (!target) return;
		void navigator.clipboard.writeText(new URL(target.href, window.location.href).toString());
		copied = true;
		if (copiedTimer) clearTimeout(copiedTimer);
		copiedTimer = setTimeout(() => (copied = false), 2000);
	}

	function cancelOpen() {
		if (openTimer) clearTimeout(openTimer);
		openTimer = null;
	}

	function cancelClose() {
		if (closeTimer) clearTimeout(closeTimer);
		closeTimer = null;
	}

	function close() {
		cancelOpen();
		cancelClose();
		openToken++;
		target = null;
		item = null;
		loading = false;
		placementAnchor = null;
		activeAnchor = null;
		// triggerAnchor deliberately survives: Escape restores focus to it after
		// close(), and a reopen overwrites it anyway.
		copied = false;
		if (copiedTimer) clearTimeout(copiedTimer);
		copiedTimer = null;
	}

	/**
	 * Measure against the triggering link. `fixed` plus measured coordinates,
	 * rather than `absolute`, because the summary sits inside containers that clip
	 * and the panel must be able to overhang them.
	 *
	 * Decide from available space, not from the panel's current height: on the
	 * first pass that height is whatever the content wants, which is about to be
	 * capped, so the branch would be chosen on a number that never comes true.
	 */
	function place() {
		if (!placementAnchor?.isConnected) return;

		const rect = placementAnchor.getBoundingClientRect();
		const vh = window.innerHeight;
		const vw = window.innerWidth;

		const spaceBelow = vh - rect.bottom - GAP - MARGIN;
		const spaceAbove = rect.top - GAP - MARGIN;
		// Prefer below, and flip only when below is genuinely too cramped to read
		// in. Choosing whichever side is merely larger flips for a few pixels and
		// lands the panel jammed against the top of the viewport.
		placeAbove = spaceBelow < MIN_USABLE && spaceAbove > spaceBelow;

		// Cap to the side it lands on so it scrolls internally instead of running
		// off the viewport — this is what actually guarantees nothing is cut off.
		maxHeight = Math.max(MIN_USABLE, Math.min(IDEAL_HEIGHT, placeAbove ? spaceAbove : spaceBelow));
		top = placeAbove ? rect.top - GAP - maxHeight : rect.bottom + GAP;
		top = Math.min(Math.max(MARGIN, top), Math.max(MARGIN, vh - maxHeight - MARGIN));

		width = Math.min(IDEAL_WIDTH, vw - MARGIN * 2);
		left = Math.min(Math.max(MARGIN, rect.left), Math.max(MARGIN, vw - width - MARGIN));
	}

	async function open(
		a: HTMLAnchorElement,
		{ focus = false, navigateOnMiss = false }: { focus?: boolean; navigateOnMiss?: boolean } = {}
	) {
		const next = parseTarget(a);
		if (!next) return;

		cancelOpen();
		cancelClose();
		const token = ++openToken;

		// A link inside the panel swaps the content without moving the panel: its
		// own anchor is about to be destroyed by that swap, so it can't be measured.
		const nested = !!panel?.contains(a);
		if (!nested) {
			placementAnchor = a;
			triggerAnchor = a;
		}
		activeAnchor = a;
		target = next;

		const known = peekItem(next.date, next.id);
		item = known;
		loading = !known;

		await tick();
		if (token !== openToken) return;
		if (!nested) place();
		if (focus) panel?.focus({ preventScroll: true });

		if (known) return;

		const resolved = await resolveItem(next.date, next.category, next.id);
		if (token !== openToken) return;

		if (!resolved) {
			// The enricher can outrun the data. A dead panel is worse than the old
			// behaviour, so fall back to it.
			close();
			if (navigateOnMiss) void goto(next.href);
			return;
		}

		item = resolved;
		loading = false;
		await tick();
		if (token === openToken && !nested) place();
	}

	function scheduleOpen(a: HTMLAnchorElement, focus = false) {
		cancelOpen();
		openTimer = setTimeout(() => {
			openTimer = null;
			void open(a, { focus });
		}, OPEN_DELAY);
	}

	function scheduleClose() {
		cancelClose();
		closeTimer = setTimeout(() => {
			closeTimer = null;
			close();
		}, CLOSE_DELAY);
	}

	function internalLinkFrom(node: EventTarget | null): HTMLAnchorElement | null {
		const el = node instanceof Element ? node : null;
		return (el?.closest('a.internal-link') as HTMLAnchorElement | null) ?? null;
	}

	function onPointerOver(event: PointerEvent) {
		// A tap also emits pointerover; that path belongs to the click handler.
		if (!canHover || event.pointerType === 'touch') return;

		const link = internalLinkFrom(event.target);
		if (link) {
			cancelClose();
			if (link !== activeAnchor) scheduleOpen(link);
			return;
		}

		const inPanel = event.target instanceof Node && !!panel?.contains(event.target);
		if (inPanel) {
			cancelOpen();
			cancelClose();
			return;
		}

		cancelOpen();
		if (target) scheduleClose();
	}

	/**
	 * Capture phase so `preventDefault()` lands before SvelteKit's own document
	 * click handler, which bails on an already-defaulted event.
	 */
	function onClick(event: MouseEvent) {
		const link = internalLinkFrom(event.target);
		if (link) {
			// Leave every deliberate "open this somewhere else" gesture alone.
			if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
			if (event.button !== 0) return;
			if (!parseTarget(link)) return;

			event.preventDefault();
			void open(link, { focus: true, navigateOnMiss: true });
			return;
		}

		if (target && event.target instanceof Node && !panel?.contains(event.target)) close();
	}

	/**
	 * Keyboard parity for the hover affordance (WCAG 1.4.13): focus shows the
	 * preview without stealing focus, so Tab still walks the summary's links.
	 * Enter fires a click, which opens it *and* moves focus in, so the card's own
	 * links are reachable; Escape dismisses and hands focus back.
	 */
	function onFocusIn(event: FocusEvent) {
		const link = internalLinkFrom(event.target);
		if (!link || panel?.contains(link)) return;
		cancelClose();
		scheduleOpen(link);
	}

	function onFocusOut(event: FocusEvent) {
		if (!target) return;
		const next = event.relatedTarget;
		// A click that opens the panel fires focusout on the link *before* the panel
		// is in the DOM, so relatedTarget is null and `panel.contains` can't help.
		// Settle on a frame and read where focus actually landed.
		requestAnimationFrame(() => {
			if (!target) return;
			const active = document.activeElement;
			if (active instanceof Node && panel?.contains(active)) return;
			if (next instanceof Node && panel?.contains(next)) return;
			if (internalLinkFrom(next) || internalLinkFrom(active)) return;
			cancelOpen();
			scheduleClose();
		});
	}

	function onKeydown(event: KeyboardEvent) {
		if (event.key !== 'Escape' || !target) return;
		event.stopPropagation();

		// Remember the destination, not the element: summaries render with {@html},
		// so a re-render swaps the whole fragment and the node that opened this is
		// often already detached by the time the panel closes. Focusing a detached
		// node silently drops focus to <body>, stranding keyboard users at the top
		// of the document.
		const href = triggerAnchor?.href;

		close();

		// Resolve after the removal commits — before it, the panel's own copy of the
		// link is still in the document and would win the lookup.
		if (href) {
			requestAnimationFrame(() => {
				const el = [...document.querySelectorAll<HTMLAnchorElement>('a.internal-link')].find(
					(a) => a.href === href
				);
				el?.focus({ preventScroll: true });
			});
		}
	}

	onMount(() => {
		// `any-hover`, not `hover`: a laptop with a touchscreen reports the touch
		// digitiser as its primary pointer and would lose hover previews entirely.
		// The pointerover handler also checks event.pointerType, so a real touch on
		// such a device still takes the tap path.
		const hoverQuery = window.matchMedia('(any-hover: hover)');
		const sheetQuery = window.matchMedia('(max-width: 640px)');
		canHover = hoverQuery.matches;
		isSheet = sheetQuery.matches;

		const onHoverChange = (e: MediaQueryListEvent) => (canHover = e.matches);
		const onSheetChange = (e: MediaQueryListEvent) => {
			isSheet = e.matches;
			place();
		};
		// Reposition rather than close: the panel can be tall enough to read while
		// the page scrolls underneath it.
		const onViewportChange = () => place();

		hoverQuery.addEventListener('change', onHoverChange);
		sheetQuery.addEventListener('change', onSheetChange);
		document.addEventListener('pointerover', onPointerOver);
		document.addEventListener('click', onClick, true);
		document.addEventListener('focusin', onFocusIn);
		document.addEventListener('focusout', onFocusOut);
		document.addEventListener('keydown', onKeydown);
		window.addEventListener('scroll', onViewportChange, true);
		window.addEventListener('resize', onViewportChange);

		return () => {
			cancelOpen();
			cancelClose();
			hoverQuery.removeEventListener('change', onHoverChange);
			sheetQuery.removeEventListener('change', onSheetChange);
			document.removeEventListener('pointerover', onPointerOver);
			document.removeEventListener('click', onClick, true);
			document.removeEventListener('focusin', onFocusIn);
			document.removeEventListener('focusout', onFocusOut);
			document.removeEventListener('keydown', onKeydown);
			window.removeEventListener('scroll', onViewportChange, true);
			window.removeEventListener('resize', onViewportChange);
		};
	});
</script>

<!--
	One portaled host, created once and never conditionally destroyed.

	Portaling the panel itself was a bug: `use:portal` relocates the node to
	<body>, but the {#if} that created it still believes it owns that position in
	the component's node range. On teardown Svelte removes siblings across that
	range — which, after the move, spans <body>'s real children — and blanked the
	entire app when a preview closed. Keeping the host static means Svelte only
	ever adds and removes nodes *inside* it, where its bookkeeping is correct.
-->
<div class="host" use:portal>
{#if target}
	{#if isSheet}
		<!-- Only the sheet gets a scrim; on desktop a transparent one would swallow
		     the hover that is meant to move between links. -->
		<div class="scrim" role="presentation" on:click={close} on:keydown={() => {}}></div>
	{/if}

	<div
		class="preview"
		class:above={placeAbove}
		bind:this={panel}
		style="top: {top}px; left: {left}px; width: {width}px; max-height: {maxHeight}px"
		role="dialog"
		aria-modal="false"
		aria-label={item ? item.title : 'Loading story'}
		tabindex="-1"
	>
		<!-- Only the card scrolls. A long story would otherwise push the footer out
		     of the panel, hiding the one control that gets you to the full page. -->
		<div class="body">
			{#if item}
				<NewsCard
					{item}
					category={target.category}
					date={target.date}
					showCategory
					anchor={false}
					showActions={false}
				/>
			{:else if loading}
				<div class="skeleton" aria-hidden="true">
					<div class="line w-1/3"></div>
					<div class="line line-lg w-full"></div>
					<div class="line line-lg w-4/5"></div>
					<div class="line w-1/2"></div>
				</div>
			{/if}
		</div>

		<!-- Reading the source is the point of the whole feature, so it is the primary
		     action and it is pinned — never something to scroll the card for. The
		     on-site card is the secondary path. -->
		<div class="actions">
			{#if sourceUrl}
				<a class="primary" href={sourceUrl} target="_blank" rel="noopener noreferrer">
					{readLabel}
					<span aria-hidden="true">&nearr;</span>
				</a>
			{/if}
			<a class="secondary" href={target.href} on:click={close}>Open card</a>
			<button class="secondary" type="button" on:click={copyLink}>
				{copied ? 'Copied' : 'Share'}
			</button>
		</div>
	</div>
{/if}
</div>

<style>
	/* The host is a zero-size anchor point; children are all `fixed`. */
	.host {
		position: absolute;
		top: 0;
		left: 0;
	}

	.scrim {
		position: fixed;
		inset: 0;
		z-index: 60;
		background: rgb(0 0 0 / 0.35);
	}

	/* `fixed` with coordinates measured from the link — see place(). The summary
	   sits inside clipping containers the panel has to overhang. */
	/* One surface, not a stack of floating cards: the panel owns the background,
	   border, radius and shadow, and the card inside it renders flush. Two
	   separately-rounded cards with a gap let the page show through between them,
	   and the scroll clip then reads as a rendering fault rather than an edge. */
	.preview {
		position: fixed;
		z-index: 61;
		display: flex;
		flex-direction: column;
		border-radius: 12px;
		border: 1px solid rgb(0 0 0 / 0.1);
		background: #fff;
		box-shadow: 0 16px 40px -12px rgb(0 0 0 / 0.35);
		/* Clip the scrolling card to the panel's own corners. */
		overflow: hidden;
		outline: none;
	}
	:global(.dark) .preview {
		background: #262626;
		border-color: rgb(255 255 255 / 0.14);
		box-shadow: 0 16px 40px -12px rgb(0 0 0 / 0.6);
	}

	/* The card is now the panel's content, so it drops its own frame. */
	.preview :global(.card) {
		background: transparent;
		border: none;
		border-radius: 0;
		box-shadow: none;
	}
	/* …except the importance accent, which is information, not chrome. */
	.preview :global(.card-importance-high),
	.preview :global(.card-importance-medium),
	.preview :global(.card-importance-standard),
	.preview :global(.card-importance-low) {
		border-left-width: 3px;
		border-left-style: solid;
	}

	.body {
		min-height: 0; /* let the flex child actually shrink and scroll */
		overflow-y: auto;
		overscroll-behavior: contain;
		scrollbar-width: thin;
	}

	.actions {
		position: relative;
		display: flex;
		align-items: center;
		gap: 0.75rem;
		padding: 0.625rem 0.75rem;
		flex: none;
		border-top: 1px solid rgb(0 0 0 / 0.08);
		background: #fff;
	}
	:global(.dark) .actions {
		border-top-color: rgb(255 255 255 / 0.1);
		background: #262626;
	}

	/* A soft edge above the bar so text scrolling under it fades out instead of
	   being cut mid-line. Sits outside the bar, ignores pointer input. */
	.actions::before {
		content: '';
		position: absolute;
		left: 0;
		right: 0;
		bottom: 100%;
		height: 1.5rem;
		pointer-events: none;
		background: linear-gradient(to top, #fff, transparent);
	}
	:global(.dark) .actions::before {
		background: linear-gradient(to top, #262626, transparent);
	}

	/* The source article: the reason the reader opened this at all. */
	.primary {
		flex: 1;
		display: flex;
		align-items: center;
		justify-content: center;
		gap: 0.35rem;
		padding: 0.5rem 0.75rem;
		border-radius: 8px;
		background: #e63946;
		color: #fff;
		font-size: 0.875rem;
		font-weight: 600;
		text-align: center;
		/* Long source names shouldn't push the secondary action off the row. */
		overflow: hidden;
		white-space: nowrap;
		text-overflow: ellipsis;
	}
	.primary:hover {
		background: #c1121f;
	}

	.secondary {
		flex: none;
		padding: 0.5rem 0.25rem;
		font-size: 0.8125rem;
		font-weight: 500;
		color: #737373;
		white-space: nowrap;
		background: none;
		border: none;
		cursor: pointer;
	}
	.secondary:hover {
		color: #e63946;
	}
	:global(.dark) .secondary {
		color: #a3a3a3;
	}
	:global(.dark) .secondary:hover {
		color: #fca5a5;
	}

	.skeleton {
		display: flex;
		flex-direction: column;
		gap: 0.6rem;
		padding: 1.5rem;
	}
	.line {
		height: 0.7rem;
		border-radius: 4px;
		background: rgb(0 0 0 / 0.08);
	}
	.line-lg {
		height: 0.95rem;
	}
	:global(.dark) .line {
		background: rgb(255 255 255 / 0.1);
	}
	@media (prefers-reduced-motion: no-preference) {
		.line {
			animation: pulse 1.4s ease-in-out infinite;
		}
		.preview {
			animation: rise 120ms ease-out;
		}
		.preview.above {
			animation-name: rise-above;
		}
	}
	@keyframes pulse {
		50% {
			opacity: 0.45;
		}
	}
	@keyframes rise {
		from {
			opacity: 0;
			transform: translateY(-4px);
		}
	}
	@keyframes rise-above {
		from {
			opacity: 0;
			transform: translateY(4px);
		}
	}

	/* A panel anchored to a link has nowhere to go on a phone, so it becomes a
	   bottom sheet. `!important` because the desktop path sets top/left/width
	   inline from a measurement, and those would otherwise win. */
	@media (max-width: 640px) {
		.preview {
			top: auto !important;
			bottom: 0;
			left: 0 !important;
			right: 0;
			width: auto !important;
			max-height: 82vh !important;
			/* Rests on the bottom edge, so only the top corners are rounded. */
			border-radius: 14px 14px 0 0;
			border-bottom: none;
			animation: none;
		}
	}
</style>
