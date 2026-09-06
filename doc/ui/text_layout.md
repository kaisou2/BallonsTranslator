# Text layout

Read [Text engine](text_engine.md) first. This guide records the behavior and
ownership shared by shaping, wrapping, vertical flow, painting, and editing.
Implementation-specific algorithms belong in code comments and focused tests.

## Mental model

```text
QTextDocument rich text (Qt UTF-16 positions)
  -> SceneTextLayout fragment metrics
  -> horizontal lines or vertical columns
  -> settled placement
  -> fill, effects, annotations, cursor, selection, and hit testing
  -> TextItemGeometryController bounds and visual mapping
```

Qt remains the editable text model and shaper. The custom layouts place Qt
`QTextLine`s; they do not create a second text representation.

## Core contract

- `FontFormat` supplies item-wide writing mode, alignment, and compatibility
  defaults. `QTextDocument` formats own range-bound typography and
  paragraph-bound line spacing.
- Placement records, ink bounds, and caches are derived. Rebuild them together
  for one settled layout generation and never persist them.
- Fill, effects, annotations, cursor, selection, hit testing, and visual bounds
  must consume the same settled cells and transforms.
- Qt positions are UTF-16 code units. Use the shared UTF-16 and grapheme helpers
  wherever Python strings meet Qt positions; never expose a caret inside a
  surrogate pair or combined run.
- Effect padding and visible ink overflow belong to source geometry, not the
  persistent logical rectangle.

`TextBlock.text_layout_version` versions item-wide layout semantics. Missing or
version-zero vertical blocks migrate to right alignment, matching their earlier
effective placement. Inline HTML extensions remain versionless and follow the
compatibility rules in [Text engine](text_engine.md).

## Writing modes

### Horizontal

`HorizontalTextDocumentLayout` keeps Qt shaping, glyph runs, cursor behavior,
and word-boundary wrapping. It adds only the geometry Qt does not expose in the
form the editor needs. In particular, overflowing trailing U+0020 spaces stay
in the document but receive derived continuation-row cells so wrapping, box
growth, cursor, selection, and hit testing agree. Other Unicode separators keep
Qt behavior.

Character spacing and font features are applied per range. Identity spacing is
left unset when common ligatures should remain available because an explicit Qt
spacing property may suppress optional ligatures. Version-specific feature-tag
handling stays inside the layout/annotation boundary.

### Vertical

`VerticalTextDocumentLayout` normally creates one cell per grapheme and places
columns from right to left. Punctuation orientation and alignment are semantic
classes near the top of `vertical_layout.py`; extend those classes instead of
adding paint-time glyph exceptions.

Standard Roman mode keeps proportional Roman glyphs upright and centered. The
alternate mode rotates them clockwise and uses the Chinese mixed-layout
punctuation path. Compact punctuation shortens eligible punctuation cells
without clipping their ink. Repeated dashes, bars, leaders, and ellipses form
indivisible runs, with character spacing applied after the run.

Tate-chu-yoko is a horizontal Qt run occupying one vertical flow cell. Its
layout ignores authored letter spacing and uses the font's half-width
punctuation plus matching half-, third-, or quarter-width feature when
available. Standard Roman mode keeps that shaped run's natural horizontal
width; the alternate mode horizontally scales any remaining excess to one em.
The resulting visible ink is centered without changing the stored text. Glyph
ink may overhang the column, but that overhang affects only painting and
interaction bounds, never neighboring columns.

Ruby/furigana is attached layout content, not a detached overlay. Group Ruby is
indivisible; mono Ruby may wrap only between base/reading pairs. Each unit uses
the larger of its base and annotation advances, and the shorter run is spaced
within that cell. Horizontal Ruby appears above or below; vertical Ruby remains
upright on the right or left. The same cells own wrapping, paint, selection,
cursor, hit testing, effects, and visible bounds. Ruby and tate-chu-yoko cannot
overlap, and automatic Ruby overhang is not supported.

## Flow and spacing

Whitespace remains document content and must consume explicit editable cells.
Horizontal and vertical layouts may represent those cells differently, but
neither may move whitespace into a second text model or drop it from cursor and
hit geometry. Vertical whitespace contributes flow advance, not the ink bounds
used to center the neighboring glyph in its column.

Character spacing is a trailing advance for the affected glyph or joined run;
W3C tate-chu-yoko composition ignores it. On a squeezed single-column vertical
item, increasing it may grow the logical height to preserve that column;
multi-column items keep normal fixed-area reflow, and automatic growth never
silently shrinks the box.

Line spacing is owned by the destination row or column. The first visual row or
column stays anchored without leading spacing; each later one uses its
paragraph's spacing value and mode. Paragraph boundaries do not restart this
visual leading-edge rule.

Settle a layout as one transaction. Wrapping, whitespace, annotations,
fragment metrics, UTF-16 positions, ink bounds, and interaction geometry are
coupled and must be published only when complete.

## Alignment and resize

Vertical alignment translates settled columns horizontally:

| Alignment | Fixed growth anchor | Added-width movement |
| --- | --- | --- |
| Left | Top-left | Columns stay fixed and grow rightward |
| Center | Top-center | Columns move by half and grow evenly |
| Right | Top-right | Columns move with the right edge and grow leftward |

Alignment changes every placement and ink-bound record together but does not
reshape text or change document content. A width-only resize may reuse that
translation when the settled content still fits; height, padding, or flow
changes require full layout. The geometry controller preserves the matching
scene-space anchor, so layout and scene movement must not both compensate for
the same resize.

## Painting and interaction

`vertical_line_placement()` is the shared boundary for rotated glyphs,
tate-chu-yoko, emphasis, Glyph Slant, and effects. Cursor, selection, and hit
testing must use the same placement. Ligatures and joined glyphs may change
shaping, but they do not change the logical UTF-16 editing range.

Document backgrounds paint below selection, and glyph ink paints above it.
Foreground and effect layouts must reuse the same settled offsets. Caches tied
to absolute placement must be invalidated with the layout generation and must
not retain records from a replaced document layout.

Long horizontal native-outline lines use `rendering/native_paint.py` on
independent QImage/QPixmap surfaces to bound path rasterization while retaining
Qt's shaped contours. Widget painters, additional device transforms, selections,
IME preedit, and short wrapped lines stay on direct Qt drawing. Raster device
ratios that the proxy's fixed-point metric cannot represent exactly also retain
direct drawing. A dry paint
checks Qt's resolved primitives before touching the destination; layouts that
include native glyph items bypass forwarding to preserve Qt's glyph rendering.
Visible outlines at or below one device pixel also bypass path partitioning.
Vertical device clipping includes visible stroke overhang. Horizontally
clipped fills with no visible stroke also retain direct Qt drawing; coverage
differences at these edges can survive tile composition. A widget's backing-store offset and
logical device size must never be treated as an independent raster's geometry.
Contour reuse requires exact serialized path data; approximate Qt path equality
can alias coordinates on opposite sides of a raster rounding boundary.
The helper owns only transient painter forwarding and bounded contour reuse;
it must not reshape text or alter document state. Screen regression checks must
capture the parent window with an offset child viewport: grabbing only the
viewport can remove the device offset and hide a foreground/effect mismatch.
See [Long-text performance](../performance/long_text.md) for measurement and
output-equivalence coverage.

## Invalidation and verification

The normal path is:

```text
document or format change
  -> rebuild fragment metrics and position maps
  -> settle lines or columns
  -> update draw offsets and ink bounds
  -> publish size and refresh geometry/effects
```

Format metrics are shared only within one rebuild. Unchanged one-line horizontal
paragraphs may reuse Qt shaping when their text, formats, layout options, width,
and device metrics match; absolute positions are recalculated each generation.
Edits invalidate affected paragraphs, while document structure and global layout
changes discard the retained entries. Wrapping, Ruby, emphasis, IME preedit, and
transient layout formats retain the full shaping path.

Vertical layout can also reuse unchanged short paragraphs that fit one column.
Its keys include flow height, padding, typography, orientation policy, and device
metrics; edits that preserve paragraph count can retain unaffected paragraphs.
Each pass checks actual paragraph inputs and live Qt lines, then reconstructs
absolute positions before the normal alignment, ink-bound, and size-publication
steps. Cached local records and normalized draw offsets contain no native
line/layout handles. Only the renderer's exact transparent Stroke-alignment
format is accepted; other transient formats, inline objects, Ruby, tate-chu-yoko,
emphasis, IME preedit, and delegated glyph rendering retain full shaping.

Test relationships rather than exact font-dependent pixels. Cover the affected
writing modes, alignments, spacing, annotations, effects, UTF-16 text, editing,
resize, and mode switches. Focused coverage lives in:

- `tests/test_horizontal_whitespace.py`
- `tests/test_vertical_alignment.py`
- `tests/test_vertical_interaction.py`
- `tests/test_vertical_roman_alignment.py`
- `tests/test_rich_text_annotations.py`
- `tests/test_ruby_furigana.py`
- `tests/test_multiline_layout_reuse.py`
- `tests/test_vertical_layout_reuse.py`

Run both PyQt5 and PyQt6 when layout lifetime, shaping, cursor geometry, or
painting behavior changes.
