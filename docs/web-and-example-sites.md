# Command center and example sites

The public source includes the original Mount Olympus command center in `payload/luminax-ui`, the smaller swarm board in `web`, and the GroupUp, Underfoot, Olympus landing, and PAPR site sources in `sites`.

These interfaces are source previews. Their buttons describe existing platform routes and planned workflows; their presence does not prove those integrations are deployed or accepted. Static examples contain sample state. Historical game pages are included as source and are not an engine project or a game-server build.

## Deliberate connection

The command center opens without its automatic API bootstrap and polling. Choose **Connect local platform** after you have deliberately started and configured your own local stack. This adds `?connect=1` to the page URL. It does not install or start the stack. Voice persistence and automatic playback are off in a fresh browser state.

The swarm board initially shows labeled demo state. **Refresh Live** is an explicit request to the configured API; opening with `?connect=1` also selects a connection. Model names in its editable role policy are examples, not verified compatibility claims. Choose models available through your configured backend.

GroupUp and Underfoot example pages need `?connect=1` to run their initial API loading/polling. GroupUp has legacy query-token room links; review their handling and access-log policy before remote use. These sites are intended for local development until their account, permission, reconnect, and ordinary-user flows are verified.

Remote font and Three.js CDN loads were removed from the public command-center copy. It uses system fonts and its existing signal-card fallback when Three.js is absent. No browser dependency is silently downloaded. Adding an optional 3D view should use a pinned dependency and retain its license.

## PAPR static example

The PAPR source is a design example, with a visible sample-data notice. It is not an operating fundraiser, a legal-status assertion, or evidence of real purchases and winners. Domain/contact defaults use `example.invalid`.

```powershell
npm ci --prefix sites/papr-src --ignore-scripts --no-audit --no-fund
npm run build --prefix sites/papr-src
```

The locked Eleventy build writes generated pages to `sites/papr`, which is ignored by Git and excluded from source releases. Dependency packages are installed separately and keep their own licenses. The other sites are plain HTML, CSS, and JavaScript.

## Checks and limits

`node --test tests/web-publication.test.mjs` parses all authored scripts and inline JavaScript and tests the actual connection bootstrap owners with inert substitutes. It checks that an ordinary page open starts no platform API polling and that a deliberate connection enables it. It also checks fresh voice/playback defaults.

The static site build has passed. These checks do not establish real browser interaction, accessibility, microphone/audio behavior, Discord, model execution, or full platform integration. Existing UI security limitations, including browser-held control tokens and legacy query-token routes, still need review before a remote deployment.
