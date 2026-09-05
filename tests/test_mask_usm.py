"""USM output and allocation regressions; no Qt, models or network required."""
import unittest
from unittest import mock

import cv2
import numpy as np

from ballontranslator.utils import textblock_mask


class MaskUsmTests(unittest.TestCase):
    def test_sharpening_preserves_pixels_dtype_and_input(self) -> None:
        rng = np.random.default_rng(20260905)
        for dtype in (np.uint8, np.uint16, np.float32):
            for channels in (3, 4):
                for height, width in ((1, 1), (23, 37)):
                    for strided in (False, True):
                        with self.subTest(dtype=dtype, channels=channels,
                                          size=(height, width), strided=strided):
                            image = rng.integers(0, 256, (height, width * 2, channels)).astype(dtype)
                            image = image[:, ::2] if strided else image[:, :width].copy()
                            original = image.copy()
                            rgb = cv2.cvtColor(image, cv2.COLOR_RGBA2RGB) if channels == 4 else image
                            expected = cv2.addWeighted(rgb, 1.5, cv2.GaussianBlur(rgb, (0, 0), 5), -0.5, 0)
                            actual = textblock_mask.usm(image)
                            np.testing.assert_array_equal(actual, expected)
                            np.testing.assert_array_equal(image, original)
                            self.assertEqual(actual.dtype, expected.dtype)
                            self.assertFalse(np.shares_memory(actual, image))

    def test_no_unused_comparison_canvas(self) -> None:
        image = np.full((32, 48, 3), 127, dtype=np.uint8)
        # The removed canvas was a Python-level NumPy allocation, not OpenCV work.
        with mock.patch.object(textblock_mask.np, 'zeros', wraps=np.zeros) as zeros:
            actual = textblock_mask.usm(image)
        zeros.assert_not_called()
        np.testing.assert_array_equal(actual, image)

    def test_text_color_caller_preserves_result(self) -> None:
        image = np.random.default_rng(7).integers(0, 256, (40, 50, 4), dtype=np.uint8)
        mask = np.zeros(image.shape[:2], dtype=np.uint8)
        mask[10:30, 15:35] = 255
        rgb = cv2.cvtColor(image, cv2.COLOR_RGBA2RGB)
        sharp = cv2.addWeighted(rgb, 1.5, cv2.GaussianBlur(rgb, (0, 0), 5), -0.5, 0)
        eroded = cv2.erode(mask, (3, 3), iterations=1)
        expected = np.mean(sharp[eroded == 255], axis=0).astype(np.uint8)
        np.testing.assert_array_equal(textblock_mask.textrgb_calculator(image, mask), expected)


if __name__ == '__main__':
    unittest.main()
