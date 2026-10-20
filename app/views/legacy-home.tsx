import { lazy } from 'react';
import Home from './home_view';
const OptionalWorkspace = lazy(() => import('./legacy-games-workspace'));

/** Full local/demo composition; never imported by the independent Study entry. */
export default function LegacyHome() {
  return <Home optionalWorkspace={{ views: ["games"], View: OptionalWorkspace }} />;
}
