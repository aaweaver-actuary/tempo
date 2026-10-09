import { z } from "zod";
import { backgroundKindSchema } from "./background-diagnostics";

const timestamp = z.iso.datetime({ offset: true });
export const activityNotificationPageSchema = z.object({
  workspace_id: z.uuid(), available: z.literal(true), monitoring_available: z.boolean(), monitoring_as_of: timestamp.nullable(),
  items: z.array(z.object({
    sequence: z.number().int().positive(), event: z.enum(["opened", "resolved"]),
    occurred_at: timestamp, id: z.uuid(), kind: backgroundKindSchema,
    source: z.enum(["durable", "game_analysis", "threat_analysis", "coverage", "sync", "derivation"]),
    work_id: z.string().min(1).max(200), generation_key: z.string().min(1).max(200),
    reason: z.enum(["running_stalled", "queue_stalled", "repeated_timeout"]),
    opened_at: timestamp, resolved_at: timestamp.nullable(), last_progress_at: timestamp.nullable(),
  }).strict()).max(100),
  next_cursor: z.number().int().nonnegative(), has_more: z.boolean(),
}).strict().superRefine((page, context) => {
  if (page.items.some(item =>
    (item.event === "resolved" && (!item.resolved_at || !item.last_progress_at)) ||
    (item.resolved_at !== null && Date.parse(item.resolved_at) < Date.parse(item.opened_at))))
    context.addIssue({ code: "custom", message: "Recovery evidence is inconsistent" });
  if (page.items.some((item, index) => index > 0 && item.sequence <= page.items[index - 1].sequence) ||
      (page.items.length > 0 && page.next_cursor !== page.items.at(-1)!.sequence) ||
      (page.has_more && page.items.length === 0))
    context.addIssue({ code: "custom", message: "Notification sequence is inconsistent" });
});
export type ActivityIncidentChange = z.infer<typeof activityNotificationPageSchema>["items"][number];
