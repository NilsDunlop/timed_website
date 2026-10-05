import { defineConfig } from "astro/config";
import react from "@astrojs/react";

const connectorOrigin =
  process.env.TIMED_CONNECTOR_ORIGIN ?? "http://127.0.0.1:8700";

export default defineConfig({
  output: "static",
  integrations: [react()],
  vite: {
    server: {
      proxy: {
        "/connector": {
          target: connectorOrigin,
          changeOrigin: true,
        },
        "/backend": {
          target: connectorOrigin,
          changeOrigin: true,
        },
      },
    },
  },
});
