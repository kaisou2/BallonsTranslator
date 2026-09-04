"""Measure real offscreen TextBlkItem construction and document edits.

Run in an installed application environment, not through the app launcher:
    QT_QPA_PLATFORM=offscreen python scripts/benchmark_text_layout.py --output result.json

Use --repo-root to compare a baseline worktree with the same interpreter/font.
The output is local UI CPU timing; it performs no OCR, translation or downloads.
"""
from __future__ import annotations

import argparse
import gc
import json
import math
import os
from pathlib import Path
import platform
import statistics
import sys
import time


def summarize(samples: list[float]) -> dict:
    ordered = sorted(samples)
    return {'samples_ms': samples, 'median_ms': statistics.median(samples),
            'p95_ms': ordered[math.ceil(len(ordered) * 0.95) - 1]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo-root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--chars', type=int, nargs='+', default=[1000, 5000])
    parser.add_argument('--samples', type=int, default=5)
    parser.add_argument('--vertical', action='store_true')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.samples < 1 or any(n < 1 for n in args.chars):
        parser.error('--samples and --chars must be positive')
    output = args.output.resolve() if args.output else None
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    root = args.repo_root.resolve()
    sys.path.insert(0, str(root))
    os.chdir(root)
    import qtpy
    from qtpy.QtCore import QCoreApplication, QEvent, qVersion
    from qtpy.QtGui import QTextCursor
    from qtpy.QtWidgets import QApplication
    from ballontranslator.ui.text_engine.item import TextBlkItem
    from ballontranslator.utils.textblock import TEXT_LAYOUT_VERSION, TextBlock
    app = QApplication.instance() or QApplication([])

    def create_item(n: int) -> TextBlkItem:
        bounds = [0, 0, 320, 240]
        block = TextBlock(bounds, text_layout_version=TEXT_LAYOUT_VERSION)
        block._bounding_rect = list(bounds)
        unit = 'Long text 한글 日本語 😀 abcdefghijklmnop '
        block.translation = (unit * (n // len(unit) + 1))[:n]
        block.vertical = args.vertical
        block.fontformat.font_family = 'DejaVu Sans'
        block.fontformat.font_size = 24
        block.fontformat.stroke_width = 0
        block.fontformat.letter_spacing = 1.0
        return TextBlkItem(block, 0)

    # Warm font/Qt paths without retaining a large paragraph in every cache.
    warm = create_item(80)
    warm.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()
    rows = []
    for count in args.chars:
        construction, insert, undo = [], [], []
        for _ in range(args.samples):
            gc.collect()
            start = time.perf_counter_ns()
            item = create_item(count)
            app.processEvents()
            construction.append((time.perf_counter_ns() - start) / 1e6)
            doc = item.document()
            cursor = QTextCursor(doc)
            cursor.movePosition(QTextCursor.MoveOperation.End)
            start = time.perf_counter_ns()
            cursor.insertText('x')
            app.processEvents()
            insert.append((time.perf_counter_ns() - start) / 1e6)
            start = time.perf_counter_ns()
            doc.undo()
            app.processEvents()
            undo.append((time.perf_counter_ns() - start) / 1e6)
            del cursor, doc
            item.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            app.processEvents()
            del item
        rows.append({'python_characters': count,
                     'construction': summarize(construction),
                     'insert_at_end': summarize(insert), 'document_undo': summarize(undo)})
    report = {'python': sys.version, 'platform': platform.platform(),
              'qt_api': qtpy.API_NAME, 'qt_version': qVersion(),
              'font': 'DejaVu Sans', 'repo_root': str(root), 'vertical': args.vertical,
              'note': 'Offscreen neutral TextBlkItem; not real-window FPS, IME or paired-editor latency.',
              'results': rows}
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if output:
        output.write_text(text + '\n', encoding='utf-8')
    print(text)


if __name__ == '__main__':
    main()
