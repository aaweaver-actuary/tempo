import { resolvePython } from './resolve-python.mjs';
const escape = value => value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
export function studyGrep(browser) {
  // Anchored title suffix plus exact project/file collection validation rejects extras.
  return browser.map(test => `(?:^| )${escape(test.title)}$`).join('|');
}
export function fastCommands(plan) {
  const commands = [
    ['conflicts','node',['scripts/check-merge-conflicts.mjs']],
    ['migrations','node',['database/tooling/check-migration-inventory.mjs']],
    ['diff','git',['diff','--check',`${plan.base}...${plan.head}`]],
  ];
  if (plan.node) commands.push(['lint','npm',['run','lint']],['typecheck','npm',['run','typecheck']]);
  if (plan.backend.length) commands.push(['python_static',resolvePython(),['-m','compileall','-q',...plan.paths.filter(path => path.endsWith('.py') && !path.startsWith('tests/'))]]);
  if (plan.frontend.length) commands.push(
    ['frontend_inventory','npx',['vitest','list',...plan.frontend,'--no-static-parse','--includeTaskLocation','--json=test-results/fast-lane/frontend-inventory.json']],
    ['frontend','npx',['vitest','run',...plan.frontend,'--includeTaskLocation','--reporter=default','--reporter=json','--outputFile.json=test-results/fast-lane/frontend.json']],
  );
  if (plan.backend.length) commands.push(
    ['backend_inventory',resolvePython(),['scripts/ci-collect-backend.py','test-results/fast-lane/backend-inventory.json',...plan.backend,'-q','--rootdir=.']],
    ['backend',resolvePython(),['-m','pytest',...plan.backend,'-q','--rootdir=.','--junitxml=test-results/fast-lane/backend.xml']],
  );
  if (plan.tier === 'STUDY') commands.push(
    ['capabilities','node',['scripts/check-test-capabilities.mjs','--docker','--loopback','--workspace-mount']],
    ['browser_inventory','npx',['playwright','test','--list','--reporter=json','--project=chromium',...new Set(plan.browser.map(test => `tests/browser/${test.file}`))]],
    ['study','node',['database/tests/test-postgres-docker.mjs','--mode','study','--browser-grep',studyGrep(plan.browser),
      ...(plan.nativeChecks ?? []).flatMap(check => ['--native-check',check])]],
  );
  return commands;
}
