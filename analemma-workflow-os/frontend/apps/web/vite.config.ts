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
    chunkSizeWarningLimit: 500,
    // Optimize dependencies to prevent circular dependency crashes
    commonjsOptions: {
      transformMixedEsModules: true,
    },
    rollupOptions: {
      // Prevent circular dependency errors during build
      onwarn(warning, warn) {
        // Suppress circular dependency warnings
        if (warning.code === 'CIRCULAR_DEPENDENCY') return;
        warn(warning);
      },
      output: {
        // Vendor chunking for optimal caching
        manualChunks: {
          'react-vendor': ['react', 'react-dom', 'react-router-dom'],
          'xyflow': ['@xyflow/react'],
          'tanstack': ['@tanstack/react-query'],
          'ui-vendor': ['lucide-react', 'sonner'],
        },
      },
    },
  },
}));
