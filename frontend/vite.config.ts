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
		}
	}
});
