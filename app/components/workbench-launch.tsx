import type { BoardAppearance } from "@tempo/board-ui";
import { usesLocalApi } from "../utils/local";
import { workbenchLaunchUrl } from "../lib/workbench-navigation";

declare const __TEMPO_WORKBENCH_URL__: string;

export function WorkbenchLaunch({ appearance }: { appearance: BoardAppearance }) {
  const configured = typeof __TEMPO_WORKBENCH_URL__ === "undefined" ? undefined : __TEMPO_WORKBENCH_URL__;
  const destination = usesLocalApi() ? workbenchLaunchUrl(configured, appearance) : null;
  if (!destination) return null;
  return <a className="ui-button" data-variant="secondary" href={destination} target="_blank" rel="noopener noreferrer"
    title="Opens a separate Analysis tab. Connect once in that tab and keep it open to retain its session.">Open Workbench</a>;
}
