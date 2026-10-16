# Product redesign

Reading: a local daily-use TikTok workflow, not a marketing site. Full visual and
information-architecture overhaul explicitly requested by the user.

Taste settings: DESIGN_VARIANCE 4, MOTION_INTENSITY 2, VISUAL_DENSITY 5.
Use contextual typography, hierarchy, accessibility and redesign rules; marketing
heroes, imagery, scroll choreography and social-proof sections do not apply.

## Audit

Previous UI: Inter, indigo plus several competing action colors, gradient emoji logo,
large rounded cards and duplicated calls to action. Eight ungrouped navigation items.
Dashboard repeated setup, search, connection state and account management. Queue
presented every text field, account selector and five buttons on every row. Connecting
was separate from manual account registration; one global session and proxy served all accounts.

## Decisions

- Keep the Lead Hunter name and all existing data. Group navigation around the actual
  workflow; secondary settings and history stay below the core tools.
- Custom native product UI, no imitation of a branded design system. System sans with
  Cyrillic support. Single green accent, neutral surfaces, semantic warning/error colors.
- Radius rules: surfaces 14px, controls 8px, badges 5px. CSS variables define both themes.
- Remove duplicate quick-search and setup blocks. Overview shows real metrics and one
  contextual next step. Search has one form and an adjacent parameters panel.
- Queue uses list/detail selection. Single-send, batch-send and optional manual mode
  have distinct locations. Preserve keyboard workflow and remembered reply account.
- Account connection is a real asynchronous proxy/login workflow. A pending connection
  is separate from an account. Only confirmed identities get account rows.
- Legacy records remain under a collapsed reconnect section. Each confirmed account
  has its own proxy and storage state. Credentials are excluded from public responses.
- Native dialogs provide keyboard focus containment and Escape handling. Inputs have
  labels, important failures are inline, server data is escaped before rendering.
- Use locally vendored MIT Tabler icons; no external runtime requests, image placeholders,
  generated imagery or decoration in this data-oriented interface.

## Verification

Browser checks cover all eight pages, light/dark themes, mobile navigation and horizontal
overflow. Automated browser tests cover queue start/stop/manual resolution and the account
wizard before and after identity confirmation. Tests also cover proxy isolation, failed
login, cancellation, credential masking, daily quota and additive database migration.
Real TikTok login and publishing are not performed in tests.

Final checks: 30 automated tests pass. All eight pages checked at 1366px, 1280px
and 390px with no page-level horizontal overflow or JavaScript errors. Real local CSV
download completes successfully. Lighthouse mobile run on the overview: performance 100,
accessibility 100, best practices 100. These scores describe the audited local page,
not every possible account/data state or TikTok network behavior.

Follow-up: the connection wizard now defaults to copying a single sessionid from
Chrome, with four concrete instructions and a masked paste field. Proxy remains
the first screen. The server validates the imported identity through that proxy
before creating an account. Interactive login remains a secondary disclosure.
Session values stay outside task parameters and API responses, in a pending
storage-state file until verification. Import checks extend the suite to 35 tests.
