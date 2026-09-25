import js from '@eslint/js'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import globals from 'globals'
import tseslint from 'typescript-eslint'

export default tseslint.config(
  { ignores: ['dist', 'playwright-report', 'test-results', 'src/api/schema.gen.ts'] },
  {
    files: ['**/*.{ts,tsx}'],
    extends: [js.configs.recommended, ...tseslint.configs.strictTypeChecked],
    languageOptions: {
      ecmaVersion: 2023,
      globals: globals.browser,
      parserOptions: {
        projectService: true,
        tsconfigRootDir: import.meta.dirname,
      },
    },
    plugins: {
      'react-hooks': reactHooks,
      'react-refresh': reactRefresh,
    },
    rules: {
      ...reactHooks.configs.recommended.rules,
      'react-refresh/only-export-components': ['warn', { allowConstantExport: true }],
      '@typescript-eslint/restrict-template-expressions': ['error', { allowNumber: true }],
      // The SPA talks to the backend only through the generated /v1 client (S1.12).
      'no-restricted-globals': [
        'error',
        { name: 'fetch', message: 'Use the generated /v1 client in src/api instead.' },
        { name: 'XMLHttpRequest', message: 'Use the generated /v1 client in src/api instead.' },
        { name: 'EventSource', message: 'Use the generated /v1 client in src/api instead.' },
      ],
      'no-restricted-properties': [
        'error',
        { object: 'window', property: 'fetch', message: 'Use the generated /v1 client.' },
        { object: 'globalThis', property: 'fetch', message: 'Use the generated /v1 client.' },
      ],
    },
  },
  {
    // Ink (docs/design/system.md §12.3): features compose the primitives in src/ui.
    files: ['src/**/*.tsx'],
    ignores: ['src/ui/**'],
    rules: {
      'no-restricted-syntax': [
        'error',
        {
          selector: 'JSXOpeningElement[name.name=/^(button|input|textarea|select)$/]',
          message: 'Use a primitive from src/ui (Button, IconButton, TextArea, …). Raw form controls live only in src/ui.',
        },
      ],
    },
  },
  {
    // The client module is the one place allowed to touch the network.
    files: ['src/api/**/*.ts'],
    rules: { 'no-restricted-globals': 'off', 'no-restricted-properties': 'off' },
  },
  {
    files: ['e2e/**/*.ts', 'playwright.config.ts', 'vite.config.ts'],
    languageOptions: { globals: globals.node },
    rules: { 'no-restricted-globals': 'off' },
  },
)
