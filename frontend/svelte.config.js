import adapter from '@sveltejs/adapter-static';
import { vitePreprocess } from '@sveltejs/vite-plugin-svelte';

/** @type {import('@sveltejs/kit').Config} */
const config = {
	preprocess: vitePreprocess(),

	kit: {
		adapter: adapter({
			pages: '../web',
			assets: '../web',
			fallback: 'index.html',
			precompress: false,
			strict: true
		}),
		paths: {
			base: ''
		},
		prerender: {
			handleHttpError: ({ path, referrer, message }) => {
				// Ignore 404s for /data/ paths - these are runtime files, not built
				if (path.startsWith('/data/')) {
					return;
				}
				// Throw for all other errors
				throw new Error(message);
			}
		}
	}
};

export default config;
