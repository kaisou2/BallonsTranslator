# PR #1238 clean redesign

This document separates observed facts from the design decisions and invariants
of the clean implementation. It is intentionally based on the fixed feature
baseline and `upstream/dev`, not on code from the rejected feature or its later
worklog.

## Observed facts

- Repository: `dmMaze/BallonsTranslator`
- Historical fixed implementation baseline: `e652479c9872efaf6a30d84bf8124c09ece3e762`
- Current implementation target described by this document:
  `a1f7c1f7b6e1e93400a4372b8e228d3908a1a966`
- The target SHA identifies the production and test code immediately before this
  documentation update. The commit containing this documentation change will
  necessarily have a different SHA and must not be confused with the target.
- Comparison-only `upstream/dev`: `6155f9b303033b24f57a2c025d2edbfed3eb847f`
- PR #1238 base: `6bff00ee017706eb54637dce828cb0149632ecca`
- PR #1238 head: `57e9f1c604fc9ccbc79dc9fbf7ad91d77592cf04`
- Worklog head: `47b47ca37e30ee2a94ab1926e9c454e89a0ecf96`
- The rejected PR has three feature commits. The later worklog is 39 commits
  ahead of the comparison snapshot and changes 23 files by roughly 13,800
  insertions and 950 deletions.
- Neither rejected head is an ancestor of this feature branch. The comparison
  snapshot is an ancestor of the fixed implementation baseline.
- The original approach mixed visual scale with document point size and font
  stretch. Later repairs added multiple compensating coordinate/state layers,
  per-line transforms, cloned render documents/layouts, and opaque future-state
  handling. This made wrapping, vertical layout, effects, editing, persistence,
  and undo disagree about which geometry was logical.

The rejected branches were inspected only for requirements, observable legacy
field names, migration signatures, and regression cases. No feature commit,
patch, file, helper, state object, or cache structure was applied or copied.

## Design decisions

### Canonical state and distinct meanings

`TextBlock.fontformat` is the only persistent owner of the four-component
`TextTransform`:

```text
horizontal_scale  = 1.0
vertical_scale    = 1.0
slant_angle       = 0.0   # Box Slant
glyph_slant_angle = 0.0   # Glyph Slant
```

`TextTransform` is a `NamedTuple` in exactly that order. Tuple unpacking and
tuple equality therefore remain supported while field names make omissions
visible. `FontFormat.text_transform` returns this type, and
`normalize_text_transform()` accepts the same four values. Its fourth argument
defaults to `0.0` only for source compatibility with existing three-argument
callers; production UI, command, persistence, and translation paths explicitly
carry all four values.

The canonical ranges are:

| Component | Inclusive range | Meaning |
| --- | --- | --- |
| Horizontal Scale | `[0.1, 4.0]` | Post-layout box x scale |
| Vertical Scale | `[0.1, 4.0]` | Post-layout box y scale |
| Box Slant (`slant_angle`) | `[-85.0, 85.0]` | Whole-item shear |
| Glyph Slant (`glyph_slant_angle`) | `[-45.0, 45.0]` | Glyph ink-only shear |

Values use six-decimal canonical precision and normalize negative zero to zero.
Booleans, nonnumbers, NaN, and infinity are rejected. Runtime/API values are
clamped; typed UI values outside the range are rejected and restored instead.
The compatibility constants `TEXT_TRANSFORM_SLANT_MIN/MAX` remain aliases for
the Box Slant range. The explicit Box and Glyph Slant constants are authoritative.

These meanings are intentionally independent:

- `FontFormat.italic` remains native font italic shaping.
- `glyph_slant_angle` changes shaped glyph ink without changing layout.
- `slant_angle` remains the existing whole-`TextBlkItem` Box Shear.
- `TextBlock.angle` remains the existing block rotation.

Native italic and Glyph Slant are cumulative; neither suppresses the other.
The rich-text document continues to own logical text and native character
formatting. No transform factor changes point size, font stretch, HTML,
wrapping, line advances, or line breaks.

### Transform composition and direction

The composition order is fixed:

1. native font shaping, native italic, fallback, ligatures, and combining marks;
2. glyph-local `glyph_slant_angle`;
3. the existing vertical glyph orientation;
4. Horizontal/Vertical Scale and `slant_angle` Box Shear;
5. `TextBlock.angle` block rotation.

The Box transform remains one post-layout `QTransform` on `TextBlkItem`, with
the center of `logical_unpadded_rect()` as its pivot. For local point `(x, y)`
and pivot `(px, py)`:

```text
k  = -tan(radians(slant_angle))
x' = px + horizontal_scale * (x - px) + k * vertical_scale * (y - py)
y' = py + vertical_scale * (y - py)
```

`text_transform_matrix()` continues to accept only these three Box values and
returns the canonical Box matrix `S`. Qt's fixed `QGraphicsItem` composition
would apply built-in rotation `R` before a raw base `S`, so the installed base
matrix is the derived compensation `C = R^-1 * S * R`. Qt then composes
`R * C == S * R`, which maps points as Box first and rotation last. `C` is
rebuilt from canonical values, the current angle, and the current pivots; it is
never accumulated or persisted. Rotation and transform-origin Qt property
writes are finalized through `ItemSendsGeometryChanges` notifications so direct
setters, meta-property writes, and property animation share the same path. The
item-level `transformations()` list must remain empty. Nonfinite rotation or
origin writes are rejected in the pre-change notification so validation never
escapes through Qt's C++ virtual-call boundary; a nonfinite persisted block
angle is repaired to zero while loading.

In concrete Qt state, `transform()` stores the derived `C`, while the built-in
`rotation` property continues to store the requested angle represented by `R`.
The pre-change notification validates the candidate and can return the current
value to reject it. The post-change notification installs the matching `C`
before `rotationChanged` observers run. Consequently a property observer never
sees the new rotation paired with the old anisotropic matrix. Neither `C` nor a
second rotation value is written to the model.

The normal Box pivot and rotation origin are both the center of
`logical_unpadded_rect()`. The derived helper keeps them explicit so a Qt origin
property write remains mathematically defined. Horizontal and vertical writing
use the same box axes; changing writing direction never swaps the scale factors.
The ±85° limit remains finite and invertible for all canonical scale values,
while ±90° is never canonical.

Glyph Slant uses each live `QTextLine` logical baseline as its pivot:

```text
x' = x - tan(radians(glyph_slant_angle)) * (y - baseline_y)
y' = y
```

Qt screen y increases downward, so a positive angle leans the glyph top toward
glyph-local `+x`. That is page-right for horizontal and upright vertical glyphs.
For Latin, digits, and punctuation that the existing vertical layout rotates,
the glyph-local shear is applied first and the existing 90° orientation second;
glyph-local `+x` then maps page-down. The existing punctuation orientation sets
remain authoritative.

### Glyph rendering and logical interaction

`glyph_slant_angle == 0.0` is an exact legacy fast path. Horizontal layout uses
the existing `QTextLayout.draw()`, and vertical layout and vertical effect masks
use their existing per-character paths and punctuation rotation. The custom
renderer is not entered at zero, preserving pixels, document state, layout
geometry, native italic, gradients, caches, and undo behavior.

Only a nonzero Glyph Slant enters `text_glyph_renderer.py`. It consumes glyph
runs from the attached live `QTextLayout`; it never clones or mutates a document
or layout. Paint spans are split at UTF-16 boundaries. Document fragment format
is the base, `QTextLayout.formats()` ranges merge in order, and paint-context
selection format merges last. Qt's glyph-run ordering and raw-font division are
preserved for fallback fonts, ligatures, combining marks, and bidirectional text.

`QRawFont.pathForGlyph()` supplies vector ink. The renderer combines glyph-run
position, line offset, glyph shear, and the orientation supplied by
`SceneTextLayout`. Empty/pathless paths use `QPainter.drawGlyphRun()` under the
same local transform, and `QRawFont.boundingRect()` supplies their bounds. One
resulting silhouette is reused by fill, `QTextCharFormat.textOutline`, app
stroke, vertical masks, and shadow masks. The gradient field remains anchored
to the item-local logical box; only the glyph geometry moves through it.

Every pass uses the live document and attached layout. Composition order is
shadow, app stroke, normal fill/gradient (with document text outline before its
fill), then the logical selection/cursor/IME overlay. Effects are measured in
item-local object space and inherit the later Box transform and block rotation.

Backgrounds, selection cells, underline, overline, strikeout, caret, IME
microfocus, and hit testing remain logical geometry and receive no glyph-local
shear. Selection foreground is a second draw of the same slanted glyph geometry
clipped to the logical selection rectangle, so a ligature's overhang outside
the selected cell keeps its normal foreground. Preedit glyph ink is slanted
because it is part of the live layout, while its decoration and microfocus stay
logical.

### Logical geometry and paint overflow

Logical persistence and layout always use the untransformed, unpadded block
rectangle. `TextBlkItem.shape()`, hit tests, saved rectangles, selection
polygons, and resize anchors use `logical_unpadded_rect()`, not effect padding
or Glyph Slant overhang. Visual callers map the four exact logical corners;
shear is never replaced by an axis-aligned approximation. Resize maps the
pointer through `item.mapFromScene()` and compensates position so the opposite
scene anchor remains fixed.

Automatic document resizing preserves its semantic anchor directly in parent
coordinates: horizontal Left uses top-left, Center uses center, and Right or
vertical writing uses top-right. A synchronous layout resize is one geometry
transaction: `prepareGeometryChange()` precedes relayout, layout signals are
held until the final expanded document size, display rect, pivot, compensated
matrix, anchor position, and model rectangle agree, then one final
`documentSizeChanged` notification is emitted. A resize requested reentrantly
from that notification is deferred until delivery completes. If synchronous
relayout mutates its size and then raises, the same final-state reconciliation
and notification happen before the original error is propagated.

Both rotation finalization and `set_size()` keep the transform-list invariant
and derived-matrix installation inside guarded transactions. Exceptions raised
while Qt calls the `itemChange()` virtual are contained and logged rather than
crossing the C++/Python callback boundary. A `set_size()` relayout error is
different: the item first reconciles to the layout's observable final size and
then re-raises the original error at the Python caller boundary.

Nonzero Glyph Slant bounds are calculated from the same live glyph runs,
orientation, and shear used to paint. `SceneTextLayout.glyphInkBounds()` caches
the vector envelope by document revision, layout generation, writing-layout
type, and effective glyph angle. Relayout and angle changes invalidate it.
Empty documents return an empty envelope; whitespace retains logical advance
without manufacturing ink.

`TextBlkItem` expands effect padding to include glyph overhang, stroke outset,
shadow offset/blur, antialiasing, and a transparent-border guard. Padding rounds
outward to the existing 1/64 layout-unit grid. It may change `boundingRect()`
only after `prepareGeometryChange()`; it never changes the absolute logical
rectangle or transform pivot. Zero Glyph Slant with no stroke or shadow keeps
the existing zero-padding path. The legacy scratch-image measurement remains
only on the zero-angle effects path; nonzero Glyph Slant uses vector bounds.

### Effect cache and bounded raster resources

`TextBlkItem.refresh_cache_policy()` is the sole owner of
`QGraphicsItem.setCacheMode()` for live text items:

- editing, a nonidentity Box transform, nonzero block rotation, or nonidentity
  built-in item scale uses `NoCache`;
- otherwise it uses `DeviceCoordinateCache`;
- Glyph Slant alone does not forbid `DeviceCoordinateCache`, but changing it
  explicitly invalidates item and effect caches.

Box preview changes only the item matrix and does not rebuild local effect
rasters. Glyph preview updates the attached layout angle, marks the effect
generation dirty, and schedules paint. Several preview events can therefore
coalesce into one rebuild. A raster from an older generation is never composed
with new glyph fill.

`plan_effect_raster()` bounds every full effect surface using:

```text
EFFECT_CACHE_MAX_SCALE     = 8.0
EFFECT_CACHE_MAX_PIXELS    = 4,194,304
EFFECT_CACHE_MAX_DIMENSION = 8192
EFFECT_CACHE_MAX_BYTES     = 32 MiB per item
EFFECT_TILE_MAX_EDGE       = 2048 device pixels
```

The requested scale is the painter transform's maximum singular value, capped
at 8. The planner tests power-of-two tiers `{8, 4, 2, 1}` from the greatest tier
not exceeding the request and chooses the largest tier satisfying dimension,
pixel, and byte caps. It measures the local effect rectangle, never the
Box-sheared scene extent. If tier 1 cannot fit, effects enter tile mode.

Tile mode renders only tiles intersecting the painter's exposed/clip logical
rectangle. Tiles include stroke, shadow, blur/offset, and antialiasing overlap;
fill and stroke remain vector geometry. Oversized shadow context alone may be
downsampled and smoothly scaled back. At most two currently visible tile
surfaces are retained, and tiles outside the current exposure are discarded.
Ordinary surfaces that fit retain the single `background_pixmap` fast path.

An in-cap allocation failure first retries tier 1. Interactive paint then drops
the failed cache, continues fill and vector stroke, omits shadow for that frame,
keeps the cache dirty for a later retry, and logs once per item/generation
without mutating the model. During
`Canvas.render_result_img()`, `set_export_effect_render(True)` makes a failed
tier-1/tile retry raise `EffectRasterAllocationError`; no partial export is
returned, and the render transaction restores its captured render state in
`finally`.

### Responsive controls, preview, and undo

Advanced Text Format presents Horizontal Scale, Vertical Scale, Box Slant, and
Glyph Slant as four independent atomic controls. Its local
`AdaptiveWrapLayout` preserves control order and widget identity, greedily wraps
whole label/editor units, supports child height-for-width, and excludes hidden
items. Horizontal scrolling remains disabled; preferred height is derived from
the current viewport width and capped, with vertical scrolling only as needed.
Relayout does not commit or cancel pending text or drag preview and does not
rebuild or reparent controls.

Each transform control retains idle, pending-text, and drag-preview states.
Typing changes no model until commit; invalid typed input restores the last
canonical display. Drag preview is transient item state. Mixed drag applies a
display-unit delta to each item's own starting value; mixed typed input applies
one absolute value. Escape restores the canonical quartet without an undo step.

`SetTextTransformCommand` owns all selected items and per-item before/after
`TextTransform` values. Redo and undo apply the quartet atomically and request
one overlay synchronization. A normalized no-op writes no model field, creates
no command, rebuilds no cache, and requests no unnecessary repaint. Commands do
not store HTML, pixmaps, scene snapshots, panel state, or derived matrices.

### Overlay ownership, invalidation, and export exclusion

Selection and text-block guides are UI overlays, not `TextBlkItem` paint.
`TextOverlayManager` owns reusable `TextGuideOverlayItem` instances under the
existing `baseLayer`, while `TextBlkShapeControl` owns the active outline and
handles. Every overlay carries `UI_OVERLAY_ITEM_DATA_KEY = 0x1238`, uses
`NoCache`, accepts no text-item paint ownership, and uses idempotent
`SourceOver` composition. The reusable guide is blue solid at 3 device pixels
for unselected text-block mode and pink dashed at 3.5 device pixels for selected
nonactive items. The active item is drawn once by the shape control.

Overlay bounds contain the cosmetic pen half-width plus a two-device-pixel
guard. Handles contribute their own device-space footprints, including
`ItemIgnoresTransformations` handles. For every `QGraphicsView` attached to the
scene, `OverlayFootprintInvalidator` records the old device region, updates
polygon/shape/handle geometry, records the new region, and calls
`viewport().update(old | new)`. `TextOverlayManager.sync_overlays()` is the one
entry point used by move preview/drop/cancel, undo/redo, keyboard movement,
transform changes, reshape, rotation, selection, item lifecycle, zoom, viewport
resize, and text-block-mode changes. Normal movement never requires
`FullViewportUpdate` or a full viewport repaint.

Movement state uses logical coordinates. `TextBlkItem.logical_position()` and
`set_logical_position()` address the absolute unpadded top-left independently of
paint padding. Mouse move snapshots selected logical positions; drop creates
one `MoveBlkItemsCommand` only for a real delta. Escape restores the snapshot,
move baselines, and old/new overlay regions without creating a command.
Keyboard move commands follow the same logical-position and overlay callback
contract.

At ±85° a true handle may be far outside the viewport. Shape controls keep true
scene geometry unchanged but clamp only the displayed proxy handle to a
12-device-pixel viewport inset. Proxy drag converts device-pointer delta through
the inverse view transform and applies it to the true scene point, avoiding a
jump at drag start.

`Canvas.render_result_img()` is the authoritative export exclusion boundary.
It snapshots and hides every item carrying `UI_OVERLAY_ITEM_DATA_KEY`, enables
fatal effect-render allocation semantics, renders, then restores overlay
visibility, painter state, scene scale, scroll positions, and layer
visibility/opacity in `finally`. Guides and handles can therefore enter neither
scene output nor saved/exported images, even when rendering raises.

### Persistence and migration

The canonical project root schema is version 2. Every schema-v2 block requires
a `fontformat` object containing all four canonical keys:

```text
horizontal_scale
vertical_scale
slant_angle
glyph_slant_angle
```

Schema-v2 loading is strict. Missing fields, booleans, nonnumbers, nonfinite
values, or out-of-range values reject the complete payload rather than clamp.
Top-level transform aliases, any `italic_angle`, any top-level
`glyph_slant_angle`, or any `rich_text_transform_version` marker also make a v2
payload noncanonical and reject it. Canonical output contains none of those
aliases or markers.

Root versions absent/0/1 migrate on a deep copy:

- canonical H/V/Box fields and supported top-level H/V aliases keep their
  established meanings;
- legacy `italic_angle` maps only to Box Slant and emits one deduplicated
  project warning;
- Glyph Slant is added as neutral `0.0` without a warning;
- any Glyph Slant field already present in v0/v1 is treated as a partially
  written future payload and rejects the project;
- finite legacy out-of-range values clamp to current ranges with warnings;
- conflicting aliases and ambiguous effective rich-text representations reject;
- failed stretch-HTML reversal remains exact and transactional;
- legacy block markers 0 and 1 retain their existing meanings, while marker 2
  or later remains unsupported.

Future root version 3 or later rejects before live-state mutation. Migration
preflights all raw versions and blocks, constructs and validates a complete
candidate project, and adopts it only after success. A failed load therefore
cannot partially replace the open project. A canonical second load/save is
idempotent. Existing generic `FontFormat` config/style/deep-copy serialization
adds neutral Glyph Slant for old presets and preserves all four values for new
presets and copy/paste; no `italic_angle` style alias is introduced.

### Translation pipeline

`_apply_global_text_transforms(block, global_format)` copies the normalized
global quartet as one update. It deliberately supplements rather than replaces
the existing font/effect/writing-mode allow-list.

When the existing Effect override is enabled,
`_apply_global_text_effects(block, global_format)` copies exactly these ten
fields to `TextBlock.fontformat`:

```text
opacity
shadow_radius
shadow_strength
shadow_color
shadow_offset
gradient_enabled
gradient_start_color
gradient_end_color
gradient_angle
gradient_size
```

The six scalar fields are assigned by value. The four mutable fields
`shadow_color`, `shadow_offset`, `gradient_start_color`, and
`gradient_end_color` are deep-copied for every result block so blocks neither
share them with the global format nor with one another. All Gradient payload
fields are copied even when `gradient_enabled` is false: disabling an existing
block Gradient is itself part of the override, and the configured colors,
angle, and size must remain available for later re-enabling. The helper writes
the canonical `FontFormat` owner directly; no parallel `TextBlock` Gradient
property or live `TextBlkItem` mutation is introduced.

For a normal non-inpaint-only run, existing style override logic runs first and
the transform helper then applies all four global values to every result block,
including a horizontal scale of exactly `1.0` and blocks newly produced by
detection.
Transform application does not depend on the other style override flags.
Effect application remains conditional on the existing Effect override flag;
when it is disabled, the result block's existing effect values are retained.

For **Run without update textstyle**, the run snapshots each existing block's
complete `FontFormat` and block identity before detection can replace blocks.
If both block identities and count still match for a page, each full backup
format is restored and neither global helper is called. `FontFormat.merge()`
deep-copies that backup, including all effect and Gradient fields. If the
backup is absent, count differs, or identity differs, the pipeline performs no
index-based guess: it falls back to normal global-format semantics and logs one
warning for that page. The fallback applies global effects only when the Effect
override is enabled. Warning deduplication resets at run start.

Inpaint-only remains style-neutral: translation postprocessing and both global
helpers are skipped. Selected-block translation commands preserve the existing
block's complete `FontFormat`; manual new blocks and Apply Global Format continue
to use the complete global format; copy/paste keeps the deep copy; and detection
identity, auto-layout, and squeeze behavior are unchanged.

## Required invariants

- The implementation starts at the fixed baseline recorded above; rejected
  feature branches remain requirements evidence only.
- Persistent transform state has one owner and exactly four named components.
- Native italic, Glyph Slant, Box Slant, and block rotation retain separate
  meanings and the specified composition order.
- Glyph Slant never changes logical HTML, font geometry, wrapping, advances,
  hit testing, caret geometry, or persisted rectangles.
- The zero-degree Glyph Slant path is the pre-feature paint path, not a custom
  approximation.
- Preview never mutates canonical state; commit never derives canonical values
  from a matrix.
- Direction, editing, scene rendering, effects, and export share live layout
  and glyph geometry; no renderer clones a document or invents another shear.
- Effect allocation observes scale, dimension, pixel, byte, tile-count, and
  visibility caps; export fails atomically if its bounded retries cannot render.
- UI guides are uncached overlays with per-view old/new device invalidation and
  are excluded transactionally from export.
- Transform-only and movement undo are selection-atomic, use logical values,
  preserve HTML/document state, and synchronize overlays once.
- Schema-v2 violations, future versions, and ambiguous legacy payloads reject
  transactionally; old Box values are never reinterpreted as Glyph Slant.
- Normal translation applies the global quartet and, when enabled, the complete
  ten-field global Effect payload; exact preserve-style matches restore the full
  backup format; ambiguous matches use warned normal fallback.
- No-op operations leave undo count, repaint notifications, layout/effect
  generations, item matrices, and canonical model values unchanged.

## Verification status for the current target

The recorded local replay script at
`artifacts/pr1238-clean-redesign/a1f7c1f7b6e1e93400a4372b8e228d3908a1a966/generate_evidence.py`
ran against implementation target
`a1f7c1f7b6e1e93400a4372b8e228d3908a1a966` on 2026-07-16. It first verified
that `HEAD` matched the target and that production and test paths had no tracked,
staged, or untracked differences from it. The recorded results are:

- the five compensation, item-change safety, rotation-property,
  translation-pipeline, and transactional `set_size()` regression modules:
  29 tests and 151 subtests passed under each of PyQt5, PyQt6, and PySide6;
- every `test_text_transform*.py` and `test_textitem*.py` module under PyQt6:
  209 tests and 409 subtests passed;
- the current full PyQt6 suite: 7 failed, 374 passed, 1 skipped, and 446
  subtests passed;
- the exact `6155f9b303033b24f57a2c025d2edbfed3eb847f` upstream full PyQt6
  suite under the same executable, dependency overlay, and environment:
  7 failed, 159 passed, 1 skipped, and 7 subtests passed;
- both full-suite JUnit files contain the same seven failure identities, so the
  target introduces no new full-suite failure relative to the fixed baseline;
- all 14 production Python modules changed from the fixed baseline passed an
  in-memory syntax compilation check, and the relevant `FontFormat`,
  text-transform, and translation-helper doctest items passed.

Raw logs, JUnit XML, environment data, Git provenance, hashes, and the generated
summary are stored under that target-specific artifact directory. These are
automated results, not independent reviewer approval or a substitute for the
remaining manual GUI work.

### Manual status

The record contains one post-fix user confirmation and one post-fix screenshot
comparison supplied by the user in the development conversation:

- after the rotation-composition correction, the user confirmed that the
  reported non-uniform-scale rotation deformation was fixed and instructed that
  the change be committed;
- after the Effect-pipeline correction, before/after screenshots from an actual
  detection, OCR, translation, and inpainting run showed the result block
  carrying the configured Horizontal Scale, Vertical Scale, Box Slant, Glyph
  Slant, Gradient enabled state, Shadow radius, and other visible effect values.
  Separate code/config inspection explained the remaining Font Family and
  Alignment differences as their configured `Keep existing` and
  `decide by program` policies; that interpretation is not a second manual pass.

Those screenshots remain conversation attachments and were not copied into the
repository. The record is therefore a user attestation, not a self-contained or
independently reproduced GUI bundle; displayed values do not prove every paint,
persistence, export, or override branch. Before PR submission, the remaining
manual checks include:

- broader lifecycle coverage beyond the confirmed rotation case: anisotropic
  H/V Scale with nonzero rotation and Box Slant during handle drag, commit,
  undo/redo, save, restart, and reload;
- a complete detection/OCR/translation/inpainting run covering global Gradient
  disable, nonzero Shadow offset, canvas paint, saved project, reload, and final
  export in addition to the already observed result-panel values;
- Effect override disabled, **Run without update textstyle**, and inpaint-only
  runs, confirming their preservation boundaries;
- Font Family and Alignment with both available override-policy choices so the
  UI policy and resulting block values are unambiguous.

### Superseded evidence and pending review

The tracked artifact bundle under
`artifacts/pr1238-clean-redesign/f101479edbaf4b35606fe7503ae752846055d71d/`
attests only the older `f101479edbaf4b35606fe7503ae752846055d71d` code. Its
three-control screenshots, schema-v1 fixture, focused-test totals, provenance,
and independent approval predate the four-component/schema-v2 redesign, the
rotation compensation, and the Effect/Gradient pipeline fix. It is therefore
superseded and must not be presented as verification of the current target.

A fresh automated artifact set now exists under the current implementation SHA.
Independent final review of the exact implementation, this documentation, and
the artifact bundle remains pending. Review must continue to distinguish
automated offscreen evidence, user attestation, and independently reproduced
human-equivalent GUI execution.
