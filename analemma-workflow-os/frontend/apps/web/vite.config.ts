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
    // 청크 크기 경고 임계값 (KB)
    chunkSizeWarningLimit: 500,
    rollupOptions: {
      output: {
        // 벤더 청킹으로 캐싱 효율 극대화
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
