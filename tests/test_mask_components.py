"""Check label selection through the real segmentation entry point."""
import unittest
from unittest import mock

import cv2
import numpy as np

from ballontranslator.utils import textblock_mask


def original_label_mask(labels: np.ndarray, stats: np.ndarray) -> np.ndarray:
    """Reference rule: exclude the first largest label and areas >= 40%."""
    result = np.zeros(labels.shape, dtype=np.uint8)
    largest = np.argmax(stats[:, cv2.CC_STAT_AREA])
    for label, stat in enumerate(stats):
        if label != largest and stat[cv2.CC_STAT_AREA] < labels.size * 0.4:
            result[labels == label] = 255
    return result


class MaskComponentsTests(unittest.TestCase):
    def test_real_images_match_original_rule(self) -> None:
        component_impl = cv2.connectedComponentsWithStats
        images = []
        for background, text in ((255, 0), (0, 255), (160, 20)):
            image = np.full((96, 96, 3), background, dtype=np.uint8)
            for y in range(8, 88, 12):
                for x in range(8, 88, 12):
                    image[y:y + 6, x:x + 4] = text
            images.extend((image, np.dstack((image, np.full((96, 96), 37, np.uint8)))))
        # More than 256 components checks that pixel labels are not cast to uint8.
        dense = np.full((96, 96, 3), 255, dtype=np.uint8)
        for y in range(3, 92, 5):
            for x in range(3, 92, 5):
                dense[y:y + 3, x:x + 3] = 0
        images.extend((dense, dense[:, ::-1]))
        for image in images:
            with self.subTest(shape=image.shape, strides=image.strides):
                captured = []

                def capture_components(*args, **kwargs) -> tuple:
                    result = component_impl(*args, **kwargs)
                    captured.append(result)
                    return result

                original = image.copy()
                with mock.patch.object(cv2, 'connectedComponentsWithStats', side_effect=capture_components):
                    with mock.patch.object(cv2, 'bitwise_and', wraps=cv2.bitwise_and) as combine:
                        mask, balloon, _ = textblock_mask.connected_canny_flood(image)
                _, labels, stats, _ = captured[-1]
                expected = original_label_mask(labels, stats)
                # This is the mask immediately before clipping it to the balloon.
                np.testing.assert_array_equal(combine.call_args_list[0].args[0], expected)
                expected = cv2.bitwise_and(expected, balloon)
                expected = cv2.GaussianBlur(expected, (3, 3), cv2.BORDER_DEFAULT)
                expected = cv2.threshold(expected, 1, 255, cv2.THRESH_BINARY)[1]
                np.testing.assert_array_equal(mask, expected)
                np.testing.assert_array_equal(image, original)
                self.assertEqual(mask.dtype, np.uint8)

    def test_strict_cutoff_nonzero_largest_label_and_ties(self) -> None:
        image = np.full((100, 100, 3), 255, dtype=np.uint8)
        for areas in ([4500, 4000, 1500], [2000, 3000, 3000, 2000],
                      [2000, 5000, 3000], [10000], [3999, 4000, 2001]):
            with self.subTest(areas=areas):
                labels = np.repeat(np.arange(len(areas), dtype=np.int32), areas).reshape(100, 100)
                stats = np.zeros((len(areas), 5), dtype=np.int32)
                for label, area in enumerate(areas):
                    y, x = np.where(labels == label)
                    stats[label] = [x.min(), y.min(), x.max() - x.min() + 1, y.max() - y.min() + 1, area]
                components = (len(areas), labels, stats, np.zeros((len(areas), 2)))
                # Supply controlled component statistics; all other mask operations stay real.
                with mock.patch.object(cv2, 'connectedComponentsWithStats', return_value=components):
                    with mock.patch.object(textblock_mask, 'textrgb_calculator', return_value=np.zeros(3, np.uint8)):
                        with mock.patch.object(cv2, 'bitwise_and', wraps=cv2.bitwise_and) as combine:
                            textblock_mask.connected_canny_flood(image)
                np.testing.assert_array_equal(combine.call_args_list[0].args[0], original_label_mask(labels, stats))

    def test_optional_stroke_filter_still_receives_clipped_mask(self) -> None:
        image = np.full((64, 64, 3), 255, dtype=np.uint8)
        image[24:40, 28:36] = 0
        # Only test dispatch here; the unchanged stroke algorithm is a separate concern.
        with mock.patch.object(textblock_mask, 'strokewidth_check', side_effect=lambda mask, *a, **k: mask) as stroke:
            _, balloon, _ = textblock_mask.connected_canny_flood(image, apply_strokewidth_check=1)
        mask, labels, _, stats = stroke.call_args.args
        expected = cv2.bitwise_and(original_label_mask(labels, stats), balloon)
        np.testing.assert_array_equal(mask, expected)
        self.assertEqual(stroke.call_count, 1)


if __name__ == '__main__':
    unittest.main()
