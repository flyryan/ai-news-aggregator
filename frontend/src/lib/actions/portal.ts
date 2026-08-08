/**
 * Move an element to `document.body`.
 *
 * `position: fixed` is only viewport-relative if no ancestor establishes a
 * containing block — and any `transform`, `filter`, or `will-change` anywhere up
 * the tree does exactly that. Coordinates measured against the viewport are then
 * applied relative to that ancestor instead, which puts the element somewhere far
 * from where it was placed.
 *
 * Reparenting to the body is the reliable fix: it cannot be broken by a future
 * transform added anywhere above. It also escapes `overflow: hidden` clipping.
 */
export function portal(node: HTMLElement) {
	// Leave a marker where the node used to be. Svelte tracks each block's extent
	// by its boundary nodes and, on teardown, removes everything between them —
	// so a block whose contents have all been relocated ends up walking <body>'s
	// real children and deleting the app. The placeholder keeps that range inside
	// the component, where it belongs.
	const placeholder = document.createComment('portal');
	node.parentNode?.insertBefore(placeholder, node);
	document.body.appendChild(node);

	return {
		destroy() {
			node.remove();
			placeholder.remove();
		}
	};
}
