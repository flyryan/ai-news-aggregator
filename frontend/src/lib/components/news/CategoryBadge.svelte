<script lang="ts">
	import type { Category } from '$lib/types';
	import { CATEGORY_CONFIG } from '$lib/types';

	export let category: Category;

	let className = '';
	export { className as class };

	$: config = CATEGORY_CONFIG[category];
</script>

<span class="badge {config.badgeClass} {className}">
	{config.title}
</span>
