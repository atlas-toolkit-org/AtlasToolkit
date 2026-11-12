# The 3D tools' settings: what matters, how far, and the recipes

Contour Lines, Sketch Hatching, Stipple, Twist and Melt bring the Blender pieces of `projects/blender3d_project`
(skill `plotter-contour-art`; the Augustus sketch; Digital divide) to any model. This page says which settings change
a drawing most, in what range, and how to reproduce the pieces.

## Units: why the settings carry over between models

| quantity | unit | why |
|---|---|---|
| model lengths (sway, ripple, focus radius, drip floor ...) | **% of the model's height** | a head and a full figure get the same look from the same numbers |
| Twist and Melt internals | the pieces' "units of a 2-tall model" (1 unit = 50 % h) | the tuned values stay exact |
| frequencies (Twist) | **waves over the height** (= the pieces' frequency / π) | 1 wave = one S along the whole model |
| paper lengths (spacing, pens, gaps) | mm on paper | what the plotter draws |
| camera | plotter.vision's units: the model's (or stage set's) largest side is 100 | the Aim tab and Plotter Vision speak the same numbers |
| smoothing (Melt softening, Sketch analysis) | passes counted for the mesh's density | a pass spreads over about one edge; n = n_ref · (e_ref / e)², so a 15k and a 300k mesh melt and draw alike |
| curvature thresholds (Sketch valleys, suggestive contours) | per unit of a 2-tall model | the analysis runs on the model scaled to 2 tall |

Up and Front put any model upright with its front at -Y: Auto reads STL/PLY as Z-up, OBJ as Y-up, and stands a statue
stored lying down; **Extra turn** (X, then Y, then Z, degrees) lays it on its side or tilts it. A light "relative to
the model" turns with that extra turn (a portrait light stays on the face of a bust laid down).

## How the ranking was measured

`scripts/dev/param_sweep.py` swept every setting of each tool over its range (4 points, the rest at the defaults) on
three models: the v17 head (126k faces), Augustus (bust with a raised arm, 319k faces, open scan) and a Julia fractal
(20k faces, not figurative). Camera, paper, pens, model frame and stage layout are framing, not form, and were not
swept. Every result was plotted (Twist and Melt: drawn by Contour Lines through one fixed camera), rasterised at the
pens' widths, blurred, and compared with the default plot over the inked part of the sheet (blank paper would
dilute every difference). A setting's effect is the largest over its sweep (very wide ranges are swept from 0 to 4×
the default).

- **Line drawings** (Contour Lines, Sketch Hatching, Twist, Melt): **effect = 1 - SSIM** at a 1 mm blur, the eye at
  reading distance; Sketch Hatching's line details again at 0.3 mm, the eye up close. 0.05 is barely visible, 0.2
  a clear change, 0.4+ a different drawing.
- **Stipple** is tone, and SSIM hardly sees a change of tone on white paper (a contrast that thins the half-tones by
  a fifth scored like a re-seed). Its effect is the **share of the ink that moves** at a 3 mm blur, where single
  dots no longer count: 0.5 = half the ink is somewhere else.

**Noise floor.** Changing only the seed (where the dots fall, where hatch strokes start, where drips form) already
scores: Sketch Hatching .17 / .06 / .17, Stipple .15 / .15 / .12, Melt .20 / .24. A setting near its tool's floor
changes the drawing about as much as a re-roll: it may still matter (another look), but not more than chance.
Twist and Contour Lines are deterministic (floor 0).

Settings that only act when another is on (Head or bust, Focus, Edge darkening with an outline) were swept again
with it on, on the models where it makes sense.

**Primary** settings are in the form; **advanced** ones behind "Show advanced settings"; **internal** constants are
fixed in code (listed so you know they exist, and why they are not settings). A setting is primary when it is a
decision per piece with a large effect that holds across models. The sweep flags an advanced setting stronger than
the tool's median primary one, and a primary one under a quarter of that median; each flag was reviewed below, and
kept only with a reason (a fix for some models, a choice of kind, an overlap with a stronger setting).

Effects are given as v17 / Augustus / Julia.

### Contour Lines

| setting | rank | effect | what it does to the drawing | why this rank |
|---|---|---|---|---|
| Line spacing on paper | P | .51 / .50 / .49 | density and darkness of the whole plate: 1.8-2.2 mm with a 0.3 pen, 1.2 dense, 3 open | the strongest, and the same on every model, because it is set on paper |
| Remove hidden lines | P | .32 / .32 / .51 | off, the far side's slices show through: a glass model | a choice per piece; strongest on open forms (Julia) |
| Taper converging lines | P | .23 / .25 / .35 | how close two lines may run before one stops; 0 lets grazing silhouettes fill with ink | visible at every silhouette |
| Shortest stroke | P | .05 / .06 / .19 | drops crumbs: fewer pen lifts, cleaner broken forms (Julia, melt drips) | small to the eye, large on plot time and on fragmented models |
| Grid offset | A | .17 / .21 / .21 | moves every slice by a fraction of the spacing | the score sees every line move, the eye sees the same plate: use it to put a slice through a feature, or to match another set |
| Simplify | A | .04 / .05 / .05 | the line simplifier's tolerance | invisible at reading distance; smaller files |
| Bridge hidden gaps, Join (same contour), Join (any) | A | .00-.06 | short hidden gaps drawn through, strokes chained | plot time; barely visible |
| Sampling step | A | (not swept) | how finely visibility is sampled along a slice | quality against time |

Internal: the visibility slack (5e-4 of the frame, the pieces' 1e-3 of a 2-tall head); 8 bisection passes per
visibility change; the taper's minimum run (3 mm) and probe step (0.15 mm).

### Twist

| setting | rank | effect | what it does to the form | why this rank |
|---|---|---|---|---|
| Sway | P | .53 / .59 / .60 | size of the S-bend | the main amplitude |
| Sway waves | P | .57 / .57 / .57 | how many S's along the height (1 = one S) | as strong as the amplitudes, on every model: the rhythm is a decision per piece, not tuning |
| Phase | P | .55 / .60 / .54 | where along the height the bends fall | same |
| Turn waves | P | .45 / .58 / .58 | how many times the torsion reverses | same |
| Turn | P | .50 / .52 / .58 | torsion about the up axis; the sign is the direction | a main amplitude |
| Amount | P | .46 / .49 / .53 | scales sway, turn and ripple together; the end of a sequence | the master knob |
| Ripple | P | .53 / .41 / .45 | the spiral ripple's depth | a main amplitude |
| Ripple waves | P | .50 / .26 / .33 | ripple frequency along the height | strong; weaker on large forms that the ripple only textures |
| Spiral lobes | P | .37 / .24 / .28 | ripples around the axis (0 = rings, 3 = a triple spiral) | the ripple's character |
| Turn about | A | .42 / .49 / .47 | the vertical axis, or the model's spine | strong, but a fix for models that lean out (a raised arm wrings about itself); the vertical axis is the pieces' |
| Ripple pushes | A | .14 / .40 / .46 | out from the axis, or along the surface | model-dependent: along the surface tears thin parts (Augustus' fingers, Julia's filaments); the v17 preset uses it |
| Calm near the axis | A | .16 / .03 / .08 | ripple faded near the axis | a fix for the crown, where a spiral has no direction; it shows on a head, hardly elsewhere |
| Smoothing subdivision | A | .02 / .02 / .07 | Loop subdivision before the twist | smoothness against time |

Internal: the growth with height (0.35 z + 0.85), the depth sway (0.35 of the sway, phase +0.7), the stage
trajectories below. No seed: Twist is deterministic.

### Melt

Swept at the default amount (0.667, a heavy melt) with Head or bust off and no subdivision; the face settings again
on v17 with Head or bust on. Seed floor: .20 / .24 (a new seed moves every drip).

| setting | rank | effect | what it does to the form | why this rank |
|---|---|---|---|---|
| Sag | P | .48 / .46 / .50 | slump into the pool, the crown's sag and spread, material mounding at the base | the strongest, alike on every model |
| Amount | P | .42 / .48 / .51 | how far the melt has gone, from intact to dissolved | the master knob |
| Dissolve grain | P | .47 / .24 / .30 | the dissolve's voxel size: fine erosion, or whole features gone | moved up from advanced by this study: stronger than most primaries |
| Drip size | P | .28 / .26 / .44 | thickness of drips and rivulets (auto for thin models) | strongest on thin, fragmented forms |
| Softening | P | .35 / .26 / .19 | how far the heat blunts crisp detail | strongest on detailed models |
| Dissolve | P | .25 / .24 / .29 | pull toward a featureless blob | the feature eraser |
| Drips | P | .11 / .32 / .20 | how many drips form | as strong as a model has overhangs (Augustus' arm and folds) |
| Pool | P | .22 / .23 / .11 | height of the pool: with contours, its rings | weaker where the base is small (Julia) |
| Head or bust | P | .18 / .13 / .15 | adds the face terms | a choice of kind, not of degree |
| Face slide (head on) | P | .20 on v17 | the face sliding off the skull, with jowls and jaw mass | the head's main term |
| Seed | P | .20 / .24 | where the drips form | as strong as Drips or Pool: a re-roll is a real choice here |
| Smoothing subdivision | A | .23 / .22 / .25 | Catmull-Clark levels before melting: smoother drips, pool and contours | strong, but quality against time (each level ×4 faces) |
| Drip length, Drip spacing | A | .20 / .32 / .17, .11 / .32 / .19 | the drips' shape once their number is set | second-order; models with many overhangs feel them most |
| Drips from overhangs | A | .08 / .32 / .16 | drips hanging from brow, nose, chin, an arm | a switch for models where hanging drips look wrong |
| Drips stop above the base | A | .08 / .33 / .08 | a falling drip ends above the floor | a fix for figures on a base (Augustus 1.5 %) |
| Seed the drips on | A | .11 / .12 / .18 | the intact or the melted model | the intact model keeps drips in place through a sequence |
| Head spans from / to (head on) | A | .42 / .32 on v17 | the head's height band | strong, but a fit, not a style: a band that misses the head puts the face terms on the wrong part |
| Pool rim, Rivulets, Flow streaks, Irregularity, Flatten base, Features droop, Asymmetry | A | ≤ .09 | refinements of the pool, streaks, the face | close to invisible at plate scale |

Internal: the zones (face, slide, protrude, ear, jowl, jaw, rim, mound) scaled by the head's depth and width
against v17 (REF_FRONT 0.911, REF_SIDE 0.726); snap distances; the drip bulb (1.35) and flare (0.3); the drip tube
resolution (24, 8 in draft); smoothing passes converted from v17's density (E_REF 0.012982); the trajectories
below.

### Sketch Hatching

At a 1 mm blur, and at 0.3 mm for the line details. Seed floor: .17 / .06 / .17 at 1 mm.

| setting | rank | effect | what it does to the drawing | why this rank |
|---|---|---|---|---|
| Ambient | P | .51 / .40 / .50 | how deep the shadows go: lower, darker and more hatched (Engraving 0.32) | the strongest; moved up from advanced by this study |
| Hatch density | P | .44 / .22 / .57 | strokes closer together (never closer than 2.5 pen widths) | the hatching's darkness |
| Hatching | P | .28 / .18 / .54 | off: lines only (preset Minimal) | a choice per piece |
| Complexity | P | .23 / .14 / .38 | how many interior lines survive, how dense and layered the hatching is | one knob over many of the pieces' values |
| Light height, Light from | P | .30 / .23 / .19, .33 / .27 / .12 | where the shadows fall | weaker on Julia, which shadows itself all over |
| Strokes follow | P | .25 / .11 / .30 | across the form, level cross-sections, bands of light, or one angle | the hatching's character |
| Seed | P | .17 / .06 / .17 | where hatch strokes start, the hand's waver | the floor: a re-roll |
| Tone curve | A | .49 / .33 / .54 | above 1 darkens the half-tones, keeping the extremes | strong, but a third way of darkening after Ambient and Hatch density |
| Key light | A | .32 / .27 / .11 | the key light's strength | overlaps Ambient |
| Longest stroke, Shortest stroke, Gap at edges | A | .31 / .12 / .39, .24 / .15 / .37, .25 / .11 / .39 | how the hatch strokes are built | above the floor, but texture rather than tone or form; the pieces' values hold on every model |
| Tone bands | A | .24 / .10 / .28 | Classic or Engraving bands | set by the presets |
| Cast shadows, Ambient occlusion, Tone blur, Light turns with, Calm angle | A | .11-.36 on v17 and Julia, ≤ .13 on Augustus | second-order tone and direction | near the floor on Augustus |
| Focus point | A | .19 / .00 / .19 | one region drawn with more care and its own light | a portrait tool (preset Portrait focus); with it on, radius .24 / .26, position .20, its light .17 on v17 |
| Shadow softness, Form shading, Tone passes | A | ≤ .21, model-dependent | the terminator's softness, big forms or small planes, how many bands | near the floor; Tone passes only act where the tone gets dark enough (Julia) |
| Crease angle | A | .24 / .06 / .00 | sharp edges drawn | below ~20° a scan's every facet edge is drawn: a fix for faceted models |
| Outline weight, Hand wobble | A | .03-.05 at 0.3 mm | the outline's weight, the hand's waver | subtle at any distance: the line's character, not the drawing's; moved down from primary by this study |
| Suggestive contours, Valleys, Creases, Lost and found (and thresholds) | A | ≤ .11 at 0.3 mm, 0 on Julia | add or drop a few lines in the relief | details: they show on a scan with relief (Augustus), hardly elsewhere |
| Max ink | A | 0 | a cap on the ink | only binds when set below the drawing's ink |

Internal: ambient occlusion (24 rays, reach 0.18 of a 2-tall model), 4 shadow samples, the depth discontinuity
ratio (6), feature size (0.03) and the suggestive, valley and ridge thresholds; the smoothing passes (contours 2,
lines 24, field 120, tone 80) converted from the Augustus sketch mesh's density (E_SKETCH 0.004739); the roles'
minimum lengths and the Jobard-Lefer ratios.

### Stipple

Share of the ink that moves, at a 3 mm blur. Seed floor: .15 / .15 / .12.

| setting | rank | effect | what it does to the drawing | why this rank |
|---|---|---|---|---|
| Ambient | P | .99 / .96 / .98 | lifts the shadows and empties the half-tones (0.1-0.3 goes a long way) | as strong as the spacing; moved up from advanced by this study |
| Dot budget | P | .98 / .97 / .99 | the most dots drawn; under the tone's need, every tone thins alike | strongest when it binds (at 50 dots almost nothing is left); above the need (v17: ~3 400 dots at the defaults) it changes nothing, so it is also the plot-time cap |
| Darkest spacing | P | .93 / .91 / .93 | dot distance in the darkest tone: the drawing's density | alike on every model |
| Dot size | P | .68 / .69 / .66 | how big a dot looks (with the pen width) | alike on every model |
| Light from | P | .98 / .81 / .05 | which side is dark | nothing on Julia, which shadows itself all over (with Cast shadows off the light reads) |
| Highlight left empty | P | .50 / .58 / .47 | how much of the lit side stays paper | |
| Light height | P | .47 / .56 / .05 | | as Light from |
| Contrast | P | .30 / .32 / .16 | above 1 empties the half-tones, below fills them | the pieces' density gamma; twice the floor on the heads |
| Cast shadows | P | .22 / .23 / .17 | parts shade the parts behind them | above the floor |
| Outline | P | .13 / .21 / .08 | a silhouette line on its own pen, dots kept clear of it | little ink, but a choice of kind (dots only, or a drawn contour) |
| Seed | P | .15 / .15 / .12 | where the dots fall | the floor |
| Tone curve | A | .47 / .50 / .41 | darkens the light's half-tones (more dots) | stronger than Contrast, but the same job: one tone curve in the form |
| Bounce, Light turns with, Patchiness, Patch size, Wrap, Dot polygon sides | A | .05-.28 | light from below, the light's frame, noise patches, the terminator, the dot's shape | around the floor |
| Edge darkening | A | .11 / .14 / .06 | darker toward the silhouette: the dots build the contour | below the floor in ink, though visible as a sparse line of dots along the shadow side; moved down from primary by this study (the Solid preset sets it); with it on, its own settings .05-.09 and the outline's clearance .11-.13 |
| Rim hint, Ambient occlusion, Shadow softness | A | ≤ .10 | | below the floor |

Internal: the packing constant of random sequential adsorption (K 0.84), candidates oversampled 14×, a dot radius
cap of 8 mm, the plot-time estimate (0.25 s per dot).

## Trajectories (stages)

**Twist**, fraction of the full amplitude at stage I .. IV (four stages at amount 1 give the v17 sequence exactly):

| | I | II | III | IV |
|---|---|---|---|---|
| sway | 0 | 0.3 | 0.625 | 1 |
| turn | 0 | 0.30645 | 0.6129 | 1 |
| ripple | 0 | 0.31111 | 0.62222 | 1 |

**Melt**, Amount 0 / 0.33 / 0.67 / 1 = the v17 stages I intact / II first drips / III heavy melt / IV dissolved
(`templates/melt_sequence.json`): magnitudes (softening, slump, sags, droops, pool height, drips, dissolve) grow from
nothing at I; shapes (pool flatness, spread, lobes, drip lengths, voxel size) hold stage II below it; in between,
every value is interpolated. Stages are seeded on the intact model, so drips grow in the same places.

## Recipes: the pieces, again

How the presets reproduce the studio pieces they were tuned on (development notes; the preset names are
generic since 1.17.0).

- **v17 torsion sequence:** Twist preset *Torsion sequence* on `3d_models_db/head_model/v17_head.stl`, then
  Contour Lines preset *Stage set* on the four stage files. The pieces' camera (32° round from the face, 17.6° above,
  55 mm lens) is θ 72.4°, ψ 148°, distance 262, field of view 36.2° (Contour Lines' defaults).
- **v17 dissolution sequence:** Melt preset *Dissolution sequence*, then Contour Lines *Fine detail* on the
  stages; the pool widens the stage set's frame, so press F in the Aim tab (or distance ~300, look at 0, 0, -12).
- **Augustus torsion:** Twist preset *Serpentine* (within ~2 % of the pieces' sway, turn, sway waves and ripple)
  with Extra turn (-90, 38, -90): the pieces' two turns `rotate_deg [[0, 90, 0], <!-- preset-leak: ok -->
  [-52, 0, 0]]` as one (`geom/frame3d.euler_from_matrix` combines any chain).
- **Augustus melt:** Melt preset *Thin figure* (drip size auto = 0.53, drips stop 1.5 % h above the floor).
- **Augustus sketch:** Sketch Hatching on `projects/blender3d_project/augustus/augustus.prima.top.01.mesh160k.stl`,
  Up +Z, Front -Y, Extra turn (-92.365, 64.981, -92.643) (the pieces' two turns `[[0, 90, 0], [-25, 1, -0.5]]` as
  one), θ 90°, ψ 180°, field of view 30°, F to fit; presets *Minimal*, *Classic*, *Engraving*; for the face, Focus at
  (0.645, 0.793), radius 8.5 % h, portrait light +25° / 55° (the pieces' face-relative -25°: their azimuth turns the
  other way).
- **Digital divide's sphere:** Stipple presets *Soft* (v1) and *Solid* (v2) on any sphere (`3d_models_db/sphere.stl`).

## When something looks wrong

| symptom | cause | fix |
|---|---|---|
| dense black streaks along a silhouette (contours) | lines converge at grazing angles | Taper converging lines 0.45 mm (default) |
| white slots where drips are (contours of a melt) | drip arcs shorter than Shortest stroke | a shorter Shortest stroke and tighter joins (the packs' *Fine detail*), or Drip size up |
| the crown crumples (twist) | a spiral has no direction on the axis | Calm near the axis 15 % h (default) |
| fingers, relief torn into spikes (twist) | ripple along the normals | Ripple pushes "Out from the axis" (default), or ripple under 1 % h |
| a melted face slides into a "tongue" in the pool | the face sinks below the pool's height | lower Face slide or Sag for that stage |
| suggestive contours scribble over a scan's relief | the threshold is too low for the noise | Suggestive threshold 3-4 (Classic, Engraving) |
| a face stays blank (sketch) | the key light is too close to the view axis (frontal: the planes never separate) | Light azimuth beyond ±30°, or a Focus with its portrait light |
| hatching too dark, coverage over 0.7 | dense passes in deep shadow | lower Hatch density, raise Ambient, or one pass fewer |
| stipple thinner than asked | the dot budget is hit (the report says so) | raise the budget or widen the darkest spacing |
| nothing drawn | the model is outside the camera's frame | Aim tab, F |
