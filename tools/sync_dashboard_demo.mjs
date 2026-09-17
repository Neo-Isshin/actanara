// Keep the public demo on the production UI. Only URLs, bundled dependencies
// and the static data adapter differ. Emit an apply_patch patch for review.
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { execFileSync } from 'node:child_process';
const root = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const production = path.join(root, 'src/dashboard/app/static');
const mappings = [
  ['index.html', text => text
    .replace(/<link rel="preconnect"[^>]+>\n/g, '')
    .replace(/<link href="https:\/\/fonts\.googleapis\.com[^>]+>\n/g, '')
    .replace('https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js', 'js/vendor/chart.umd.min.js')
    .replace('https://cdn.jsdelivr.net/npm/marked/marked.min.js', 'js/vendor/marked.min.js')
    .replace('<script src="/static/js/autorefresh.js"></script>', '<script src="js/demo-assets.js"></script>\n<script src="js/static-mock.js"></script>')
    .replaceAll('/static/', '')
    .replaceAll('href="/tasks"', 'href="tasks.html"')
    .replace('class="asset-dashboard"', 'class="asset-dashboard public-demo"')
    .replace('</head>', '  <link rel="stylesheet" href="css/demo.css">\n</head>')
    .replace('<main class="main">', '<main class="main">\n  <aside class="demo-notice" aria-label="Demo information"><b>v1.8.0 · Interactive demo / 在线演示</b><span>示例数据 · 不连接本机 · 生成、注册与服务操作需安装后使用 / Sample data; no local runtime access.</span><a href="https://github.com/Neo-Isshin/actanara/releases/latest" target="_blank" rel="noopener noreferrer">安装 / Install ↗</a></aside>')],
  ['css/style.css', text => text],
  ['css/dashboard.css', text => text],
  ['js/dashboard.js', text => text.replaceAll("'/tasks'", "'tasks.html'").replaceAll('href="/tasks"', 'href="tasks.html"')],
  ['js/app.js', text => text.replaceAll('href="/tasks"', 'href="tasks.html"')
    .replaceAll('/Volumes/Backup/Actanara', '~/Backups/Actanara')
    .replaceAll('/Users/example/work/TokenClock', '~/work/TokenClock')
    .replaceAll('/Users/you/.openclaw-2', '~/.openclaw-2')],
];
let patch = '*** Begin Patch\n';
const mismatches = [];
const patches = [];
for (const [relative, transform] of mappings) {
  const filename = 'docs/dashboard-demo/' + relative;
  const expected = transform(fs.readFileSync(path.join(production, relative), 'utf8'));
  const previous = fs.existsSync(path.join(root, filename)) ? fs.readFileSync(path.join(root, filename), 'utf8') : null;
  if (expected === previous) continue;
  mismatches.push(filename);
  if (previous === null) patches.push('*** Add File: ' + filename + '\n' + expected.trimEnd().split('\n').map(line => '+' + line).join('\n') + '\n');
  else {
    const diff = execFileSync('python3', ['-c', 'import sys,json,difflib; d=json.load(sys.stdin); print("".join(list(difflib.unified_diff(d[0].splitlines(True),d[1].splitlines(True),n=3))[2:]),end="")'], { input: JSON.stringify([previous, expected]), encoding: 'utf8', maxBuffer: 8 * 1024 * 1024 });
    for (const hunk of diff.split(/(?=^@@ )/m).filter(Boolean)) {
      patches.push('*** Update File: ' + filename + '\n' + hunk.replace(/^@@[^\n]*/, '@@'));
    }
  }
}
if (process.argv.includes('--check')) {
  if (mismatches.length) { console.error('Demo UI differs from production: ' + mismatches.join(', ')); process.exitCode = 1; }
  else console.log('Demo UI matches production (documented URL/static-data adaptations only).');
} else if (process.argv.includes('--list')) console.log(JSON.stringify(patches.map((text, index) => ({index, bytes: text.length, file: text.split('\n')[0]}))));
else {
  const selected = process.argv.find(value => value.startsWith('--chunk='));
  const grouped = new Map();
  for (const change of patches) {
    const index = change.indexOf('\n'), header = change.slice(0, index);
    grouped.set(header, (grouped.get(header) || '') + change.slice(index + 1));
  }
  const complete = Array.from(grouped, ([header, hunks]) => header + '\n' + hunks).join('');
  process.stdout.write(patch + (selected ? patches[Number(selected.slice(8))] || '' : complete) + '*** End Patch\n');
}
