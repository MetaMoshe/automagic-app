# Automagic portfolio site

A static site that joins the Automagic client portfolio and the case studies
(Hunter Executive, Zugi.ai, Revscale) with a shared top navigation and
in-site cross-links.

- `src/home.html` is the homepage (featured case studies, engagement types,
  filterable client grid, template key). Its client data is pulled at build
  time from the portfolio artifact (`src/portfolio.html`), which is served at
  `/clients/` as the full client profiles.
- `src/portfolio.html`, `hunter.html`, `zugi.html`, `revscale.html` are each
  page's HTML exactly as published from its claude.ai artifact.
- `src/welcome.html` and `src/terms.html` are the access cover page and terms.
- Put the logo at `src/brand/wordmark-light.svg` and the nav uses it.
- `build.py` adds the navigation bar and "keep exploring" cards, rewrites
  artifact links to in-site paths, and writes `public/`.
- `render.yaml` deploys `public/` as a Render static site.

To update a page: replace its file in `src/` with the artifact's latest HTML,
run `python3 build.py`, commit and push. Render redeploys on push.

To add a case study: add its file to `src/`, add a row to `PAGES` in
`build.py` (slug, file, nav label, artifact id, one-line blurb), and rebuild.

## Client portal and CRM

- `/admin/clients`: add, edit and delete clients, set their portal login, upload project documents and drive files (drag and drop), link a Stripe customer.
- Admin has a left menu: Inbox (new Book a Call requests, client uploads and portfolio sign-ups; Mark all as read), Analytics (`/admin`), Lead CRM (`/admin/leads`: pipeline status, private notes, add leads, convert to client), Clients, Consultants, Access and visitors. Shared look in `admin_ui.py`; inbox, analytics, leads and visitors in `admin.py`.
- On first start a demo consultant (Alex Rivera) and demo client (Northwind Logistics (Demo), demo.client@example.com) are created once. Generate a password on the demo client page to sign in as them; delete both when done.
- `/admin/consultants`: add, edit and delete consultants (photo, bio, email, phone, LinkedIn, booking link). Assign one to each client in the CRM; the client sees them under "Your Consultant". Clients with none assigned see the default (the first consultant). The old single profile is migrated as the first consultant.
- `/login/` → `/portal/`: client sign in (email + password) and the portal: overview, Your Consultant, Project docs, Invoices, Your Drive, Settings.
- Files are stored on the Render disk under `$DATA_DIR/files/`. Invoices need `STRIPE_SECRET_KEY` set in Render and a Stripe customer ID on the client.
