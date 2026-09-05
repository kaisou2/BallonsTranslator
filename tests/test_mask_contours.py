"""Balloon selection regressions, including candidates that must not be drawn."""
import unittest
from unittest import mock

import cv2
import numpy as np

from ballontranslator.utils import textblock_mask


def rectangle(x1: int, y1: int, x2: int, y2: int) -> np.ndarray:
    return np.array([[[x1, y1]], [[x2, y1]], [[x2, y2]], [[x1, y2]]], dtype=np.int32)


def reference_selection(contours: tuple, mask: np.ndarray) -> tuple:
    """Original rule: test pixel coverage before considering a contour's area."""
    x, y, w, h = cv2.boundingRect(cv2.findNonZero(mask))
    minimum, balloon = mask.size, None
    for index, contour in enumerate(contours):
        cx, cy, cw, ch = cv2.boundingRect(contour)
        if cx > x or cy > y or cx + cw < x + w or cy + ch < y + h:
            continue
        candidate = np.zeros_like(mask)
        cv2.drawContours(candidate, contours, index, 255, -1, cv2.LINE_8)
        if cv2.bitwise_and(candidate, mask).sum() >= mask.sum():
            area = cv2.contourArea(contour)
            if area < minimum:
                minimum, balloon = area, candidate
    background = None if balloon is None else cv2.bitwise_and(balloon, 255 - mask)
    return balloon, background


class MaskContoursTests(unittest.TestCase):
    def assert_masks_equal(self, actual: tuple, expected: tuple) -> None:
        for result, reference in zip(actual, expected):
            if reference is None:
                self.assertIsNone(result)
            else:
                np.testing.assert_array_equal(result, reference)
                self.assertEqual(result.dtype, np.uint8)

    def test_real_contours_match_original_selection(self) -> None:
        find_contours = cv2.findContours
        for channels in (3, 4):
            for mode in ('center', 'edge', 'empty', 'full', 'gray', 'strided'):
                with self.subTest(channels=channels, mode=mode):
                    image = np.full((128, 160, channels), 255, dtype=np.uint8)
                    for inset in (8, 18, 28):
                        cv2.rectangle(image, (inset, inset), (159 - inset, 127 - inset), (0,) * channels, 2)
                    mask = np.zeros(image.shape[:2], dtype=np.uint8)
                    if mode == 'edge':
                        mask[:8, :8] = 255
                    elif mode == 'full':
                        mask[:] = 255
                    elif mode != 'empty':
                        mask[58:70, 74:86] = 73 if mode == 'gray' else 255
                    if mode == 'strided':
                        image, mask = image[:, ::-1], mask[:, ::-1]
                    original_image, original_mask = image.copy(), mask.copy()
                    captured = []

                    def capture_contours(*args, **kwargs) -> tuple:
                        result = find_contours(*args, **kwargs)
                        captured.append(result[0])
                        return result

                    with mock.patch.object(cv2, 'findContours', side_effect=capture_contours):
                        actual = textblock_mask.extract_ballon_mask(image, mask)
                    self.assert_masks_equal(actual, reference_selection(captured[-1], mask))
                    np.testing.assert_array_equal(image, original_image)
                    np.testing.assert_array_equal(mask, original_mask)

    def test_larger_and_equal_candidates_are_not_rasterized(self) -> None:
        image = np.full((64, 64, 3), 255, dtype=np.uint8)
        mask = np.zeros(image.shape[:2], dtype=np.uint8)
        mask[28:36, 28:36] = 255
        contours = (rectangle(20, 20, 43, 43), rectangle(10, 10, 53, 53),
                    rectangle(21, 20, 44, 43), rectangle(-10, -10, 80, 80))
        with mock.patch.object(cv2, 'findContours', return_value=(contours, None)):
            with mock.patch.object(cv2, 'drawContours', wraps=cv2.drawContours) as draw:
                actual = textblock_mask.extract_ballon_mask(image, mask)
        self.assertEqual([call.args[2] for call in draw.call_args_list], [0])
        self.assert_masks_equal(actual, reference_selection(contours, mask))

    def test_smaller_candidate_must_cover_all_text_pixels(self) -> None:
        image = np.full((64, 64, 3), 255, dtype=np.uint8)
        mask = np.zeros(image.shape[:2], dtype=np.uint8)
        mask[28:36, 28:36] = 255
        diamond = np.array([[[32, 26]], [[38, 32]], [[32, 38]], [[26, 32]]], dtype=np.int32)
        for contours in ((diamond, rectangle(24, 24, 40, 40)),
                         (rectangle(24, 24, 40, 40), diamond)):
            with self.subTest(order=[cv2.contourArea(c) for c in contours]):
                with mock.patch.object(cv2, 'findContours', return_value=(contours, None)):
                    actual = textblock_mask.extract_ballon_mask(image, mask)
                self.assert_masks_equal(actual, reference_selection(contours, mask))

    def test_no_matching_contour_preserves_none_pair(self) -> None:
        image = np.full((64, 64, 3), 255, dtype=np.uint8)
        mask = np.zeros(image.shape[:2], dtype=np.uint8)
        mask[28:36, 28:36] = 255
        for contours in ((), (rectangle(1, 1, 8, 8),)):
            with self.subTest(contours=len(contours)):
                with mock.patch.object(cv2, 'findContours', return_value=(contours, None)):
                    self.assertEqual(textblock_mask.extract_ballon_mask(image, mask), (None, None))


if __name__ == '__main__':
    unittest.main()
