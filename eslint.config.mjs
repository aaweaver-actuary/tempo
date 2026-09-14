import { defineConfig, globalIgnores } from 'eslint/config';
import nextVitals from 'eslint-config-next/core-web-vitals';
import nextTs from 'eslint-config-next/typescript';

const eslintConfig = defineConfig([
  ...nextVitals,
  ...nextTs,
  globalIgnores(['.next/**', 'out/**', 'build/**', 'dist/**', 'public/engines/**', 'public/ort/**', 'public/maia-worker.js', 'public/stockfish-worker.js', 'next-env.d.ts']),
]);

export default eslintConfig;
