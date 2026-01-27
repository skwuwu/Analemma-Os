import { defineConfig } from "vite";
import react from "@vitejs/plugin-react-swc";
import path from "path";
import { componentTagger } from "lovable-tagger";

// https://vitejs.dev/config/
export default defineConfig(({ mode }) => ({
  server: {
    host: "::",
    port: 8080,
  },
  plugins: [react(), mode === "development" && componentTagger()].filter(Boolean),
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  build: {
    // Chunk size warning threshold (KB)
    chunkSizeWarningLimit: 800,
    // Use esbuild minifier
    minify: 'esbuild',
    // Optimize dependencies
    commonjsOptions: {
      transformMixedEsModules: true,
    },
    rollupOptions: {
      onwarn(warning, warn) {
        // Suppress circular dependency warnings from node_modules
        if (warning.code === 'CIRCULAR_DEPENDENCY' && warning.message?.includes('node_modules')) {
          return;
        }
        // Suppress eval warnings
        if (warning.code === 'EVAL') return;
        warn(warning);
      },
      output: {
        // Only split vendor libraries, let Rollup handle app code automatically
        manualChunks(id) {
          // React ecosystem - must be first loaded
          if (id.includes('node_modules/react-dom')) {
            return 'react-vendor';
          }
          if (id.includes('node_modules/react/') || id.includes('node_modules/react-is')) {
            return 'react-vendor';
          }
          // XYFlow - separate heavy library
          if (id.includes('node_modules/@xyflow')) {
            return 'xyflow';
          }
          // TanStack Query
          if (id.includes('node_modules/@tanstack')) {
            return 'tanstack';
          }
          // DO NOT manually chunk application code
          // Let Rollup determine the optimal split to avoid circular deps
        },
      },
    },
  },
}));
