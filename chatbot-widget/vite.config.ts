import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Vite configuration with React support and dev server on port 3001
export default defineConfig({
  plugins: [react()],
  server: {
    port: 3001,
    host: "localhost",
  },
});
