import { defineConfig } from "astro/config";
import react from "@astrojs/react";

export default defineConfig({
  output: "static",
  integrations: [react()],
  vite: {
    server: {
      proxy: {
        "/connector": {
          target: "http://127.0.0.1:8700",
          changeOrigin: true,
        },
        "/backend": {
          target: "http://127.0.0.1:8700",
          changeOrigin: true,
        },
      },
    },
  },
});
