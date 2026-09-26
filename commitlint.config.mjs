// Commit message standard for this repo. Rationale and examples: CONTRIBUTING.md.
// Enforced locally by the husky commit-msg hook and in CI on every pushed commit.

const SCOPES = [
  // backend modules
  'api', 'agent', 'ingestion', 'retrieval', 'memory', 'providers', 'policy', 'metering',
  'auth', 'jobs', 'observability', 'evals', 'config', 'core', 'db', 'prompts', 'corrections',
  // frontend and delivery
  'web', 'e2e', 'infra', 'docker', 'ci', 'deps',
  // docs and repo
  'docs', 'adr', 'sprint', 'repo', 'release',
]

// Past tense / third person openers: subjects are imperative ("add", not "added"/"adds").
const NON_IMPERATIVE = /^(added|adds|fixed|fixes|updated|updates|changed|changes|removed|removes|implemented|implements|refactored|refactors|created|creates|improved|improves|made|makes|moved|moves|renamed|renames|deleted|deletes|bumped|bumps)\b/i

// Subjects that say nothing about the outcome.
const VAGUE = /^(wip|misc|stuff|tmp|temp|minor|changes|update|updates|fix|fixes|cleanup|tweaks?|progress|work|save)$/i

// Attribution badges and co-author trailers are not used in this repo.
const CO_AUTHOR = /^co-authored-by:/im
const GENERATED_BADGE = /generated with \[?claude|🤖/i

/** @type {import('@commitlint/types').UserConfig} */
export default {
  extends: ['@commitlint/config-conventional'],
  plugins: [
    {
      rules: {
        'subject-imperative': ({ subject }) => [
          !subject || !NON_IMPERATIVE.test(subject.trim()),
          'subject must be imperative: "add x", not "added x" / "adds x"',
        ],
        'subject-outcome': ({ subject }) => [
          !subject || !VAGUE.test(subject.trim()),
          'subject must state the outcome, e.g. "stream replies over SSE", not "update"',
        ],
        'body-max-lines': ({ body }, _when = 'always', max = 6) => {
          const lines = (body ?? '').split('\n').filter((l) => l.trim() !== '')
          return [lines.length <= max, `body must be ${max} lines or fewer; no paragraphs`]
        },
        'no-attribution': ({ raw }) => [
          !CO_AUTHOR.test(raw ?? '') && !GENERATED_BADGE.test(raw ?? ''),
          'remove Co-authored-by trailers and "Generated with" badges',
        ],
      },
    },
  ],
  rules: {
    'header-max-length': [2, 'always', 72],
    'subject-min-length': [2, 'always', 10],
    'subject-full-stop': [2, 'never', '.'],
    'subject-imperative': [2, 'always'],
    'subject-outcome': [2, 'always'],
    'scope-enum': [2, 'always', SCOPES],
    'scope-case': [2, 'always', 'kebab-case'],
    'body-leading-blank': [2, 'always'],
    'body-max-line-length': [2, 'always', 100],
    'body-max-lines': [2, 'always', 6],
    'footer-leading-blank': [2, 'always'],
    'footer-max-line-length': [2, 'always', 100],
    'no-attribution': [2, 'always'],
  },
}
