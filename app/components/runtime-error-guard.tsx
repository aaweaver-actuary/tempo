"use client";

import { useEffect, type ReactNode } from "react";
import { TempoErrorBoundary } from "./error-boundary";
import { NotificationViewport } from "./notification-center";
import { installGlobalDebugErrorHandlers } from "../lib/debug-reporting";

export default function RuntimeErrorGuard({ children }: { children: ReactNode }) {
  useEffect(() => installGlobalDebugErrorHandlers(), []);
  return <><TempoErrorBoundary>{children}</TempoErrorBoundary><NotificationViewport /></>;
}
