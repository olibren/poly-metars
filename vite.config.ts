import tailwindcss from '@tailwindcss/postcss';
import vinext from 'vinext';
import { defineConfig } from 'vite';
import deployment from './cloudflare/deployment.json' with { type: 'json' };

// This viewer is a static export. The Python collector owns storage and networking.
// A Workers runtime is unnecessary and cannot be imported by Node's prerenderer.
export default defineConfig({
  css: { postcss: { plugins: [tailwindcss()] } },
  plugins: [vinext()],
  server: {
    // Preview local UI changes against the read-only published evidence by default.
    // Override for a local Wrangler site or an offline fixture server.
    proxy: {
      '/data/': {
        target: process.env.METAR_DATA_ORIGIN || deployment.site,
        changeOrigin: true,
        headers: { 'User-Agent': 'PolyMETARs-Audit/1' },
      },
    },
    watch: {
      ignored: ['**/archive/**', '**/catalog-archive/**', '**/work/**'],
    },
  },
});
