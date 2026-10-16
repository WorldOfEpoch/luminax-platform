> Historical design/source record. Statements below describe plans and an earlier development state; they are not current deployment or acceptance evidence. Paths and identities were sanitized for this public copy.

# Underfoot Master Plan

## Vision
Underfoot is a colorful cooperative PC multiplayer adventure built for Unreal Engine. Tiny underground scavenger creatures burst up through roots, walls, and burrows to gather resources, rescue friends, complete quests, and survive the oversized hazards of a hostile world ruled by an evil sorcerer.

## Pillars
- Immediate drop-in: no account creation, just name entry and play.
- Joyful danger: slapstick hazards, fast respawns, and momentum physics.
- Rescue-first loop: save captured creatures, recover ingredients, and sabotage potion plans.
- Shared world energy: multiple players, map voting, social questing, and world events.
- Strong project observability: plan, status, swarm ownership, and deployment state always visible.

## Production Tracks
1. World + lore
2. Unreal client prototype
3. Underfoot world service
4. Public project site + devlog
5. Discord playtest operations
6. Art / 3D pipeline
7. QA ladder + autonomy loop

## Core Gameplay Loop
1. Pick a map zone from the burrow network.
2. Pop out at a spawn hole, stump, wall crack, or tree root.
3. Gather mushrooms, sap, shiny scraps, herbs, feathers, and rescue tokens.
4. Dodge giant-world hazards: stomps, blades, frost, fire, poison, cats, rats, spiders, owls, traps, and unstable objects.
5. Rescue captured creatures before the sorcerer's cauldrons consume them.
6. Cash in points for camp upgrades, rescue streaks, cosmetics, and team perks.
7. If smashed, respawn at the nearest safe exit point and jump back in.

## MVP
- Guest name entry only.
- One large overworld prototype map with underground exits and surface zones.
- Simple multiplayer-ready world service with guest sessions, map selection, spawn selection, points, quests, rescue actions, and instant respawn.
- Colorful public website with plan/status/swarm views.
- Discord playtest bootstrap and recurring playtest events.
- Unreal integration contract and starter folder structure.

## Phase 2
- Inventory crafting.
- Sorcerer lair raid events.
- Dynamic weather and owl patrols.
- Cosmetic unlocks and camp decorations.
- Persistent progression and cloud save.
- Full authoritative multiplayer simulation.

## 3D Pipeline Decision
Recommended primary 3D generation path: Microsoft TRELLIS.2 for local image-to-3D concept generation, then manual cleanup and Unreal import. Secondary texture/style option: Tencent Hunyuan3D-2 pipeline for stylized asset exploration where local GPU budget allows.

## Deliverables Created In This Pass
- Project folder and docs.
- Client web lobby with no-registration entry flow.
- Underfoot world FastAPI service scaffold.
- Public Underfoot site on the Luminax server.
- Swarm/status visibility pages.
- Art concept SVG mascot set inspired by the provided creature direction.
