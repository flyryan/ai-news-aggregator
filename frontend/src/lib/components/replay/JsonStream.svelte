<script lang="ts">
	/**
	 * Structured view of streamed JSON model output.
	 *
	 * Most of what this pipeline's models write is a JSON object holding one big
	 * array — `items`, `topics`, `top_10`, `matches`. As raw text that is a wall of
	 * braces; as structure it is the actual work: N records landing one at a time,
	 * each with a summary and a score. This renders the principal array as cards and
	 * the remaining root fields beneath it.
	 *
	 * Everything here is driven by a *prefix* of the stream, so a card can be
	 * half-written and a value can be missing. That in-progress state is rendered
	 * deliberately (a pulsing rail on the newest card) rather than hidden — watching
	 * the array grow is the point.
	 */
	import { flip } from 'svelte/animate';
	import type { PartialValue, PartialEntry } from '$lib/services/replayJson';
	import { humaniseKey, previewOf, principalArray, scoreFraction } from '$lib/services/replayJson';

	export let root: PartialValue;
	export let complete = false;
	export let live = false;
	export let reduced = false;

	$: principal = principalArray(root);
	$: cards = principal?.value.kind === 'array' ? principal.value.items : [];
	// Root fields that are not the headline array: rendered as a compact block below.
	$: sideEntries =
		root.kind === 'object' ? root.entries.filter((e) => e.key !== principal?.key) : [];
	// A bare array at the root (no wrapper object) is still worth carding.
	$: rootArrayItems = root.kind === 'array' ? root.items : [];
	$: items = cards.length > 0 ? cards : rootArrayItems;
	$: itemsLabel = principal ? humaniseKey(principal.key) : 'items';

	/**
	 * The score on a card, if it has one.
	 *
	 * `scoreFraction` already encodes which keys count as a score and that the scale is
	 * 0-100; reusing it keeps the collapsed badge and the expanded bar in agreement.
	 * Returns the raw number too, since the badge prints it.
	 */
	function scoreOf(item: PartialValue): { n: number; frac: number } | null {
		if (item.kind !== 'object') return null;
		for (const e of item.entries) {
			const frac = scoreFraction(e.key, e.value);
			if (frac !== null && e.value?.kind === 'number') return { n: Number(e.value.value), frac };
		}
		return null;
	}

	/** Which field the badge is showing, so its tooltip can name it. */
	function scoreKeyOf(item: PartialValue): string | null {
		if (item.kind !== 'object') return null;
		for (const e of item.entries) {
			if (scoreFraction(e.key, e.value) !== null) return e.key;
		}
		return null;
	}

	type SortMode = 'score' | 'order';
	let sortMode: SortMode = 'score';
	// A new call resets the view; the default is score because the ranking is the point.
	$: if (root) sortMode = 'score';

	/**
	 * Render order, as indices into `items`. The underlying array is never reordered —
	 * the streaming parser appends to it, and sorting it in place would fight that.
	 *
	 * While a call is still streaming the last card is mid-write. Sorting it by a score
	 * that has not been parsed yet would make it jump around under the cursor, so it is
	 * pinned to the end until the array completes.
	 *
	 * Cards with no score keep their model order among themselves, after the scored
	 * ones — an unscored card has no position to claim.
	 */
	/**
	 * Sorting applies live: watching the ranking assemble itself as each score lands is
	 * the point of this view, so it must not wait for the response to finish.
	 *
	 * Everything this depends on is read *inside* the reactive block on purpose. Svelte
	 * derives a `$:` statement's dependencies from the variables it reads directly, so
	 * factoring the body out into `computeOrder(sortMode)` silently narrowed them to
	 * `sortMode` alone — the order then never recomputed as `items` grew, and a
	 * streaming call rendered only its first card while the header counted the rest.
	 */
	$: viewOrder = (() => {
		const idx = items.map((_, i) => i);
		if (sortMode === 'order') return idx;
		// The last card is mid-write and its score has not parsed yet, so it is pinned
		// to the end rather than thrashing position under the cursor.
		const writingIdx = live && !complete ? items.length - 1 : -1;
		const scored = idx.filter((i) => i !== writingIdx);
		scored.sort((a, b) => {
			const sa = scoreOf(items[a]);
			const sb = scoreOf(items[b]);
			if (sa && sb) return sb.n - sa.n || a - b;
			// Unscored cards hold model order among themselves, after the scored ones.
			if (sa) return -1;
			if (sb) return 1;
			return a - b;
		});
		return writingIdx >= 0 ? [...scored, writingIdx] : scored;
	})();

	// Any score at all to sort by? Matches (topics, matches) have none, and offering a
	// sort control that does nothing is worse than not offering one.
	$: hasScores = items.some((it) => scoreOf(it) !== null);

	/**
	 * Stable identity for a card, so reordering animates instead of re-mounting.
	 *
	 * Keying by array index would make a sort look like every card's *content*
	 * changed, which re-renders the in-progress card and kills its typewriter. The
	 * index is part of the key because it is the only thing that is stable while a
	 * card's own text is still arriving.
	 */
	const cardKey = (i: number) => i;

	/** Cards are numerous; only the newest few stay expanded while streaming. */
	let expanded = new Set<number>();
	function toggle(i: number) {
		const next = new Set(expanded);
		if (next.has(i)) next.delete(i);
		else next.add(i);
		expanded = next;
	}
	// Reset when the underlying call changes (root identity changes with it).
	$: if (root) expanded = new Set<number>();

	function isOpen(i: number, n: number, set: Set<number>): boolean {
		if (set.has(i)) return true;
		// While streaming, the last card is the one being written — keep it visible.
		return live && !complete && i === n - 1;
	}

	function entriesOf(v: PartialValue): PartialEntry[] {
		return v.kind === 'object' ? v.entries : [];
	}

	/** Long prose fields get their own block; short scalars sit inline. */
	function isProse(e: PartialEntry): boolean {
		return e.value?.kind === 'string' && e.value.value.length > 90;
	}

	function isArrayOfStrings(v: PartialValue | null): boolean {
		return !!v && v.kind === 'array' && v.items.every((x) => x.kind === 'string');
	}
</script>

<div class="js" class:js-reduced={reduced}>
	{#if items.length > 0}
		<div class="js-head">
			<span class="js-count">{items.length}</span>
			<span class="js-label">{itemsLabel}</span>
			{#if !complete && live}
				<span class="js-live" aria-label="still arriving">
					<i></i><i></i><i></i>
				</span>
			{/if}
			{#if hasScores}
				<!-- Applies once the stream lands, so the sort animation and the
				     follow-the-newest scroll never move the list at the same time. -->
				<span class="js-sort" role="group" aria-label="Sort items">
					<button
						type="button"
						class:on={sortMode === 'score'}
						on:click={() => (sortMode = 'score')}
						aria-pressed={sortMode === 'score'}
						title="Highest scoring first">Score</button
					>
					<button
						type="button"
						class:on={sortMode === 'order'}
						on:click={() => (sortMode = 'order')}
						aria-pressed={sortMode === 'order'}
						title="The order the model emitted them">Model order</button
					>
				</span>
			{/if}
		</div>

		<ol class="js-cards">
			{#each viewOrder as i (cardKey(i))}
				{@const item = items[i]}
				{@const open = isOpen(i, items.length, expanded)}
				{@const writing = live && !complete && i === items.length - 1}
				{@const score = scoreOf(item)}
				<li
					class="js-card"
					class:writing
					class:open
					animate:flip={{ duration: reduced ? 0 : 320 }}
				>
					<button type="button" class="js-card-head" on:click={() => toggle(i)}>
						<!-- The model's own position, kept even when sorted by score: it is how
						     you tell what the sort actually moved. -->
						<span class="js-idx">{i + 1}</span>
						<span class="js-preview">{previewOf(item, 160)}</span>
						{#if score}
							<span
								class="js-badge"
								style="--f: {score.frac}"
								title="{humaniseKey(scoreKeyOf(item) ?? 'score')}: {score.n}">{score.n}</span
							>
						{/if}
						<span class="js-caret" class:open aria-hidden="true">▸</span>
					</button>

					{#if open && item.kind === 'object'}
						<dl class="js-fields">
							{#each entriesOf(item) as e (e.key)}
								{@const frac = scoreFraction(e.key, e.value)}
								<div class="js-field" class:prose={isProse(e)}>
									<dt>{humaniseKey(e.key)}</dt>
									<dd>
										{#if e.value === null}
											<span class="js-pending">…</span>
										{:else if frac !== null}
											<span class="js-score">
												<span class="js-score-bar"
													><span class="js-score-fill" style="width: {frac * 100}%"></span></span
												>
												<span class="js-score-num">{e.value.kind === 'number' ? e.value.value : ''}</span>
											</span>
										{:else if isArrayOfStrings(e.value)}
											<span class="js-tags">
												{#each e.value.kind === 'array' ? e.value.items : [] as tag, ti (ti)}
													<span class="js-tag">{tag.kind === 'string' ? tag.value : ''}</span>
												{/each}
											</span>
										{:else if e.value.kind === 'string'}
											<span class="js-str">{e.value.value}{#if !e.value.complete}<span
														class="js-cursor"
													></span>{/if}</span
											>
										{:else if e.value.kind === 'number'}
											<span class="js-num">{e.value.value}</span>
										{:else if e.value.kind === 'boolean'}
											<span class="js-bool">{e.value.value}</span>
										{:else if e.value.kind === 'null'}
											<span class="js-null">null</span>
										{:else if e.value.kind === 'array'}
											<span class="js-nested">{e.value.items.length} entries</span>
										{:else}
											<span class="js-nested">{e.value.entries.length} fields</span>
										{/if}
									</dd>
								</div>
							{/each}
						</dl>
					{:else if open && item.kind === 'string'}
						<p class="js-loose">{item.value}</p>
					{/if}
				</li>
			{/each}
		</ol>
	{/if}

	{#each sideEntries as e (e.key)}
		<section class="js-side">
			<h5>{humaniseKey(e.key)}</h5>
			{#if e.value === null}
				<p class="js-pending">…</p>
			{:else if isArrayOfStrings(e.value)}
				<ul class="js-bullets">
					{#each e.value.kind === 'array' ? e.value.items : [] as s, si (si)}
						<li>{s.kind === 'string' ? s.value : ''}</li>
					{/each}
				</ul>
			{:else if e.value.kind === 'string'}
				<p class="js-side-text">
					{e.value.value}{#if !e.value.complete}<span class="js-cursor"></span>{/if}
				</p>
			{:else if e.value.kind === 'array'}
				<ol class="js-bullets">
					{#each e.value.items as v, vi (vi)}
						<li>{previewOf(v, 220)}</li>
					{/each}
				</ol>
			{:else}
				<p class="js-side-text">{previewOf(e.value, 300)}</p>
			{/if}
		</section>
	{/each}

	{#if items.length === 0 && sideEntries.length === 0}
		<p class="js-empty">waiting for the first field…</p>
	{/if}
</div>

<style>
	.js {
		font-size: 0.76rem;
		line-height: 1.5;
	}

	.js-head {
		display: flex;
		align-items: center;
		gap: 0.4rem;
		margin-bottom: 0.4rem;
	}
	.js-count {
		font-size: 0.95rem;
		font-weight: 700;
		font-variant-numeric: tabular-nums;
		color: var(--accent, #E63946);
	}
	.js-label {
		font-size: 0.58rem;
		letter-spacing: 0.09em;
		text-transform: uppercase;
		color: #737373;
	}
	.js-live {
		display: inline-flex;
		gap: 2px;
		margin-left: 0.15rem;
	}
	.js-live i {
		width: 3px;
		height: 3px;
		border-radius: 50%;
		background: var(--accent, #E63946);
		animation: jsdot 1.1s ease-in-out infinite;
	}
	.js-live i:nth-child(2) {
		animation-delay: 0.15s;
	}
	.js-live i:nth-child(3) {
		animation-delay: 0.3s;
	}
	@keyframes jsdot {
		0%,
		60%,
		100% {
			opacity: 0.25;
		}
		30% {
			opacity: 1;
		}
	}

	.js-cards {
		display: flex;
		flex-direction: column;
		gap: 3px;
		list-style: none;
		padding: 0;
		margin: 0;
	}

	.js-card {
		border-radius: 5px;
		background: rgb(0 0 0 / 0.028);
		border-left: 2px solid transparent;
		overflow: hidden;
	}
	:global(.dark) .js-card {
		background: rgb(255 255 255 / 0.04);
	}
	.js-card.open {
		border-left-color: var(--accent, #E63946);
		background: rgb(0 0 0 / 0.045);
	}
	:global(.dark) .js-card.open {
		background: rgb(255 255 255 / 0.06);
	}
	.js-card.writing {
		border-left-color: var(--accent, #E63946);
		animation: jsedge 1.4s ease-in-out infinite;
	}
	@keyframes jsedge {
		0%,
		100% {
			border-left-color: color-mix(in srgb, var(--accent, #E63946) 35%, transparent);
		}
		50% {
			border-left-color: var(--accent, #E63946);
		}
	}

	.js-card-head {
		display: flex;
		align-items: baseline;
		gap: 0.45rem;
		width: 100%;
		text-align: left;
		padding: 0.35rem 0.5rem;
		background: none;
		border: none;
		cursor: pointer;
		color: inherit;
	}
	@media (hover: hover) and (pointer: fine) {
		.js-card-head:hover .js-preview {
			color: #262626;
		}
	}
	@media (hover: hover) and (pointer: fine) {
		:global(.dark) .js-card-head:hover .js-preview {
			color: #f5f5f5;
		}
	}
	.js-idx {
		font-size: 0.58rem;
		font-variant-numeric: tabular-nums;
		font-weight: 700;
		color: #a3a3a3;
		flex: none;
		min-width: 1.1rem;
	}
	.js-preview {
		flex: 1;
		min-width: 0;
		color: #525252;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
	:global(.dark) .js-preview {
		color: #a3a3a3;
	}
	.js-card.open .js-preview {
		white-space: normal;
		font-weight: 600;
		color: #262626;
	}
	:global(.dark) .js-card.open .js-preview {
		color: #f5f5f5;
	}
	/* The score, on the collapsed row. Tinted by value rather than a bar: at this size
	   a bar is a smear, whereas a number you can actually read is the thing being
	   sorted on. The expanded field keeps its bar. */
	.js-badge {
		flex: none;
		font-size: 0.6rem;
		font-weight: 700;
		font-variant-numeric: tabular-nums;
		padding: 0 5px;
		border-radius: 3px;
		color: #525252;
		background: rgb(0 0 0 / 0.05);
		/* --f is 0..1; high scores lift toward the accent so a scan finds them. */
		background: color-mix(in srgb, var(--accent, #E63946) calc(var(--f) * 22%), rgb(0 0 0 / 0.05));
	}
	:global(.dark) .js-badge {
		color: #d4d4d4;
		background: color-mix(
			in srgb,
			var(--accent, #E63946) calc(var(--f) * 30%),
			rgb(255 255 255 / 0.08)
		);
	}

	.js-sort {
		display: inline-flex;
		margin-left: auto;
		flex: none;
		border-radius: 5px;
		overflow: hidden;
		border: 1px solid rgb(0 0 0 / 0.12);
	}
	:global(.dark) .js-sort {
		border-color: rgb(255 255 255 / 0.14);
	}
	.js-sort button {
		font-size: 0.55rem;
		font-weight: 650;
		letter-spacing: 0.03em;
		padding: 2px 6px;
		color: #737373;
		background: transparent;
		cursor: pointer;
		transition: background 120ms ease, color 120ms ease;
	}
	@media (hover: hover) and (pointer: fine) {
		.js-sort button:hover {
			color: var(--accent, #E63946);
			background: rgb(0 0 0 / 0.04);
		}
	}
	.js-sort button.on {
		color: #fff;
		background: var(--accent, #E63946);
	}

	.js-caret {
		flex: none;
		font-size: 0.6rem;
		color: #a3a3a3;
		transition: transform 140ms ease;
	}
	.js-caret.open {
		transform: rotate(90deg);
	}

	.js-fields {
		padding: 0.1rem 0.55rem 0.5rem 1.65rem;
		display: flex;
		flex-direction: column;
		gap: 0.28rem;
	}
	.js-field {
		display: grid;
		grid-template-columns: 7.5rem 1fr;
		gap: 0.5rem;
		align-items: baseline;
	}
	.js-field.prose {
		grid-template-columns: 1fr;
		gap: 0.1rem;
	}
	.js-field dt {
		font-size: 0.58rem;
		letter-spacing: 0.06em;
		text-transform: uppercase;
		color: #a3a3a3;
	}
	.js-field dd {
		margin: 0;
		min-width: 0;
		color: #404040;
	}
	:global(.dark) .js-field dd {
		color: #d4d4d4;
	}

	.js-str,
	.js-loose {
		white-space: pre-wrap;
		overflow-wrap: anywhere;
	}
	.js-num,
	.js-bool {
		font-variant-numeric: tabular-nums;
		font-weight: 600;
		color: #0369a1;
	}
	:global(.dark) .js-num,
	:global(.dark) .js-bool {
		color: #7dd3fc;
	}
	.js-null,
	.js-pending {
		color: #a3a3a3;
		font-style: italic;
	}
	.js-nested {
		color: #737373;
		font-style: italic;
	}

	.js-score {
		display: inline-flex;
		align-items: center;
		gap: 0.4rem;
	}
	.js-score-bar {
		width: 5rem;
		height: 5px;
		border-radius: 3px;
		background: rgb(0 0 0 / 0.09);
		overflow: hidden;
	}
	:global(.dark) .js-score-bar {
		background: rgb(255 255 255 / 0.12);
	}
	.js-score-fill {
		display: block;
		height: 100%;
		border-radius: 3px;
		background: var(--accent, #E63946);
	}
	.js-score-num {
		font-variant-numeric: tabular-nums;
		font-weight: 700;
		font-size: 0.7rem;
	}

	.js-tags {
		display: inline-flex;
		flex-wrap: wrap;
		gap: 3px;
	}
	.js-tag {
		font-size: 0.6rem;
		padding: 1px 6px;
		border-radius: 999px;
		background: rgb(0 0 0 / 0.06);
		color: #525252;
	}
	:global(.dark) .js-tag {
		background: rgb(255 255 255 / 0.09);
		color: #d4d4d4;
	}

	.js-side {
		margin-top: 0.75rem;
	}
	.js-side h5 {
		font-size: 0.58rem;
		letter-spacing: 0.09em;
		text-transform: uppercase;
		color: #737373;
		margin-bottom: 0.25rem;
	}
	.js-side-text {
		white-space: pre-wrap;
		overflow-wrap: anywhere;
		color: #404040;
	}
	:global(.dark) .js-side-text {
		color: #d4d4d4;
	}
	.js-bullets {
		display: flex;
		flex-direction: column;
		gap: 0.2rem;
		padding-left: 1rem;
		color: #404040;
	}
	:global(.dark) .js-bullets {
		color: #d4d4d4;
	}
	.js-bullets li {
		list-style: disc;
	}

	.js-empty {
		color: #a3a3a3;
		font-style: italic;
	}

	/* The value currently being written, mirroring the text transcript's caret. */
	.js-cursor {
		display: inline-block;
		width: 0.42em;
		height: 1em;
		margin-left: 1px;
		vertical-align: text-bottom;
		background: var(--accent, #E63946);
		animation: jscaret 1s steps(2, start) infinite;
	}
	@keyframes jscaret {
		0%,
		50% {
			opacity: 1;
		}
		50.01%,
		100% {
			opacity: 0;
		}
	}

	.js-reduced .js-cursor,
	.js-reduced .js-card.writing,
	.js-reduced .js-live i {
		animation: none;
	}

	@media (prefers-reduced-motion: reduce) {
		.js-cursor,
		.js-card.writing,
		.js-live i {
			animation: none;
		}
		.js-caret,
		.js-sort button {
			transition: none;
		}
	}

	@media (max-width: 640px) {
		.js-field {
			grid-template-columns: 1fr;
			gap: 0.05rem;
		}
	}
</style>
