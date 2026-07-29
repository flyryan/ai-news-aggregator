import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vite';
import path from 'path';
import fs from 'fs';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

export default defineConfig({
	plugins: [
		sveltekit(),
		{
			name: 'serve-data',
			configureServer(server) {
				// Serve /data requests from ../web/data (relative to frontend/)
				server.middlewares.use('/data', (req, res, next) => {
					// Strip query string from URL for file path lookup
					const urlPath = (req.url || '').split('?')[0];
					const filePath = path.join(__dirname, '../web/data', urlPath);
					if (fs.existsSync(filePath) && fs.statSync(filePath).isFile()) {
						const ext = path.extname(filePath);
						const contentType = ext === '.json' ? 'application/json'
							: ext === '.webp' ? 'image/webp'
							: 'application/octet-stream';
						res.setHeader('Content-Type', contentType);
						fs.createReadStream(filePath).pipe(res);
					} else {
						next();
					}
				});
			}
		}
	],
	server: {
		fs: {
			allow: ['..']
		},
		proxy: {
			// The admin panel talks to the admin service. In production the two are
			// the same origin behind the Cloudflare tunnel; in dev the service runs
			// separately, so forward /api to it. Start it with:
			//   ./scripts/admin_dev.sh
			'/api': {
				target: 'http://127.0.0.1:8200',
				changeOrigin: false
			},
			// Preview rendering is served by the admin service too. In production
			// both live on the admin origin behind the tunnel; in dev SvelteKit
			// would otherwise claim /preview and 404 it.
			'/preview': {
				target: 'http://127.0.0.1:8200',
				changeOrigin: false
			}
		}
	}
});
