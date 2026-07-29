<script lang="ts">
	import { tick as svelteTick } from 'svelte';
	import type { ReplayCall, ReplayCallStream, ReplayPrompts } from '$lib/types/replay';
	import {
		providerColor,
		profileColor,
		formatClock,
		formatDuration,
		formatTokens,
		formatCost,
		ROLE_LABELS,
		OUTCOME_COLORS,
		ttftOf,
		isImageCall
	} from '$lib/services/replayViz';
	import { createStreamRenderer, withCaret } from '$lib/services/replayMarkdown';
	import { parsePrefix, highlightJson, splitFence } from '$lib/services/replayJson';
	import JsonStream from './JsonStream.svelte';

	// One renderer per pane: they re-render on the same frames, so a shared cache
	// would be invalidated by the other caller every time.
	const renderThinking = createStreamRenderer();
	const renderAnswer = createStreamRenderer();
	// Prompts are static rather than streamed, but they go through the same renderer
	// so every pane shares one markdown dialect. System and user get their own
	// instance because they render together and would evict each other from a
	// single-entry cache.
	const renderPrompt = createStreamRenderer();
	const renderPromptSystem = createStreamRenderer();
	// Prose the model wrote around its JSON payload. Both are markdown in practice —
	// one call opened with a 3k-char `## Analysis` write-up before its fence.
	const renderPreamble = createStreamRenderer();
	const renderEpilogue = createStreamRenderer();

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
	/** The day's prompts, once fetched. Null until then, or when not retained. */
	export let prompts: ReplayPrompts | null = null;
	export let promptsState: 'idle' | 'loading' | 'ready' | 'unavailable' = 'idle';
	/** Fetches the prompts artifact; called the first time a prompt is unfolded. */
	export let onLoadPrompts: () => void | Promise<void> = () => {};
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
	//
	// This scrubber spans one call, so its readout must be call-local. Showing the
	// global run clock made a 3m55s call read "07:25" and kept counting after the
	// bar was full — the number and the bar were measuring different things.
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

	// ------------------------------------------------------- structured output
	//
	// Nearly every call in this pipeline answers with JSON — a wrapper object around
	// one big array of records. As wrapped text that is a wall of braces; parsed, it
	// is the work itself. `parsePrefix` returns null for genuine prose (the executive
	// summary, the release-detection write-up), which falls through to markdown.
	//
	// Parsing runs per frame on a growing string, so it is memoised on the exact
	// input: a paused transcript re-renders for free.
	let jsonCacheKey = '';
	let jsonCacheVal: ReturnType<typeof parsePrefix> = null;
	function parseCached(text: string) {
		if (text === jsonCacheKey) return jsonCacheVal;
		jsonCacheKey = text;
		jsonCacheVal = parsePrefix(text);
		return jsonCacheVal;
	}
	$: parsedJson = answerText ? parseCached(answerText) : null;
	/** Whether *this* call is one the structured view can render at all. */
	$: isJsonCall = parsedJson !== null;

	// Structured is the default when it applies; the raw stream stays one click away
	// because it is the literal thing the model emitted and the replay's whole claim
	// is that nothing here is embellished.
	type OutputView = 'structured' | 'raw';
	let outputView: OutputView = 'structured';
	$: if (call.id) outputView = 'structured';
	$: showStructured = isJsonCall && outputView === 'structured';

	// The raw view's whole job is to show the literal bytes, so it must not go
	// through the markdown renderer — that strips the fence and reflows the payload
	// into paragraphs, which is the opposite of "raw". Highlighted, monospaced, with
	// the model's own line breaks intact.
	let rawCacheKey = '';
	let rawCacheVal: { lang: string | null; html: string } = { lang: null, html: '' };
	function highlightCached(text: string) {
		if (text === rawCacheKey) return rawCacheVal;
		rawCacheKey = text;
		const { lang, body } = splitFence(text);
		rawCacheVal = { lang, html: highlightJson(body) };
		return rawCacheVal;
	}
	$: rawJson = isJsonCall && outputView === 'raw' && answerText ? highlightCached(answerText) : null;

	// ------------------------------------------------------------ image calls
	//
	// The hero call is the one request in the run whose output you can look at. It
	// ran on an image client rather than an LLM route, so it has no stream, no
	// thinking, and no token accounting — every one of those fields is a structural
	// zero. This branch renders the picture and the prompt instead, and the header
	// above drops the token/cost stats rather than printing zeros as if measured.
	$: isImage = isImageCall(call);
	$: imageUrl = call.image_url ?? null;
	$: imagePrompt = call.image_prompt ?? null;

	// A missing hero (pruned, 404, never generated) degrades to prompt-only rather
	// than a broken-image glyph. Reset when the selected call changes.
	let imageFailed = false;
	$: if (call.id) imageFailed = false;

	// Prompts are big (a research batch sends ~130k chars), so this always starts
	// folded and the artifact holding them is only fetched when it is opened.
	let promptOpen = false;
	$: if (call.id) promptOpen = false;

	/**
	 * The prompt for the selected call.
	 *
	 * The hero carries its own on the call record — it is synthesized rather than
	 * recorded, so it never went through the recorder. Every other call reads from
	 * the lazily-fetched prompts artifact.
	 */
	$: promptEntry = prompts?.calls?.[call.id] ?? null;
	$: promptSystem = promptEntry?.system ?? null;
	$: promptMessages = promptEntry?.messages ?? (isImage ? imagePrompt : null);
	$: promptChars =
		promptEntry?.chars ??
		(isImage && imagePrompt ? imagePrompt.length : null);
	$: promptTruncated = promptEntry?.truncated === true;
	$: hasPrompt = !!(promptSystem || promptMessages);
	// Rendering happens only while open: these are large enough that formatting them
	// eagerly for every pane the user clicks through would be wasted work.
	$: promptSystemHtml = promptOpen && promptSystem ? renderPromptSystem(promptSystem) : '';
	$: promptMessagesHtml = promptOpen && promptMessages ? renderPrompt(promptMessages) : '';

	/**
	 * Open the prompt, fetching the artifact on first use.
	 *
	 * `autoScroll = false` matters even though the section now sits at the top: a live
	 * call's stream auto-scrolls the body to the bottom on every delta, which would
	 * drag the prompt out of view the moment it was revealed.
	 */
	function togglePrompt() {
		promptOpen = !promptOpen;
		if (!promptOpen) return;
		autoScroll = false;
		void onLoadPrompts();
	}

	// The image lands at the end of the call, not progressively: before then, show
	// the same "still working" state the station does rather than a finished picture.
	$: imageArrived = t >= call.end_ms;
</script>

<div class="transcript card !p-0 overflow-hidden" style="--accent: {accent}">
	<header class="ts-head">
		<div class="min-w-0">
			<div class="ts-eyebrow">
				<span class="route" style="background: {accent}">{call.provider_id}</span>
				{#if !isImage}
					<!-- Analysis profiles map to LLM effort levels; they mean nothing for an
					     image client, so the badge would be decorative rather than true. -->
					<span class="profile" style="--p: {profileColor(call.profile)}">{call.profile}</span>
				{/if}
				<span class="role">{ROLE_LABELS[call.role] ?? call.role}</span>
				{#if call.worker != null}<span class="worker">worker {call.worker}</span>{/if}
				{#if call.outcome !== 'ok'}
					<span
						class="outcome"
						style="--o: {call.recovered_by ? '#d97706' : (OUTCOME_COLORS[call.outcome] ?? '#737373')}"
						title={call.recovered_by
							? 'This attempt failed; a retry completed the work'
							: undefined}
						>{call.outcome}{call.recovered_by ? ' · retried' : ''}</span
					>
				{/if}
				{#if call.recovers}
					<span class="outcome" style="--o: #10b981" title="Completed work an earlier attempt failed to finish"
						>retry of a failed attempt</span
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
		{#if isImage}
			<!-- An image client, not an LLM route, so it never reaches the cost tracker.
			     The provider does report usage though, and when it did we show it: image
			     tokens bill at ~10x the thinking tokens beside them, which is why the two
			     are split rather than summed. When it reported nothing, "n/a" — a zero
			     would present a structural absence as a measurement. -->
			<div><dt>Model</dt><dd class="ts-model">{call.model}</dd></div>
			<div><dt>Output</dt><dd>1 image</dd></div>
			{#if call.usage_measured}
				<div>
					<dt>Image tokens</dt>
					<dd title="Billed at $120 per million">{formatTokens(call.image_tokens ?? 0)}</dd>
				</div>
				{#if (call.output_tokens ?? 0) - (call.image_tokens ?? 0) > 0}
					<div>
						<dt>Thinking</dt>
						<dd title="The model reasoning before it drew — billed at $12 per million">
							{formatTokens((call.output_tokens ?? 0) - (call.image_tokens ?? 0))}
						</dd>
					</div>
				{/if}
				<div><dt>Cost</dt><dd>{formatCost(call.cost_usd)}</dd></div>
			{:else}
				<div>
					<dt>Tokens / cost</dt>
					<dd class="ts-unrecorded" title="This provider reported no usage for the image call">
						n/a
					</dd>
				</div>
			{/if}
		{:else if timingsMeasured}
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
		{#if !isImage && call.billed === false}
			<!-- The request never returned, so the cost tracker recorded nothing for it.
			     But it streamed real output and was really billed — printing $0.00 here
			     would claim it was free, which is the one thing we know it wasn't. -->
			<div><dt>Effort</dt><dd>{call.effort}</dd></div>
			<div>
				<dt>Wrote</dt>
				<dd>{call.text_chars.toLocaleString()} chars before failing</dd>
			</div>
			<div>
				<dt>Tokens / cost</dt>
				<dd
					class="ts-unrecorded"
					title="Token counts arrive with the response. This call never returned one, so its spend is real but unmeasured, and is not included in the run total."
				>
					billed, not measured
				</dd>
			</div>
			{#if call.error_type}
				<div><dt>Error</dt><dd>{call.error_type}</dd></div>
			{/if}
		{:else if !isImage}
			<div><dt>Effort</dt><dd>{call.effort}</dd></div>
			<div><dt>Input</dt><dd>{formatTokens(call.input_tokens)}</dd></div>
			<div><dt>Output</dt><dd>{formatTokens(call.output_tokens)}</dd></div>
			{#if call.cache_read_tokens > 0}
				<div><dt>Cache read</dt><dd>{formatTokens(call.cache_read_tokens)}</dd></div>
			{/if}
			{#if call.billed_exact === false}
				<!-- Tokens came off the SSE stream, not a final response, so this is a
				     floor. Real spend, counted in the run total — just not exact. -->
				<div>
					<dt>Cost</dt>
					<dd
						title="Measured up to the point of failure: tokens emitted after the last stream event are unaccounted for."
						>≥ {formatCost(call.cost_usd)}</dd
					>
				</div>
			{:else}
				<div><dt>Cost</dt><dd>{formatCost(call.cost_usd)}</dd></div>
			{/if}
			<div><dt>Stop</dt><dd>{call.stop_reason ?? '—'}</dd></div>
		{/if}
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
		<span class="mini-clock" class:settled={isAfter} title={isAfter
			? `Finished at ${formatClock(call.end_ms)} of the run`
			: `Run clock ${formatClock(t)}`}
			>{isBefore ? '—' : formatDuration(localT)}{#if isAfter}<span class="mini-done"
					>done</span
				>{/if}</span
		>
		<button
			type="button"
			class="jump"
			on:click={() => onSeek(call.end_ms)}
			title="Jump to the end of this call">end ⏭</button
		>
	</div>

	<div class="ts-body" class:ts-body-art={isImage} bind:this={bodyEl} on:scroll={onBodyScroll}>
		<!-- What the model was asked, above what it answered. Outside the isImage
		     branch: every call has a prompt, and it is the half of the exchange the
		     replay used to omit entirely. Folded by default — a research batch sends
		     ~130k chars, which must not be the first thing in a 22rem scroller. -->
		<section class="prompt">
			<h4>
				<button
					type="button"
					class="prompt-toggle"
					on:click={togglePrompt}
					aria-expanded={promptOpen}
				>
					<span class="prompt-caret" class:open={promptOpen} aria-hidden="true">▸</span>
					The prompt
				</button>
				<span class="answer-meta">
					{#if promptChars}
						{promptChars.toLocaleString()} chars{#if promptTruncated} · trimmed{/if}
					{:else if promptsState === 'loading'}
						loading…
					{:else if call.input_tokens}
						{formatTokens(call.input_tokens)} tokens in
					{/if}
				</span>
			</h4>
			{#if promptOpen}
				{#if promptsState === 'loading' && !hasPrompt}
					<p class="answer-text pending">Loading the prompt…</p>
				{:else if hasPrompt}
					{#if promptSystemHtml}
						<p class="prompt-part">System</p>
						<!-- eslint-disable-next-line svelte/no-at-html-tags -->
						<div class="prompt-text md">{@html promptSystemHtml}</div>
					{/if}
					{#if promptMessagesHtml}
						{#if promptSystemHtml}<p class="prompt-part">User</p>{/if}
						<!-- eslint-disable-next-line svelte/no-at-html-tags -->
						<div class="prompt-text md">{@html promptMessagesHtml}</div>
					{/if}
					{#if promptTruncated}
						<p class="prompt-peek">
							Trimmed to fit the artifact's size cap — the head of the prompt is shown.
						</p>
					{/if}
				{:else}
					<p class="answer-text pending">
						Prompts are not retained for this date. The timeline and transcript are unaffected.
					</p>
				{/if}
			{:else if isImage}
				<p class="prompt-peek">
					Assembled from the day's detected topics, the mascot reference, and a fixed style
					brief.
				</p>
			{/if}
		</section>

		{#if isImage}
			<!-- The picture first: it is the whole point of this one call. -->
			<section class="art">
				{#if imageUrl && !imageFailed}
					<figure class="art-frame" class:pending={!imageArrived}>
						<img
							src={imageUrl}
							alt="Hero image generated for {call.task}"
							decoding="async"
							on:error={() => (imageFailed = true)}
						/>
						{#if !imageArrived}
							<figcaption class="art-working">
								<span class="art-dots" aria-hidden="true"><i></i><i></i><i></i></span>
								painting — lands at {formatClock(call.end_ms)}
							</figcaption>
						{/if}
					</figure>
					{#if imageArrived}
						<p class="art-note">
							Published as the day's hero image.
							<a href={imageUrl} target="_blank" rel="noopener noreferrer">Open full size ↗</a>
						</p>
					{/if}
				{:else}
					<p class="ts-note ts-note-block">
						{#if imageUrl}
							The generated image is no longer on disk for this date, so only the prompt survives.
						{:else}
							This run recorded the image step but not a path to its output.
						{/if}
					</p>
				{/if}
			</section>

		{:else if streamState === 'loading'}
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
					{#if isJsonCall}
						<!-- Only offered when there is something to switch between. -->
						<span class="out-switch" role="group" aria-label="Output rendering">
							<button
								type="button"
								class:on={outputView === 'structured'}
								on:click={() => (outputView = 'structured')}>Structured</button
							>
							<button
								type="button"
								class:on={outputView === 'raw'}
								on:click={() => (outputView = 'raw')}>Raw</button
							>
						</span>
					{/if}
					<span class="answer-meta">
						{answerText.length.toLocaleString()} / {call.text_chars.toLocaleString()} chars
					</span>
				</h4>
				{#if answerText && showStructured && parsedJson?.root}
					{#if parsedJson.preamble}
						<!-- eslint-disable-next-line svelte/no-at-html-tags -->
						<div class="json-aside md">{@html renderPreamble(parsedJson.preamble)}</div>
					{/if}
					<JsonStream
						root={parsedJson.root}
						complete={parsedJson.complete}
						live={isLive}
						{reduced}
					/>
					{#if parsedJson.epilogue}
						<!-- eslint-disable-next-line svelte/no-at-html-tags -->
						<div class="json-aside json-aside-after md">
							{@html renderEpilogue(parsedJson.epilogue)}
						</div>
					{/if}
				{:else if answerText && rawJson}
					<div class="rawblock">
						{#if rawJson.lang}<span class="rawblock-lang">{rawJson.lang}</span>{/if}
						<!-- Only <span class="j-*"> is emitted, over escaped text. -->
						<!-- eslint-disable-next-line svelte/no-at-html-tags -->
						<pre class="rawblock-pre"><code>{@html rawJson.html}{#if showCaret}<span
									class="caret"
								></span>{/if}</code></pre>
					</div>
				{:else if answerText}
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
	@media (hover: hover) and (pointer: fine) {
		.ts-close:hover {
			color: #E63946;
			background: rgb(230 57 70 / 0.1);
		}
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
	@media (hover: hover) and (pointer: fine) {
		.jump:hover {
			color: var(--accent);
		}
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
		display: inline-flex;
		align-items: baseline;
		gap: 0.3rem;
		white-space: nowrap;
	}
	.mini-clock.settled {
		color: #a3a3a3;
	}
	.mini-done {
		font-size: 0.52rem;
		letter-spacing: 0.08em;
		text-transform: uppercase;
		opacity: 0.75;
	}

	.ts-body {
		padding: 0.75rem 0.9rem 1rem;
		max-height: 22rem;
		overflow-y: auto;
	}
	/* The transcript body is capped so a long stream scrolls inside the card. A 21:9
	   image at full card width is taller than that cap, so the one call whose output
	   is a picture would open showing a horizontal slice of it. Give the image pane
	   more room — the folded prompt keeps the whole thing bounded. */
	.ts-body-art {
		max-height: 40rem;
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

	/* --- image-generation result ------------------------------------------- */
	.art {
		margin-bottom: 0.9rem;
	}
	.art-frame {
		position: relative;
		border-radius: 0.6rem;
		overflow: hidden;
		border: 1px solid rgb(0 0 0 / 0.1);
		background: rgb(0 0 0 / 0.04);
		line-height: 0;
	}
	:global(.dark) .art-frame {
		border-color: rgb(255 255 255 / 0.1);
		background: rgb(255 255 255 / 0.04);
	}
	.art-frame img {
		width: 100%;
		height: auto;
		display: block;
	}
	/* Before the clock reaches the end of the call the image has not been produced
	   yet, so it is shown dimmed and unresolved rather than as a finished result. */
	.art-frame.pending img {
		filter: blur(9px) saturate(0.6);
		opacity: 0.5;
	}
	.art-working {
		position: absolute;
		inset: 0;
		display: flex;
		align-items: center;
		justify-content: center;
		gap: 0.4rem;
		font-size: 0.72rem;
		font-weight: 600;
		font-variant-numeric: tabular-nums;
		line-height: 1.4;
		color: #fff;
		/* The blurred artwork underneath can be light or dark depending on the image,
		   so the caption carries its own scrim rather than relying on the art. */
		background: radial-gradient(60% 100% at 50% 50%, rgb(0 0 0 / 0.6), transparent 75%);
		text-shadow: 0 1px 6px rgb(0 0 0 / 0.9);
	}
	.art-dots {
		display: inline-flex;
		gap: 2.5px;
	}
	.art-dots i {
		width: 4px;
		height: 4px;
		border-radius: 999px;
		background: currentColor;
		animation: paintpulse 1.2s ease-in-out infinite;
	}
	.art-dots i:nth-child(2) {
		animation-delay: 0.2s;
	}
	.art-dots i:nth-child(3) {
		animation-delay: 0.4s;
	}
	@keyframes paintpulse {
		0%,
		100% {
			opacity: 0.25;
		}
		50% {
			opacity: 1;
		}
	}
	.art-note {
		margin-top: 0.35rem;
		font-size: 0.68rem;
		color: #737373;
	}
	.art-note a {
		color: #E63946;
		text-decoration: underline;
		text-underline-offset: 2px;
	}

	.prompt h4 {
		display: flex;
		align-items: center;
		gap: 0.35rem;
		font-size: 0.55rem;
		font-weight: 700;
		letter-spacing: 0.1em;
		text-transform: uppercase;
		color: #737373;
		margin-bottom: 0.3rem;
	}
	.prompt-toggle {
		display: inline-flex;
		align-items: center;
		gap: 0.3rem;
		font: inherit;
		letter-spacing: inherit;
		text-transform: inherit;
		color: inherit;
		cursor: pointer;
	}
	.prompt-toggle:focus-visible {
		color: #E63946;
		outline: none;
	}
	@media (hover: hover) and (pointer: fine) {
		.prompt-toggle:hover {
			color: #E63946;
		}
	}
	.prompt-caret {
		display: inline-block;
		font-size: 0.7rem;
		line-height: 1;
		transition: transform 150ms ease;
	}
	.prompt-caret.open {
		transform: rotate(90deg);
	}
	.prompt-peek {
		font-size: 0.72rem;
		line-height: 1.5;
		color: #737373;
		font-style: italic;
	}
	/* Sits above everything else in the body, so it needs its own gap; `.art` used
	   to supply the spacing when the prompt followed it. */
	.prompt {
		margin-bottom: 0.9rem;
	}

	/* System vs user. The two halves read very differently — one is the operator's
	   instructions, the other the fenced source data — and unlabelled they run
	   together into one wall. */
	.prompt-part {
		font-size: 0.52rem;
		font-weight: 700;
		letter-spacing: 0.1em;
		text-transform: uppercase;
		color: #a3a3a3;
		margin: 0.4rem 0 0.2rem;
	}
	.prompt-part:first-of-type {
		margin-top: 0;
	}

	/* Accent-tinted rather than the hardcoded image pink it used to carry: this now
	   renders for every call, so it follows the call's own route colour. */
	.prompt-text {
		font-size: 0.76rem;
		line-height: 1.6;
		color: #404040;
		word-break: break-word;
		padding: 0.55rem 0.7rem;
		border-radius: 0.5rem;
		border-left: 2px solid var(--accent);
		background: color-mix(in srgb, var(--accent) 6%, transparent);
		/* A 130k-char prompt would otherwise own the whole scroller. Bounded here so
		   the answer below stays reachable; the block scrolls internally. */
		max-height: 26rem;
		overflow-y: auto;
	}
	:global(.dark) .prompt-text {
		color: #d4d4d4;
		background: color-mix(in srgb, var(--accent) 12%, transparent);
	}

	.ts-model {
		font-size: 0.68rem !important;
		word-break: break-word;
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

	.out-switch {
		display: inline-flex;
		border-radius: 5px;
		overflow: hidden;
		border: 1px solid rgb(0 0 0 / 0.12);
		margin-left: 0.15rem;
	}
	:global(.dark) .out-switch {
		border-color: rgb(255 255 255 / 0.16);
	}
	.out-switch button {
		font-size: 0.55rem;
		font-weight: 700;
		letter-spacing: 0.04em;
		padding: 1px 6px;
		background: transparent;
		color: #737373;
		border: none;
		cursor: pointer;
	}
	.out-switch button.on {
		background: var(--accent);
		color: #fff;
	}

	/* Prose the model wrote around its JSON. Set apart from the structured cards so
	   it reads as commentary, but never hidden — it is real model output. */
	.json-aside {
		font-size: 0.74rem;
		line-height: 1.55;
		color: #525252;
		padding-left: 0.55rem;
		border-left: 2px solid rgb(0 0 0 / 0.1);
		margin-bottom: 0.55rem;
	}
	.json-aside-after {
		margin-bottom: 0;
		margin-top: 0.7rem;
	}

	/* Raw view: a real code block. Horizontal scroll rather than wrapping, so the
	   model's own line structure survives — that is what "raw" is for. */
	.rawblock {
		position: relative;
		border-radius: 6px;
		border: 1px solid rgb(0 0 0 / 0.08);
		background: #fafafa;
		overflow: hidden;
	}
	:global(.dark) .rawblock {
		background: rgb(0 0 0 / 0.28);
		border-color: rgb(255 255 255 / 0.1);
	}
	.rawblock-lang {
		position: absolute;
		top: 0;
		right: 0;
		font-size: 0.5rem;
		font-weight: 700;
		letter-spacing: 0.09em;
		text-transform: uppercase;
		color: #a3a3a3;
		padding: 2px 6px;
		background: rgb(0 0 0 / 0.04);
		border-bottom-left-radius: 5px;
	}
	:global(.dark) .rawblock-lang {
		background: rgb(255 255 255 / 0.06);
	}
	.rawblock-pre {
		margin: 0;
		padding: 0.55rem 0.7rem;
		overflow-x: auto;
		font-size: 0.68rem;
		line-height: 1.5;
		font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
		tab-size: 2;
	}
	.rawblock-pre code {
		font: inherit;
		white-space: pre;
	}

	/* JSON token colours. Tuned for contrast in both themes rather than for
	   fidelity to any one editor scheme. */
	.rawblock :global(.j-key) {
		color: #0369a1;
		font-weight: 600;
	}
	.rawblock :global(.j-str) {
		color: #15803d;
	}
	.rawblock :global(.j-num) {
		color: #b45309;
	}
	.rawblock :global(.j-lit) {
		color: #7c3aed;
		font-weight: 600;
	}
	.rawblock :global(.j-brace) {
		color: #525252;
		font-weight: 700;
	}
	.rawblock :global(.j-punct) {
		color: #a3a3a3;
	}
	:global(.dark) .rawblock :global(.j-key) {
		color: #7dd3fc;
	}
	:global(.dark) .rawblock :global(.j-str) {
		color: #86efac;
	}
	:global(.dark) .rawblock :global(.j-num) {
		color: #fcd34d;
	}
	:global(.dark) .rawblock :global(.j-lit) {
		color: #c4b5fd;
	}
	:global(.dark) .rawblock :global(.j-brace) {
		color: #d4d4d4;
	}
	:global(.dark) .json-aside {
		color: #a3a3a3;
		border-left-color: rgb(255 255 255 / 0.14);
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
		.art-dots i {
			animation: none;
			opacity: 0.75;
		}
		.prompt-caret {
			transition: none;
		}
	}
</style>
