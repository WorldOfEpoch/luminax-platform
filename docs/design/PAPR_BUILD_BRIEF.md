> Historical design/source record. Statements below describe plans and an earlier development state; they are not current deployment or acceptance evidence. Paths and identities were sanitized for this public copy.

# PAPR Build Brief

Domain: `example.invalid`

PAPR is a `501(c)(3)` nonprofit raffle platform. The platform must be designed as a real production-ready system, not just a simple landing page. It needs to support fair raffle operations, clear public transparency, ticket sales, scheduled raffle drawings, and administrative control.

This is not a streamer platform. Remove all streamer-related features.

## Core raffle rules

- Raffles must run fairly and transparently.
- A raffle does not close early unless manually stopped by an authorized admin for a valid reason.
- A raffle closes automatically only when all available tickets are sold out.
- Once sold out, the raffle is marked closed and locked from further ticket purchases.
- The winner drawing runs at its scheduled time.
- Some raffles may run immediately after sellout if configured that way.
- Other raffles may be queued for the next televised or publicly scheduled drawing event.
- The system must support admin input to assign a sold-out raffle to the next televised/public drawing slot.
- The platform must preserve an auditable trail showing exactly when ticket sales closed, when the drawing was scheduled, and how the winner was selected.

## Scope

Define everything required for the website, dashboards, backend systems, compliance handling, raffle engine, and supporting infrastructure.

## Required coverage

1. Brand foundation
- name usage
- tagline ideas
- nonprofit/public trust tone
- color palette
- typography
- logo and visual identity needs
- trust-building design elements

2. Public-facing website
- every page the site needs
- what each page should contain
- homepage section breakdown
- navigation structure
- footer structure
- SEO/basic marketing needs
- public trust/transparency sections

3. Public raffle experience
- active raffle listings
- raffle detail pages
- ticket package options
- countdowns/status labels
- ticket availability display
- sold-out state
- drawing schedule display
- immediate-draw vs next-broadcast-draw handling
- winner announcement pages
- past raffle archive
- charitable mission messaging
- fairness/transparency explanations

4. User/account features
- signup/login
- guest checkout if allowed
- identity verification if needed
- profile/account page
- purchase history
- ticket history
- raffle participation history
- saved payment/contact info if appropriate
- notification preferences
- email/SMS alerts for purchase confirmation, sellout, and drawing results

5. Raffle engine / drawing system
- create raffle with fixed ticket inventory
- enforce hard cap on ticket quantity
- stop sales automatically at sellout
- lock raffle after sellout
- configure drawing mode:
  - immediate after sellout
  - scheduled future draw
  - assign to next televised/public drawing
- random winner selection logic
- fairness rules
- anti-tamper protections
- reroll/void handling rules with strict audit controls
- drawing logs
- timestamped state transitions
- public-facing proof/explanation of how drawings work
- handling of failed payments, refunded tickets, chargebacks, and disputed entries
- reserve/hold timeout logic for carts so inventory is not oversold

6. Scheduling and televised/public draw support
- drawing calendar
- next available televised drawing slot
- admin assignment of raffle to broadcast slot
- countdown to televised event
- event page for scheduled drawings
- public results publication
- archived draw recordings/links if available
- handling multiple raffles in one drawing event
- cut-off rules for inclusion in a given televised draw

7. Admin tools
- raffle creation/editing
- ticket inventory controls
- prize management
- schedule management
- televised draw assignment
- purchase monitoring
- refund/void workflow
- fraud/risk review
- user management
- role-based admin permissions
- audit logs
- content management
- winner publishing tools
- reporting/dashboard views
- manual intervention tools with strict logging and approval controls

8. Content and CMS needs
- FAQ editor
- nonprofit mission pages
- blog/news/updates
- event announcements
- raffle rules pages
- winner stories/testimonials if appropriate
- banners/alerts
- media asset management
- homepage editable content blocks

9. Payments and financial systems
- payment processor integration
- checkout flow
- tax/compliance considerations
- donation options if applicable
- billing history/receipts
- refunds workflow
- failed payment handling
- anti-fraud checks
- reconciliation tools
- financial reporting for nonprofit/admin use

10. Legal/compliance/support
- privacy policy
- terms of service
- official raffle rules
- nonprofit disclosures
- eligibility rules by location/age if applicable
- prohibited jurisdictions handling
- account deletion/data deletion
- cookie/privacy controls if needed
- support/contact flow
- bug reporting
- abuse/fraud reporting
- dispute handling
- clear public disclosures around odds, inventory, closing, and draw timing

11. Technical/backend requirements
- auth system
- database needs
- API structure
- raffle state machine
- real-time ticket count updates
- scheduling architecture
- payment webhook handling
- user roles/permissions
- logging
- analytics tracking
- caching
- backups
- file/media storage
- security protections
- auditability requirements
- deterministic/event logging for raffle lifecycle actions

12. Infrastructure/devops
- domain/DNS
- SSL
- staging vs production
- deployment pipeline
- environment variables
- monitoring
- uptime checks
- error/crash reporting
- database backup/restore
- scaling considerations for ticket rushes
- queueing strategy for heavy traffic during popular raffles and drawing events

13. Design/dev deliverables
- sitemap
- wireframes
- UI component list
- design system
- frontend deliverables
- backend deliverables
- raffle engine deliverables
- admin deliverables
- compliance deliverables
- launch checklist

14. Trust, fairness, and transparency requirements
- how users can see the current raffle status
- how ticket counts are displayed
- how sellout is enforced
- how a raffle is locked once tickets are exhausted
- how the scheduled draw is communicated
- how televised/public draw assignment works
- how winner selection is documented
- what audit records are stored
- what public information should be visible to build trust without exposing sensitive data

15. MVP recommendation
- what the lean first version should include
- what can wait until phase 2
- what the minimum viable nonprofit raffle launch version would actually need to go live safely and credibly

## Output requirements

- Organize everything in clean sections.
- Be specific and practical.
- Treat this like a serious production product build plan.
- Assume the goal is to launch a real nonprofit raffle platform.
- Include both the visible website and the hidden systems required to operate it.
- Do not include streamer features.
- Do not just list generic pages; explain the purpose of each major part.

## Required end section

1. a full master checklist
2. a recommended build order
3. the lean MVP feature set for launch
4. a dedicated checklist for raffle fairness, locking, scheduling, and auditability
