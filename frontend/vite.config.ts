import { readFileSync } from "node:fs";
import { join } from "node:path";
import { defineConfig, type Plugin } from "vite";
import react from "@vitejs/plugin-react";

// Auth and legal HTML are served separately from React, but they must use the
// same pinned, locally bundled font package. Emit stable URLs because these
// pages do not pass through Vite's asset hashing pipeline.
function publicPageFonts(): Plugin {
  return {
    name: "sde-public-page-fonts",
    generateBundle() {
      const packages = [
        ["ibm-plex-sans", [400, 500, 600]],
        ["ibm-plex-mono", [400]],
      ] as const;
      const emitted = new Set<string>();
      const css: string[] = [];

      for (const [family, weights] of packages) {
        for (const weight of weights) {
          const fontDir = join(
            process.cwd(),
            "node_modules", "@fontsource", family,
          );
          const stylesheet = readFileSync(
            join(fontDir, `${weight}.css`),
            "utf8",
          );
          let references = 0;
          const rewritten = stylesheet.replace(
            /url\(\.\/files\/([a-z0-9-]+\.woff2?)\)/g,
            (_match, fileName: string) => {
              references += 1;
              if (!emitted.has(fileName)) {
                this.emitFile({
                  type: "asset",
                  fileName: `fonts/${fileName}`,
                  source: readFileSync(
                    join(fontDir, "files", fileName),
                  ),
                });
                emitted.add(fileName);
              }
              return `url(/dashboard/app/fonts/${fileName})`;
            },
          );
          if (references === 0 || rewritten.includes("url(./files/")) {
            throw new Error(
              `Fontsource CSS format changed: ${family}/${weight}.css`,
            );
          }
          css.push(rewritten);
        }
      }
      this.emitFile({
        type: "asset",
        fileName: "public-fonts.css",
        source: css.join("\n"),
      });
    },
  };
}

export default defineConfig({
  plugins: [react(), publicPageFonts()],
  base: "/dashboard/app/",
  build: {
    outDir: "dist",
    emptyOutDir: true,
    sourcemap: false,
  },
});
