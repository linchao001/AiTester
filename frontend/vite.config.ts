import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      // 走查/评审可用 VITE_PROXY_TARGET 覆盖后端端口，缺省仍走本机 8000
      "/api": { target: process.env.VITE_PROXY_TARGET || "http://127.0.0.1:8000" },
    },
  },
});
