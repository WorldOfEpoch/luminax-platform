import { lstat, readdir, readFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createHash } from 'node:crypto';

const root = fileURLToPath(new URL('../', import.meta.url));
const fileLimit = 256 * 1024;
const reviewedLargeFiles = new Set([
  'payload/assistant-core/main.py',
  'payload/discord-bot/bot.py',
  'payload/luminax-ui/index.html',
  'payload/underfoot-world/main.py',
]);
const reviewedArtwork = new Map([
  [
    "payload/luminax-ui/assets/olympus/discord/hermes-avatar-discord.png",
    "cad324ac1951528cf64678ccc462aa9f70fe826945b7a00384cc1f0edecc1f59"
  ],
  [
    "payload/luminax-ui/assets/olympus/discord/olympus-control-banner.png",
    "2700df65a3d062e2d78fe9035729347b714749fb72fb829ce75c31d90caf108d"
  ],
  [
    "payload/luminax-ui/assets/olympus/discord/revamp-staging-banner.png",
    "2aa942461c3b07bb466f713fc5b87cb263aa6eb62a0044af1f5ff364a99d9de0"
  ],
  [
    "payload/luminax-ui/assets/olympus/discord/worldofepoch-realm-banner.png",
    "2fdd32d943fbc2934e00ceb3c61e327075e2d961d0bf6d565f5c0e964d66a381"
  ],
  [
    "payload/luminax-ui/assets/olympus/discord/worldofepoch-realm-icon.png",
    "0c26f9608047eda6d5b0525ba171ce50e092ed78110d0abca6533abd5a83abe5"
  ],
  [
    "payload/luminax-ui/assets/olympus/discord/zeus-sigil-discord.png",
    "e5fef6c674e84b59b27726cdaeb088271547b4cac775ef720d4e514e8c9a0082"
  ]
]);
const rules = [
  ['provider_key', /\bsk-(?:or-v1-|proj-)?[A-Za-z0-9_-]{20,}\b/],
  ['github_token', /\b(?:gh[pousr]_|github_pat_)[A-Za-z0-9_]{20,}\b/],
  ['cloud_access_key', /\b(?:AKIA|ASIA)[A-Z0-9]{16}\b/],
  ['model_hub_token', /\bhf_[A-Za-z0-9]{20,}\b/],
  ['google_api_key', /\bAIza[A-Za-z0-9_-]{30,}\b/],
  ['slack_token', /\bxox[baprs]-[A-Za-z0-9-]{20,}\b/],
  ['discord_token', /\b(?:mfa\.[A-Za-z0-9_-]{20,}|[A-Za-z0-9_-]{23,28}\.[A-Za-z0-9_-]{6,7}\.[A-Za-z0-9_-]{25,110})\b/],
  ['private_key', /-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----/],
  ['windows_personal_path', /\b[A-Z]:[\\/]+Users[\\/]+[^\\/\s"']+/i],
  ['unix_personal_path', /\/(?:home|Users)\/[^\s/"']+/],
  ['credential_url', /https?:\/\/[^/\s:@]+:[^/\s@]+@/i],
];

// A narrow guard against common accidental disclosures, not a complete secret detector.
export function scanText(value) {
  return rules.filter(([, pattern]) => pattern.test(value)).map(([code]) => code);
}

export async function auditPublication() {
  const manifestPath = resolve(root, 'PUBLICATION_FILES.json');
  const manifestStat = await lstat(manifestPath);
  if (!manifestStat.isFile() || manifestStat.isSymbolicLink() || manifestStat.size > fileLimit) throw new Error('invalid_manifest');
  const files = JSON.parse(await readFile(manifestPath, 'utf8'));
  if (!Array.isArray(files) || files.length === 0 || files.some(file =>
    typeof file !== 'string' || !/^[A-Za-z0-9_.-]+(?:\/[A-Za-z0-9_.-]+)*$/.test(file) ||
    file.split('/').some(part => ['.', '..', '.git'].includes(part)))) throw new Error('invalid_manifest');
  const expected = new Set(files);
  if (expected.size !== files.length || !expected.has('PUBLICATION_FILES.json')) throw new Error('invalid_manifest');
  if (files.some(file =>
    /(?:^|\/)(?:\.env|\.env\.windows|windows\.env|node_modules|__pycache__)(?:\/|$)/i.test(file) ||
    /\.(?:key|pem|pfx|p12|crt|cer|db|sqlite3?)$/i.test(file) ||
    /^payload\/(?:runtime|certs|reports|memory|projects|generated_media|published_sites|upgrades)\//.test(file) ||
    file === 'payload/config/private-secret-refs.json')) throw new Error('forbidden_manifest_path');
  const seen = new Set();
  const issues = [];
  async function visit(directory, prefix = '') {
    for (const entry of await readdir(directory, { withFileTypes: true })) {
      if (prefix === '' && entry.name === '.git') continue;
      // CI checks the clean checkout before installing dependencies.
      // Local environments/caches are not part of the source release.
      if (entry.isDirectory() && (entry.name === '__pycache__' || entry.name === '.pytest_cache' ||
          (prefix === '' && ['.venv', 'dist'].includes(entry.name)) ||
          (prefix === 'sites/' && entry.name === 'papr') ||
          (prefix === 'sites/papr-src/' && entry.name === 'node_modules') ||
          (prefix === 'desktop-shell/' && ['bin', 'obj'].includes(entry.name)) ||
          (prefix === 'command-center/' && ['node_modules', 'dist'].includes(entry.name)))) continue;
      if (prefix === 'command-center/' && entry.name.endsWith('.tsbuildinfo')) continue;
      const name = prefix + entry.name;
      const path = resolve(directory, entry.name);
      const stat = await lstat(path);
      if (stat.isSymbolicLink()) { issues.push({ code: 'linked_entry' }); continue; }
      if (stat.isDirectory()) {
        if (!files.some(file => file.startsWith(name + '/'))) { issues.push({ code: 'unexpected_directory' }); continue; }
        await visit(path, name + '/');
        continue;
      }
      if (!stat.isFile() || !expected.has(name)) { issues.push({ code: 'unexpected_file' }); continue; }
      seen.add(name);
      if (stat.nlink > 1) { issues.push({ file: name, code: 'hard_linked_file' }); continue; }
      if (stat.size > (reviewedLargeFiles.has(name) ? 2 * 1024 * 1024 : fileLimit)) {
        issues.push({ file: name, code: 'file_too_large' }); continue;
      }
      const content = await readFile(path);
      if (reviewedArtwork.has(name)) {
        if (createHash('sha256').update(content).digest('hex') !== reviewedArtwork.get(name)) issues.push({ file: name, code: 'artwork_hash_mismatch' });
        continue;
      }
      if (content.includes(0)) { issues.push({ file: name, code: 'binary_content' }); continue; }
      for (const code of scanText(content.toString('utf8'))) issues.push({ file: name, code });
    }
  }
  await visit(root);
  for (const file of expected) if (!seen.has(file)) issues.push({ file, code: 'missing_file' });
  return { ok: issues.length === 0, checkedFiles: seen.size, issues };
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    const result = await auditPublication();
    process.stdout.write(JSON.stringify(result, null, 2) + '\n');
    if (!result.ok) process.exitCode = 1;
  } catch {
    process.stderr.write('Publication audit failed to read or validate the package.\n');
    process.exitCode = 2;
  }
}
