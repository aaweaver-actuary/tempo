"use client";

import { useEffect, type ReactNode } from "react";
import { TempoErrorBoundary } from "./error-boundary";
import { DebugErrorPanel } from "./debug-error-panel";
import { installGlobalDebugErrorHandlers } from "../lib/debug-reporting";

export default function RuntimeErrorGuard({ children }: { children: ReactNode }) {
  useEffect(() => installGlobalDebugErrorHandlers(), []);
  return <><TempoErrorBoundary>{children}</TempoErrorBoundary><DebugErrorPanel /></>;
}
