import type { SyncSchedule } from "@/api/types";
import { Calendar, Check, Clock, Webhook } from "lucide-react";
import type { ComponentType, SVGProps } from "react";

export type SyncScheduleMeta = {
  value: SyncSchedule;
  /** Short label shown on the card chip and the settings trigger. */
  label: string;
  /** One-line cadence hint for dropdown rows and tooltips. */
  hint: string;
  icon: ComponentType<SVGProps<SVGSVGElement>>;
  /** True for the cron cadences — the "regular syncs" the scheduler drives. */
  automated: boolean;
};

/**
 * Single source of truth for how every `SyncSchedule` value is presented.
 * Consumed by the `SyncSettings` picker (ordered) and the `RepoCard` footer
 * chip (lookup) so the cadence label/icon can never drift between the place
 * you set it and the place you read it at a glance.
 */
export const SYNC_SCHEDULE_OPTIONS: SyncScheduleMeta[] = [
  { value: "manual", label: "Manual", hint: "Only on demand", icon: Check, automated: false },
  { value: "hourly", label: "Hourly", hint: "Every hour", icon: Clock, automated: true },
  { value: "daily", label: "Daily", hint: "Once per day", icon: Calendar, automated: true },
  { value: "weekly", label: "Weekly", hint: "Mondays", icon: Calendar, automated: true },
  { value: "webhook", label: "Webhook", hint: "On push", icon: Webhook, automated: false },
];

const BY_VALUE = new Map(SYNC_SCHEDULE_OPTIONS.map((o) => [o.value, o]));

/** Look up presentation metadata for a schedule; falls back to `manual`. */
export function syncScheduleMeta(value: SyncSchedule): SyncScheduleMeta {
  return BY_VALUE.get(value) ?? SYNC_SCHEDULE_OPTIONS[0];
}
