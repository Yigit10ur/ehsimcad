import { resolve } from 'node:path';

import { defineConfig } from 'vitest/config';

export default defineConfig({
  resolve: {
    alias: {
      '@': resolve(__dirname, 'src'),
      // Next has no exports map for this one, so Node finds it by adding the
      // extension and Vite, which resolves bare subpaths strictly, does not.
      // Only the auth handler reaches it, and only under test.
      'next/server': resolve(__dirname, 'node_modules/next/server.js'),
    },
  },
  test: {
    environment: 'node',
    include: ['tests/**/*.test.ts'],
    // PGlite compiles Postgres to WebAssembly and the first instance in a run
    // pays for loading it.
    testTimeout: 30_000,
    hookTimeout: 30_000,
    setupFiles: ['tests/setup.ts'],
    // Processed by Vite rather than loaded straight by Node, so the alias
    // above reaches the import it makes.
    server: { deps: { inline: ['next-auth'] } },
  },
});
