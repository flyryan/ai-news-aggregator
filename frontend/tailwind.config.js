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
				// trend-red as TEXT. The brand red measures 4.17:1 on white and 3.63:1
				// on a dark card; this is the nearest shade of the same hue that
				// reaches 4.5:1 in each theme (values in app.css). Fills, borders,
				// dots and tints keep trend-red.
				'trend-red-text': 'rgb(var(--trend-red-text) / <alpha-value>)',
				'trend-dark': '#1a1a1a',
				'trend-gray': {
					100: '#f5f5f5',
					200: '#e5e5e5',
					300: '#d4d4d4',
					400: '#a3a3a3',
					500: '#6f6f6f',
					600: '#525252',
					700: '#404040',
					800: '#262626',
					900: '#171717'
				},
				// Category accent colors
				'category-news': '#667eea',
				'category-research': '#10b981',
				'category-social': '#f59e0b',
				'category-reddit': '#ef4444',
				// trend-red as a FILL under white text (pills, buttons). White on the
				// brand red is 4.17:1; this is the nearest shade that gives 4.5:1.
				'trend-red-fill': '#e32533'
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
			textColor: {
				// guardian-red is the hover colour of red links. On dark surfaces it is
				// darker than the link itself (2.59:1); there it becomes red-300.
				'guardian-red': 'rgb(var(--guardian-red-text) / <alpha-value>)'
			},
			boxShadow: {
				'card': '0 2px 8px rgba(0, 0, 0, 0.08)',
				'card-hover': '0 4px 12px rgba(0, 0, 0, 0.12)'
			},
			typography: {
				DEFAULT: {
					css: {
						// The plugin draws literal backticks around inline code with
						// ::before/::after and sets font-weight 600, so a <code> span
						// renders as **`like this`** — bold, with the markers we just
						// converted away put back. Style it as a chip instead.
						'code::before': { content: 'none' },
						'code::after': { content: 'none' },
						code: {
							fontWeight: '400',
							backgroundColor: 'rgb(0 0 0 / 0.06)',
							padding: '0.1em 0.35em',
							borderRadius: '0.25rem',
							fontSize: '0.9em'
						}
					}
				},
				invert: {
					css: {
						code: {
							backgroundColor: 'rgb(255 255 255 / 0.1)',
							color: 'inherit'
						}
					}
				}
			}
		}
	},
	plugins: [
		require('@tailwindcss/typography')
	]
};
