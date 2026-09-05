# Long-text performance

Measure page-switch latency through the desktop application's real caller chain.
The isolated item benchmark also supports the desktop font backend with
`--native`; its default offscreen backend can substantially underestimate native
outline cost and excludes page images, paired editors, and automatic saving.

## Desktop page transition

Measured on Windows 10, Python 3.10.6, PyQt6 / Qt 6.11.1. The comparison baseline
is `8b28633`, which already batches saved-text effects and indexes layout by
format run. Runs use fresh application processes with the same dependencies,
fonts, configuration, and copied project. Ranges include the comparison runs
and final verification; timings vary with desktop/widget work as well as text.

| Action | Baseline range | Optimized range |
| --- | ---: | ---: |
| Click box 13 on page 15, then immediately advance to page 16 | 3.54–3.96 s | 1.17–1.81 s |
| Same action with an unsaved change requiring automatic saving | 4.11–4.16 s | 1.78–2.38 s |

The original three-run comparison medians were 3.57 → 1.23 seconds without
saving and 4.14 → 1.79 seconds with saving. Two additional clean verification
runs took 1.81 / 1.78 seconds; one additional unsaved run took 2.38 seconds.

Timing starts before `QTest.mouseClick()` and stops after the destination canvas's
first `paintEvent()`. The normal `shortcutNext()` application slot handles the
transition. The unsaved case first moves the selected item by one pixel through
the canvas undo command. Image loading, paired-editor construction, required
saving, and the first canvas paint are included; screenshot capture is excluded.
The destination contains 14 blocks, including two 512-character strings with
511 zeros each in logical boxes approximately 7,124 pixels wide.

## Isolated native text items

The following synthetic cases use Malgun Gothic with the same Qt 6 Windows
backend and baseline. Construction uses three fresh items with warm Qt/font
caches; insertion and repaint use 15 samples. These measurements isolate text
work and do not predict complete application response time.

| Fixture / operation | Baseline | Optimized |
| --- | ---: | ---: |
| 512 repeated zeros, construct | 1,143 ms | 80 ms |
| Same item, insert one character | 1,141 ms | 77 ms |
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

For a long horizontal native-outline line, `rendering/native_paint.py` receives
Qt's own shaped paths and partitions their closed contours into device-aligned
strips. Each strip includes every overlapping contour and stroke overhang, with
one disjoint pixel clip. Curve coordinates, fill rules, holes, and overlapping
glyphs retain their native representation. Open, short, rotated/sheared, or
unsupported pen paths keep native drawing. Window/viewport transforms bypass the
proxy. A small bounded cache reuses contours only after exact Qt path equality.
The document, text, undo history, logical geometry, and effect cache keys retain
their existing owners.

## Verification and limits

- Eight native-path regression tests pass on both PyQt5 and PyQt6 using both
  offscreen and Windows backends. They cover overlapping curves, holes,
  fractional device scales, rich shaping, selections, clipping, painter-state
  restoration, bounded reuse, editing, undo, and export.
- Compared 412 existing layout, annotation, Ruby, effect, preview, alpha-mask,
  transform, initialization, and performance tests against `8b28633` on both
  bindings. Results match exactly: Qt 5 has 63 failures and one error; Qt 6 has
  23 failures; each has 12 skips. These suites are not entirely green on this
  machine; the comparison found no new failures or errors.
- All 12 desktop comparison runs selected box 13 and loaded 14 destination
  items. All complete item-effect RGBA surfaces, dimensions, and text hashes
  match. Export differs only at four outer-page-edge pixels by at most 1/255
  per color channel, from Qt's clipped-curve rounding. Interior pixels match.
- Touched Python files compile and `git diff --check` passes.

Complete page navigation still includes image processing, widget construction,
and saving. The observed 1.2–1.8 / 1.8–2.4 seconds include that remaining work.
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
python -m unittest discover -s tests -p test_native_path_paint.py
python -m unittest discover -s tests -p test_text_item_initialization.py
python -m unittest discover -s tests -p test_text_layout_performance.py
$env:QT_QPA_PLATFORM = 'windows'
python -m unittest discover -s tests -p test_native_path_paint.py
```
