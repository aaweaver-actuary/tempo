// Test-only subset of jsdom's replay API. Keep script execution enabled in BUI-16
// without adding a dependency on declarations for APIs this package does not use.
declare module "jsdom" {
  export class JSDOM {
    constructor(html: string, options: { runScripts: "dangerously" });
    readonly window: {
      readonly document: Document;
      readonly MouseEvent: typeof MouseEvent;
      close(): void;
    };
  }
}
