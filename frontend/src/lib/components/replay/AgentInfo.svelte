<script lang="ts">
	/**
	 * What one agent does, opened from the ⓘ on its station.
	 *
	 * Rendered as a popover anchored to the station rather than a modal: the point
	 * is to read about an agent *while* watching it work, so covering the stage
	 * would defeat it. On narrow screens it becomes a sheet, because a popover
	 * pinned to a 300px-wide card has nowhere to go.
	 *
	 * Content comes from `agentDocs.ts` and is static. Live figures (call counts,
	 * spend) stay on the station itself — mixing the two would make it unclear
	 * which numbers describe today and which describe the pipeline in general.
	 */
	import { onMount, tick } from 'svelte';
	import type { AgentDoc } from '$lib/services/agentDocs';

	export let label: string;
	export let doc: AgentDoc;
	export let accent = '#E63946';
	/** The station this was opened from; the panel is positioned against it. */
	export let anchor: HTMLElement | null = null;
	export let onClose: () => void = () => {};

	let panel: HTMLDivElement | null = null;

	/**
	 * Move an element to `document.body`.
	 *
	 * `position: fixed` is only viewport-relative if no ancestor establishes a
	 * containing block — and a live station carries `transform: translateY(-1px)`
	 * for its lift, which does exactly that. Coordinates measured against the
	 * viewport were then applied relative to the card, putting panels for the lower
	 * stations hundreds of pixels below the fold.
	 *
	 * Reparenting to the body is the reliable fix: it cannot be broken by a future
	 * transform, filter, or `will-change` added anywhere in the stage.
	 */
	function portal(node: HTMLElement) {
		document.body.appendChild(node);
		return {
			destroy() {
				node.remove();
			}
		};
	}

	// Positioned with `fixed` and measured coordinates rather than `absolute`
	// inside the station.
	//
	// The stage clips to its rounded corners (`overflow: hidden`), and lifting that
	// is not an option — it is what keeps the ambient backdrop and the conveyor
	// animations inside the frame. An absolutely-positioned child is clipped by any
	// scrolling or hidden ancestor, so a panel opened on a lower station had its
	// bottom cut off. `fixed` takes it out of that chain entirely, at the cost of
	// having to place it ourselves.
	let top = 0;
	let left = 0;
	let width = 0;
	let maxHeight = 0;
	let placeAbove = false;

	const MARGIN = 8;
	const MIN_WIDTH = 300;
	const GAP = 4;
	const IDEAL_HEIGHT = 384; // 24rem, the design cap

	function place() {
		if (!anchor) return;
		const a = anchor.getBoundingClientRect();
		const vh = window.innerHeight;
		const vw = window.innerWidth;

		// Decide from the space available, not from the panel's current height.
		// Measuring the panel first made placement race its own layout: on the first
		// pass the height is whatever the content wants, which may exceed the cap
		// that is about to be applied, so the branch was chosen on a number that
		// never ends up being true. Space is known before anything renders.
		const spaceBelow = vh - a.bottom - GAP - MARGIN;
		const spaceAbove = a.top - GAP - MARGIN;
		placeAbove = spaceBelow < Math.min(IDEAL_HEIGHT, spaceAbove);

		// Cap the panel to the side it is on, so it always scrolls internally rather
		// than running off the viewport. This is what actually guarantees no clipping.
		maxHeight = Math.max(140, Math.min(IDEAL_HEIGHT, placeAbove ? spaceAbove : spaceBelow));
		top = placeAbove ? a.top - GAP - maxHeight : a.bottom + GAP;
		// Guard against sub-pixel drift pushing it past an edge.
		top = Math.min(Math.max(MARGIN, top), vh - maxHeight - MARGIN);

		// Match the station's width where possible, but never go under MIN_WIDTH or
		// off either edge — a narrow desk column would otherwise squeeze the text.
		width = Math.min(Math.max(a.width, MIN_WIDTH), vw - MARGIN * 2);
		left = Math.min(Math.max(MARGIN, a.left), vw - width - MARGIN);
	}

	onMount(() => {
		void tick().then(() => {
			// Focus first, then place. Focusing can scroll an ancestor even with
			// `preventScroll` (browsers honour it inconsistently for nested scroll
			// containers), and the coordinates are measured from the anchor's live
			// viewport position — so placing first meant computing against a layout
			// that focus then moved out from under us. That is how three panels ended
			// up positioned hundreds of pixels below the fold.
			panel?.focus({ preventScroll: true });
			place();
			// One more frame for any scroll the focus triggered to settle.
			requestAnimationFrame(place);
		});

		// Reposition rather than reflow: the replay animates constantly and the page
		// can scroll underneath an open panel.
		const onMove = () => place();
		window.addEventListener('scroll', onMove, true);
		window.addEventListener('resize', onMove);
		return () => {
			window.removeEventListener('scroll', onMove, true);
			window.removeEventListener('resize', onMove);
		};
	});

	function onKeydown(event: KeyboardEvent) {
		if (event.key === 'Escape') {
			event.stopPropagation();
			onClose();
		}
	}
</script>

<!-- Click-away layer. Pointer events only; Escape is handled on the panel. -->
<div
	class="scrim"
	use:portal
	role="presentation"
	on:click={onClose}
	on:keydown={() => {}}
></div>

<div
	class="info"
	use:portal
	class:above={placeAbove}
	style="--accent: {accent}; top: {top}px; left: {left}px; width: {width}px; max-height: {maxHeight}px"
	role="dialog"
	aria-modal="false"
	aria-label="About {label}"
	tabindex="-1"
	bind:this={panel}
	on:keydown={onKeydown}
>
	<header>
		<h4>{label}</h4>
		<button type="button" class="close" on:click={onClose} aria-label="Close">×</button>
	</header>

	<p class="lede">{doc.summary}</p>

	<dl class="io">
		<div><dt>Takes</dt><dd>{doc.input}</dd></div>
		<div><dt>Produces</dt><dd>{doc.output}</dd></div>
	</dl>

	{#if doc.roles?.length}
		<section class="calls">
			<h5>What it asks the model</h5>
			{#each doc.roles as r (r.label)}
				<div class="call">
					<div class="call-head">
						<span class="call-label">{r.label}</span>
						<span class="call-role">{r.role}</span>
					</div>
					<p class="call-asks">{r.asks}</p>
					<p class="call-effort">{r.effort}</p>
				</div>
			{/each}
		</section>
	{/if}

	<section class="why">
		<h5>Why it matters</h5>
		<p>{doc.matters}</p>
	</section>

	{#if doc.note}
		<p class="note">{doc.note}</p>
	{/if}
</div>

<style>
	.scrim {
		position: fixed;
		inset: 0;
		z-index: 40;
		background: transparent;
	}

	/* `fixed`, with coordinates set inline from a measurement of the station — see
	   the note in the script. Escapes the stage's `overflow: hidden`. */
	.info {
		position: fixed;
		z-index: 41;
		overflow-y: auto;
		padding: 0.7rem 0.8rem 0.8rem;
		border-radius: 8px;
		border: 1px solid rgb(0 0 0 / 0.1);
		border-top: 3px solid var(--accent);
		background: #fff;
		box-shadow: 0 12px 32px -8px rgb(0 0 0 / 0.25);
		text-align: left;
		outline: none;
	}
	:global(.dark) .info {
		background: #1c1c1c;
		border-color: rgb(255 255 255 / 0.14);
	}

	header {
		display: flex;
		align-items: baseline;
		justify-content: space-between;
		gap: 0.5rem;
		margin-bottom: 0.35rem;
	}
	h4 {
		font-size: 0.8rem;
		font-weight: 700;
		color: #262626;
	}
	:global(.dark) h4 {
		color: #f5f5f5;
	}
	.close {
		font-size: 1rem;
		line-height: 1;
		color: #a3a3a3;
		background: none;
		border: none;
		cursor: pointer;
		padding: 0 2px;
	}
	.close:hover {
		color: #525252;
	}

	.lede {
		font-size: 0.74rem;
		line-height: 1.5;
		color: #404040;
		margin-bottom: 0.6rem;
	}
	:global(.dark) .lede {
		color: #d4d4d4;
	}

	h5 {
		font-size: 0.52rem;
		letter-spacing: 0.09em;
		text-transform: uppercase;
		color: #a3a3a3;
		margin-bottom: 0.25rem;
	}

	.io {
		display: flex;
		flex-direction: column;
		gap: 0.3rem;
		margin-bottom: 0.65rem;
	}
	.io div {
		display: grid;
		grid-template-columns: 4.2rem 1fr;
		gap: 0.5rem;
		align-items: baseline;
	}
	.io dt {
		font-size: 0.52rem;
		letter-spacing: 0.08em;
		text-transform: uppercase;
		color: #a3a3a3;
	}
	.io dd {
		margin: 0;
		font-size: 0.7rem;
		line-height: 1.45;
		color: #525252;
	}
	:global(.dark) .io dd {
		color: #a3a3a3;
	}

	.calls {
		margin-bottom: 0.65rem;
	}
	.call {
		padding: 0.4rem 0.5rem;
		border-radius: 5px;
		background: rgb(0 0 0 / 0.03);
		margin-bottom: 3px;
	}
	:global(.dark) .call {
		background: rgb(255 255 255 / 0.045);
	}
	.call-head {
		display: flex;
		align-items: baseline;
		gap: 0.35rem;
		margin-bottom: 0.15rem;
	}
	.call-label {
		font-size: 0.68rem;
		font-weight: 650;
		color: #262626;
	}
	:global(.dark) .call-label {
		color: #e5e5e5;
	}
	.call-role {
		font-size: 0.5rem;
		font-weight: 700;
		letter-spacing: 0.06em;
		text-transform: uppercase;
		color: #a3a3a3;
	}
	.call-asks {
		font-size: 0.7rem;
		line-height: 1.45;
		color: #525252;
	}
	:global(.dark) .call-asks {
		color: #a3a3a3;
	}
	.call-effort {
		font-size: 0.62rem;
		color: #a3a3a3;
		margin-top: 0.15rem;
	}

	.why p {
		font-size: 0.7rem;
		line-height: 1.45;
		color: #525252;
	}
	:global(.dark) .why p {
		color: #a3a3a3;
	}

	.note {
		margin-top: 0.6rem;
		padding-top: 0.5rem;
		border-top: 1px solid rgb(0 0 0 / 0.07);
		font-size: 0.66rem;
		line-height: 1.45;
		font-style: italic;
		color: #737373;
	}
	:global(.dark) .note {
		border-top-color: rgb(255 255 255 / 0.09);
	}

	/* A popover anchored to a narrow card has nowhere to go on a phone, so it
	   becomes a bottom sheet instead. */
	@media (max-width: 640px) {
		.scrim {
			background: rgb(0 0 0 / 0.35);
		}
		/* Full-width sheet pinned to the bottom. `!important` because the desktop
		   path sets top/left/width inline from a measurement, and those would
		   otherwise win over this rule. */
		.info {
			top: auto !important;
			bottom: 0;
			left: 0 !important;
			right: 0;
			width: auto !important;
			margin: 0;
			max-height: 78vh !important;
			border-radius: 12px 12px 0 0;
			border-left: none;
			border-right: none;
			border-bottom: none;
		}
	}
</style>
