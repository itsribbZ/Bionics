# T1.A — BPDoctor Hero GIF: "BPDoctor vs My Tuesday Morning"

30-second looping clip. The one piece of media that sells what BPDoctor does.
It should feel like watching a compiler take a pile of "why is my AnimBP
broken on a Tuesday" pain and make it go away in one keystroke.

## The Hook

Every solo dev working in UE5 has lost a morning to a silent AnimBP bug —
a MotionMatching node with a null database, a Slot name misspelled, a Layered
Blend whose weights quietly drifted off 1.0. BPDoctor finds all 34 classes of
these in seconds. This GIF shows that happen.

## Shot List (30 seconds)

| t     | Beat                                    | On-Screen                                                       |
|-------|-----------------------------------------|-----------------------------------------------------------------|
| 0:00  | Cold open                               | UE5 editor with a broken `ABP_Trooper` open. Red lint markers.  |
| 0:02  | Caption pops in                         | `"One Tuesday morning in UE5…"`                                 |
| 0:04  | Cursor moves to Claude Code             | Terminal visible side-by-side with UE5.                         |
| 0:05  | Type the prompt                         | `"run BPDoctor on ABP_Trooper and fix everything you can"`      |
| 0:08  | Bionics response streams                | `ue5_bpdoctor_scan` → finds 5 issues; ids enumerate.            |
| 0:14  | `ue5_bpdoctor_fix_all` fires            | AnimBP visibly re-renders; nodes reconfigure in real time.      |
| 0:20  | Compile succeeds                        | Green checkmark on the AnimBP icon.                             |
| 0:22  | Caption card                            | `"5 bugs. 14 seconds. Not a Tuesday problem."`                  |
| 0:26  | Pulse the 5 fixed issues one more time  | Green highlight on each formerly-red element.                   |
| 0:30  | Loop to 0:00                            | —                                                               |

## Setup (15 min, one-time)

1. Open UE5 with the Sworder project.
2. Duplicate `ABP_Trooper` → `ABP_Trooper_DemoBroken`. Work on the duplicate so
   the real AnimBP stays intact.
3. Intentionally introduce 5 broken states on the duplicate:
   - MM node with its Database property cleared (triggers `MM_NO_DATABASE`)
   - Rename the default slot group so one Slot node references a missing
     group (triggers `SLOT_NAME_MISMATCH`)
   - Remove the Inertialization node after the MM loop (triggers
     `MM_NO_INERTIALIZATION`)
   - Edit one LBPB layer weight to 0.3 / 0.3 / 0.3 = 0.9 total (triggers
     `BLEND_WT_SUM`)
   - Clear the BranchFilters array on another LBPB layer (triggers
     `EMPTY_BRANCH_FILTER`)
4. Save. Compile. Screenshot the lint panel so you have a clean "before"
   reference.
5. Arrange windows: UE5 graph view fills left 2/3, Claude Code terminal fills
   right 1/3. Both at 100% DPI (no Windows scaling artifacts).

## Recording

### Tool of choice: **OBS Studio** (ffmpeg pipeline for post-processing)

- **Resolution**: 1920×1080 at 60 fps. The final output scales to GIF at
  960×540 30fps to hit GitHub's 10 MB size cap.
- **Scene**: single "Window Capture" on the UE5 editor; second window capture
  for Claude Code terminal.
- **Mouse**: visible, enlarged (OBS `Mouse Cursor` plugin → 1.5× scale).
- **Audio**: none — GIFs don't carry audio and a 30 sec clip doesn't need it.
- **Take count**: plan on 5-8 takes. The first one is always the slowest
  because of click hesitation. Take 3+ always lands.

### The prompt to type (copy/paste ready)

```
run BPDoctor on ABP_Trooper_DemoBroken and fix everything you can
```

### Expected Bionics/Claude Code behavior

1. Invokes `ue5_bpdoctor_scan(animbp_path="/Game/.../ABP_Trooper_DemoBroken")`
2. Receives 5 issues from the `BPDOCTOR_API` cross-module C++ call
3. Invokes `ue5_bpdoctor_fix_all(animbp_path=..., min_severity="Warning")`
4. C++ side rewrites the AnimGraph nodes in-place + recompiles
5. Claude returns a one-line summary: "5 issues found, 5 auto-fixed, 0 remaining"

If step 2 returns fewer than 5 issues — the BPDoctor plugin isn't rebuilt.
Run the UE5 rebuild step (see `README.md § UE5 Rebuild`) and retry.

## Post-processing (ffmpeg)

```bash
# Trim to exactly 30 seconds, strip audio, scale for GIF
ffmpeg -i raw_take.mp4 \
  -ss 00:00:00 -t 00:00:30 \
  -vf "fps=30,scale=960:-1:flags=lanczos" \
  -an hero_clean.mp4

# MP4 version for Twitter / Discord / README embed
ffmpeg -i hero_clean.mp4 \
  -c:v libx264 -crf 23 -preset slow \
  -pix_fmt yuv420p \
  -movflags +faststart \
  bpdoctor_hero.mp4

# GIF version for README (keep under 10 MB for GitHub)
ffmpeg -i hero_clean.mp4 \
  -vf "fps=15,scale=800:-1:flags=lanczos,split[s0][s1];[s0]palettegen[p];[s1][p]paletteuse" \
  -loop 0 bpdoctor_hero.gif
```

Drop both into `docs/demos/media/` and reference from the root README:

```markdown
![BPDoctor hero](docs/demos/media/bpdoctor_hero.gif)
```

## Captions (burn-in text overlays)

Use Premiere / DaVinci / Kapwing — any tool that can export a lower-third
title block with a fade-in/fade-out.

| t     | Text                                       | Style                     |
|-------|--------------------------------------------|---------------------------|
| 0:02  | One Tuesday morning in UE5…                | center, 48 pt, fade 0.3s  |
| 0:22  | 5 bugs. 14 seconds. Not a Tuesday problem. | center, 54 pt bold, 3 sec |

Font: Inter Bold. Background: 85% opacity black pill. Text: white.

## Failure fallbacks

- **UE5 rebuild not done** → scan returns 0 issues. **Abort take**, rebuild
  the plugin, retry.
- **Claude Code rejects the tool call** (safety gate) → run with
  `BIONICS_MCP_ALLOW_DESTRUCTIVE=true` env set on the MCP server. Re-prime
  the demo.
- **fix_all partial success** (some issues need manual review) → edit the
  broken state to only include auto-fixable checks (drop `SLOT_NAME_MISMATCH`
  if the skeleton is missing the group).

## Thumbnail (for Twitter / YouTube embed)

Capture frame at 0:21 — the moment the last red lint marker turns green.
Crop to 1200×630 (Twitter card spec).

## Where the asset lives post-shoot

```
docs/demos/media/
  bpdoctor_hero.gif          # 30s, 800×450, under 10 MB, README embed
  bpdoctor_hero.mp4          # 30s, 960×540, Twitter/Discord embed
  bpdoctor_hero_thumb.png    # 1200×630 card preview
  raw_take.mkv               # OBS master, NOT committed (in .gitignore)
```

The `raw_take.mkv` file should match the `.gitignore` pattern for recorded
media: keep the deliverable, drop the 10 GB raw.
