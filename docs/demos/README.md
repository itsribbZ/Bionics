# Bionics Demos (T1 WWZ Ignition)

Everything needed to record the two launch demos from the 2026-04-23
hellscape audit. Each file is a self-contained recording guide: shot list,
prompts, post-processing commands, captions, fallback plans.

## Files

| File                                 | Purpose                                              |
|--------------------------------------|------------------------------------------------------|
| `T1A_bpdoctor_hero_gif.md`           | 30-second hero GIF. "BPDoctor vs My Tuesday Morning" |
| `T1B_one_prompt_locomotion.md`       | 4-minute full-pipeline demo. One prompt → locomotion |
| `media/` (created post-shoot)        | Committed final deliverables (GIF + MP4 + thumbs)    |
| `media/raw_takes/` (gitignored)      | OBS masters — NOT committed                          |

## Supporting plans

These live in the main `plans/` directory so `bionics execute-plan` can drive
them:

| Plan                                       | Used by       | Purpose                                        |
|--------------------------------------------|---------------|------------------------------------------------|
| `plans/demo_bpdoctor_broken_setup.json`    | T1.A          | Pre-breaks ABP_Trooper_DemoBroken with 5 bugs  |
| `plans/demo_full_locomotion.json`          | T1.B (fallback) | Reference locomotion plan (hand-checked)     |

## Recording pre-flight

Applies to both shoots:

- [ ] UE5 rebuild done (BionicsBridge + BPDoctor compiled this session)
- [ ] `.mcp.json` at the project root points to the current Bionics install
- [ ] `BIONICS_MCP_ALLOW_DESTRUCTIVE=true` env set for the MCP server process
- [ ] `.bionics-bridge/instance.json` exists (plugin is live on start-up)
- [ ] OBS Studio installed; profile set to 1920×1080 / 60 fps / x264 slow / CRF 18
- [ ] Windows DPI = 100% (no scaling artifacts in captures)
- [ ] Notifications silenced (focus assist on)
- [ ] Terminal font ≥ 18 pt; monospace

## Publish workflow

1. Record → OBS `.mkv` master in `docs/demos/media/raw_takes/`.
2. Post-process via ffmpeg (commands embedded in each shoot guide).
3. Commit the deliverables (`.gif`, `.mp4`, `.png` thumb) to `docs/demos/media/`.
4. Reference from root `README.md`:
   - T1.A hero → hero slot directly below the title.
   - T1.B full demo → "What it does" section, linked via a screenshot.
5. Draft the launch thread (not in this repo; lives in Jacob's notes).
6. Post to /r/UnrealEngine, /r/gamedev, Epic forums, BlueprintsLab Discord.

## Why these two clips

From the hellscape audit (`memory/project_hellscape_audit_2026-04-23.md`):

> **Moat (verified via competitive scan):**
> 1. C++ bridge @ 5-20ms — StraySpark users complain about HTTP token burn;
>    Aura is closed
> 2. Complete AAA animation pipeline — MM → IK Rig → Retargeter → batch →
>    Linked Layer → Control Rig → AnimGraph. Zero competitors ship end-to-end
> 3. BPDoctor 34-check autofix — category nobody else has entered
> 4. MCP-native + native latency — Aura closed, StraySpark slow; Bionics is both
> 5. Solo-dev-first, local-first — GDC 2026: 52% of devs say AI harms the
>    industry; privacy care

T1.A shows moats #3 + #4 in 30 seconds. T1.B shows moat #2 end-to-end. Together
they're the visual proof that Bionics is doing things no competitor can match.
