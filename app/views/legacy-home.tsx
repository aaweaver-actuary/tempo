import { lazy } from 'react';
import Home from './home_view';
const LegacyWorkspace = lazy(() => import('./legacy-workspace'));

/** Full local/demo composition; never imported by the independent Study entry. */
export default function LegacyHome() {
  return <Home optionalWorkspace={LegacyWorkspace} />;
}
