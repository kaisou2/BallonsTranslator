"""Measure long-text item construction, canvas painting, and local editing.

Run from a checkout with the application's existing dependencies installed::

    python scripts/benchmark_long_text.py --repeat 5
    python scripts/benchmark_long_text.py --native --case short-lines --repeat 3
    python scripts/benchmark_long_text.py --project-json PATH --page 16

Set QT_API=pyqt5 or QT_API=pyqt6 before running to compare bindings. The
optional project is opened read-only; neither its text nor its path appears in
the report. No page images, models, or network services are loaded. Use --native
for the desktop font backend; the default is offscreen Qt. Both modes measure
individual text items and exclude the complete application page-switch path.
"""

from __future__ import annotations

import argparse
import copy
import cProfile
import html
import json
import os
from pathlib import Path
import platform
import pstats
import statistics
import sys
import time
from typing import Callable


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repeat', type=int, default=5)
    parser.add_argument(
        '--case', choices=('all', 'zeros', 'short-lines', 'korean', 'korean-vertical'),
        default='all', help='Synthetic fixture; ignored with --project-json.',
    )
    parser.add_argument('--font', default='Malgun Gothic')
    parser.add_argument(
        '--native', action='store_true',
        help='Use the desktop platform/font backend instead of offscreen Qt.',
    )
    parser.add_argument('--project-json', type=Path)
    parser.add_argument('--page', type=int, default=16, help='One-based page.')
    parser.add_argument(
        '--source-root', type=Path, default=Path(__file__).resolve().parents[1],
        help='Import another checkout for before/after comparisons.',
    )
    parser.add_argument(
        '--profile', action='store_true',
        help='Also profile one construction pass, printing to stderr.',
    )
    arguments = parser.parse_args()
    if arguments.repeat < 1 or arguments.page < 1:
        parser.error('--repeat and --page must be positive')
    return arguments


def timing_summary(samples: list[float]) -> dict[str, object]:
    return {
        'median_ms': round(statistics.median(samples), 3),
        'samples_ms': [round(value, 3) for value in samples],
    }


def elapsed_ms(action: Callable[[], object]) -> float:
    started = time.perf_counter()
    action()
    return (time.perf_counter() - started) * 1000


def main() -> None:
    arguments = parse_arguments()
    if arguments.native:
        os.environ.pop('QT_QPA_PLATFORM', None)
    else:
        os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    sys.path.insert(0, str(arguments.source_root.resolve()))

    import qtpy
    from qtpy.QtCore import QCoreApplication, QEvent, QRectF, Qt, qVersion
    from qtpy.QtGui import QImage, QPainter, QTextCursor
    from qtpy.QtWidgets import QApplication, QGraphicsScene

    from ballontranslator.ui.text_engine.item import TextBlkItem
    from ballontranslator.utils.fontformat import (
        ProjectiveTextTransform, TextTransformStack,
    )
    from ballontranslator.utils.textblock import TextBlock

    app = QApplication.instance() or QApplication([])

    def synthetic_block(case: str) -> TextBlock:
        bounds = [0, 0, 7124, 35] if case == 'zeros' else [0, 0, 900, 700]
        if case == 'short-lines':
            bounds = [0, 0, 286, 4200]
        block = TextBlock(bounds)
        block._bounding_rect = list(bounds)
        block.fontformat.font_family = arguments.font
        block.fontformat.font_size = 26
        block.fontformat.stroke_width = 0.1
        if case == 'zeros':
            block.fontformat.letter_spacing = 0.95
            block.fontformat.line_spacing = 1.3
            block.translation = '0' * 512
            # A saved rich-text block exercises the full import/format path.
            family = html.escape(arguments.font, quote=True)
            block.rich_text = (
                '<html><head><meta name="qrichtext" content="1" /></head>'
                f'<body style="font-family:&quot;{family}&quot;;font-size:19.5pt;">'
                '<p align="center"><span style="letter-spacing:0.95px;">'
                + block.translation + '</span></p></body></html>'
            )
            block.fontformat.text_transform = TextTransformStack((
                ProjectiveTextTransform(vertical_scale=1.2),
            ))
        elif case == 'short-lines':
            block.vertical = False
            block.fontformat.font_size = 20
            block.fontformat.letter_spacing = 0.95
            block.fontformat.line_spacing = 1.3
            block.fontformat.ligature_discretionary = 'enabled'
            block.translation = '짧은글\n' * 150
            family = html.escape(arguments.font, quote=True)
            paragraphs = []
            for index in range(150):
                spacing = 0.95 if index % 2 == 0 else 1.0
                paragraphs.append(
                    '<p style="margin:0; line-height:1.3;">'
                    f'<span style="letter-spacing:{spacing - 1:g}em; '
                    'font-variant-ligatures:discretionary-ligatures;" '
                    f'data-btrans-letter-spacing="{spacing:g}">짧은글</span></p>'
                )
            # Keep the final empty paragraph: 150 three-unit lines plus their
            # newlines contain 600 UTF-16 units, without any private project.
            paragraphs.append(
                '<p style="-qt-paragraph-type:empty; margin:0; '
                'line-height:1.3;"><br /></p>'
            )
            block.rich_text = (
                '<html><head><meta name="qrichtext" content="1" /></head>'
                f'<body style="font-family:&quot;{family}&quot;;font-size:15pt;">'
                + ''.join(paragraphs) + '</body></html>'
            )
        else:
            sentence = (
                '안녕하세요. 오랜 시간 작품을 읽어 주신 모든 독자 여러분께 '
                '감사드립니다. 다음 이야기에서도 즐거운 모습으로 다시 '
                '만나기를 바랍니다. '
            )
            block.translation = (sentence * 20)[:1500]
            block.vertical = case == 'korean-vertical'
        return block

    if arguments.project_json is not None:
        with arguments.project_json.open(encoding='utf-8') as source:
            payload = json.load(source)
        pages = list(payload['pages'].values())
        if arguments.page > len(pages):
            raise ValueError('Requested page exceeds the project page count')
        selected_page = pages[arguments.page - 1]
        cases = [f'project-page-{arguments.page}']

        def make_blocks(_case: str) -> list[TextBlock]:
            return [TextBlock(**copy.deepcopy(value)) for value in selected_page]
    else:
        cases = (
            ['zeros', 'short-lines', 'korean', 'korean-vertical']
            if arguments.case == 'all' else [arguments.case]
        )

        def make_blocks(case: str) -> list[TextBlock]:
            return [synthetic_block(case)]

    def release_items(items: list[TextBlkItem]) -> None:
        for item in items:
            scene = item.scene()
            if scene is not None:
                scene.removeItem(item)
            item.geometry_controller.release_render_resources()
            item.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        app.processEvents()

    warm_block = TextBlock([0, 0, 240, 80])
    warm_block._bounding_rect = [0, 0, 240, 80]
    warm_block.translation = 'Warmup 안녕하세요 0123'
    warm_block.fontformat.font_family = arguments.font
    release_items([TextBlkItem(warm_block)])

    report: dict[str, object] = {
        'environment': {
            'python': platform.python_version(),
            'platform': platform.platform(),
            'binding': qtpy.API_NAME,
            'qt': qVersion(),
            'qpa': app.platformName(),
        },
        'repeat': arguments.repeat,
        'viewport_pixels': [1200, 1800],
        'cases': [],
    }
    for case in cases:
        construction_times: list[float] = []
        first_paint_times: list[float] = []
        warm_paint_times: list[float] = []
        edit_times: list[float] = []
        editing_paint_times: list[float] = []
        block_count = 0
        character_count = 0

        def construct() -> list[TextBlkItem]:
            return [TextBlkItem(block, index) for index, block in enumerate(
                make_blocks(case)
            )]

        for _ in range(arguments.repeat):
            started = time.perf_counter()
            items = construct()
            construction_times.append((time.perf_counter() - started) * 1000)
            scene = QGraphicsScene()
            try:
                for item in items:
                    scene.addItem(item)
                block_count = len(items)
                character_count = sum(
                    item.document().characterCount() - 1 for item in items
                )
                image = QImage(
                    1200, 1800, QImage.Format.Format_ARGB32_Premultiplied,
                )

                def paint() -> None:
                    image.fill(Qt.GlobalColor.transparent)
                    painter = QPainter(image)
                    try:
                        scene.render(
                            painter, QRectF(0, 0, 1200, 1800),
                            QRectF(0, 0, 1200, 1800),
                        )
                    finally:
                        painter.end()

                first_paint_times.append(elapsed_ms(paint))
                warm_paint_times.extend(elapsed_ms(paint) for _ in range(5))
                if items:
                    target = max(
                        items, key=lambda item: item.document().characterCount(),
                    )
                    target.startEdit()
                    cursor = target.textCursor()
                    cursor.clearSelection()
                    cursor.movePosition(QTextCursor.MoveOperation.End)
                    target.setTextCursor(cursor)

                    def insert_character() -> None:
                        cursor = target.textCursor()
                        cursor.insertText('가' if case.startswith('korean') else '0')
                        target.setTextCursor(cursor)

                    # These edits affect transient Qt items only; no save occurs.
                    edit_times.extend(elapsed_ms(insert_character) for _ in range(5))
                    editing_paint_times.extend(elapsed_ms(paint) for _ in range(5))
                    target.endEdit()
            finally:
                release_items(items)

        case_result = {
            'case': case,
            'blocks': block_count,
            'utf16_characters': character_count,
            'construction': timing_summary(construction_times),
            'first_paint': timing_summary(first_paint_times),
            'warm_paint': timing_summary(warm_paint_times),
        }
        if edit_times:
            case_result['insert_character'] = timing_summary(edit_times)
            case_result['editing_paint'] = timing_summary(editing_paint_times)
        report['cases'].append(case_result)

        if arguments.profile:
            profiler = cProfile.Profile()
            profiler.enable()
            items = construct()
            profiler.disable()
            release_items(items)
            print(f'Construction profile: {case}', file=sys.stderr)
            pstats.Stats(profiler, stream=sys.stderr).strip_dirs().sort_stats(
                'cumtime',
            ).print_stats(25)

    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
