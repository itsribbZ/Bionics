# T1.B — "One Prompt Full Locomotion" (4 min demo)

The kill-shot demo: one natural-language prompt from Jacob, and Bionics
delivers a Bible-aligned locomotion system — retargeted anims, a Motion
Matching schema + database, IK Rig chains, a Linked Anim Layer, a bound
Niagara emitter, and a BPDoctor clean-bill all in four minutes.

**Working title (from the hellscape audit)**:
> "I told Bionics to build AAA locomotion. It did it in 4 minutes."

This is the video that every indie dev who has burned a weekend on motion
matching will share.

## The Prompt (the whole point of the demo)

```
Build a full AAA locomotion system for the Trooper character:
retarget every GASP anim we have, set up a Motion Matching schema with
pelvis, ball_l, ball_r, and trajectory channels, build the database,
create IK Rig chains for feet + hands, spawn a linked Rifle Combat
layer, and bind a muzzle-flash Niagara emitter. Then run BPDoctor on
the result and fix anything it finds.
```

That's it. One prompt. Everything downstream is Bionics deciding which of its
187 tools to call, in what order, with which args.

## Scene Breakdown

### 0:00 – 0:30 — Setup + Prompt

| Beat | Action                                                              |
|------|---------------------------------------------------------------------|
| 0:00 | Cold open — UE5 editor with Content Browser empty of Trooper anims. |
| 0:04 | Cut to Claude Code terminal. Empty prompt.                          |
| 0:08 | Type the prompt above (6 sec typing speed).                         |
| 0:14 | Hit enter. Claude Code begins streaming response.                   |
| 0:18 | Caption: "1 prompt. 187 tools available. 0 hand-written steps."     |
| 0:25 | Bionics emits its plan — visible as `execute_plan` tool-use block.  |
| 0:30 | Plan has 18 steps. Cut to animation pipeline.                       |

### 0:30 – 1:30 — Animation Pipeline Live

Show the retargeting + MM schema setup happening in UE5 with Claude Code
commentary on the side.

| Beat | Action                                                                  |
|------|-------------------------------------------------------------------------|
| 0:30 | `ue5_batch_retarget` call — 70 anims enumerated in the terminal.        |
| 0:40 | Fast-forward montage (3× speed) of assets appearing in Content Browser. |
| 0:55 | Caption: "70 anims retargeted via IK Retargeter batch API."             |
| 1:00 | `ue5_create_asset` call for `DB_Locomotion` Pose Search database.       |
| 1:05 | `ue5_run_python` populates the DB with the 70 anims.                    |
| 1:15 | MM Schema config: channels panel shows pelvis/ball_l/ball_r/trajectory. |
| 1:20 | Caption: "Schema + DB built. 5-20 ms C++ bridge latency, not 400 ms."   |
| 1:30 | Cut to Linked Layer setup.                                              |

### 1:30 – 2:30 — Linked Layers + VFX

| Beat | Action                                                                  |
|------|-------------------------------------------------------------------------|
| 1:30 | `ue5_linked_anim_layer_create` — ABP_RifleLayer appears in Content.     |
| 1:40 | ABP_Trooper opens — the Rifle layer slot auto-wires via `ALI_Combat`.   |
| 1:50 | Caption: "Linked Anim Layer pattern from Lyra. Out of the box."         |
| 2:00 | `ue5_niagara_spawn_emitter` — NS_MuzzleFlash placed at the weapon tip.  |
| 2:10 | `ue5_niagara_set_param` binds User.FireRate to a scalar in the AnimBP.  |
| 2:20 | Caption: "VFX parameter bound. Cymatic-friendly for Jacob's tone pipe." |
| 2:30 | Cut to BPDoctor.                                                        |

### 2:30 – 3:30 — BPDoctor Closes the Loop

| Beat | Action                                                                |
|------|-----------------------------------------------------------------------|
| 2:30 | `ue5_bpdoctor_scan` runs on ABP_Trooper. 34 checks enumerate.         |
| 2:45 | 3 issues surface: MM_NO_DATABASE (fixed — the DB we just built?),     |
|      | SLOT_NAME_MISMATCH (auto-fixable), DEAD_CACHED_POSE (info only).      |
| 2:55 | `ue5_bpdoctor_fix_all` — 2 of 3 auto-fix. Info-level skip.            |
| 3:05 | Recompile — green checkmark on the AnimBP.                            |
| 3:15 | Caption: "34 checks. 3 issues found. 2 auto-fixed. 0 manual."         |
| 3:30 | Cut to PIE.                                                           |

### 3:30 – 4:00 — Play-In-Editor Proof

| Beat | Action                                                                |
|------|-----------------------------------------------------------------------|
| 3:30 | `ue5_pie_start` launches the level. Trooper idles, then the player   |
|      | moves with WASD. No foot sliding. No pose-jumping.                    |
| 3:45 | Player crouches (C), rolls (shift-space). Transitions are clean.      |
| 3:50 | Text card overlay: "Normally 3-5 days. Done in 4 minutes."            |
| 3:56 | Call to action: "github.com/jbro1/bionics · pip install bionics-agent"|
| 4:00 | End.                                                                  |

## The Actual Plan Bionics Will Build

The one-prompt test. If Bionics can't produce this plan from that prompt,
the demo isn't ready. Save this as a ground-truth reference plan so we can
inspect what the agent emits against it.

See `plans/demo_full_locomotion.json` for the hand-checked reference build
(kept out-of-line so the demo script stays readable).

## Recording

- **Tool**: OBS Studio (two scenes: "UE5 Full" and "Claude + UE5 split").
- **Resolution**: 1920×1080 at 60 fps master. 1080p30 deliverable.
- **Mic**: none. Clean voiceover in post if desired.
- **Background music**: royalty-free, 90-120 BPM, low-mid energy. Suggested:
  Epidemic Sound "Ambient Tech" library.
- **Takes**: plan on a single clean run. If it fails mid-take, start over
  rather than cutting — the "one-shot" framing is load-bearing.

### Pre-record checklist

- [ ] Sworder project open, Content Browser scrolled to a clean state.
- [ ] All GASP anims present in the project but NOT yet retargeted.
- [ ] `ABP_Trooper` exists but has no MM node wired (so the plan will).
- [ ] `.mcp.json` pointing at the current Bionics install.
- [ ] `BIONICS_MCP_ALLOW_DESTRUCTIVE=true` set (some tool calls are DESTRUCTIVE tier).
- [ ] UE5 Output Log cleared (so only the demo's log lines show).
- [ ] Terminal font ≥ 18 pt.
- [ ] OBS encoder: x264 slow, CRF 18, keyframe 2 sec.

## Post-Production

### Cuts (using DaVinci Resolve or Premiere)

- Keep 0:00–0:18 at real time.
- 0:30–1:00 at 3× speed with caption "[3× speed]".
- 1:00–1:30 at real time.
- 1:30–2:30 cut tight — skip re-renders, hold on the UI confirmation frames.
- 2:30–3:30 at 1.5× for the scan output, real time for the compile.
- 3:30–4:00 at real time.

### Color

- UE5 viewport at default; don't LUT-grade over the editor chrome.
- Terminal text: bump contrast +10% so the syntax colors read on YouTube
  compression.

### Captions

Burn in English subtitles so autoplay (muted) viewers get the narrative
beats. 18 pt Inter Bold, white-on-black-pill, 85% opacity.

### Export targets

| Target            | Res         | FPS | Codec    | Notes                          |
|-------------------|-------------|-----|----------|--------------------------------|
| YouTube master    | 1920×1080   | 60  | h.264    | 30 Mbps                        |
| Twitter / X       | 1280×720    | 30  | h.264    | ≤ 2:20 → cut a Twitter edit    |
| Reddit            | 1280×720    | 30  | h.264    | 4:00 full is fine              |
| Discord           | 1280×720    | 30  | h.264    | Under 25 MB (bump CRF to 30)   |

### Twitter edit (2:20 cut)

Drop the 2:30–3:30 BPDoctor section and tighten the 1:30–2:30 Linked Layer
section to 45 sec. End on the PIE card.

## Where the asset lives post-shoot

```
docs/demos/media/
  locomotion_demo_full.mp4       # 4:00, 1080p, master deliverable
  locomotion_demo_twitter.mp4    # 2:20, 720p, Twitter card
  locomotion_demo_thumb.png      # 1280×720 poster frame (t=3:50)
  raw_takes/                     # NOT committed — OBS master files
```

## Failure fallbacks

If Bionics's emitted plan doesn't match the reference plan closely enough
to produce a working demo:

1. **Tighten the prompt** with explicit tool names (see the hand-checked
   reference in `plans/demo_full_locomotion.json`).
2. **Record the shorter 2:20 Twitter edit first** — it skips the fragile
   BPDoctor loop and shows the win earlier.
3. **Use the hand-built plan** as the reference and show it via
   `bionics execute-plan demo_full_locomotion` — still a legit demo of
   Bionics's capabilities, just not the zero-shot version.

## Publish-day checklist

- [ ] Upload master to YouTube (unlisted for review).
- [ ] Commit `locomotion_demo_twitter.mp4` + `locomotion_demo_thumb.png` to
      `docs/demos/media/`.
- [ ] Update root `README.md` hero to embed the locomotion demo.
- [ ] Draft the viral thread (see `docs/demos/T1B_launch_thread.md` when
      ready).
- [ ] Post to /r/UnrealEngine, /r/gamedev, Epic forums, BlueprintsLab
      Discord.
- [ ] Pin to personal Twitter, tag @UnrealEngine, @AnthropicAI, @FabMarketplace.
