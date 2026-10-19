import {defineConfig} from 'vitest/config';
export default defineConfig({
  base: '/workspace-assets/',
  build: {outDir: '../src/mcdxkit/web/workspace-dist', emptyOutDir: true,
    rollupOptions: {output: {entryFileNames: 'workspace.js', chunkFileNames: '[name]-[hash].js', assetFileNames: '[name].[ext]'}}},
  test: {environment: 'jsdom', setupFiles: ['./src/test-setup.ts']}
});
