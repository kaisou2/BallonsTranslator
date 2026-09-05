# Long-text performance

The benchmark covers saved-text construction, offscreen scene painting, and
local character insertion. It does not run OCR, translation, image loading,
or the complete application page-switch workflow.

## Measurement

Measured on Windows, Python 3.10.6, Qt 6.11.1 / Qt 5.15.2, against upstream
`682c752` (v1.5.14). Values below are medians of three warm construction runs,
including fresh TextBlock/TextBlkItem creation. Existing font and Qt caches
remain warm, as on repeated page navigation. Timings vary with fonts and hardware.

| Fixture | Qt | Before | After | Speedup |
| --- | --- | ---: | ---: | ---: |
| Supplied page 16, 14 text blocks | 6 | 1,523 ms | 264 ms | 5.8x |
| Same page | 5 | 1,394 ms | 274 ms | 5.1x |
| One supplied 512-character repeated-zero block | 6 | 686 ms | 103 ms | 6.7x |
| Synthetic 1,500-character Korean afterword | 6 | 173 ms | 84 ms | 2.1x |
| Same synthetic Korean text, vertical | 6 | 637 ms | 341 ms | 1.9x |

The supplied page contains two 512-character strings with 511 zeros each,
in logical boxes approximately 7,124 pixels wide. Rich-text restoration was
repeatedly rendering large stroke surfaces during synchronous formatting
signals. A profiled Qt 6 page construction produced 102 full stroke layers;
the optimized construction produces 14, one per text item. Document changes,
layout, geometry updates, and text content retain their normal ownership.

For independent horizontal reflow measurements (Arial 24, 600-pixel box,
Qt 5, 20 warm iterations), 700 / 4,000 / 20,000 characters took
1.235 / 10.18 / 140.97 ms before and 0.748 / 5.11 / 53.30 ms after.
The layout now indexes format runs instead of allocating a map entry for every
UTF-16 unit, finds line font metrics by run, and avoids repeatedly copying a
whole paragraph for each wrapped line. Empty Ruby layout skips its optional work.

## Reproduce

Use the application's existing dependencies. No additional runtime dependency
is required. From the candidate checkout, select either Qt binding and compare
against an untouched checkout using the same benchmark script:

```powershell
$env:QT_API = 'pyqt6' # repeat with pyqt5
python scripts/benchmark_long_text.py --repeat 5
python scripts/benchmark_long_text.py --source-root PATH_TO_BASELINE --repeat 5
python scripts/benchmark_long_text.py --project-json PATH_TO_PROJECT_JSON --page 16 --repeat 5
```

`--page` selects the one-based position in the JSON pages mapping.
`--profile` prints a construction profile to stderr. Output includes timing
samples, environment, block counts, and UTF-16 lengths. Input projects are
read-only; no source text, project paths, or page images are emitted in the
JSON timing report. Synthetic cases are reproducible without the private project;
the supplied-project timings above use its saved fonts and rich text.

## Verification and limits

- Ten added regression tests pass on both bindings: completed import renders
  once, subsequent edits and undo repaint correctly, rich formatting and active
  transforms survive, UTF-16/IME lookup stays compatible, and long horizontal
  reflow avoids per-character font queries and per-line paragraph copies.
- Compared 402 existing layout, annotation, Ruby, effect, preview, alpha-mask,
  and transform tests against the untouched baseline under each binding.
  Failure/error/skip results are identical. These suites are **not entirely
  green** on this machine; no new failures were introduced by this patch.
- Before/after offscreen page and Korean fixture renders have zero differing
  pixels, and plain text, Qt HTML, logical rectangles, and live bounds agree.
  All 14 complete page-item effect surfaces also have identical RGBA hashes and
  dimensions in each binding, including the offscreen portions of the two
  7,128-pixel-wide padded zero-block rasters.
- Touched Python files compile and `git diff --check` passes.

Warm page painting is already around 6 ms and is largely unchanged. Actual
character insertion still rebuilds one complete stroke surface: roughly
98 ms for the unusually wide zero block and 82 ms for the Korean fixture
in this environment. This patch substantially reduces page construction and
layout overhead; it does not eliminate native Qt stroke cost for every edit.
Complex effects and very large text can therefore still pause while editing.
Full foreground application interaction, including the user's exact installed
font setup and image loading, has not been manually verified.

Focused checks:

```powershell
python -m unittest discover -s tests -p test_text_item_initialization.py
python -m unittest discover -s tests -p test_text_layout_performance.py
```

Existing comparison suites: `test_horizontal_whitespace`,
`test_vertical_alignment`, `test_vertical_interaction`,
`test_vertical_roman_alignment`, `test_rich_text_annotations`,
`test_ruby_furigana`, `test_typed_text_effect_renderer`,
`test_text_effect_preview`, `test_text_alpha_mask_renderer`, and
`test_text_transform_undo`.
