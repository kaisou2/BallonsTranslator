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
After the thin-stroke and native-glyph guards, one fresh follow-up run per
condition completed UI processing in 1.85 seconds without saving and 2.04 seconds
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
was remeasured after the thin-stroke and native-glyph guards; the wrapped and
vertical figures are from the earlier comparison.

| Fixture / operation | Baseline | Optimized |
| --- | ---: | ---: |
| 512 repeated zeros, construct | 1,165 ms | 83 ms |
| Same item, insert one character | 1,169 ms | 80 ms |
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
coverage for thin outlines. Widgets and any device whose matrix
contains more than the world transform and pixel-ratio scale bypass the proxy.
Widget backing-store offsets and logical sizes are not raster-surface geometry.
Window/viewport transforms, selections, and IME preedit also bypass it. Before
forwarding, a dry paint checks for native glyph items from mixed formats; those
layouts draw directly because PyQt cannot forward shaped text items through
QPainter. Both passes inherit the caller's complete painter state, and the
probe never touches the destination. A small bounded cache reuses contours
only after exact Qt path equality.
The document, text, undo history, logical geometry, and effect cache keys retain
their existing owners.

## Verification and limits

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
  match. Export differs only at four outer-page-edge pixels by at most 1/255
  per color channel, from Qt's clipped-curve rounding. Interior pixels match.
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
- Touched Python files compile and `git diff --check` passes.

Complete page navigation still includes image processing, widget construction,
and saving. The observed 1.9–2.0 / 2.3–2.4 seconds include that remaining work.
Wrapped/vertical text and complex effects
can still pause during editing. Font choice, dimensions, effects, Qt backend,
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
