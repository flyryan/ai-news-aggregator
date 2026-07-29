// The admin panel is authenticated and entirely dynamic. Prerendering it would
// bake an empty shell into the public build and ship it to news.aatf.ai.
export const prerender = false;
export const ssr = false;
