import { lazy } from "react";
import { renderApplication } from "./render-application";
const Study = lazy(() => import("../../app/components/study-connection-boundary"));
renderApplication(Study);
