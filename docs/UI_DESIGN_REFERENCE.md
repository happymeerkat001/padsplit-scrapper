# UI design references

Reviewed 2026-11-04. The following public reference pages were fetched as HTML/text; no claim is made that their full interactive behavior or visual assets were inspected.

- [shadcn/ui dashboard example](https://ui.shadcn.com/examples/dashboard): observed the navigation grouping, dashboard summaries, status-bearing rows and table controls. The page currently presents an embedded dashboard example within the shadcn/ui site.
- [shadcn/ui authentication example](https://ui.shadcn.com/examples/authentication): observed the labelled email form, primary action and separated alternative authentication choices. The requested password-reset behavior is implemented against Firebase rather than copied from the example.
- [Tabler preview](https://preview.tabler.io/): observed the navigation taxonomy and examples for sign-in, forgot password, task lists, inbox, tables, toasts and responsive layouts.

Local design direction: light neutral workspace and white surfaces, restrained blue accent, clear semantic labels, focus indicators, compact navigation and readable controls. The application remains static HTML/CSS/JavaScript; these references are inspiration, not a framework migration. Production authorization is enforced by Firebase rules, not visual gates.

On this continuation, the three linked pages were fetched again as HTML/text. The dashboard example exposes grouped navigation, compact metrics and a controllable table; the authentication example exposes a labelled email form; Tabler lists authentication, inbox, table, task and navigation examples. The browser rendering and interactive behavior of those references were not independently inspected during this pass.
