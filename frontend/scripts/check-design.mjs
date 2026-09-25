#!/usr/bin/env node
/**
 * `npm run check:design`: keeps every screen on the Ink system (docs/design/system.md §12).
 *
 * 1. Rejects, outside src/styles/tokens.css: hex and rgb()/hsl() literals, Tailwind palette
 *    names, arbitrary values (`w-[13px]`, `bg-[#fff]`), raw durations and easings (outside
 *    src/ui/motion.ts), `transition-all` / `transition: all`, and inline style colours.
 * 2. Computes the contrast of every text/surface pair from tokens.css: at least 4.5 : 1
 *    (3 : 1 for line-control), fg-4 excepted (never text that must be read).
 * 3. With a build in dist/: initial JS <= 200 KB gzipped, first-render fonts <= 150 KB.
 */
import { readFileSync, readdirSync, statSync, existsSync } from 'node:fs'
import { join, relative, extname, basename } from 'node:path'
import { gzipSync } from 'node:zlib'
import { fileURLToPath } from 'node:url'

const root = fileURLToPath(new URL('..', import.meta.url))
const src = join(root, 'src')
const TOKENS = join(src, 'styles', 'tokens.css')
const MOTION = join(src, 'ui', 'motion.ts')
const SKIP = new Set([join(src, 'api', 'schema.gen.ts')])
const problems = []

function walk(dir) {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name)
    return statSync(path).isDirectory() ? walk(path) : [path]
  })
}

const PALETTE =
  'slate|gray|grey|zinc|neutral|stone|red|orange|amber|yellow|lime|green|emerald|teal|cyan|sky|blue|indigo|violet|purple|fuchsia|pink|rose|white|black'
const UTIL =
  'bg|text|border|border-[trblxy]|ring|ring-offset|fill|stroke|from|via|to|outline|decoration|divide|placeholder|accent|caret|shadow'

const rules = [
  { name: 'hex colour in a string', ext: ['.ts', '.tsx'], re: /['"`]#(?:[0-9a-fA-F]{3,4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})['"`]/ },
  { name: 'hex colour', ext: ['.css'], re: /#(?:[0-9a-fA-F]{3,4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})\b(?![-\w])/ },
  { name: 'rgb()/hsl() colour', ext: ['.ts', '.tsx', '.css'], re: /\b(?:rgba?|hsla?|oklch|oklab|lab|lch)\(/ },
  { name: 'Tailwind palette name', ext: ['.ts', '.tsx', '.css'], re: new RegExp(`\\b(?:${UTIL})-(?:${PALETTE})(?:-\\d{2,3})?(?:\\/\\d+)?\\b`) },
  { name: 'arbitrary value', ext: ['.ts', '.tsx', '.css'], re: /\b[a-z][a-z0-9-]*-\[[^\]\s]+\]/ },
  { name: 'transition-all', ext: ['.ts', '.tsx', '.css'], re: /\btransition-all\b|transition:\s*all\b/ },
  { name: 'raw duration class', ext: ['.ts', '.tsx'], re: /\b(?:duration|delay)-\d+\b/ },
  { name: 'raw easing', ext: ['.ts', '.tsx', '.css'], re: /cubic-bezier\(/, allow: [MOTION] },
  { name: 'raw duration in CSS', ext: ['.css'], re: /(?:transition|animation)(?:-duration|-delay)?\s*:[^;]*\b\d+(?:\.\d+)?m?s\b/ },
  { name: 'raw duration in motion props', ext: ['.ts', '.tsx'], re: /\b(?:duration|delay)\s*:\s*\d/, allow: [MOTION] },
  { name: 'raw easing array', ext: ['.ts', '.tsx'], re: /\bease\s*:\s*\[/, allow: [MOTION] },
  { name: 'inline style colour', ext: ['.tsx'], re: /style=\{\{[^}]*\b(?:color|background|backgroundColor|borderColor|fill|stroke|outlineColor)\s*:/ },
]

for (const file of walk(src)) {
  if (file === TOKENS || SKIP.has(file)) continue
  const ext = extname(file)
  if (!['.ts', '.tsx', '.css'].includes(ext)) continue
  const lines = readFileSync(file, 'utf8').split('\n')
  lines.forEach((line, i) => {
    if (/check:design-ignore/.test(line)) return
    for (const rule of rules) {
      if (!rule.ext.includes(ext) || rule.allow?.includes(file)) continue
      const m = line.match(rule.re)
      if (m) problems.push(`${relative(root, file)}:${i + 1}  ${rule.name}: ${m[0]}`)
    }
  })
}

// ---------------------------------------------------------------- contrast from tokens.css
const tokens = Object.fromEntries(
  [...readFileSync(TOKENS, 'utf8').matchAll(/--color-([a-z0-9-]+):\s*(#[0-9a-fA-F]{6})\s*;/g)].map((m) => [m[1], m[2]]),
)

function luminance(hex) {
  const c = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255)
  const [r, g, b] = c.map((v) => (v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4))
  return 0.2126 * r + 0.7152 * g + 0.0722 * b
}
export function contrast(a, b) {
  const [x, y] = [luminance(a), luminance(b)].sort((p, q) => q - p)
  return (x + 0.05) / (y + 0.05)
}

const SURFACES = ['canvas', 'surface', 'surface-2', 'surface-3']
const pairs = [
  ...['fg', 'fg-2', 'fg-3', 'ok', 'warn', 'bad'].flatMap((t) => SURFACES.map((s) => [t, s, 4.5])),
  ['on-inverse', 'inverse', 4.5],
  ...['canvas', 'surface', 'surface-2'].map((s) => ['line-control', s, 3]),
]
const report = []
for (const [text, surface, min] of pairs) {
  if (!tokens[text] || !tokens[surface]) {
    problems.push(`tokens.css: missing --color-${tokens[text] ? surface : text}`)
    continue
  }
  const ratio = contrast(tokens[text], tokens[surface])
  report.push(`${text} on ${surface}: ${ratio.toFixed(2)}`)
  if (ratio < min) problems.push(`contrast: ${text} on ${surface} is ${ratio.toFixed(2)} : 1 (needs ${min} : 1)`)
}

// ---------------------------------------------------------------- bundle budget
const dist = join(root, 'dist')
const JS_BUDGET = 200 * 1024
const FONT_BUDGET = 150 * 1024
if (existsSync(join(dist, 'index.html'))) {
  const html = readFileSync(join(dist, 'index.html'), 'utf8')
  const initial = [...html.matchAll(/<(?:script[^>]*src|link[^>]*rel="modulepreload"[^>]*href)="\/?([^"]+\.js)"/g)].map((m) => m[1])
  const js = initial.reduce((sum, f) => sum + gzipSync(readFileSync(join(dist, f))).length, 0)
  const assets = existsSync(join(dist, 'assets')) ? readdirSync(join(dist, 'assets')) : []
  // First render: the latin, upright file of each face (latin-ext and italic load on demand).
  const fonts = assets
    .filter((f) => f.endsWith('.woff2') && /-latin-(?!ext)/.test(f) && !/italic/.test(f))
    .reduce((sum, f) => sum + statSync(join(dist, 'assets', f)).size, 0)
  report.push(`initial JS: ${(js / 1024).toFixed(1)} KB gzipped (${initial.length} files)`)
  report.push(`first-render fonts: ${(fonts / 1024).toFixed(1)} KB`)
  if (initial.length === 0) problems.push('budget: no scripts found in dist/index.html')
  if (js > JS_BUDGET) problems.push(`budget: initial JS is ${(js / 1024).toFixed(1)} KB gzipped (max 200)`)
  if (fonts > FONT_BUDGET) problems.push(`budget: first-render fonts are ${(fonts / 1024).toFixed(1)} KB (max 150)`)
  if (!/rel="preload"[^>]*as="font"/.test(html)) problems.push('budget: the Interface regular is not preloaded')
  for (const file of assets.filter((f) => f.endsWith('.woff2') || f.endsWith('.woff'))) {
    if (!/-latin-/.test(basename(file))) problems.push(`fonts: ${file} is not a latin or latin-ext file`)
  }
} else {
  report.push('bundle budget: skipped (no dist/, run the build first)')
}

if (process.argv.includes('--verbose') || problems.length) console.log(report.join('\n'))
if (problems.length) {
  console.error(`\ncheck:design found ${problems.length} problem(s):\n  ${problems.join('\n  ')}`)
  process.exit(1)
}
console.log('check:design: clean')
