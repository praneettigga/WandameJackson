
# HNX26EPS06: 3D Scene Generation from Blueprints and Room Video

**Event:** HACKNEX 2026 — National Hackathon  
**Category:** Computer Vision  
**Problem Statement ID:** HNX26EPS06

---

## Pitch

Turn a 2D description of a static space into a navigable 3D model. The input is either an architectural blueprint / floor plan, or ordinary images and video of a real room. The system outputs a 3D representation of the space and, for video input, generates the parts that were never seen (the wall behind the sofa, the ceiling, the corner nobody filmed) instead of leaving holes.

## Scope (static spaces only)

This track is about static environments: a room or building that does not change while it is captured. Anything that moves, and any time-based playback, belongs to HNX26EPS07 (4D) and is out of scope here. Entries are judged only on how well they reconstruct and complete the space itself.

## Why It's Hard

Reconstruction from what the camera saw is a solved-ish problem; completion of what it did not see is the research frontier. Teams have to recover metric scale and consistent geometry from messy handheld footage, parse symbolic blueprints (walls, doors, windows, dimensions) into real structure, and then fill unobserved regions plausibly while being honest about which parts are observed and which are generated.

## Baseline to Beat (chosen by the team)

The strongest option of this kind available today, chosen by the team for the mode it enters (for example an off-the-shelf floor-plan parser for Mode A, or a standard structure-from-motion plus Gaussian splatting / NeRF pipeline for Mode B), run on the same inputs.

## Minimum Bar to Qualify (24h)

Pick one input mode and do it end to end, beating the baseline for that mode on the held-out inputs.

Either:

- **(a)** A floor plan → a 3D model with correctly placed walls, doors, windows and room dimensions.

- **(b)** A short walkthrough video of a static room → a 3D scene (mesh, point cloud or Gaussian splat) that can be orbited in a viewer, with at least one visibly completed unseen region.

The output must open in a real 3D viewer. A short write-up is also required.

## Stretch Goals

- Both input modes in one pipeline (blueprint-guided completion of a video reconstruction).
- Clear visual distinction between observed geometry and generated geometry (confidence map or colour overlay).
- Furniture / object-level reconstruction, not just the room shell.
- Reconstruction from a sparse set of photos instead of a full video.
- Exporting to a standard format (GLB / PLY / USD) usable in common 3D tools.

## Data and Judging

Teams capture or find their own room videos and floor plans. At judging, judges bring unseen floor plans and room videos with measured dimensions, with some camera views held out.

---

## Judging Rubric

Pick one mode. Your entry is scored only with that mode's rubric, and each mode is worth 100 points. Doing both modes in one pipeline is a bonus.

### Mode A: Floor Plan → 3D Model

- **Layout accuracy vs. baseline (40%):** Wall, door and window placement and room dimensions (layout IoU and dimension error).
- **Completeness (15%):** Every room and opening in the plan is present and correct.
- **Model quality (15%):** Clean, correctly scaled, sensible wall heights and proportions.
- **Usability of the output in a 3D viewer (10%).**
- **Research contribution (20%):** What is new compared with the baseline, shown by an ablation or comparison.

### Mode B: Room Video → 3D Scene

- **Novel-view quality on held-out camera views vs. baseline (20%):** PSNR / SSIM / LPIPS.
- **Geometric accuracy vs. baseline (25%):** Dimensions, Chamfer distance.
- **Quality and plausibility of generated unseen regions (15%).**
- **Honesty (15%):** Observed vs. generated regions are clearly separated (no silently hallucinated geometry).
- **Usability of the output in a 3D viewer (5%).**
- **Research contribution (20%):** What is new compared with the baseline, shown by an ablation or comparison.

### Bonus (either mode)

- Both modes in one pipeline.
- Object-level reconstruction.
- Standard-format export.

---

## Skills

- 3D vision
- Structure-from-motion
- Neural rendering / Gaussian splatting
- Generative completion (diffusion / inpainting)
- Document parsing
