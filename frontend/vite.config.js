import { svelte } from '@sveltejs/vite-plugin-svelte';
import { defineConfig } from 'vite';

export default defineConfig({
  plugins: [svelte()],
  // Home Assistant ingress serves the UI below a token path, so every URL in
  // the build must be relative.
  base: './',
  server: {
    // Local development: backend started with HTTP_HOST=127.0.0.1 and
    // TRUSTED_CLIENTS=127.0.0.1 (see README.md).
    proxy: { '/api': 'http://127.0.0.1:8714' },
  },
});
