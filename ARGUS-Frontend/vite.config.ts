import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: { port: 5173 },
  build: {
    // Tiny font subsets fall under Vite's 4 KB inline limit and become data: URIs, which the
    // nginx CSP (font-src falls back to default-src 'self') blocks. Keep fonts as files.
    assetsInlineLimit: (file, content) => (/\.(woff2?|ttf|otf)$/.test(file) ? false : content.length < 4096),
  },
});
