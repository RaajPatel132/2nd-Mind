/** The ring's colour: chrome, warn below 25% left, bad below 10%. */
export function quotaTone(remaining: number): 'chrome' | 'warn' | 'bad' {
  if (remaining < 0.1) return 'bad'
  if (remaining < 0.25) return 'warn'
  return 'chrome'
}
