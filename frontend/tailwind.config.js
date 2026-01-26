/** @type {import('tailwindcss').Config} */
export default {
	content: ['./src/**/*.{html,js,svelte,ts}'],
	darkMode: 'class',
	theme: {
		extend: {
			colors: {
				// AATF Brand Colors
				'trend-red': '#E63946',
				'guardian-red': '#C1272D',
				'trend-dark': '#1a1a1a',
				'trend-gray': {
					100: '#f5f5f5',
					200: '#e5e5e5',
					300: '#d4d4d4',
					400: '#a3a3a3',
					500: '#737373',
					600: '#525252',
					700: '#404040',
					800: '#262626',
					900: '#171717'
				},
				// Category accent colors
				'category-news': '#667eea',
				'category-research': '#10b981',
				'category-social': '#f59e0b',
				'category-reddit': '#ef4444'
			},
			fontFamily: {
				sans: [
					'-apple-system',
					'BlinkMacSystemFont',
					'Segoe UI',
					'Roboto',
					'Oxygen',
					'Ubuntu',
					'Cantarell',
					'sans-serif'
				]
			},
			boxShadow: {
				'card': '0 2px 8px rgba(0, 0, 0, 0.08)',
				'card-hover': '0 4px 12px rgba(0, 0, 0, 0.12)'
			}
		}
	},
	plugins: [
		require('@tailwindcss/typography')
	]
};
