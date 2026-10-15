import type { Route, BrowserContext } from "@playwright/test";
import { matchesContract, type ControlledContract, type ControlledRequest } from "./contracts";
type RouteHandler = (route: Route) => Promise<void> | void;
export class ControlledApi {
  contracts: { contract: ControlledContract; handler: RouteHandler }[] = [];
  traffic: { method: string; path: string; phase: string }[] = [];
  phase = "before document navigation";
  failures: string[] = [];
  pending = new Set<Promise<void>>();
  constructor(readonly origin: string, readonly assetPaths: ReadonlySet<string> = new Set(["/"])) {}
  register(contract: ControlledContract, handler: RouteHandler) { this.contracts.unshift({ contract, handler }); }
  assertClean() { if (this.failures.length) throw new Error(`Unexpected controlled traffic: ${this.failures.join("; ")}`); }
  async install(context: BrowserContext) {
    await context.routeWebSocket("**/*", socket => {
      this.failures.push(`Forbidden WebSocket ${socket.url()}`);
      socket.close({ code: 1008, reason: "Undeclared controlled connection" });
    });
    await context.route("**/*", async route => {
      const handling = (async () => {
      const request = route.request();
      const url = new URL(request.url());
      this.traffic.push({ phase: this.phase, method: request.method(), path: `${url.origin}${url.pathname}${url.search}` });
      try {
        if (url.origin === this.origin && this.assetPaths.has(url.pathname) && !url.search && ["GET", "HEAD"].includes(request.method()) && !request.postData()) {
          if (request.isNavigationRequest()) this.phase = "after document request";
          await route.continue(); return;
        }
        const input: ControlledRequest = { url: request.url(), method: request.method(), headers: request.headers(),
          body: request.postData() ? request.postDataJSON() : null };
        const matched = this.contracts.find(({ contract }) => matchesContract(contract, input, this.origin));
        if (!matched) throw new Error("Unexpected request");
        if (!matched.contract.validate && input.body !== null) throw new Error("Unexpected request body");
        matched.contract.validate?.(input);
        await matched.handler(route);
      } catch (error) {
        this.failures.push(`[${this.phase}] ${request.method()} ${url.origin}${url.pathname}${url.search}: ${String(error)}`);
        await route.abort("blockedbyclient");
      }
      })();
      this.pending.add(handling);
      try { await handling; } finally { this.pending.delete(handling); }
    });
  }
}

/** Stop native polling before releasing interception, then account every in-flight route. */
export async function finishControlledContext(context: BrowserContext, api: ControlledApi) {
  await context.close();
  await Promise.allSettled([...api.pending]);
  api.assertClean();
}
