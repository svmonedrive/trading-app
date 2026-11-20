// `npm run package` - builds the UI and assembles a ready-to-share NIFTYBEES-Algo folder + zip.
//
//   frontend/release/NIFTYBEES-Algo/        (inspect it)
//   frontend/release/NIFTYBEES-Algo.zip     (send it)
//
// Contents: app/ (built UI), backend/ (code + requirements + .env.example + CONFIG.md), launcher.py,
// setup.bat/.ps1, uninstall.bat/.ps1, GETTING_STARTED.md, VERSION.txt.
// Python is NOT included: the receiver's setup.bat downloads portable Python 3.12 (python.org) and the
// packages (PyPI) into the app folder.
// NEVER included: .env, data/ (token, orders), .venv, logs. A safety check fails the build otherwise.
import { execSync } from 'node:child_process';
import { cpSync, existsSync, mkdirSync, readdirSync, readFileSync, rmSync, statSync, writeFileSync } from 'node:fs';
import { dirname, join, relative, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';

const FRONTEND = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const REPO = resolve(FRONTEND, '..');
const NAME = 'NIFTYBEES-Algo';
const RELEASE = join(FRONTEND, 'release');
const OUT = join(RELEASE, NAME);
const ZIP = join(RELEASE, `${NAME}.zip`);

const step = (msg) => console.log(`\n==> ${msg}`);
const run = (cmd, cwd) => execSync(cmd, { cwd, stdio: 'inherit' });

// ---------------------------------------------------------------- 1. build the UI
step('Building the UI (production)');
run('npx ng build', FRONTEND);
const dist = join(FRONTEND, 'dist', 'frontend', 'browser');
if (!existsSync(join(dist, 'index.html'))) throw new Error(`Build output not found: ${dist}`);

// ---------------------------------------------------------------- 2. assemble
step(`Assembling ${relative(REPO, OUT)}`);
rmSync(OUT, { recursive: true, force: true });
rmSync(ZIP, { force: true });
mkdirSync(OUT, { recursive: true });

const NEVER = /(^|[\\/])(\.env|\.venv|data|logs|runtime|__pycache__|node_modules|\.git)([\\/]|$)|\.(pyc|db|db-wal|db-shm|tmp|log)$/i;
const copy = (from, to) =>
  cpSync(join(REPO, from), join(OUT, to ?? from), { recursive: true, filter: (src) => !NEVER.test(relative(REPO, src)) });

cpSync(dist, join(OUT, 'app'), { recursive: true });
copy('backend/src');
copy('backend/requirements.txt');
copy('backend/CONFIG.md');
// .env.example is the one ".env*" file we DO ship (template, no secrets) - copied explicitly.
cpSync(join(REPO, 'backend', '.env.example'), join(OUT, 'backend', '.env.example'));
copy('launcher.py');
copy('packaging/setup.bat', 'setup.bat');
copy('packaging/setup.ps1', 'setup.ps1');
copy('packaging/uninstall.bat', 'uninstall.bat');
copy('packaging/uninstall.ps1', 'uninstall.ps1');
copy('packaging/GETTING_STARTED.md', 'GETTING_STARTED.md');
writeFileSync(join(OUT, 'VERSION.txt'), `NIFTYBEES Algo\nPackaged: ${new Date().toISOString()}\nCommit: ${gitCommit()}\n`);
if (!existsSync(join(OUT, 'app', 'favicon.ico'))) throw new Error('app/favicon.ico missing - the shortcut icon needs it');

// ---------------------------------------------------------------- 3. safety check
step('Safety check: no secrets, tokens, databases or logs');
const files = walk(OUT);
const problems = [];
for (const f of files) {
  const rel = relative(OUT, f);
  if (NEVER.test(rel) && !rel.endsWith(`backend${sep}.env.example`)) problems.push(`forbidden file: ${rel}`);
  if (/[\\/](config\.json|algo\.db)$/i.test(f)) problems.push(`forbidden file: ${rel}`);
  if (/\.(json|py|md|txt|bat|ps1|example|js|html)$/i.test(f)) {
    const text = readFileSync(f, 'utf8');
    if (/"access_token"\s*:\s*"[A-Za-z0-9]{10,}"/.test(text)) problems.push(`Zerodha token found in ${rel}`);
  }
}
const example = readFileSync(join(OUT, 'backend', '.env.example'), 'utf8');
if (!/^KITE_API_KEY=your_api_key\s*$/m.test(example) || !/^KITE_API_SECRET=your_api_secret\s*$/m.test(example)) {
  problems.push('backend/.env.example must contain the placeholder KITE_API_KEY / KITE_API_SECRET, not real keys');
}
if (!/^DRY_RUN=true\s*$/m.test(example)) problems.push('backend/.env.example must ship with DRY_RUN=true (paper mode)');
if (problems.length) {
  console.error('\nPACKAGING STOPPED:\n  ' + problems.join('\n  '));
  rmSync(OUT, { recursive: true, force: true });
  process.exit(1);
}
console.log(`  ${files.length} files checked - OK`);

// ---------------------------------------------------------------- 4. zip (Windows' own zip tool, so Explorer's
// "Extract All" and Expand-Archive handle it)
step(`Creating ${relative(REPO, ZIP)}`);
// Retried: a virus scanner can briefly lock freshly written files and make Compress-Archive fail.
for (let attempt = 1; ; attempt++) {
  try {
    execSync(`powershell -NoProfile -Command "Compress-Archive -Path '${OUT}' -DestinationPath '${ZIP}' -Force"`,
      { cwd: RELEASE, stdio: ['ignore', 'inherit', 'pipe'] });
    break;
  } catch (e) {
    if (attempt === 3) throw new Error(`Creating the zip failed:\n${e.stderr?.toString() ?? e.message}`);
    console.log(`  zip attempt ${attempt} failed (file busy?) - retrying in 3 s`);
    execSync('powershell -NoProfile -Command "Start-Sleep -Seconds 3"');
  }
}
const mb = (statSync(ZIP).size / 1024 / 1024).toFixed(1);
console.log(`\nDone.\n  Folder: ${OUT}\n  Zip:    ${ZIP} (${mb} MB)\n`);
console.log('Share the zip. The receiver unzips it and runs setup.bat once (needs internet); after that the');
console.log('"NIFTYBEES Algo" shortcut opens the app.');

// ---------------------------------------------------------------- helpers
function walk(dir) {
  return readdirSync(dir, { withFileTypes: true }).flatMap((e) =>
    e.isDirectory() ? walk(join(dir, e.name)) : [join(dir, e.name)]);
}

function gitCommit() {
  try {
    return execSync('git rev-parse --short HEAD', { cwd: REPO }).toString().trim();
  } catch {
    return 'unknown';
  }
}
