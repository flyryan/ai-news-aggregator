<script lang="ts">
	import { tick as svelteTick } from 'svelte';
	import type { ReplayCall, ReplayCallStream } from '$lib/types/replay';
	import {
		providerColor,
		profileColor,
		formatClock,
		formatDuration,
		formatTokens,
		formatCost,
		ROLE_LABELS,
		OUTCOME_COLORS,
		ttftOf
	} from '$lib/services/replayViz';
	import { createStreamRenderer, withCaret } from '$lib/services/replayMarkdown';

	// One renderer per pane: they re-render on the same frames, so a shared cache
	// would be invalidated by the other caller every time.
	const renderThinking = createStreamRenderer();
	const renderAnswer = createStreamRenderer();

	export let call: ReplayCall;
	export let stream: ReplayCallStream | null = null;
	export let streamState: 'idle' | 'loading' | 'ready' | 'unavailable' = 'idle';
	/**
	 * False when the whole run predates token capture (every call is stream-less), as
	 * opposed to this one call's deltas having been pruned to fit the size cap. The
	 * two cases get different copy — "we never recorded it" is not "we dropped it".
	 */
	export let runHasStreams = true;
	/** False for offline-reconstructed runs: no queue wait, no first-token time. */
	export let timingsMeasured = true;
	export let t: number;
	export let reduced = false;
	export let onSeek: (ms: number) => void = () => {};
	export let onClose: () => void = () => {};

	let bodyEl: HTMLDivElement | null = null;
	let autoScroll = true;

	// Prefix sums over the deltas: rendering at time `t` is a binary search plus a
	// slice, never a per-frame accumulation. Recomputed only when the call changes.
	//
	// Store cumulative *lengths*, not cumulative strings — a marquee call can carry
	// ~800 deltas over 150k chars, and materialising a growing string per delta is
	// O(n²) in both time and memory (tens of MB, enough to lock the tab). Two joined
	// strings plus two Int32Arrays give the same O(log n) lookup for O(n) memory.
	interface Prefixes {
		times: number[];
		thinking: string;
		text: string;
		/** Chars of reasoning emitted after delta i. */
		thinkingLen: Int32Array;
		/** Chars of answer emitted after delta i. */
		textLen: Int32Array;
	}

	function buildPrefixes(s: ReplayCallStream | null): Prefixes | null {
		if (!s || s.t.length === 0) return null;
		const n = s.t.length;
		const thinkingLen = new Int32Array(n);
		const textLen = new Int32Array(n);
		const thinkingParts: string[] = [];
		const textParts: string[] = [];
		let tl = 0;
		let xl = 0;
		for (let i = 0; i < n; i++) {
			const chunk = s.text[i] ?? '';
			if (s.kind[i] === 0) {
				thinkingParts.push(chunk);
				tl += chunk.length;
			} else {
				textParts.push(chunk);
				xl += chunk.length;
			}
			thinkingLen[i] = tl;
			textLen[i] = xl;
		}
		return {
			times: s.t,
			thinking: thinkingParts.join(''),
			text: textParts.join(''),
			thinkingLen,
			textLen
		};
	}

	$: prefixes = buildPrefixes(stream);

	function upperBound(arr: number[], value: number): number {
		let lo = 0;
		let hi = arr.length;
		while (lo < hi) {
			const mid = (lo + hi) >> 1;
			if (arr[mid] <= value) lo = mid + 1;
			else hi = mid;
		}
		return lo;
	}

	$: cursor = prefixes ? upperBound(prefixes.times, t) : 0;
	$: thinkingText =
		prefixes && cursor > 0 ? prefixes.thinking.slice(0, prefixes.thinkingLen[cursor - 1]) : '';
	$: answerText =
		prefixes && cursor > 0 ? prefixes.text.slice(0, prefixes.textLen[cursor - 1]) : '';
	$: streamDone = prefixes ? cursor >= prefixes.times.length : false;
	$: isBefore = t < call.start_ms;
	$: isLive = t >= call.start_ms && t < call.end_ms;
	$: isAfter = t >= call.end_ms;

	// Where the clock sits inside this call, as a caption + a mini progress bar.
	$: localT = Math.min(Math.max(0, t - call.queued_ms), call.end_ms - call.queued_ms);
	$: localSpan = Math.max(1, call.end_ms - call.queued_ms);

	$: ttft = ttftOf(call);
	$: accent = providerColor(call.provider_id);

	// Keep the newest text in view while it's being written.
	//
	// Two separate feedback loops have to be broken here, and both lock the tab:
	//
	//  1. Svelte instruments *member* assignments on reactive `let`s, so writing
	//     `bodyEl.scrollTop = …` inside a `$:` block that also reads `bodyEl`
	//     invalidates `bodyEl` and re-runs the block forever. Aliasing to a plain
	//     local (`el`) keeps the write out of the reactive graph entirely.
	//  2. Programmatic scrolling fires `on:scroll`, which would reassign
	//     `autoScroll`. The `selfScrolling` flag makes our own scroll writes
	//     invisible to the handler; only genuine user scrolls change intent.
	let selfScrolling = false;

	function stickToBottom(el: HTMLDivElement) {
		selfScrolling = true;
		el.scrollTop = el.scrollHeight;
		// Cleared after the scroll event has been dispatched, not before.
		requestAnimationFrame(() => {
			selfScrolling = false;
		});
	}

	$: if (bodyEl && autoScroll && (thinkingText || answerText)) {
		const el = bodyEl;
		void svelteTick().then(() => {
			if (autoScroll && el.isConnected) stickToBottom(el);
		});
	}

	function onBodyScroll(event: Event) {
		if (selfScrolling) return;
		const el = event.currentTarget as HTMLDivElement | null;
		if (!el) return;
		const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
		if (nearBottom !== autoScroll) autoScroll = nearBottom;
	}

	$: hasThinking = call.thinking_chars > 0;

	// Model output is markdown; rendering it as preformatted text turns every
	// heading and bullet into literal `##` / `-` noise. The caret is spliced into
	// the last block so it trails the final word instead of dropping a line.
	// The caret marks where the model is writing *now*, so it belongs to exactly one
	// pane: the answer once text has started, the reasoning pane before that.
	$: showCaret = isLive && !streamDone && !reduced;
	$: caretHtml = showCaret ? '<span class="caret"></span>' : '';
	$: thinkingHtml = withCaret(renderThinking(thinkingText), answerText ? '' : caretHtml);
	$: answerHtml = withCaret(renderAnswer(answerText), caretHtml);
	$: showEmptyStream = streamState === 'unavailable' || (streamState === 'ready' && !prefixes);
</script>

<div class="transcript card !p-0 overflow-hidden" style="--accent: {accent}">
	<header class="ts-head">
		<div class="min-w-0">
			<div class="ts-eyebrow">
				<span class="route" style="background: {accent}">{call.provider_id}</span>
				<span class="profile" style="--p: {profileColor(call.profile)}">{call.profile}</span>
				<span class="role">{ROLE_LABELS[call.role] ?? call.role}</span>
				{#if call.worker != null}<span class="worker">worker {call.worker}</span>{/if}
				{#if call.outcome !== 'ok'}
					<span class="outcome" style="--o: {OUTCOME_COLORS[call.outcome] ?? '#737373'}"
						>{call.outcome}</span
					>
				{/if}
				{#if call.fallback_from}
					<span class="outcome" style="--o: #a855f7">failover ← {call.fallback_from}</span>
				{/if}
			</div>
			<h3 class="ts-title">{call.task}</h3>
			<p class="ts-caller">{call.caller}</p>
		</div>

		<button type="button" class="ts-close" on:click={onClose} aria-label="Close transcript">
			×
		</button>
	</header>

	<dl class="ts-stats">
		<div><dt>Duration</dt><dd>{formatDuration(call.end_ms - call.start_ms)}</dd></div>
		{#if timingsMeasured}
			<div><dt>Queue wait</dt><dd>{formatDuration(call.wait_ms)}</dd></div>
			<div><dt>First token</dt><dd>{ttft != null ? formatDuration(ttft) : '—'}</dd></div>
		{:else}
			<div>
				<dt>Queue / TTFT</dt>
				<dd class="ts-unrecorded" title="This run's timings were reconstructed from logs after the fact">
					not recorded
				</dd>
			</div>
		{/if}
		<div><dt>Effort</dt><dd>{call.effort}</dd></div>
		<div><dt>Input</dt><dd>{formatTokens(call.input_tokens)}</dd></div>
		<div><dt>Output</dt><dd>{formatTokens(call.output_tokens)}</dd></div>
		{#if call.cache_read_tokens > 0}
			<div><dt>Cache read</dt><dd>{formatTokens(call.cache_read_tokens)}</dd></div>
		{/if}
		<div><dt>Cost</dt><dd>{formatCost(call.cost_usd)}</dd></div>
		<div><dt>Stop</dt><dd>{call.stop_reason ?? '—'}</dd></div>
	</dl>

	<!-- Scrub within this one call -->
	<div class="ts-scrub">
		<button
			type="button"
			class="jump"
			on:click={() => onSeek(call.queued_ms)}
			title="Jump to the start of this call">⏮ start</button
		>
		<div class="mini">
			<span class="mini-fill" style="width: {(localT / localSpan) * 100}%"></span>
			{#if call.first_token_ms != null}
				<span
					class="mini-mark"
					style="left: {((call.first_token_ms - call.queued_ms) / localSpan) * 100}%"
					title="First token"
				></span>
			{/if}
		</div>
		<span class="mini-clock">{formatClock(t)}</span>
		<button
			type="button"
			class="jump"
			on:click={() => onSeek(call.end_ms)}
			title="Jump to the end of this call">end ⏭</button
		>
	</div>

	<div class="ts-body" bind:this={bodyEl} on:scroll={onBodyScroll}>
		{#if streamState === 'loading'}
			<p class="ts-note">Loading stream…</p>
		{:else if showEmptyStream}
			<div class="ts-note ts-note-block">
				<p class="font-medium">
					{runHasStreams ? 'Stream not retained for this call.' : 'No token capture for this run.'}
				</p>
				<p>
					{#if runHasStreams}
						The replay index keeps every call's timing and token accounting forever; the token-level
						deltas are pruned by size.
					{:else}
						This run finished before the recorder existed, so nothing captured the model's output
						token by token. Everything else about the call is exact — it is accounting, not a guess.
					{/if}
				</p>
				<dl class="ts-fallback">
					<div>
						<dt>Wrote</dt>
						<dd>{formatTokens(call.output_tokens)} output tokens</dd>
					</div>
					<div>
						<dt>Read</dt>
						<dd>{formatTokens(call.input_tokens)} input tokens</dd>
					</div>
					<div>
						<dt>Took</dt>
						<dd>{formatDuration(call.end_ms - call.start_ms)}</dd>
					</div>
					<div>
						<dt>Cost</dt>
						<dd>{formatCost(call.cost_usd)}</dd>
					</div>
					<div>
						<dt>Rate</dt>
						<dd>
							{Math.max(
								1,
								Math.round(call.output_tokens / Math.max(1, (call.end_ms - call.start_ms) / 1000))
							)} tok/s
						</dd>
					</div>
				</dl>
				<p class="ts-fallback-foot">
					Ran on <strong>{call.model}</strong> at <strong>{call.effort}</strong> effort
					{#if call.thinking_chars > 0}
						· {call.thinking_chars.toLocaleString()} characters of reasoning
					{/if}
					· the bar above still scrubs the real {formatDuration(call.end_ms - call.queued_ms)} this
					request occupied.
				</p>
			</div>
		{:else if isBefore}
			<p class="ts-note">
				This call starts at <button type="button" class="linkish" on:click={() => onSeek(call.queued_ms)}
					>{formatClock(call.queued_ms)}</button
				>. Play on, or jump to it.
			</p>
		{:else}
			{#if hasThinking}
				<section class="reasoning" class:collapsed={!thinkingText}>
					<h4>
						<span class="reasoning-glyph" aria-hidden="true">✻</span>
						Reasoning
						<span class="reasoning-meta">
							{thinkingText.length.toLocaleString()} / {call.thinking_chars.toLocaleString()} chars
						</span>
					</h4>
					{#if thinkingText}
						<!-- eslint-disable-next-line svelte/no-at-html-tags -->
						<div class="reasoning-text md">{@html thinkingHtml}</div>
					{:else}
						<p class="reasoning-text pending">waiting for the first thinking delta…</p>
					{/if}
				</section>
			{/if}

			<section class="answer">
				<h4>
					Output
					<span class="answer-meta">
						{answerText.length.toLocaleString()} / {call.text_chars.toLocaleString()} chars
					</span>
				</h4>
				{#if answerText}
					<!-- eslint-disable-next-line svelte/no-at-html-tags -->
					<div class="answer-text md">{@html answerHtml}</div>
				{:else if isLive}
					<p class="answer-text pending">
						{hasThinking && thinkingText ? 'still reasoning…' : 'waiting for first token…'}
					</p>
				{:else if isAfter}
					<p class="answer-text pending">No text deltas were captured for this call.</p>
				{/if}
			</section>
		{/if}
	</div>
</div>

<style>
	.transcript {
		display: flex;
		flex-direction: column;
		border-top: 3px solid var(--accent);
	}

	.ts-head {
		display: flex;
		align-items: flex-start;
		justify-content: space-between;
		gap: 0.5rem;
		padding: 0.7rem 0.9rem 0.5rem;
	}

	.ts-eyebrow {
		display: flex;
		align-items: center;
		gap: 0.3rem;
		flex-wrap: wrap;
		margin-bottom: 2px;
	}
	.route {
		font-size: 0.55rem;
		font-weight: 700;
		letter-spacing: 0.05em;
		text-transform: uppercase;
		color: #fff;
		padding: 1px 5px;
		border-radius: 3px;
	}
	.profile {
		font-size: 0.55rem;
		font-weight: 700;
		letter-spacing: 0.05em;
		padding: 1px 5px;
		border-radius: 3px;
		color: var(--p);
		border: 1px solid color-mix(in srgb, var(--p) 45%, transparent);
	}
	.role,
	.worker {
		font-size: 0.58rem;
		color: #737373;
	}
	.outcome {
		font-size: 0.55rem;
		font-weight: 700;
		text-transform: uppercase;
		letter-spacing: 0.04em;
		color: var(--o);
		border: 1px solid color-mix(in srgb, var(--o) 45%, transparent);
		border-radius: 3px;
		padding: 1px 5px;
	}

	.ts-title {
		font-size: 0.95rem;
		font-weight: 700;
		color: #262626;
		line-height: 1.2;
	}
	:global(.dark) .ts-title {
		color: #f5f5f5;
	}
	.ts-caller {
		font-size: 0.62rem;
		font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
		color: #737373;
		word-break: break-all;
	}

	.ts-close {
		font-size: 1.4rem;
		line-height: 1;
		color: #737373;
		padding: 0 0.3rem;
		border-radius: 4px;
		flex: none;
	}
	.ts-close:hover {
		color: #E63946;
		background: rgb(230 57 70 / 0.1);
	}

	.ts-stats {
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(5.2rem, 1fr));
		gap: 0.35rem 0.6rem;
		padding: 0 0.9rem 0.6rem;
	}
	.ts-stats dt {
		font-size: 0.52rem;
		letter-spacing: 0.07em;
		text-transform: uppercase;
		color: #737373;
	}
	.ts-stats dd {
		font-size: 0.75rem;
		font-weight: 650;
		font-variant-numeric: tabular-nums;
		color: #262626;
	}
	:global(.dark) .ts-stats dd {
		color: #e5e5e5;
	}

	.ts-scrub {
		display: flex;
		align-items: center;
		gap: 0.5rem;
		padding: 0.4rem 0.9rem;
		border-top: 1px solid rgb(0 0 0 / 0.07);
		border-bottom: 1px solid rgb(0 0 0 / 0.07);
		background: rgb(0 0 0 / 0.02);
	}
	:global(.dark) .ts-scrub {
		border-color: rgb(255 255 255 / 0.08);
		background: rgb(255 255 255 / 0.03);
	}
	.jump {
		font-size: 0.6rem;
		font-weight: 600;
		color: #525252;
		white-space: nowrap;
	}
	:global(.dark) .jump {
		color: #a3a3a3;
	}
	.jump:hover {
		color: var(--accent);
	}
	.mini {
		position: relative;
		flex: 1;
		height: 4px;
		border-radius: 999px;
		background: rgb(0 0 0 / 0.1);
		overflow: visible;
	}
	:global(.dark) .mini {
		background: rgb(255 255 255 / 0.12);
	}
	.mini-fill {
		position: absolute;
		left: 0;
		top: 0;
		bottom: 0;
		border-radius: 999px;
		background: var(--accent);
	}
	.mini-mark {
		position: absolute;
		top: -3px;
		bottom: -3px;
		width: 1.5px;
		background: #8b5cf6;
	}
	.mini-clock {
		font-size: 0.62rem;
		font-variant-numeric: tabular-nums;
		color: #737373;
	}

	.ts-body {
		padding: 0.75rem 0.9rem 1rem;
		max-height: 22rem;
		overflow-y: auto;
	}

	.ts-note {
		font-size: 0.75rem;
		color: #737373;
		line-height: 1.5;
	}
	.ts-note-block {
		border-left: 2px solid #a3a3a3;
		padding-left: 0.6rem;
	}
	.ts-note-block p + p {
		margin-top: 0.3rem;
	}

	/* The stats that stand in for a stream we do not have. */
	.ts-fallback {
		display: flex;
		flex-wrap: wrap;
		gap: 0.45rem 1.4rem;
		margin: 0.7rem 0 0.55rem;
	}
	.ts-fallback dt {
		font-size: 0.55rem;
		letter-spacing: 0.09em;
		text-transform: uppercase;
		color: #a3a3a3;
	}
	.ts-fallback dd {
		font-size: 0.9rem;
		font-weight: 650;
		font-variant-numeric: tabular-nums;
		color: #404040;
	}
	:global(.dark) .ts-fallback dd {
		color: #e5e5e5;
	}
	.ts-fallback-foot {
		font-size: 0.7rem;
	}
	.ts-unrecorded {
		font-style: italic;
		opacity: 0.75;
	}
	.linkish {
		color: #E63946;
		text-decoration: underline;
		font-variant-numeric: tabular-nums;
	}

	.reasoning {
		margin-bottom: 0.9rem;
		padding: 0.55rem 0.7rem;
		border-radius: 0.5rem;
		border-left: 2px solid #8b5cf6;
		background: rgb(139 92 246 / 0.06);
	}
	:global(.dark) .reasoning {
		background: rgb(139 92 246 / 0.1);
	}
	.reasoning h4,
	.answer h4 {
		display: flex;
		align-items: center;
		gap: 0.35rem;
		font-size: 0.55rem;
		font-weight: 700;
		letter-spacing: 0.1em;
		text-transform: uppercase;
		color: #8b5cf6;
		margin-bottom: 0.3rem;
	}
	.answer h4 {
		color: #737373;
	}
	.reasoning-glyph {
		font-size: 0.7rem;
	}
	.reasoning-meta,
	.answer-meta {
		margin-left: auto;
		font-weight: 500;
		letter-spacing: 0;
		text-transform: none;
		font-variant-numeric: tabular-nums;
		opacity: 0.75;
	}

	.reasoning-text {
		font-size: 0.78rem;
		font-style: italic;
		line-height: 1.6;
		color: #5b21b6;
		word-break: break-word;
	}
	:global(.dark) .reasoning-text {
		color: #ddd6fe;
	}

	.answer-text {
		font-size: 0.82rem;
		line-height: 1.65;
		color: #262626;
		word-break: break-word;
	}
	:global(.dark) .answer-text {
		color: #e5e5e5;
	}

	/* Rendered markdown. `:global` because the HTML comes from {@html}, so the
	   compiler cannot see these selectors used and would prune them. Scoped under
	   `.md` so nothing here leaks into the rest of the page. */
	.md :global(p) {
		margin: 0 0 0.6em;
	}
	.md :global(p:last-child) {
		margin-bottom: 0;
	}
	.md :global(h2),
	.md :global(h3),
	.md :global(h4) {
		font-size: 0.85rem;
		font-weight: 700;
		font-style: normal;
		line-height: 1.35;
		margin: 1em 0 0.4em;
	}
	.md :global(h2:first-child),
	.md :global(h3:first-child),
	.md :global(h4:first-child) {
		margin-top: 0;
	}
	.md :global(h3) {
		font-size: 0.8rem;
	}
	.md :global(h4) {
		font-size: 0.76rem;
		opacity: 0.85;
	}
	.md :global(ul) {
		margin: 0 0 0.6em;
		padding-left: 1.1em;
		list-style: disc;
	}
	.md :global(ul:last-child) {
		margin-bottom: 0;
	}
	.md :global(li) {
		margin-bottom: 0.22em;
	}
	/* Numbered lists keep the model's own marker, so drop the bullet. */
	.md :global(li.md-ord) {
		list-style: none;
		margin-left: -1.1em;
	}
	.md :global(strong) {
		font-weight: 700;
	}
	.md :global(.md-code) {
		font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
		font-style: normal;
		font-size: 0.92em;
		padding: 0.05em 0.3em;
		border-radius: 3px;
		background: rgb(0 0 0 / 0.06);
	}
	:global(.dark) .md :global(.md-code) {
		background: rgb(255 255 255 / 0.09);
	}
	.md :global(a) {
		color: #E63946;
		text-decoration: underline;
		text-underline-offset: 2px;
	}

	.pending {
		opacity: 0.55;
		font-style: italic;
	}

	/* Global: the caret is spliced into the sanitised HTML, so the compiler never
	   sees it in this component's markup and would otherwise prune the rule. */
	.md :global(.caret) {
		display: inline-block;
		width: 0.45em;
		height: 1em;
		margin-left: 1px;
		vertical-align: text-bottom;
		background: currentColor;
		animation: blink 1.05s step-end infinite;
	}
	@keyframes blink {
		0%,
		49% {
			opacity: 1;
		}
		50%,
		100% {
			opacity: 0;
		}
	}

	@media (prefers-reduced-motion: reduce) {
		.md :global(.caret) {
			animation: none;
			opacity: 0.6;
		}
	}
</style>
