"""Long outlined text must retain native selection painting in warped views."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')


def _selection_probe(effect_kind: str, transform_kind: str, text_kind: str) -> None:
    """Compare real view selection and effect pixels against direct Qt painting.

    >>> callable(_selection_probe)
    True
    """
    from qtpy.QtCore import QCoreApplication, QEvent, QPointF, QRectF, Qt
    from qtpy.QtGui import QImage, QPainter, QTextCursor
    from qtpy.QtWidgets import QApplication, QGraphicsScene, QGraphicsView

    from ballontranslator.ui.text_engine import horizontal_layout
    from ballontranslator.ui.text_engine.item import TextBlkItem
    from ballontranslator.utils.fontformat import (
        BendTextTransform, GridTextTransform, SineTextTransform, TextTransformStack,
    )
    from ballontranslator.utils.text_effects import (
        HollowEffect, StrokeEffect, TextEffectStack, TextFillEffect,
    )
    from ballontranslator.utils.textblock import TextBlock

    app = QApplication.instance() or QApplication([])
    app.setStyle('Fusion')
    app.setCursorFlashTime(0)
    original_draw = horizontal_layout.draw_native_layout

    def native_draw(layout, painter, selections, clip) -> None:
        layout.draw(painter, QPointF(), selections, clip)

    if transform_kind == 'bend':
        transform = BendTextTransform(0.2)
    elif transform_kind == 'sine':
        transform = SineTextTransform(amplitude_x=0.04)
    else:
        grid = GridTextTransform(2, 2, 'catmull_rom')
        points = list(grid.control_points)
        points[4] = (0.53, 0.46)
        transform = grid.with_control_points(points)

    outputs = []
    for paint in (native_draw, original_draw):
        # Qt 5 can use wider fallback metrics than Qt 6. Keep the zero run
        # above the native batching threshold in either binding.
        block = TextBlock([30, 100, 3030, 280])
        block._bounding_rect = [30, 100, 3000, 180]
        block.translation = (
            '0' * 160 if text_kind == 'zeros' else 'AB😀漢字 ' * 26
        )
        block.fontformat.font_family = 'Arial'
        block.fontformat.font_size = 14
        block.fontformat.text_effects = TextEffectStack(effects=(
            HollowEffect() if effect_kind == 'hollow' else TextFillEffect(),
            StrokeEffect(width=0.1),
        ))
        block.fontformat.text_transform = TextTransformStack((transform,))
        with patch.object(horizontal_layout, 'draw_native_layout', side_effect=paint) as calls:
            item = TextBlkItem(block)
            scene = QGraphicsScene()
            scene.addItem(item)
            view = QGraphicsView(scene)
            view.setFixedSize(1000, 500)
            scene_rect = QRectF(0, 0, 3300, 1155)
            view.setSceneRect(scene_rect)
            view.fitInView(scene_rect, Qt.AspectRatioMode.KeepAspectRatio)
            try:
                view.show()
                view.raise_()
                view.activateWindow()
                app.setActiveWindow(view)
                app.processEvents()
                item.startEdit()
                view.setFocus(Qt.FocusReason.ActiveWindowFocusReason)
                item.setFocus(Qt.FocusReason.ActiveWindowFocusReason)
                app.processEvents()
                assert view.hasFocus() and item.hasFocus(), 'selection comparison requires active editing focus'
                assert scene.focusItem() is item and item.isEditing()
                before_selection = view.viewport().grab().toImage()
                cursor = item.textCursor()
                cursor.setPosition(10)
                cursor.setPosition(50, QTextCursor.MoveMode.KeepAnchor)
                item.setTextCursor(cursor)
                # This ordinary view repaint used to abort inside Qt's virtual
                # paint callback. A child process keeps a regression observable
                # as a test failure instead of terminating the entire suite.
                app.processEvents()
                selected_view = view.viewport().grab().toImage()
                assert selected_view != before_selection, 'selection must be visible'
                assert item.geometry_controller.surface_renderer is not None
                assert item.geometry_controller.surface_renderer.cached_pixmap is not None
                assert any(
                    item.document().firstBlock().layout().lineAt(index).textLength() >= 128
                    for index in range(item.document().firstBlock().layout().lineCount())
                ), 'fixture must exercise a long shaped line'

                image = QImage(1200, 420, QImage.Format.Format_ARGB32_Premultiplied)
                image.fill(Qt.GlobalColor.transparent)
                painter = QPainter(image)
                try:
                    scene.render(painter, QRectF(0, 0, 1200, 420), scene_rect)
                finally:
                    painter.end()
                effect = item.effect_renderer.background_pixmap
                assert effect is not None
                outputs.append((selected_view, image, effect.toImage()))
                assert calls.call_count > 0, 'fixture must reach native layout integration'
            finally:
                item.geometry_controller.release_render_resources()
                scene.removeItem(item)
                item.deleteLater()
                view.close()
                view.deleteLater()
                scene.deleteLater()
                QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    for label, expected, actual in zip(
        ('selected viewport', 'selected scene render', 'effect raster'),
        outputs[0], outputs[1],
    ):
        assert expected == actual, f'{label} differs from direct Qt painting'


class NativeSelectionPaintTest(unittest.TestCase):
    def test_long_warped_effect_selection_matches_native_without_crashing(self) -> None:
        import qtpy

        environment = os.environ.copy()
        environment['QT_API'] = qtpy.API_NAME.lower()
        test_path = Path(__file__).resolve()
        for transform in ('bend', 'sine', 'grid'):
            for effect in ('hollow', 'gradient'):
                for text in ('zeros', 'mixed'):
                    with self.subTest(transform=transform, effect=effect, text=text):
                        result = subprocess.run(
                            [sys.executable, '-B', str(test_path), '--selection-probe', effect, transform, text],
                            cwd=test_path.parents[1],
                            env=environment,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT,
                            text=True,
                            encoding='utf-8',
                            errors='replace',
                            timeout=60,
                            check=False,
                        )
                        self.assertEqual(
                            result.returncode, 0,
                            f'Qt selection probe exited {result.returncode}:\n{result.stdout}',
                        )


if __name__ == '__main__':
    if len(sys.argv) == 5 and sys.argv[1] == '--selection-probe':
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        _selection_probe(*sys.argv[2:])
    else:
        unittest.main()
