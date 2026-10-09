import { spawn } from "node:child_process";
import { closeSync, mkdirSync, openSync, readFileSync, writeFileSync } from "node:fs";
import { dirname } from "node:path";
import { redact } from "./tempo-deployment.mjs";

// Capture before browser recovery cases can replace the containers whose
// history explains an earlier failure. File descriptors keep draining while
// the parent waits synchronously for Playwright.
export async function withBrowserServiceLogs(verify, { compose, environment, project, output, secrets,
  spawnLogs = spawn }) {
  if (!/^tempo-pg-regressions-\d+-[a-f0-9]+$/.test(project))
    throw new Error("Browser evidence requires an owned disposable PostgreSQL project");
  mkdirSync(dirname(output), { recursive: true });
  const descriptor = openSync(output, "w", 0o600);
  let logProcess;
  let captureError;
  let verificationError;
  try {
    logProcess = spawnLogs("docker", [...compose, "logs", "--follow", "--no-color", "--timestamps"],
      { env: environment, stdio: ["ignore", descriptor, descriptor] });
    const logProcessClosed = new Promise(resolve => {
      logProcess.once("error", error => { captureError = error; });
      logProcess.once("close", (code, signal) => resolve({ code, signal }));
    });
    try { await verify(); } catch (error) { verificationError = error; }
    const stopRequested = logProcess.exitCode === null && logProcess.signalCode === null
      && logProcess.kill("SIGTERM");
    const terminationDeadline = setTimeout(() => {
      captureError = new Error("Browser service log capture did not stop within its cleanup deadline");
      logProcess.kill("SIGKILL");
    }, 5_000);
    const captureExit = await logProcessClosed;
    clearTimeout(terminationDeadline);
    // Docker Compose handles our SIGTERM and returns 130 without a signal.
    // Accept that code only when this invocation requested termination.
    const expectedTermination = stopRequested && (captureExit.signal === "SIGTERM" || captureExit.code === 130);
    if (captureExit.code !== 0 && !expectedTermination && !captureError)
      captureError = new Error(`Browser service log capture ended with code ${captureExit.code} and signal ${captureExit.signal}`);
  } catch (error) { captureError = error; }
  finally {
    try {
      closeSync(descriptor);
      writeFileSync(output, redact(readFileSync(output, "utf8"), secrets));
    } catch (error) {
      captureError = captureError ? new AggregateError([captureError, error], "Browser evidence cleanup failed") : error;
    }
  }
  if (verificationError && captureError)
    throw new AggregateError([verificationError, captureError], "Browser verification and evidence capture failed");
  if (verificationError) throw verificationError;
  if (captureError) throw captureError;
}
