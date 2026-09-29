import { existsSync } from "node:fs";
import { basename, join } from "node:path";

export function parsePostgresTestOptions(argumentsList, environment = process.env, root = process.cwd()) {
  if (environment.TEMPO_PG_BROWSER_GREP || environment.TEMPO_PG_BROWSER_FILE) {
    throw new Error("PostgreSQL browser focus must be passed as runner arguments; clear inherited TEMPO_PG_BROWSER_* filters");
  }

  const options = { list: false, skipBrowser: false, browserFile: null, browserGrep: null };
  for (let argumentIndex = 0; argumentIndex < argumentsList.length; argumentIndex += 1) {
    const argument = argumentsList[argumentIndex];
    if (argument === "--list") options.list = true;
    else if (argument === "--skip-browser") options.skipBrowser = true;
    else if (argument === "--browser-file" || argument === "--browser-grep") {
      const value = argumentsList[argumentIndex + 1];
      if (!value || value.startsWith("--") || !value.trim()) {
        throw new Error(`${argument} requires a non-empty value`);
      }
      argumentIndex += 1;
      if (argument === "--browser-file") options.browserFile = value;
      else options.browserGrep = value;
    } else {
      throw new Error(`Unknown PostgreSQL test option: ${argument}`);
    }
  }

  if (options.browserFile && options.browserGrep) {
    throw new Error("Choose one browser focus: --browser-file or --browser-grep");
  }
  if (options.skipBrowser && (options.browserFile || options.browserGrep)) {
    throw new Error("Browser focus cannot be combined with --skip-browser");
  }
  if (options.browserFile) {
    if (basename(options.browserFile) !== options.browserFile || !options.browserFile.endsWith(".spec.ts")) {
      throw new Error("--browser-file must be a .spec.ts basename from tests/browser");
    }
    const browserFilePath = join(root, "tests", "browser", options.browserFile);
    if (!existsSync(browserFilePath)) throw new Error(`Browser spec does not exist: ${options.browserFile}`);
  }
  return options;
}

export function buildPostgresPlaywrightArguments(options) {
  const browserArguments = ["playwright", "test"];
  if (options.browserFile) browserArguments.push(join("tests", "browser", options.browserFile));
  if (options.browserGrep) browserArguments.push("--grep", options.browserGrep);
  return browserArguments;
}
