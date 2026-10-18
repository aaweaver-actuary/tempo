# Minimal connected Workbench navigation

The full local Tempo app can show **Open Workbench** in its top actions, including
while Study-only mode is enabled. The independent Workbench can show a general
**Open Study** link. Both links are explicit navigation; neither starts analysis,
imports games, admits cards, creates reviews or writes Content/Learning state.

## Public configuration

For a full local frontend build:

```sh
TEMPO_WORKBENCH_URL='http://127.0.0.1:8150/?run=example-run' npm run build:local
```

For the independent Workbench:

```sh
TEMPO_STUDY_URL='http://127.0.0.1:3000/' TEMPO_INTELLIGENCE_ORIGIN='http://127.0.0.1:8149' npm run build --prefix apps/workbench-web
```

For a separately authorized Docker build, `Dockerfile.web` accepts the optional
public `--build-arg TEMPO_WORKBENCH_URL=...` with an empty default. No image build
or deployment is performed by this feature change.

Use the actual installed non-secret URLs and run identity. No private report,
account or credential is hard-coded. Defaults are empty, so unconfigured links
are absent. The local app launcher is absent in demo mode and does not replace
the independent Study shell's existing launcher.

Each owning build boundary validates its setting **before** emitting a JavaScript
define. Errors describe the invalid contract without printing the supplied URL.
Runtime anchors validate again. Workbench URLs allow only HTTP(S), no userinfo or
fragment, and only `run`, `category` and `dismissal` query keys. Study URLs must be
HTTP(S) origins with a root path and no userinfo, query or fragment. These public
build settings must never contain credentials. An already built deployment does
not gain a link by changing its runtime environment. Deployment is separate from
this feature PR.

Links open separate tabs with `noopener noreferrer`; no token or context payload
crosses between services. Keep the connected Workbench tab open. Its existing
session storage preserves the connection across reloads and returning to that
tab; a fresh tab requires Connect. The separate public `TEMPO_INTELLIGENCE_ORIGIN` setting prefills that form with
a validated root origin. It contains no credential and never auto-connects.
Unconfigured builds leave the field empty rather than guessing port 8049.
The Intelligence origin belongs in the form,
not in the browser address bar. This does not add a proxy, localStorage token,
persistent credential or automatic login.

## Honest Study authority

The link is labeled **Open Study** and opens the target's normal queue. A
repertoire-miss notice says its snapshot card has not been verified against that
service. A gap notice says it does not identify an existing Study card. Engine
findings likewise do not establish a matching card. Neither an exported physical
ID nor a rehearsal activation proves live Study identity.

The current legacy `GET /api/compare/cards` returns a bulk opening inventory but
omits revision, serving/publication and workspace authority. Teaching history is
not card-existence proof. Independent Learning move-membership needs separate
Study credentials and known revision/move authority, so the Workbench must not
silently use it as a live Tempo lookup. No existing route supports a specific-card
Study handoff. This PR does not invent one or add a backend adapter.

A future specific-card inspection contract would need a read-only target-authority
identifier, physical card identity and revision, executable fingerprint, relevant
policy/source/publication state, and typed missing/stale/mismatch responses.
Navigation would additionally need supported semantics that preserve issued
attempts and normal eligibility, with no implicit admission, obligation or review.
That work requires its own owning contract and qualification.

## Focused verification

The frontend owning suite registers `workbench-launch-regressions.test.tsx` plus
the full Home integration case. Workbench's regular client suite covers URL and
notice contracts. Its owning build adapter tests execute the actual Vite config,
and native Chromium covers fresh unauthenticated navigation, general Study
navigation and existing per-tab connection recovery. The source-removed qualifier
retains these checks. No engine, producer database or live Study transaction is
required. The existing native producer fixture is explicitly synthetic; this
feature does not claim another real-game analysis rehearsal.
