> Historical design/source record. Statements below describe plans and an earlier development state; they are not current deployment or acceptance evidence. Paths and identities were sanitized for this public copy.

# PAPR Master Plan

Domain: `example.invalid`
Organization type: `501(c)(3)` nonprofit raffle platform
Primary objective: launch a real, production-ready raffle platform that is fair, auditable, legally defensible, and understandable to the public.

## Product Positioning

PAPR should feel like a public-trust fundraising platform, not a hype-driven gaming product. The system has to communicate three things immediately:

- raffles are run fairly and transparently
- funds support a charitable mission
- operational rules are enforced by software and audit controls, not by ad hoc staff decisions

## 1. Brand Foundation

### Name usage

- Primary brand: `PAPR`
- Domain-first reference: `example.invalid`
- Public description: `PAPR is a nonprofit raffle platform built to run fair, transparent fundraising raffles.`
- Internal product description: `PAPR Platform`

### Tagline ideas

- `Fair Raffles. Public Trust.`
- `Transparent Raffles for Real Impact.`
- `Play With Purpose.`
- `Fund Good. Draw Fair.`
- `Charity Raffles, Built for Trust.`

### Brand tone

- calm, credible, civic, transparent
- nonprofit-first instead of promotional-first
- avoids casino language, streamer language, and hype slang
- explains operations in plain English

### Color palette

- Deep Navy: trust, seriousness, primary backgrounds
- Warm Paper: clean content background and card contrast
- Forest Green: confirms success, compliance, ticket purchase completion
- Brass/Gold Accent: charitable event emphasis and winner reveal moments
- Soft Slate: borders, metadata, admin tables
- Alert Red: only for compliance warnings, sold-out errors, fraud/risk states

### Typography

- Headlines: editorial serif or humanist display type for trust and gravitas
- UI/body: highly readable sans-serif with strong numeric clarity
- Tabular numerals required for ticket counts, timestamps, and financial amounts

### Logo and visual identity needs

- Wordmark version for header/footer
- compact mark for favicon/social avatar
- monochrome version for legal docs and televised overlays
- usage rules for spacing, contrast, and minimum size

### Trust-building design elements

- visible status labels: `Open`, `Sold Out`, `Scheduled Draw`, `Draw Complete`
- timestamp blocks with timezone labels
- fairness explainer cards
- direct links to official rules, nonprofit disclosures, and winner archive
- visible public count of remaining tickets
- event timeline modules showing sellout, lock, draw assignment, draw completion

## 2. Public-Facing Website

### Core navigation

- Home
- Active Raffles
- Winners
- How It Works
- Mission
- FAQ
- Official Rules
- Events / Draw Schedule
- Account
- Support

### Footer structure

- About PAPR
- Mission
- Official Rules
- Terms of Service
- Privacy Policy
- Eligibility / Restricted Jurisdictions
- Winner Archive
- Draw Schedule
- Contact / Support
- Nonprofit disclosures
- EIN / registration details where required
- social links if used

### Required public pages

#### Home
Purpose:
- establish trust fast
- explain what PAPR is
- highlight active raffles and mission impact
- show fairness rules at a glance

Sections:
- hero with nonprofit trust positioning
- active raffle spotlight
- how raffle lifecycle works
- how funds support the mission
- fairness and transparency summary
- upcoming draw calendar preview
- recent winners / archived results preview
- compliance disclosures and restricted state messaging
- FAQ preview
- newsletter / updates opt-in

#### Active Raffles
Purpose:
- primary browse and conversion page
- let users compare raffles without confusion

Contents:
- searchable/sortable raffle cards
- status badges
- ticket inventory remaining
- price and package options
- draw mode label
- countdown to sellout or draw event
- mission impact summary for each raffle

#### Raffle Detail Page
Purpose:
- transaction page plus trust page

Contents:
- prize details and media gallery
- total ticket inventory
- remaining ticket count live
- ticket pricing and package options
- maximum tickets per order/user if required
- drawing mode
- sellout behavior explanation
- scheduled draw date/time/timezone
- immediate-draw rules if configured
- official rules link
- refund and chargeback handling statement
- charity impact panel
- purchase module
- public audit summary after close

#### Winners Page
Purpose:
- build credibility through outcomes

Contents:
- latest winners
- historical archive filters
- raffle ID
- prize title
- sellout timestamp
- drawing timestamp
- winner published timestamp
- public proof summary
- optional winner story/testimonial subject to consent

#### How It Works
Purpose:
- remove ambiguity before purchase

Contents:
- raffle lifecycle walkthrough
- ticket purchase and hold window explanation
- sellout lock rules
- draw timing modes
- fairness and randomness explanation
- refund/void/chargeback impacts
- what happens if a raffle is stopped by admin

#### Mission
Purpose:
- anchor charitable legitimacy

Contents:
- nonprofit mission
- who benefits
- how raffle proceeds are used
- annual impact reports or summaries
- board/leadership info if appropriate

#### Draw Schedule / Events
Purpose:
- public scheduling trust surface

Contents:
- next scheduled draw event
- raffles assigned to each event
- event cutoff rules
- livestream/televised details if applicable
- archive of prior draw events and recordings/links

#### FAQ
Purpose:
- answer operational, legal, and trust questions clearly

#### Official Rules
Purpose:
- canonical legal-operational rulebook per raffle and platform-wide

#### Support / Contact
Purpose:
- structured help intake
- bug, abuse, fraud, and dispute reporting

### SEO/basic marketing needs

- unique metadata for each raffle page
- schema markup for organization, events, FAQ, and article pages
- canonical URLs
- sitemap.xml and robots.txt
- Open Graph/Twitter preview cards
- content pages for nonprofit mission, fairness, draw process, and winner archive
- search-friendly raffle and event URLs

### Public trust/transparency sections

Every major public surface should expose links to:
- official rules
- current draw schedule
- how winner selection works
- restricted jurisdictions and eligibility rules
- public winner archive
- mission and nonprofit disclosures

## 3. Public Raffle Experience

### Active raffle listings

Each card should show:
- raffle title
- prize teaser image
- ticket price
- available/total ticket count
- current status
- scheduled draw mode
- countdown
- mission impact snippet

### Raffle detail behavior

- show exact inventory total and remaining count
- display cart hold timer when tickets are reserved
- prevent checkout if inventory changes before payment confirmation
- show sold-out state immediately when count reaches zero
- if sold out and scheduled draw exists, show draw countdown instead of purchase CTA

### Ticket package options

- single-ticket purchase
- bundle packs with simple pricing
- optional donation add-on at checkout, clearly separate from ticket purchase
- server-enforced max quantities if required by compliance or fraud rules

### Status labels

- `Open`
- `Low Inventory`
- `Sold Out`
- `Scheduled for Public Draw`
- `Drawing Soon`
- `Draw Complete`
- `Admin Review` only if purchase-facing actions are paused

### Immediate-draw vs next-broadcast-draw handling

- immediate mode: sold out -> locked -> queued draw job runs immediately -> winner recorded and published
- public event mode: sold out -> locked -> assigned to next public draw slot -> event page updates -> draw occurs at event time
- both modes must expose timestamps and reason/state labels publicly

### Past raffle archive

- searchable archive by date, prize, mission campaign, and winner announcement
- public summary page per completed raffle

## 4. User / Account Features

### Account system

- email/password or passwordless magic link
- optional social login only if it does not complicate compliance
- MFA for admin accounts

### Guest checkout

Recommendation:
- allow guest checkout only if compliance and eligibility collection can still be enforced
- guest users should be invited to claim an account after purchase

### Identity and eligibility checks

Potential requirements:
- age verification gate
- location / jurisdiction screening
- sanctions/prohibited geography checks if required by processor or counsel
- fraud-risk scoring for unusual purchase patterns

### Account pages

- profile and contact information
- saved billing details through processor tokenization only
- ticket history
- raffle participation history
- draw result history
- notification preferences
- tax receipts/donation receipts if applicable
- support case history

### Notifications

- purchase confirmation email
- payment failure notice
- cart expiration notice optional
- raffle sold out notice optional
- draw scheduled reminder
- winner announcement / result email
- refund, chargeback, or entry adjustment notice
- SMS only if consented and compliant

## 5. Raffle Engine / Drawing System

### Core rules engine

The raffle engine must enforce:
- fixed inventory per raffle
- no oversell
- no early close except authorized manual stop
- automatic close only at sellout
- hard lock once inventory exhausted
- state machine controlled transitions only

### Required raffle states

- Draft
- Scheduled / Prelaunch
- Open
- Paused by Admin
- Sold Out Locked
- Assigned to Draw Event
- Drawing In Progress
- Winner Pending Review
- Winner Published
- Voided / Canceled
- Archived

### Ticket inventory and hold logic

- inventory ledger, not just a decrementing counter
- short cart hold reservation rows with expiration timestamp
- expired holds automatically release inventory
- payment success converts hold to issued tickets
- payment failure or timeout releases holds

### Random winner selection

Requirement:
- winner must be selected from final valid issued-ticket pool only
- selection event must log:
  - raffle ID
  - drawing mode
  - ticket pool size
  - seed or randomness source metadata
  - selected ticket ID(s)
  - operator/system actor
  - timestamp

Recommendation:
- use cryptographically secure RNG
- hash and persist draw input/output package for later verification
- optionally publish a public-facing proof package summary

### Anti-tamper protections

- append-only event log for raffle lifecycle actions
- immutable audit row for any admin override
- dual-approval requirement for void/reroll actions
- signed system events for critical actions if feasible
- database constraints preventing status rollback without audit event

### Reroll / void handling

Only permitted under explicit policy, such as:
- winner ineligible
- payment fraud invalidated associated tickets
- legal/compliance disqualification

Controls:
- reason code required
- admin approval chain required
- original result preserved in audit trail
- public-facing explanation if outcome changes materially

### Failed payments / refunds / chargebacks

- failed payments never issue final tickets
- refunded tickets after draw close require explicit policy
- chargebacks create case state and potential ticket invalidation only if rules/counsel permit
- any ticket invalidation after sellout must preserve auditability and may require replacement draw flow

## 6. Scheduling and Televised / Public Draw Support

### Drawing calendar

- central calendar of public draw slots
- each slot has event ID, date/time, host/location/stream URL, cutoff time, and assigned raffles

### Slot assignment

Admin must be able to:
- assign sold-out raffle to next draw slot
- reassign before lock cutoff with full logging
- see slot capacity and assigned raffles

### Event page

- public event title, date/time/timezone
- raffles included
- countdown
- stream/watch information if applicable
- results after completion
- archive link or recording after event

### Cutoff rules

- configurable cutoff before public event
- sold-out raffles after cutoff move to next eligible event automatically or by admin rule
- cutoff decision and assignment must be logged

### Multiple raffles in one event

- event queue/order field
- per-raffle draw timestamps
- separate result proof package for each raffle even within shared event

## 7. Admin Tools

### Admin areas

- Admin dashboard home
- Raffles
- Inventory and tickets
- Draw schedule
- Orders and payments
- Users
- Fraud / risk review
- Content / CMS
- Support / disputes
- Audit logs
- Reports
- System settings

### Raffle management

- create/edit raffle
- prize details and media
- inventory and pricing
- launch date
- draw mode
- public rules attachment
- eligibility restrictions
- charity campaign linkage

### Operations controls

- manual stop with reason code
- publish/unpublish result
- assign to public draw slot
- refund/void workflow
- support note and dispute handling
- winner verification workflow

### Role-based access control

Suggested roles:
- Super Admin
- Compliance Admin
- Raffle Operations Admin
- Finance Admin
- Support Admin
- Content Editor
- Read-only Auditor

### Audit log requirements

Log every critical action:
- who did it
- what changed
- before/after values
- why it happened
- when it happened
- request ID / correlation ID

## 8. Content and CMS Needs

### CMS-managed sections

- homepage hero and trust blocks
- mission pages
- FAQ
- blog/news
- event announcements
- official rules pages
- banners/alerts
- winner stories
- static legal pages

### CMS capabilities

- draft/publish workflow
- scheduled publishing
- media library
- revision history
- rollback
- editor role permissions

## 9. Payments and Financial Systems

### Payment processor needs

Use a mainstream processor with:
- strong fraud tooling
- webhook reliability
- tokenized payment methods
- refund support
- nonprofit-friendly reporting

### Checkout flow

- ticket selection
- eligibility confirmation
- cart hold creation
- payment intent creation
- payment confirmation
- ticket issuance
- confirmation page and receipt

### Financial reporting

- gross sales by raffle
- processor fees
- refunds/chargebacks
- donations split from ticket revenue
- reconciliation report vs processor settlement
- nonprofit accounting exports if needed

### Anti-fraud checks

- IP/device/email heuristics
- velocity checks
- multiple cards or identities on same raffle thresholds
- manual review queue for suspicious orders

## 10. Legal / Compliance / Support

### Required legal pages

- Privacy Policy
- Terms of Service
- Official Raffle Rules
- Nonprofit disclosures
- Eligibility by age and geography
- Refund and dispute policy
- Cookie/privacy controls if applicable

### Compliance handling

- prohibited jurisdictions blocking
- age gate
- data retention policy
- account deletion / data deletion workflow
- processor and legal audit trails retained per policy

### Support flows

- purchase issue
- account issue
- eligibility issue
- payment/refund issue
- fraud/abuse report
- bug report
- winner dispute

## 11. Technical / Backend Requirements

### Suggested platform architecture

- Frontend web app
- Admin web app or admin section
- API backend
- relational database
- background jobs / scheduler
- payment webhook processor
- notification service
- audit/event log service
- media storage
- analytics and observability stack

### Core database domains

- users
- identities / verification
- raffles
- raffle_inventory
- ticket_holds
- ticket_issuances
- orders
- order_items
- payments
- refunds
- chargebacks
- draw_events
- draw_assignments
- draw_results
- audit_log
- support_cases
- CMS content tables
- notification deliveries

### API surface

Public APIs:
- raffle list/detail
- ticket availability
- draw calendar
- winner archive
- account auth/profile
- checkout and order history

Admin APIs:
- raffle CRUD
- inventory controls
- draw slot management
- fraud/review queues
- reporting
- content/CMS
- audit log access

System APIs:
- payment webhooks
- scheduler triggers
- notification dispatch
- export/report jobs

### Raffle state machine

Must be explicit and enforced in backend logic plus database constraints.
Every transition should emit an event row.

### Real-time updates

- ticket counts via short polling or websocket/SSE
- order completion updates
- admin dashboard live operational health

### Security protections

- MFA for admins
- CSRF protection
- rate limiting
- WAF/CDN as needed
- secret management
- strict RBAC
- signed webhook verification
- audit logging of privileged access

### Auditability requirements

- immutable event log for critical lifecycle actions
- correlation IDs across API, payment, scheduling, and admin actions
- deterministic recording of ticket pool at draw time
- exportable compliance reports

## 12. Infrastructure / DevOps

### Domain / DNS

- `example.invalid` primary
- `www.example.invalid` redirect
- staging subdomain
- admin subdomain optional
- email sending domain with SPF/DKIM/DMARC

### Environments

- local dev
- staging
- production
- optional ephemeral preview environments for frontend/admin

### Deployment pipeline

- CI checks
- tests
- schema migration gate
- build artifacts
- staged deploy
- smoke tests
- rollback path

### Monitoring

- uptime checks
- API latency
- payment webhook failures
- job queue depth
- sellout event alerting
- draw event failure alerting
- audit log write failures
- security alerts

### Backup and restore

- automated database backups
- media backups if not provider-managed
- restore drill procedure
- point-in-time recovery if feasible

### Scaling

- queue-heavy architecture for checkout spikes
- inventory reservation handled transactionally
- cached public raffle pages with fresh inventory subrequests
- horizontally scalable API workers
- read replicas optional if reporting grows

## 13. Design / Development Deliverables

### Design deliverables

- sitemap
- low-fidelity wireframes
- high-fidelity responsive designs
- component library
- design tokens
- iconography set
- content templates

### Frontend deliverables

- public marketing site
- raffle browse/detail flows
- account area
- checkout flow
- winners archive
- draw calendar pages
- legal/support pages

### Backend deliverables

- auth service
- raffle service
- order/payment service
- draw scheduling and execution service
- notification service
- audit/event service
- admin APIs
- CMS APIs

### Raffle engine deliverables

- inventory ledger
- hold timeout worker
- sellout lock logic
- draw mode assignment logic
- winner selection service
- reroll/void approval flow
- proof package generation

### Admin deliverables

- operations dashboard
- raffle builder
- draw schedule manager
- finance/reconciliation views
- fraud/risk queue
- CMS editor
- audit explorer

### Compliance deliverables

- official rules framework
- restriction engine
- disclosures pages
- export/report package
- retention/deletion procedures

## 14. Trust, Fairness, and Transparency Requirements

### What users should always be able to see

- current raffle status
- current ticket availability
- whether a raffle is open or sold out
- whether draw is immediate or scheduled
- next draw time and timezone
- completed draw result and timestamp
- links to official rules and winner archive

### How sellout is enforced

- ticket issuance only occurs through inventory-backed transactions
- hold and payment logic cannot exceed total inventory
- once remaining inventory reaches zero after confirmed issuance, raffle transitions to `Sold Out Locked`
- purchase endpoints reject further orders immediately

### How the lock is communicated

- public label changes to `Sold Out`
- CTA removed or disabled
- draw timing replaces purchase prompt
- exact sellout timestamp shown publicly

### How televised/public draw assignment works

- sold-out raffle is linked to a specific draw event ID
- event name/date/time visible publicly
- if cutoff misses the next event, platform shows the next assigned event
- reassignment requires admin log entry

### How winner selection is documented

- record valid ticket pool count
- record draw execution timestamp
- record draw operator/system actor
- record randomness metadata
- record winning ticket identifier
- publish a public summary with private data redacted

### Stored audit records

- raffle created/edited
- inventory changed
- ticket hold created/expired
- payment authorized/captured/failed/refunded
- raffle sold out
- raffle locked
- draw assigned/reassigned
- draw executed
- result published/unpublished
- admin overrides and reasons

### Public vs private transparency split

Public:
- status, counts, timestamps, schedule, result summary, rules, archive

Private/admin-only:
- customer PII
- full payment details
- fraud models and internal notes
- sensitive compliance evidence

## 15. Lean MVP Recommendation

### MVP must include

- public homepage and mission site
- active raffle listing and detail pages
- secure checkout with one processor
- user accounts or compliant guest checkout
- fixed inventory raffle engine
- transactional sellout lock
- one draw mode at launch plus one optional scheduled mode
- admin raffle management
- winner archive
- official rules and legal pages
- audit log for critical lifecycle events
- email notifications
- basic reporting and reconciliation
- staging/prod deployment pipeline
- monitoring and backups

### Phase 2 can wait

- televised draw production tooling
- advanced CMS workflows
- SMS notifications
- richer analytics dashboards
- public proof package downloads
- support portal enhancements
- advanced fraud models
- multi-charity campaign segmentation
- recorded event archive media system

### Recommended launch MVP

Safest credible launch shape:
- one nonprofit mission profile
- one payment processor
- email-only notifications
- one primary public draw calendar
- one admin console
- one public winner archive
- immediate draw and scheduled draw support
- no complex broadcast tooling yet unless required at launch

## Suggested Database Schema Starter

### Core tables

- `users`
- `user_profiles`
- `raffles`
- `raffle_prizes`
- `raffle_ticket_inventory`
- `raffle_ticket_holds`
- `raffle_ticket_entries`
- `orders`
- `order_payments`
- `refunds`
- `chargeback_cases`
- `draw_events`
- `draw_event_raffles`
- `draw_results`
- `audit_events`
- `admin_roles`
- `admin_role_assignments`
- `cms_pages`
- `cms_posts`
- `support_cases`
- `notification_events`

## API Checklist

- `POST /auth/signup`
- `POST /auth/login`
- `GET /raffles`
- `GET /raffles/:slug`
- `POST /raffles/:id/holds`
- `POST /checkout/session`
- `POST /payments/webhook`
- `GET /account/orders`
- `GET /account/tickets`
- `GET /draw-events`
- `GET /winners`
- `POST /admin/raffles`
- `PATCH /admin/raffles/:id`
- `POST /admin/raffles/:id/stop`
- `POST /admin/raffles/:id/assign-draw`
- `POST /admin/draw-events`
- `POST /admin/draw-results/:id/publish`
- `GET /admin/audit-events`

## Full Master Checklist

### Brand and public trust
- Define final tagline and brand voice
- Approve palette, typography, and logo system
- Produce trust UI patterns and disclosure components

### Website
- Build homepage
- Build raffle browse page
- Build raffle detail page
- Build winners archive
- Build mission page
- Build how-it-works page
- Build FAQ
- Build official rules pages
- Build support/contact page
- Build draw schedule/event pages

### Accounts and checkout
- Implement auth
- Implement guest checkout rules
- Implement cart holds
- Implement payment integration
- Implement receipts and confirmations
- Implement account history pages
- Implement notifications

### Raffle engine
- Build inventory ledger
- Build transactional hold/issue flow
- Enforce sellout lock
- Implement draw mode configuration
- Implement draw executor
- Implement result publishing
- Implement reroll/void approval flow
- Implement audit event emission

### Admin
- Build RBAC
- Build raffle CRUD
- Build inventory/prize editor
- Build draw assignment UI
- Build finance/reconciliation UI
- Build fraud queue
- Build content editor
- Build audit log explorer

### Compliance and legal
- Draft terms/privacy/rules
- Implement jurisdiction restrictions
- Implement age gate
- Implement deletion/support flows
- Define record retention policy

### Infrastructure
- Configure DNS and SSL
- Set up staging and production
- Set up CI/CD
- Set up backups
- Set up monitoring and alerts
- Set up error reporting
- Run restore drill

### Operations
- Define support SOPs
- Define winner verification SOPs
- Define manual stop SOPs
- Define dispute escalation paths
- Define incident response

## Recommended Build Order

1. Brand, trust language, sitemap, and system architecture
2. Database schema, raffle state machine, and audit event model
3. Auth, accounts, and payment/checkout foundation
4. Public raffle pages and winner/archive pages
5. Admin raffle management and draw scheduling
6. Draw engine, sellout lock enforcement, and audit proof layer
7. Legal/compliance pages and restriction engine
8. Notifications, reporting, and reconciliation
9. Discord/operator workflows and launch support tooling
10. Broadcast/public draw enhancements and phase-2 polish

## Lean MVP Feature Set For Launch

- Public nonprofit website
- Active raffles and raffle details
- Secure checkout
- Email notifications
- Inventory-backed raffle engine
- Automatic sellout lock
- Immediate draw or one scheduled draw mode
- Admin dashboard for raffle operations
- Winner publication and archive
- Official rules and core legal pages
- Basic audit logs and reporting
- Monitoring, backups, and staging/prod deployment

## Raffle Fairness, Locking, Scheduling, and Auditability Checklist

### Fairness
- Use cryptographically secure randomness
- Draw only from valid issued ticket pool
- Preserve complete draw metadata
- Require policy-driven reroll handling

### Locking
- Enforce transactional inventory caps
- Expire holds automatically
- Reject post-sellout purchases immediately
- Record exact sellout timestamp

### Scheduling
- Record draw mode on raffle
- Record event assignment and cutoff decisions
- Show public schedule and timezone
- Preserve reassignment history

### Auditability
- Emit event rows for every critical lifecycle action
- Preserve before/after values on admin changes
- Record actor, timestamp, and reason codes
- Expose public trust summaries without leaking PII
