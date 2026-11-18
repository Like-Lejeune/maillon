import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// En développement, les appels au serveur passent par Vite : l'interface et
// l'API partagent la même origine, comme en préproduction derrière Caddy.
const server = process.env.MAILLON_API ?? "http://localhost:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": server,
      "/v1": server,
      "/agent": server,
      "/health": server,
    },
  },
  build: {
    sourcemap: false,
  },
});
