import { execFileSync } from 'node:child_process'

/**
 * The operator's CLI, run inside the api container of the stack under test (the same commands as
 * `make kill-switch` and `make set-tier`). `E2E_COMPOSE` names the compose files, so the same
 * specs drive the plain stack and the production-shaped one.
 */
const compose = (process.env.E2E_COMPOSE ?? '-f ../compose.yaml').split(' ').filter(Boolean)

export function admin(...args: string[]): string {
  return execFileSync('docker', ['compose', ...compose, 'exec', '-T', 'api', 'python', '-m', 'secondmind.api.admin', ...args], {
    encoding: 'utf8',
  })
}

export function killSwitch(state: 'on' | 'off'): void {
  admin('kill-switch', state)
}

export function setTier(email: string, tier: 'guest' | 'standard' | 'premium'): void {
  admin('set-tier', email, tier)
}

/** Every process re-reads the flag within FLAG_TTL_S (2 s); wait a little longer. */
export const FLAG_SETTLE_MS = 3_000
