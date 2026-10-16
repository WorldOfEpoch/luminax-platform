import { readFile, readdir } from 'node:fs/promises';
import { resolve, relative } from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawnSync } from 'node:child_process';

const root = fileURLToPath(new URL('../', import.meta.url));

export async function checkWeb() {
  let checked = 0;
  async function visit(directory) {
    for (const entry of await readdir(directory, { withFileTypes: true })) {
      if (['node_modules', 'papr'].includes(entry.name) && entry.isDirectory()) continue;
      const path = resolve(directory, entry.name);
      if (entry.isDirectory()) { await visit(path); continue; }
      if (!/\.(?:js|html)$/.test(entry.name)) continue;
      const content = await readFile(path, 'utf8');
      const scripts = entry.name.endsWith('.js') ? [['', content]] :
        [...content.matchAll(/<script\b([^>]*)>([\s\S]*?)<\/script\s*>/gi)]
          .filter(match => !/\bsrc\s*=|application\/(?:ld\+)?json/i.test(match[1]))
          .map(match => [match[1], match[2]]);
      for (const [attributes, code] of scripts) {
        const kind = /\bmodule\b/.test(attributes) ? 'module' : 'commonjs';
        const result = spawnSync(process.execPath, ['--check', '--input-type=' + kind], {
          input: code, encoding: 'utf8', timeout: 10000,
        });
        if (result.status !== 0) throw new Error(`${relative(root, path)}: ${result.stderr || result.error || 'syntax check failed'}`);
        checked++;
      }
    }
  }
  for (const folder of ['web', 'sites', 'payload/luminax-ui']) await visit(resolve(root, folder));
  return checked;
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  process.stdout.write(`JavaScript syntax checks passed: ${await checkWeb()}\n`);
}
