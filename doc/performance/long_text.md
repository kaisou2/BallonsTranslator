# Long-text performance

Measure page-switch latency through the desktop application's real caller chain.
Isolated item runs omit images, paired editors, and automatic saving. Use
`--native` for the desktop font backend; offscreen can underestimate outline cost.
See [Text engine](../ui/text_engine.md#invalidation-and-performance)
for construction/layout ownership and [Text layout](../ui/text_layout.md)
for native-paint eligibility, fallback, clipping, and contour-cache contracts.

## Desktop page transition

Measured on Windows 10, Python 3.10.6, PyQt6 / Qt 6.11.1. The performance baseline
`8b28633` already batches saved-text effects and indexes layout by format run;
upstream `682c752` is the original-behavior audit baseline. The table alternates
`8b28633` and widget-corrected `a1756c7` three times in fresh processes with the
same dependencies, fonts, configuration, and copied project. Values are medians.

| Action / measurement endpoint | Baseline | Optimized |
| --- | ---: | ---: |
| Click box 13 on page 15 and advance: UI event processing completed | 4.20 s | 1.99 s |
| Same action with an unsaved change requiring automatic saving | 4.65 s | 2.31 s |

Optimized event-processing ranges were 1.91–2.01 seconds without saving and
2.31–2.40 seconds with saving. A cleanup comparison alternated `a70cb22` and
`c58e3d0` in three fresh processes per version and condition. Complete response
was 1.869 → 1.854 seconds without saving and
2.023 → 2.031 seconds with saving: effectively unchanged within desktop variance.

Timing starts before `QTest.mouseClick()` and uses the normal `shortcutNext()`
slot. Complete response is measured after the following
`QApplication.processEvents()` returns. The unsaved case first moves the selected
item one pixel through a canvas undo command. Image loading, paired editors,
required saving, and paint are included; screenshot capture is excluded.
The destination has 14 blocks, including two 512-character strings with 511
zeros each in logical boxes approximately 7,124 pixels wide.

## Many short paragraphs

A saved page containing 600 characters in 150 short horizontal paragraphs
(151 Qt blocks including the final empty paragraph) was measured with Noto Sans
CJK KR, 20-pixel text, and an outside stroke. Three alternating fresh-process
pairs compared `a16aa81` with the current implementation on the same Qt 6 Windows
backend. Timing covers the normal previous-page shortcut and subsequent UI
event processing; capture and export are outside the timed interval.

| Measurement | Before | After |
| --- | ---: | ---: |
| Page load, median | 5.426 s | 1.564 s |
| Page load, range | 5.422–5.482 s | 1.564–1.574 s |
| First character insertion, median | 139.5 ms | 141.0 ms |
| Warm insertion, median of five later edits per process | 99.7 ms | 102.9 ms |

This improves loading by about 3.5 times; it does not demonstrate an editing
speedup for this fixture. All six runs produced identical effect surfaces,
complete page exports, and edited text. The largest shaped line contains only
three UTF-16 units, so long-outline strip painting does not accelerate it.
Separate profiles retain all 609 full-layout notifications while reducing
native `QTextLayout.beginLayout()` calls from 91,364 to 2,262 by reusing unchanged
single-line paragraphs. Format metrics are also shared within each rebuild.
Full document traversal remains; the optimization does not make the entire
import linear in paragraph count.

The synthetic `--case short-lines` fixture reproduces this input pattern without
private project data. Its isolated-item timing excludes application page work.

## Isolated native text items

Malgun Gothic, the same Qt 6 Windows backend, and baseline `8b28633` are used.
Construction uses three fresh items with warm Qt/font caches; insertion and
paint use 15 samples. The zero fixture includes the precision corrections;
wrapped and vertical figures are from the earlier comparison.

| Fixture / operation | Baseline | Optimized |
| --- | ---: | ---: |
| 512 repeated zeros, construct | 1,165 ms | 84 ms |
| Same item, insert one character | 1,169 ms | 81 ms |
| 1,500-character wrapped Korean paragraph, construct | 257 ms | 253 ms |
| Same paragraph, insert one character | 254 ms | 252 ms |
| Same paragraph, warm paint | 33.4 ms | 33.4 ms |
| Vertical Korean paragraph, construct | 435 ms | 428 ms |

The native optimization targets expensive long shaped lines. Short wrapped
lines and vertical rendering retain their existing Qt path. Wrapped Korean
already benefits from construction/layout optimizations in `8b28633`; these
isolated timings do not predict complete application response.

The `a70cb22` → `c58e3d0` check used four process pairs in alternating order,
each constructing five zero items and making 25 edits. Medians of process medians
were 84.26 → 84.42 ms for construction and 81.11 → 81.28 ms for insertion.
Wrapped/vertical controls also retained their performance.

## Verification and limits

Regression suites and differential audits cover the following on both bindings:

- The four paragraph-reuse checks pass on both Windows bindings. Differential
  comparisons of 40 rich-text fixtures at construction/reload and 104 editing
  states per binding preserve geometry, cursor positions, formats, and pixels.
  They include opposing spacing changes, narrow boxes, mixed fonts, annotations,
  resize, undo/redo, writing-mode changes, and actual IME preedit/commit events.
- All 27 native regressions pass on Qt 5/6 Windows. They cover
  contours/holes, shaping, thin strokes, clipping and tile composition,
  device scales, painter state, mixed glyph fallback, editing/undo, and export.
  Overlapping setup is shared; distinct failure cases remain separate.
  Run GUI suites separately: focus, clipboard, and IME are shared resources.
- Earlier upstream comparisons cover screen paint, editing, UTF-16/Ruby,
  transforms/effects, cache reuse, document and MainWindow lifecycle, pending
  edits, and saved results. Screen checks capture the parent window with an
  offset child; `viewport.grab()` can hide backing-store offsets.
- All 12 cleanup timing runs preserved item-effect RGBA surfaces, dimensions,
  text hashes, layouts, and complete page exports against `a70cb22`.
  Standalone visible-stroke raster checks still allow outermost-column rounding
  of at most 1/255; this is not a whole-image equality claim.
- Actual `result/15.png` and `result/16.png` files written by `manual_save()`
  match upstream `682c752` byte for byte at 1440×2048 under the same Qt 6, fonts,
  project, and PNG settings. This is evidence for that fixture, not every font.
- Precision guards have narrower proven scope: exact contour keys fix a
  five-pixel cache-history error in helper tests, and device-ratio fallback fixes
  Qt 6 proxy truncation at 1.1 / 1.2 / 1.3 / 4/3. Neither defect has an established
  normal-editor reproduction. A real-app check at display scale 1.3 used native
  widget painting and effect pixmaps at ratio 1. Ratios 1 / 1.25 / 1.5 / 2 retain
  acceleration in helper tests.

Broader suites are not fully green. The current 402-test Windows run reports
six failures per binding and no errors. Qt 5 matches `a16aa81`; Qt 6 retains its
five baseline failures and also hit an intermittent selection-color assertion.
The same assertion failed in the older `a70cb22` baseline. In a current isolated
failure, the text items lacked focus while the check expected the active
selection color. Isolated runs also show intermittent results. Execution
conditions and Qt 5 emoji rendering can
affect results even upstream, so failures require reproducible attribution.

Named-font evidence requires the Windows backend, available families, and actual
glyph IDs. Offscreen may have an empty font database and draw replacement boxes;
that checks structure, not real glyph equivalence. Font-dependent tile tests
skip when required glyphs are absent; font-independent curve tests remain active.

Page navigation still includes image work, widget construction, and saving.
Wrapped/vertical text and complex effects can still pause during editing.
Correct stroke clipping can restore native cost: a 400-character CJK Center-stroke
tile increased from about 0.19 to 0.57 seconds per export after the vertical
overhang guard. The supplied page has no such clipped strokes and retains its
acceleration. Timings depend on fonts, dimensions, effects, backend, load, and
hardware. OCR, translation services, and model loading were outside this scope.

## Reproduce

Use the application's existing dependencies; no new runtime dependency is needed.
From the candidate checkout, repeat these commands with `QT_API=pyqt5`:

```powershell
$env:QT_API = 'pyqt6'
python scripts/benchmark_long_text.py --native --repeat 3
python scripts/benchmark_long_text.py --native --case short-lines --repeat 3
python scripts/benchmark_long_text.py --native --source-root PATH_TO_BASELINE --repeat 3
python scripts/benchmark_long_text.py --native --project-json PATH_TO_PROJECT_JSON --page 16 --repeat 3
$env:QT_QPA_PLATFORM = 'offscreen'
python -m unittest discover -s tests -p 'test_native_*.py'
python -m unittest discover -s tests -p test_text_item_initialization.py
python -m unittest discover -s tests -p test_text_layout_performance.py
python -m unittest discover -s tests -p test_multiline_layout_reuse.py
$env:QT_QPA_PLATFORM = 'windows'
python -m unittest discover -s tests -p 'test_native_*.py'
git diff --check
```

Omit `--native` for offscreen benchmarks. `--page` is the one-based position in
the JSON pages mapping; `--profile` prints an item-construction profile to stderr.
Projects are read-only. Reports include backend, samples, block counts, and
UTF-16 lengths without source text, project paths, or images. Synthetic cases
need no private project. Complete-app timings require a local two-page copy
and the application's normal font/startup setup; the item script omits that work.
