/** Where the source and the write-ups live: one place, so the landing page and the docs agree. */
export const REPO_URL = 'https://github.com/RaajPatel132/2nd-Mind'

export const SOURCE_LINKS = [
  { id: 'source', label: 'Source', href: REPO_URL },
  { id: 'architecture', label: 'Architecture', href: `${REPO_URL}/blob/main/docs/architecture.md` },
  { id: 'decisions', label: 'Decisions', href: `${REPO_URL}/tree/main/docs/adr` },
] as const
