<script lang="ts">
	export let text: string;
	export let query: string;

	$: parts = highlightMatches(text, query);

	function highlightMatches(text: string, query: string): { text: string; highlight: boolean }[] {
		if (!query.trim()) {
			return [{ text, highlight: false }];
		}

		const parts: { text: string; highlight: boolean }[] = [];
		const queryLower = query.toLowerCase();
		const textLower = text.toLowerCase();
		let lastIndex = 0;

		let index = textLower.indexOf(queryLower);
		while (index !== -1) {
			// Add non-matching text before
			if (index > lastIndex) {
				parts.push({
					text: text.slice(lastIndex, index),
					highlight: false
				});
			}

			// Add matching text
			parts.push({
				text: text.slice(index, index + query.length),
				highlight: true
			});

			lastIndex = index + query.length;
			index = textLower.indexOf(queryLower, lastIndex);
		}

		// Add remaining text
		if (lastIndex < text.length) {
			parts.push({
				text: text.slice(lastIndex),
				highlight: false
			});
		}

		return parts;
	}
</script>

<span>
	{#each parts as part}
		{#if part.highlight}
			<mark class="bg-trend-red/20 text-trend-red px-0.5 rounded">{part.text}</mark>
		{:else}
			{part.text}
		{/if}
	{/each}
</span>
