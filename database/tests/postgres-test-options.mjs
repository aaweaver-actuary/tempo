import { existsSync } from "node:fs";
import { basename, join } from "node:path";
import { checkpoint1BrowserGrep, checkpoint1NativeChecks } from "./postgres-test-plan.mjs";

export const maintainedStudyBrowser = "study_only_mode_preserves_foreground_review_and_queue_progression across reload and resumption$";

export function parsePostgresTestOptions(argumentsList, environment = process.env, root = process.cwd()) {
  if (environment.TEMPO_PG_BROWSER_GREP || environment.TEMPO_PG_BROWSER_FILE) {
    throw new Error("PostgreSQL browser focus must be passed as runner arguments; clear inherited TEMPO_PG_BROWSER_* filters");
  }

  const options = { list: false, skipBrowser: false, browserFile: null, browserGrep: null, mode: null };
  for (let argumentIndex = 0; argumentIndex < argumentsList.length; argumentIndex += 1) {
    const argument = argumentsList[argumentIndex];
    if (argument === "--p14-checkpoint1") {
      if (options.p14Checkpoint1) throw new Error("Duplicate P14 checkpoint selection");
      options.p14Checkpoint1 = true;
    }
    else if (argument === "--maintained-study-proof") {
      if (options.maintainedStudyProof) throw new Error("Duplicate maintained Study proof option");
      options.maintainedStudyProof = true;
    }
    else if (argument === "--list") options.list = true;
    else if (argument === '--native-check') {
      const check = argumentsList[++argumentIndex];
      if (!['owner-trio', 'redis-fixture-isolation', 'position-grade-effects', 'position-review-atomicity', 'position-introductions', 'position-queue-admission', 'frozen-requeue-cycle', 'prefix-split-placement', 'game-review-reader'].includes(check) || options.nativeChecks?.includes(check)) throw new Error('Unknown or duplicate native check');
      (options.nativeChecks ??= []).push(check);
    }
    else if (argument === "--skip-browser") options.skipBrowser = true;
    else if (["--mode", "--browser-file", "--browser-grep"].includes(argument)) {
      const value = argumentsList[argumentIndex + 1];
      if (!value || value.startsWith("--") || !value.trim()) {
        throw new Error(`${argument} requires a non-empty value`);
      }
      argumentIndex += 1;
      const property = argument === "--mode" ? "mode" : argument === "--browser-file" ? "browserFile" : "browserGrep";
      if (options[property] !== null) throw new Error(`Duplicate PostgreSQL test option: ${argument}`);
      options[property] = value;
    } else {
      throw new Error(`Unknown PostgreSQL test option: ${argument}`);
    }
  }

  if (options.p14Checkpoint1) {
    if (options.mode !== "study" || options.skipBrowser || options.browserFile || options.browserGrep || options.nativeChecks?.length || options.maintainedStudyProof || environment.TEMPO_PG_STALE_DELIVERY_ONLY === "1" || environment.TEMPO_RELEASE_CANDIDATE_MODE)
      throw new Error("P14 checkpoint requires its fixed Study, browser and native selection");
    options.browserGrep = checkpoint1BrowserGrep;
    options.nativeChecks = [...checkpoint1NativeChecks];
  }

  if (options.maintainedStudyProof) {
    if (environment.TEMPO_PG_STALE_DELIVERY_ONLY === "1" || environment.TEMPO_RELEASE_CANDIDATE_MODE || options.mode !== "study" || options.skipBrowser || options.browserFile || options.browserGrep || options.nativeChecks?.length)
      throw new Error("Maintained Study proof requires explicit study mode and no alternate selections");
    options.browserGrep = maintainedStudyBrowser;
  }
  if (options.browserFile && options.browserGrep) {
    throw new Error("Choose one browser focus: --browser-file or --browser-grep");
  }
  if (options.mode !== null && !["full", "browser", "study", "durability", "lifecycle", "lifecycle-current", "priority-historical"].includes(options.mode)) {
    throw new Error("--mode must be full, browser, study, durability, lifecycle, lifecycle-current, or priority-historical");
  }
  if (options.skipBrowser && options.mode !== null && options.mode !== "durability") {
    throw new Error("--skip-browser cannot be combined with a mode other than durability");
  }
  const hasBrowserFocus = Boolean(options.browserFile || options.browserGrep);
  options.mode ??= options.skipBrowser ? "durability" : hasBrowserFocus ? "browser" : "full";
  if (options.nativeChecks?.length && !['study','browser'].includes(options.mode)) throw new Error('Native checks require study or browser mode');
  options.skipBrowser = ["durability", "lifecycle", "lifecycle-current", "priority-historical"].includes(options.mode);
  if (options.mode === "priority-historical" && (environment.TEMPO_PG_STALE_DELIVERY_ONLY === "1" || environment.TEMPO_RELEASE_CANDIDATE_MODE || options.nativeChecks?.length || options.maintainedStudyProof)) throw new Error("Historical priority proof requires its fixed selection");
  if (hasBrowserFocus && !["browser", "study"].includes(options.mode)) {
    throw new Error("Browser focus cannot be combined with non-browser mode or --skip-browser; use --mode browser");
  }
  if (options.browserFile) {
    if (/[\\/]/.test(options.browserFile) || basename(options.browserFile) !== options.browserFile
      || !options.browserFile.endsWith(".spec.ts")) {
      throw new Error("--browser-file must be a .spec.ts basename from tests/browser");
    }
    const browserFilePath = join(root, "tests", "browser", options.browserFile);
    if (!existsSync(browserFilePath)) throw new Error(`Browser spec does not exist: ${options.browserFile}`);
  }
  if (options.browserGrep) {
    try { new RegExp(options.browserGrep); }
    catch { throw new Error("--browser-grep must be a valid regular expression"); }
  }
  if (options.mode === "study" && !options.browserGrep) throw new Error("Study mode requires an explicit browser smoke selection");
  return options;
}

export function buildPostgresPlaywrightArguments(options) {
  const browserArguments = ["playwright", "test"];
  if (options.browserFile) browserArguments.push(join("tests", "browser", options.browserFile));
  if (options.browserGrep) browserArguments.push("--grep", options.browserGrep);
  if (options.p14Checkpoint1) browserArguments.push("--max-failures=1");
  return browserArguments;
}
