# Stair Sculpt 0.1.0

Stair Sculpt turns a closed model into a staircase. It either builds stairs inside a cavity or makes the stair out of
the model itself. Every result is one closed, fused solid in the model's own units, ready for Plotter Vision, Contour
Lines or hatching, and for a 3D print.

## Modes

**Carve** opens a cavity behind a window and builds stairs in it. The cavity follows the model, leaving an even
**Wall**, and goes as far in as **Depth** says. With **Flights** at 1 you get one staircase of the chosen type. Above 1
you get an ensemble: flights, landings, piers and arches on a seeded grid of storeys, placed only where the cavity has
room. **Seed** rearranges it.

**Treads** keeps only the model's material inside the steps. The model's silhouette survives in the outline of each
tread, like a strata stack that climbs. **Keep base** and **Keep top** leave part of the model whole: Stair Crown keeps
the head on a stair where the body was. Flights give intertwined stairs (2 on a helix is a double helix) or, on a linear
stair, a zigzag back and forth up the body.

**Cut** cuts the steps into the body, and what stays becomes the walls.

- **Linear:** a flight up the model. **Follow the surface** at 100 climbs with the surface; at 0 it runs in one straight
  pitch from the base, through whatever stands in its way, which is the stronger image on most models.
- **Helix:** a groove around the model, like a road up a tower. **Well** limits how deep it can reach.
- **Turning:** a square ramp around the model, cut back at each terrace.

## Stair types

| Type | Controls | Notes |
|---|---|---|
| Linear | Width, Heading, Climb | Runs across the model at Heading 0 and from front to back at Heading 90. |
| Turning | Turns, Turn by, Width, Well | Quarter turns go around a well. Half turns are switchbacks, side by side. Cut mode always uses quarter turns. |
| Helix | Helix turns, Well, Solid core, Heading, Climb | **Follow the model's axis** keeps it inside a neck or a leaning figure. |

**Underside** sets how the stair is carried. Stepped band makes each step a deep block, Smooth puts the treads over a
sloped soffit, Open makes thin treads on a carriage, and Solid to floor keeps everything below the steps.

## Choosing where a cavity opens

**On a side** needs no painting: choose a side, a turn around the model, a shape and its size. **Follow the outline**
keeps an even rim around the silhouette.

**Painted area** opens the cavity where you paint, seen from the painted faces' average direction. Open the Selection
tab and choose **Paint / select opening**. The selection is tied to this exact mesh. On the command line or in a
Workflow, pass an exported selection as **Selection file**, or use **Aim in tool**, paint, then **Back to workflow**.

The cavity is swept out toward the opening, so an overhang in front of it (hair, an ear) can be opened too, leaving a
small extra window. Use a smaller opening, or another side, if that is not wanted.

## Drawing it

- Look into the opening, with the camera about 15° off its direction. The README's image is drawn this way.
- Plotter Vision with **Flat angle** 25° draws treads, risers and arches as clean edges, with no mesh noise.
- Contour Lines slices horizontally, so it hides treads. It suits Treads and helix presets better than ensembles.
- **Lip** gives each step a second parallel line. Risers should stay wider than a pen line: at a 200 mm drawing, 100-200
  steps in one flight is about the limit.

## Limits

- The input must be a closed solid. **Repair by resampling** is opt-in, changes the surface and cannot be combined with
  painting: repair and save the model first, then paint that.
- A model's shape can cut a stair in two. By default only the largest connected solid is kept, and the report says how
  many pieces were left out. **Keep floating pieces** keeps them.
- Where steps touch along an exact edge, the two sides open by a tiny distance (at most 4e-4 of a model 2 tall, usually
  2e-5), so the mesh stays closed when saved. The report gives the distance when this happens.
- A flight that does not climb along its heading is refused: change **Heading** or **Climb**.
