import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  base: '/static/build/',
  plugins: [react()],
  build: {
    outDir: 'static/build',
    emptyOutDir: true,
    cssCodeSplit: false,
    rollupOptions: {
      input: {
        'aif-graph': 'frontend/aif-graph/main.tsx',
        'toulmin-graph': 'frontend/toulmin-graph/main.tsx',
      },
      output: {
        entryFileNames: '[name].js',
        chunkFileNames: 'graph-chunk-[hash].js',
        assetFileNames: (asset) => asset.name?.endsWith('.css') ? 'argument-graphs.css' : 'graph-asset-[hash][extname]',
      },
    },
  },
});
