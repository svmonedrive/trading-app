// `npm run deploy` - builds the UI, assembles the ready-to-share NIFTYBEES-Algo folder + zip, and pushes the
// zip to the `downloads` branch on GitHub (like angular-cli-ghpages does with gh-pages).
//
//   frontend/release/NIFTYBEES-Algo/        (inspect it)
//   frontend/release/NIFTYBEES-Algo.zip     (the file that gets published)
//   origin/downloads: NIFTYBEES-Algo.zip + README.md   (download link for invited people)
//
// Options (after `--`, e.g. `npm run deploy -- --no-push`):
//   --no-push      build everything, but don't upload (prints what would be pushed)
//   --allow-dirty  allow deploying with uncommitted changes (normally refused, so the zip matches a commit)
//
// Publishing never touches your working folder, index or current branch: the `downloads` commit is built
// directly in git (hash-object / mktree / commit-tree) and pushed to origin.
//
// Contents: app/ (built UI), backend/ (code + requirements + .env.example + CONFIG.md), launcher.py,
// setup.bat/.ps1, uninstall.bat/.ps1, GETTING_STARTED.md, VERSION.txt.
// Python is NOT included: the receiver's setup.bat downloads portable Python 3.12 (python.org) and the
// packages (PyPI) into the app folder.
// NEVER included: .env, data/ (token, orders), .venv, logs. A safety check fails the build otherwise.
import { execFileSync, execSync } from 'node:child_process';
import { cpSync, existsSync, mkdirSync, readdirSync, readFileSync, rmSync, statSync, writeFileSync } from 'node:fs';
import { dirname, join, relative, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';

const FRONTEND = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const REPO = resolve(FRONTEND, '..');
const NAME = 'NIFTYBEES-Algo';
const RELEASE = join(FRONTEND, 'release');
const OUT = join(RELEASE, NAME);
const ZIP = join(RELEASE, `${NAME}.zip`);

const BRANCH = 'downloads';
const NO_PUSH = process.argv.includes('--no-push');
const ALLOW_DIRTY = process.argv.includes('--allow-dirty');

const step = (msg) => console.log(`\n==> ${msg}`);
const run = (cmd, cwd) => execSync(cmd, { cwd, stdio: 'inherit' });
const git = (args, input) =>
  execFileSync('git', args, { cwd: REPO, input, encoding: 'utf8', stdio: ['pipe', 'pipe', 'pipe'] }).trim();

// ---------------------------------------------------------------- 0. pre-checks
step('Checking git');
const dirty = git(['status', '--porcelain']);
if (dirty && !ALLOW_DIRTY) {
  console.error('\nDEPLOY STOPPED: you have uncommitted changes. Commit them first, so the published zip matches a\n' +
    'commit (or run `npm run deploy -- --allow-dirty`).\n\n' + dirty);
  process.exit(1);
}
const COMMIT = git(['rev-parse', '--short', 'HEAD']) + (dirty ? '-dirty' : '');
const ORIGIN = git(['remote', 'get-url', 'origin']);
console.log(`  commit ${COMMIT}  ->  ${ORIGIN} (branch ${BRANCH})${NO_PUSH ? '  [--no-push]' : ''}`);

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
const BUILT = new Date();
const VERSION = BUILT.toISOString().slice(0, 16).replace('T', ' ') + ' UTC';
writeFileSync(join(OUT, 'VERSION.txt'), `NIFTYBEES Algo\nBuilt: ${VERSION}\nCommit: ${COMMIT}\n`);
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
console.log(`  ${ZIP} (${mb} MB)`);

// ---------------------------------------------------------------- 5. publish to the `downloads` branch
step(`Publishing to branch '${BRANCH}'`);
const url = downloadUrl(ORIGIN);
const readme = [
  '# NIFTYBEES Algo - download',
  '',
  `**[Download NIFTYBEES-Algo.zip](${url ?? `${NAME}.zip`})** (${mb} MB)`,
  '',
  `- Built: ${VERSION}`,
  `- From commit: \`${COMMIT}\``,
  '',
  '## Install (Windows 10/11, 64-bit)',
  '1. Unzip into a folder you keep, e.g. `Documents\\NIFTYBEES-Algo`.',
  '2. Double-click `setup.bat` (needs internet, ~2-5 min, no admin rights).',
  '3. Open the **NIFTYBEES Algo** shortcut. The app guides you through the rest.',
  '',
  'Details: `GETTING_STARTED.md` inside the zip.',
  '',
  'This branch only holds the release zip. It is updated by `npm run deploy` (frontend/) - do not commit to it by hand.',
  '',
].join('\n');

// Build the commit directly in git - the working folder, index and current branch are never touched.
const zipBlob = git(['hash-object', '-w', ZIP]);
const readmeBlob = git(['hash-object', '-w', '--stdin'], readme);
const tree = git(['mktree'], `100644 blob ${zipBlob}\t${NAME}.zip\n100644 blob ${readmeBlob}\tREADME.md\n`);
const remoteTip = git(['ls-remote', '--heads', 'origin', BRANCH]).split(/\s+/)[0] || '';
if (remoteTip) git(['fetch', '--quiet', 'origin', BRANCH]); // parent commit must exist locally
const message = `NIFTYBEES-Algo ${VERSION} (from ${COMMIT})`;
const commit = git(['commit-tree', tree, ...(remoteTip ? ['-p', remoteTip] : []), '-m', message]);
console.log(`  ${remoteTip ? `new commit on top of ${remoteTip.slice(0, 7)}` : 'first commit of the branch'}: ` +
  `${commit.slice(0, 7)} "${message}"`);

if (NO_PUSH) {
  console.log(`\n--no-push: nothing uploaded. To publish this exact commit:\n  git push origin ${commit}:refs/heads/${BRANCH}`);
} else {
  execFileSync('git', ['push', 'origin', `${commit}:refs/heads/${BRANCH}`], { cwd: REPO, stdio: 'inherit' });
  console.log(`\nDeployed.${url ? `\n  Download link (people you invited to the repo, logged in to GitHub):\n  ${url}` : ''}`);
}

// ---------------------------------------------------------------- helpers
function walk(dir) {
  return readdirSync(dir, { withFileTypes: true }).flatMap((e) =>
    e.isDirectory() ? walk(join(dir, e.name)) : [join(dir, e.name)]);
}

/** https://github.com/<owner>/<repo>/raw/refs/heads/downloads/NIFTYBEES-Algo.zip (GitHub remotes only). */
function downloadUrl(remote) {
  const m = remote.match(/github\.com[:/]([^/]+)\/(.+?)(\.git)?$/);
  return m ? `https://github.com/${m[1]}/${m[2]}/raw/refs/heads/${BRANCH}/${NAME}.zip` : null;
}