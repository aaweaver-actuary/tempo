import { lazy } from "react";
import { renderApplication } from "./render-application";
const Home = lazy(() => import("../../app/views/legacy-home"));
renderApplication(Home);
