/** Whether an address is one the app may link to: http(s) only, never javascript:, data: or file:. */
export function isWebAddress(href: string): boolean {
  try {
    const url = new URL(href)
    return url.protocol === 'https:' || url.protocol === 'http:'
  } catch {
    return false
  }
}
