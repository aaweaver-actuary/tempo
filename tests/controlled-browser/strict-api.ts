import type { Route, BrowserContext } from "@playwright/test";
import { matchesContract, type ControlledContract, type ControlledRequest } from "./contracts";
type RouteHandler = (route: Route) => Promise<void> | void;
export class ControlledApi {
  contracts: { contract: ControlledContract; handler: RouteHandler }[] = [];
  traffic: { method: string; path: string; phase: string }[] = [];
  phase = "before document navigation";
  failures: string[] = [];
  constructor(readonly origin: string) {}
  register(contract: ControlledContract, handler: RouteHandler) {
    this.contracts.unshift({ contract, handler });
  }
  assertClean() { if (this.failures.length) throw new Error(`Unexpected controlled traffic: ${this.failures.join("; ")}`); }
  async install(context: BrowserContext) {
    await context.route("**/*", async route => {
      const request = route.request();
      const url = new URL(request.url());
      if (url.origin === this.origin && url.pathname !== "/api" && !url.pathname.startsWith("/api/")) {
        if (request.isNavigationRequest()) this.phase = "after document request";
        await route.continue(); return;
      }
      this.traffic.push({ phase: this.phase, method: request.method(), path: `${url.origin}${url.pathname}${url.search}` });
      try {
        const input: ControlledRequest = { url: request.url(), method: request.method(), headers: request.headers(),
          body: request.postData() ? request.postDataJSON() : null };
        const matched = this.contracts.find(({ contract }) => matchesContract(contract, input, this.origin));
        if (!matched) throw new Error("Unexpected request");
        matched.contract.validate?.(input);
        await matched.handler(route);
      } catch (error) {
        this.failures.push(`[${this.phase}] ${request.method()} ${url.origin}${url.pathname}${url.search}: ${String(error)}`);
        await route.abort("blockedbyclient");
      }
    });
  }
}
