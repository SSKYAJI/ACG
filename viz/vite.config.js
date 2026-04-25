import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "node:path";
// We import demo-app/agent_lock.json from outside this package, so Vite needs
// permission to read that path. publicDir stays viz-local.
export default defineConfig({
    plugins: [react()],
    server: {
        port: 5174,
        fs: {
            allow: [path.resolve(__dirname, "..")],
        },
    },
});
