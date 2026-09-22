import tailwindcss from '@tailwindcss/postcss';
import vinext from 'vinext';
import { defineConfig } from 'vite';

// This viewer is a static export. The Python collector owns storage and networking.
// A Workers runtime is unnecessary and cannot be imported by Node's prerenderer.
export default defineConfig({
  css: { postcss: { plugins: [tailwindcss()] } },
  plugins: [vinext()],
  server: {
    proxy: { '/data': 'http://127.0.0.1:8001' },
    watch: {
      ignored: ['**/archive/**', '**/catalog-archive/**', '**/work/**'],
    },
  },
});
