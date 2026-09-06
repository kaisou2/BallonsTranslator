# Long-text performance

Measure page-switch latency through the desktop application's real caller chain.
The isolated item benchmark also supports the desktop font backend with
`--native`; its default offscreen backend can substantially underestimate native
outline cost and excludes page images, paired editors, and automatic saving.

## Desktop page transition

Measured on Windows 10, Python 3.10.6, PyQt6 / Qt 6.11.1. The comparison baseline
is `8b28633`, which already batches saved-text effects and indexes layout by
format run. Each condition alternates baseline and widget-corrected code (`a1756c7`)
three times in fresh processes with the same dependencies, fonts, configuration,
and copied project. Values below are medians; desktop/widget work adds variance.

| Action / measurement endpoint | Baseline | Optimized |
| --- | ---: | ---: |
| Click box 13 on page 15 and advance: first canvas frame | 4.19 s | 1.30 s |
| Same action: pending UI event processing completed | 4.20 s | 1.99 s |
| Unsaved change requiring automatic saving: first canvas frame | 3.88 s | 1.55 s |
| Same unsaved case: pending UI event processing completed | 4.65 s | 2.31 s |

The optimized event-processing endpoint ranges were 1.91–2.01 seconds without
saving and 2.31–2.40 seconds with saving. Use that endpoint when describing
complete UI response; first-frame timings alone omit subsequent event work.
After the contour-key and device-edge corrections, one fresh follow-up run per
condition completed UI processing in 1.84 seconds without saving and 2.02 seconds
with saving. These single-run checks confirm retained acceleration rather than
replacing the repeated-sample ranges above.

Timing starts before `QTest.mouseClick()`. The first-frame endpoint is after
the destination canvas's first `paintEvent()`; the event-processing endpoint
is after the following `QApplication.processEvents()` returns. The normal
`shortcutNext()` application slot handles the
transition. The unsaved case first moves the selected item by one pixel through
the canvas undo command. Image loading, paired-editor construction, required
saving, and the first canvas paint are included; screenshot capture is excluded.
The destination contains 14 blocks, including two 512-character strings with
511 zeros each in logical boxes approximately 7,124 pixels wide.

## Isolated native text items

The following synthetic cases use Malgun Gothic with the same Qt 6 Windows
backend and baseline. Construction uses three fresh items with warm Qt/font
caches; insertion and repaint use 15 samples. These measurements isolate text
work and do not predict complete application response time. The zero fixture
was remeasured after the contour-key and device-edge corrections; the wrapped and
vertical figures are from the earlier comparison.

| Fixture / operation | Baseline | Optimized |
| --- | ---: | ---: |
| 512 repeated zeros, construct | 1,165 ms | 84 ms |
| Same item, insert one character | 1,169 ms | 81 ms |
| 1,500-character wrapped Korean paragraph, construct | 257 ms | 253 ms |
| Same paragraph, insert one character | 254 ms | 252 ms |
| Same paragraph, warm paint | 33.4 ms | 33.4 ms |
| Vertical Korean paragraph, construct | 435 ms | 428 ms |

The additional native optimization targets expensive long shaped lines. Short
wrapped lines continue through Qt directly; forwarding them through a Python
paint engine costs more than it saves. Long Korean paragraphs still benefit from
the saved-text construction and format-run layout optimizations already present
in the comparison baseline. Vertical native rendering keeps its existing path.

## Ownership and mechanism

Saved-text construction keeps document signals and layout live while restoring
HTML, annotations, and the initial cursor, then builds one completed effect
surface per item. Fragment lookup uses layout-owned UTF-16 format-run ends;
line metrics visit intersecting runs, and horizontal reflow shares one paragraph
text snapshot. These changes avoid repeated intermediate rasterization and
unnecessary per-character/per-line work.

For a long horizontal native-outline line on an independent QImage/QPixmap,
`rendering/native_paint.py` receives Qt's own shaped paths and partitions their
closed contours into device-aligned strips. Each strip includes every
overlapping contour and stroke overhang, with
one disjoint pixel clip. Curve coordinates, fill rules, holes, and overlapping
glyphs retain their native representation. Open, short, rotated/sheared, or
unsupported pen paths keep native drawing. Visible strokes at or below one
device pixel also keep native drawing, because strip clipping changes Qt's
coverage for thin outlines. Vertical clipping includes the visible stroke's
device-space overhang, including cosmetic widths and join reach. Horizontally
clipped fills with no visible stroke also retain direct Qt drawing. These
coverage differences can otherwise survive tile composition and positioned
stroke masks. Visible horizontal stroke clipping keeps partitioning so large
clipped stroke passes retain their acceleration. Widgets and any device whose matrix
contains more than the world transform and pixel-ratio scale bypass the proxy.
Widget backing-store offsets and logical sizes are not raster-surface geometry.
Window/viewport transforms, selections, and IME preedit also bypass it. Device
ratios that cannot be represented exactly by the proxy's
fixed-point DPR metric retain the real paint device. Values such as 1.25 and 1.5
remain eligible. Before forwarding, a dry paint checks for native glyph items
from mixed formats; those
layouts draw directly because PyQt cannot forward shaped text items through
QPainter. Both passes inherit the caller's complete painter state, and the
probe never touches the destination. A small bounded cache reuses contours
only after exact serialized path-data equality. Qt's approximate equality can
accept tiny coordinate changes that cross a raster rounding boundary. The
serialized keys remain process-local and bounded by the same element/entry
limits; they are not project data.
The document, text, undo history, logical geometry, and effect cache keys retain
their existing owners.

## Verification and limits

- All 29 native-rendering regressions pass on the final source under both Qt 5
  and Qt 6 with the Windows backend, run separately to avoid GUI focus conflicts.
- Eight native-path regression tests pass on both PyQt5 and PyQt6 using both
  offscreen and Windows backends. They cover overlapping curves, holes,
  fractional device scales, rich shaping, selections, clipping, painter-state
  restoration, bounded reuse, editing, undo, and export.
- Three widget/raster regressions compare parent-window captures with an offset
  child, graphics-view zoom and all three Qt item cache modes, and retained
  QImage/QPixmap acceleration. They fail on the displaced/clipped text produced
  by the earlier unrestricted proxy. Widget/cached comparisons require exact
  pixels; standalone raster tests allow only outermost-column rounding of 1/255.
- Compared 144 complete-parent captures against upstream `682c752`: both Qt
  bindings, display scales 1 / 1.25 / 2, view zooms 0.44 / 0.66 / 1 / 1.5, saved
  and synthetic text, and idle/selected/editing states all match exactly.
  The reported two-item project also matches the original application in four
  full-window-derived viewport captures, including its selection guides.
  Comparing only an effect image or calling `viewport.grab()` is insufficient:
  it can remove the backing-store offset responsible for a live text mismatch.
- Compared 412 existing layout, annotation, Ruby, effect, preview, alpha-mask,
  transform, initialization, and performance tests against `8b28633` on both
  bindings. Results match exactly: Qt 5 has 63 failures and one error; Qt 6 has
  23 failures; each has 12 skips. These suites are not entirely green on this
  machine; the comparison found no new failures or errors.
- All 12 desktop comparison runs selected box 13 and loaded 14 destination
  items. All complete item-effect RGBA surfaces, dimensions, and text hashes
  match. The former four outer-page-edge pixel differences in this fixture are
  eliminated by the clipped-fill guard; three repeated page exports match the
  direct Qt image exactly. Standalone visible-stroke raster tests still allow
  outermost-column rounding of at most 1/255.
- Thin-stroke regressions use actual text items with italic and mixed saved
  font/size runs, widths 0.01/0.02/0.1, editing, and undo. The new cases detect
  the earlier path splitter's interior coverage differences on both bindings.
- Isolated live-view regressions exercise Bend/Sine/Grid with Hollow/Gradient,
  long numeric and mixed Unicode lines, selection, and effect images. They
  detect the earlier native text-item callback crash on both bindings and
  require exact pixels against Qt's direct path after the fix.
- Four painter-state regressions require exact pixels for inherited foreground,
  mixed outlined/native glyphs, format overrides, selections, and IME preedit.
  They check unchanged caller state, retained outline acceleration, and identical
  primitives in the dry and real passes; native glyph fallback must begin before
  any destination paint. All 112 pairs pass across both bindings and backends.
- Differential audits against upstream cover format lookup, UTF-16 cursor and
  hit testing, Ruby, initialization, editing/undo, transforms in both orders,
  effect stacks, masks, resource restore, and export. Only differences caused
  by this optimization are findings; unchanged upstream failures stay separate.
- Real tile-policy page tests cover Inside Stroke at scales 1 / 1.25 / 2 and
  compare final page pixels, alongside full-surface and Outside Stroke controls.
  A font-independent clipped-ellipse test covers both the top and bottom edge.
  Both variants detect the earlier splitter. Additional boundary tests cover
  horizontal fills, visible vertical stroke overhang, reflection, nonuniform
  scales, cosmetic and miter pens, and retained acceleration.
- Named-font render comparisons use Windows and verify available families and
  actual glyph IDs. The default offscreen backend can have an empty font
  database and render replacement boxes, which is useful for structural checks
  but insufficient evidence for real glyph equivalence. The new tile scene
  tests skip explicitly when their required font/glyphs are unavailable; the
  font-independent curve test remains active.
- Additional Windows differential checks cover 1,134 edit/cache states, including
  equal-metric/different-contour candidates and eviction. Repeated Qt 5 emoji
  captures can also vary in the unmodified baseline; such variations are not
  counted as optimization regressions without reproducible attribution.
- Re-ran the 412-test comparison with the Windows backend. Established failures
  are 9 on Qt 5 and 6 on Qt 6. Concurrent GUI tests also caused isolated clipboard
  and IME failures; each passed on baseline and candidate when rerun separately,
  without source changes. The latest Qt 5 batch therefore records 10 failures,
  including that separately passing IME test. No new reproducible failure was
  found; the broader Windows regression suite is not entirely green either.
- Two font-independent precision regressions verify contour coordinates and
  cold/warm image equality for paths with equal bounds/counts and a minute vertex
  change. They detect a five-pixel cache-history error in the approximate-key
  implementation on both bindings. This is a cache-unit finding; an editor UI
  sequence producing it has not been established.
- Two device-ratio regressions compare QImage/QPixmap output and painter state
  at ratios 1.1 / 1.2 / 1.3 / 4/3, while checking retained acceleration at
  1 / 1.25 / 1.5 / 2. The former ratios expose a proxy-metric truncation error
  on Qt 6. A real-app check at display scale 1.3 used native widget painting and
  effect pixmaps at ratio 1, so exposure through the normal editor was not
  established. The guard fixes the independently reproduced helper defect.
- Additional Windows checks compare 192 RGB/RGBA, opacity and fractional-offset
  renders; 298 document states with nested lists/tables/frames, many format runs,
  Unicode separators and IME edits; and 42 real MainWindow lifecycle states.
  All match the original comparison path. The real-app runs also retain identical
  saved results and release replaced items after pending input callbacks.
- Touched Python files compile and `git diff --check` passes.

Complete page navigation still includes image processing, widget construction,
and saving. The observed 1.9–2.0 / 2.3–2.4 seconds include that remaining work.
Wrapped/vertical text and complex effects
can still pause during editing. Correctly clipped visible strokes can revert to
native cost: a 400-character CJK Center-stroke tile example increased from about
0.19 to 0.57 seconds per export after the vertical-overhang guard. The supplied
page has no such clipped stroke paths and retains its acceleration. Font choice, dimensions, effects, Qt backend,
machine load, and hardware affect timings. OCR, translation services, and model
loading were outside this verification.

## Reproduce

Use the application's existing dependencies; no additional runtime dependency
is required. From the candidate checkout:

```powershell
$env:QT_API = 'pyqt6' # repeat with pyqt5
python scripts/benchmark_long_text.py --native --repeat 3
python scripts/benchmark_long_text.py --native --source-root PATH_TO_BASELINE --repeat 3
python scripts/benchmark_long_text.py --native --project-json PATH_TO_PROJECT_JSON --page 16 --repeat 3
```

Omit `--native` for offscreen comparisons. `--page` selects the one-based position
in the JSON pages mapping; `--profile` prints an item-construction profile to
stderr. Input projects are read-only. Reports include backend, timing samples,
block counts, and UTF-16 lengths, without source text, project paths, or images.
Synthetic cases require no private project. The complete-app measurements above
use a local two-page copy and the application's normal font/startup setup.

```powershell
$env:QT_API = 'pyqt6' # repeat with pyqt5
python -m unittest discover -s tests -p 'test_native_*.py'
python -m unittest discover -s tests -p test_text_item_initialization.py
python -m unittest discover -s tests -p test_text_layout_performance.py
$env:QT_QPA_PLATFORM = 'windows'
python -m unittest discover -s tests -p 'test_native_*.py'
```
