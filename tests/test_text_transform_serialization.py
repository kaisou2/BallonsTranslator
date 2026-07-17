import copy
import json
import math
import os
import subprocess
import sys
import tempfile
import unittest

from ballontranslator.utils import config as config_module
from ballontranslator.utils.fontformat import (
    TEXT_TRANSFORM_BOX_SLANT_MAX,
    TEXT_TRANSFORM_BOX_SLANT_MIN,
    TEXT_TRANSFORM_GLYPH_SLANT_MAX,
    TEXT_TRANSFORM_GLYPH_SLANT_MIN,
    TEXT_TRANSFORM_SCALE_MAX,
    TEXT_TRANSFORM_SCALE_MIN,
    FontFormat,
    TextTransform,
    normalize_text_transform,
)
from ballontranslator.utils.proj_imgtrans import (
    InvalidTextTransformPayloadError,
    ProjectLoadFailureException,
    ProjImgTrans,
    TextBlkEncoder,
    UnsupportedTextTransformVersionError,
    migrate_text_transform_payload,
)
from ballontranslator.utils.textblock import TextBlock


FIXTURE_DIR = os.path.join(
    os.path.dirname(__file__), 'fixtures', 'text_transform'
)
CANONICAL_FIELDS = (
    'horizontal_scale',
    'vertical_scale',
    'slant_angle',
    'glyph_slant_angle',
)
NEUTRAL_TRANSFORM = (1.0, 1.0, 0.0, 0.0)


def load_fixture(name):
    with open(os.path.join(FIXTURE_DIR, name), 'r', encoding='utf-8') as fixture:
        return json.load(fixture)


def canonical_fontformat(**overrides):
    fontformat = dict(zip(CANONICAL_FIELDS, NEUTRAL_TRANSFORM))
    fontformat.update(overrides)
    return fontformat


def project_payload(blocks, *, version=2):
    if isinstance(blocks, dict):
        blocks = [blocks]
    payload = {
        'directory': 'fixture-project',
        'pages': {'page.png': blocks},
    }
    if version is not None:
        payload['text_transform_schema_version'] = version
    return payload


def block_transform(block):
    return tuple(block['fontformat'][name] for name in CANONICAL_FIELDS)


class TextTransformModelTest(unittest.TestCase):

    def test_defaults_ranges_normalization_and_types(self):
        self.assertEqual(
            (
                TEXT_TRANSFORM_SCALE_MIN,
                TEXT_TRANSFORM_SCALE_MAX,
                TEXT_TRANSFORM_BOX_SLANT_MIN,
                TEXT_TRANSFORM_BOX_SLANT_MAX,
                TEXT_TRANSFORM_GLYPH_SLANT_MIN,
                TEXT_TRANSFORM_GLYPH_SLANT_MAX,
            ),
            (0.1, 4.0, -85.0, 85.0, -45.0, 45.0),
        )

        transform = FontFormat().text_transform
        self.assertIsInstance(transform, TextTransform)
        self.assertEqual(transform, NEUTRAL_TRANSFORM)
        self.assertEqual(transform._fields, CANONICAL_FIELDS)
        self.assertEqual(
            normalize_text_transform(1.25, 0.75, 60.0),
            (1.25, 0.75, 60.0, 0.0),
        )
        self.assertEqual(
            normalize_text_transform(9.0, 0.01, 90.0, -90.0),
            (4.0, 0.1, 85.0, -45.0),
        )
        self.assertEqual(
            normalize_text_transform(1.23456789, 1.0, -0.0, -0.0),
            (1.234568, 1.0, 0.0, 0.0),
        )

        for component in range(4):
            for invalid in (True, None, '1', math.nan, math.inf, -math.inf):
                values = [1.0, 1.0, 0.0, 0.0]
                values[component] = invalid
                with self.subTest(component=component, invalid=invalid):
                    with self.assertRaises(ValueError):
                        normalize_text_transform(*values)

    def test_fontformat_and_textblock_copies_have_independent_ownership(self):
        original_format = FontFormat(
            horizontal_scale=1.25,
            vertical_scale=0.75,
            slant_angle=9.0,
            glyph_slant_angle=-11.0,
            shadow_offset=[2.0, 3.0],
        )
        format_copies = (original_format.deepcopy(), original_format.copy())
        for duplicate in format_copies:
            self.assertIsNot(duplicate, original_format)
            self.assertEqual(duplicate.text_transform, original_format.text_transform)
            duplicate.horizontal_scale = 2.0
            duplicate.shadow_offset[0] = 99.0
        self.assertEqual(original_format.horizontal_scale, 1.25)
        self.assertEqual(original_format.shadow_offset, [2.0, 3.0])

        original_block = TextBlock(
            translation='copy-paste source',
            fontformat=original_format,
        )
        pasted_block = copy.deepcopy(original_block)
        self.assertIsNot(pasted_block.fontformat, original_block.fontformat)
        self.assertEqual(pasted_block.fontformat.text_transform, original_format.text_transform)
        pasted_block.fontformat.glyph_slant_angle = 20.0
        self.assertEqual(original_block.fontformat.glyph_slant_angle, -11.0)

    def test_old_and_new_style_preset_round_trip(self):
        old_styles = list(config_module.text_styles)
        old_path = config_module.pcfg.text_styles_path
        try:
            with tempfile.TemporaryDirectory() as directory:
                style_path = os.path.join(directory, 'styles.json')
                with open(style_path, 'w', encoding='utf-8') as output:
                    json.dump([{'_style_name': 'upstream preset'}], output)

                config_module.load_textstyle_from(style_path, raise_exception=True)
                self.assertEqual(
                    config_module.text_styles[0].text_transform,
                    NEUTRAL_TRANSFORM,
                )

                expected = (1.4, 0.65, 17.0, -12.5)
                style = config_module.text_styles[0]
                for name, value in zip(CANONICAL_FIELDS, expected):
                    setattr(style, name, value)
                self.assertTrue(config_module.save_text_styles(raise_exception=True))

                config_module.load_textstyle_from(style_path, raise_exception=True)
                self.assertEqual(config_module.text_styles[0].text_transform, expected)
                with open(style_path, 'r', encoding='utf-8') as saved:
                    saved_style = json.load(saved)[0]
                self.assertEqual(
                    tuple(saved_style[name] for name in CANONICAL_FIELDS),
                    expected,
                )
        finally:
            config_module.text_styles.clear()
            config_module.text_styles.extend(old_styles)
            config_module.pcfg.text_styles_path = old_path


class TextTransformSchemaTest(unittest.TestCase):

    def test_schema_v2_requires_all_four_fields_under_fontformat(self):
        for missing in CANONICAL_FIELDS:
            fontformat = canonical_fontformat()
            fontformat.pop(missing)
            source = project_payload({'fontformat': fontformat})
            with self.subTest(missing=missing):
                with self.assertRaisesRegex(
                    InvalidTextTransformPayloadError,
                    'required in schema v2',
                ):
                    migrate_text_transform_payload(source)

        for invalid_fontformat in (None, [], 'bad'):
            source = project_payload({'fontformat': invalid_fontformat})
            with self.subTest(fontformat=invalid_fontformat):
                with self.assertRaises(InvalidTextTransformPayloadError):
                    migrate_text_transform_payload(source)

    def test_schema_v2_rejects_aliases_markers_and_every_top_level_field(self):
        cases = []
        for name in CANONICAL_FIELDS:
            block = {'fontformat': canonical_fontformat(), name: 0.0}
            cases.append((f'top-level {name}', block))
        cases.extend(
            [
                (
                    'top-level alias',
                    {'fontformat': canonical_fontformat(), 'italic_angle': 4.0},
                ),
                (
                    'fontformat alias',
                    {
                        'fontformat': canonical_fontformat(italic_angle=4.0),
                    },
                ),
                (
                    'block marker',
                    {
                        'fontformat': canonical_fontformat(),
                        'rich_text_transform_version': 1,
                    },
                ),
                (
                    'fontformat marker',
                    {
                        'fontformat': canonical_fontformat(
                            rich_text_transform_version=1
                        ),
                    },
                ),
                (
                    'failed stretch marker',
                    {
                        'fontformat': canonical_fontformat(),
                        'rich_text': (
                            '<!--ballontranslator-logical-stretch-v1:[]-->'
                        ),
                    },
                ),
            ]
        )

        for name, block in cases:
            source = project_payload(block)
            before = copy.deepcopy(source)
            with self.subTest(name=name):
                with self.assertRaises(InvalidTextTransformPayloadError):
                    migrate_text_transform_payload(source)
                self.assertEqual(source, before)

        for name in CANONICAL_FIELDS:
            source = project_payload({'fontformat': canonical_fontformat()})
            source[name] = 1.0
            before = copy.deepcopy(source)
            with self.subTest(name=f'project-root {name}'):
                with self.assertRaises(InvalidTextTransformPayloadError):
                    migrate_text_transform_payload(source)
                self.assertEqual(source, before)

    def test_schema_v2_rejects_nonfinite_wrong_type_and_out_of_range(self):
        invalid_values = {
            'horizontal_scale': (True, '1', None, math.nan, math.inf, 0.099999, 4.000001),
            'vertical_scale': (True, '1', None, -math.inf, 0.099999, 4.000001),
            'slant_angle': (True, '1', None, math.nan, -85.000001, 85.000001),
            'glyph_slant_angle': (
                True,
                '1',
                None,
                math.inf,
                -45.000001,
                45.000001,
            ),
        }
        for field_name, values in invalid_values.items():
            for invalid in values:
                fontformat = canonical_fontformat()
                fontformat[field_name] = invalid
                source = project_payload({'fontformat': fontformat})
                with self.subTest(field=field_name, invalid=invalid):
                    with self.assertRaises(InvalidTextTransformPayloadError):
                        migrate_text_transform_payload(source)

    def test_only_missing_upstream_marker_or_canonical_v2_are_supported(self):
        for version in (0, 1):
            with self.subTest(version=version):
                with self.assertRaises(UnsupportedTextTransformVersionError):
                    migrate_text_transform_payload(
                        {'text_transform_schema_version': version, 'pages': {}}
                    )

        future = load_fixture('future_root_v3.json')
        with self.assertRaisesRegex(
            UnsupportedTextTransformVersionError,
            'schema version 3',
        ):
            migrate_text_transform_payload(future)

        for malformed in (True, None, '2', 2.5, math.nan, math.inf, -1):
            source = {'text_transform_schema_version': malformed, 'pages': {}}
            with self.subTest(malformed=malformed):
                with self.assertRaises(InvalidTextTransformPayloadError):
                    migrate_text_transform_payload(source)

    def test_upstream_payload_migrates_neutral_without_changing_content(self):
        source = load_fixture('upstream_legacy.json')
        before = copy.deepcopy(source)

        migrated = migrate_text_transform_payload(source)

        original_block = before['pages']['page.png'][0]
        migrated_block = migrated['pages']['page.png'][0]
        self.assertEqual(source, before)
        self.assertEqual(migrated['text_transform_schema_version'], 2)
        self.assertEqual(block_transform(migrated_block), NEUTRAL_TRANSFORM)
        self.assertEqual(migrated_block['rich_text'], original_block['rich_text'])
        self.assertEqual(migrated_block['translation'], original_block['translation'])
        self.assertEqual(migrated_block['fontformat']['font_family'], 'Arial')
        self.assertEqual(migrated_block['fontformat']['font_size'], 24)

        without_fontformat = project_payload({'translation': 'old'}, version=None)
        migrated = migrate_text_transform_payload(without_fontformat)
        self.assertEqual(
            block_transform(migrated['pages']['page.png'][0]),
            NEUTRAL_TRANSFORM,
        )

    def test_unversioned_intermediate_payloads_are_rejected_not_migrated(self):
        cases = [
            {'fontformat': {field: 1.0}}
            for field in CANONICAL_FIELDS
        ]
        cases.extend(
            [
                {'fontformat': {'italic_angle': 4.0}},
                {'rich_text_transform_version': 0},
                {'rich_text_transform_version': 1},
                {
                    'rich_text': (
                        '<!--ballontranslator-logical-stretch-v1:[]-->'
                    )
                },
                load_fixture('future_block_marker_v2.json')['pages']['page.png'][0],
            ]
        )
        for block in cases:
            source = project_payload(block, version=None)
            with self.subTest(block=block):
                with self.assertRaises(InvalidTextTransformPayloadError):
                    migrate_text_transform_payload(source)

    def test_valid_v2_is_canonical_idempotent_and_does_not_mutate_input(self):
        source = project_payload(
            {
                'rich_text': '<p>logical HTML</p>',
                'fontformat': canonical_fontformat(
                    horizontal_scale=1.23456789,
                    vertical_scale=4.0,
                    slant_angle=-85.0,
                    glyph_slant_angle=-0.0,
                ),
            }
        )
        before = copy.deepcopy(source)

        first = migrate_text_transform_payload(source)
        second = migrate_text_transform_payload(first)

        self.assertEqual(source, before)
        self.assertEqual(first, second)
        self.assertEqual(
            block_transform(first['pages']['page.png'][0]),
            (1.234568, 4.0, -85.0, 0.0),
        )
        self.assertEqual(first['pages']['page.png'][0]['rich_text'], '<p>logical HTML</p>')

    def test_late_block_failure_does_not_mutate_source(self):
        valid = {'fontformat': canonical_fontformat(horizontal_scale=1.5)}
        invalid = {'fontformat': canonical_fontformat(glyph_slant_angle=46.0)}
        source = project_payload([valid, invalid])
        before = copy.deepcopy(source)

        with self.assertRaises(InvalidTextTransformPayloadError):
            migrate_text_transform_payload(source)

        self.assertEqual(source, before)


class ProjectPersistenceTest(unittest.TestCase):

    @staticmethod
    def seeded_project():
        project = ProjImgTrans()
        project.directory = 'original-directory'
        project.proj_path = 'original-project.json'
        project.pages = {'existing.png': [TextBlock(translation='keep me')]}
        project.not_found_pages = {'missing.png': []}
        project.new_pages = ['new.png']
        project._pagename2idx = {'existing.png': 0}
        project._idx2pagename = {0: 'existing.png'}
        project._image_info = {'existing.png': {'finish_code': 7}}
        project.current_img = 'existing.png'
        return project

    def assert_seed_unchanged(self, project, snapshot):
        self.assertEqual(project.directory, 'original-directory')
        self.assertEqual(project.proj_path, 'original-project.json')
        self.assertEqual(project.current_img, 'existing.png')
        for name, previous in snapshot.items():
            self.assertIs(getattr(project, name), previous)

    @staticmethod
    def state_snapshot(project):
        return {
            name: getattr(project, name)
            for name in (
                'pages',
                'not_found_pages',
                'new_pages',
                '_pagename2idx',
                '_idx2pagename',
                '_image_info',
            )
        }

    def test_encoder_save_and_reload_emit_canonical_schema_v2(self):
        block = TextBlock(
            xyxy=[3, 4, 103, 54],
            translation='round trip',
            rich_text='<p><b>logical rich text</b></p>',
            fontformat=FontFormat(
                horizontal_scale=1.234568,
                vertical_scale=0.625,
                slant_angle=-14.5,
                glyph_slant_angle=11.25,
            ),
        )

        with tempfile.TemporaryDirectory() as directory:
            source = ProjImgTrans()
            source.directory = directory
            source.proj_path = os.path.join(directory, source.proj_name() + '.json')
            source.pages = {'missing.png': [block]}
            source._image_info = {'missing.png': {'finish_code': 0}}
            source.current_img = None

            encoded = json.loads(json.dumps(source.to_dict(), cls=TextBlkEncoder))
            self.assertEqual(encoded['text_transform_schema_version'], 2)
            self.assertEqual(
                block_transform(encoded['pages']['missing.png'][0]),
                (1.234568, 0.625, -14.5, 11.25),
            )

            source.save()
            with open(source.proj_path, 'r', encoding='utf-8') as saved:
                first_disk_payload = json.load(saved)
            self.assertEqual(first_disk_payload, encoded)

            child_code = """
import json
import sys
from ballontranslator.utils.proj_imgtrans import ProjImgTrans

project = ProjImgTrans(sys.argv[1])
block = project.not_found_pages['missing.png'][0]
print(json.dumps({
    'transform': list(block.fontformat.text_transform),
    'rich_text': block.rich_text,
}))
"""
            try:
                child = subprocess.run(
                    [sys.executable, '-c', child_code, directory],
                    cwd=os.path.dirname(os.path.dirname(__file__)),
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
            except subprocess.TimeoutExpired as error:
                self.fail(
                    'save/reload child process timed out after '
                    f'{error.timeout}s; stdout={error.stdout!r}; '
                    f'stderr={error.stderr!r}'
                )
            child_result = json.loads(child.stdout.strip().splitlines()[-1])
            self.assertEqual(
                child_result['transform'],
                [1.234568, 0.625, -14.5, 11.25],
            )
            self.assertEqual(child_result['rich_text'], block.rich_text)

            restored = ProjImgTrans(directory)

            restored.save()
            with open(restored.proj_path, 'r', encoding='utf-8') as saved:
                second_disk_payload = json.load(saved)
            remigrated = migrate_text_transform_payload(second_disk_payload)
            self.assertEqual(remigrated, second_disk_payload)

    def test_invalid_and_future_loads_leave_existing_state_unchanged(self):
        cases = (
            project_payload(
                {
                    'fontformat': canonical_fontformat(
                        glyph_slant_angle=45.000001
                    )
                }
            ),
            load_fixture('future_root_v3.json'),
        )
        for source in cases:
            project = self.seeded_project()
            snapshot = self.state_snapshot(project)
            with self.subTest(source=source):
                with self.assertRaises(
                    (InvalidTextTransformPayloadError,
                     UnsupportedTextTransformVersionError)
                ):
                    project.load_from_dict(source)
                self.assert_seed_unchanged(project, snapshot)

    def test_future_json_load_leaves_existing_state_unchanged(self):
        project = self.seeded_project()
        snapshot = self.state_snapshot(project)
        future_path = os.path.join(FIXTURE_DIR, 'future_root_v3.json')

        with self.assertRaisesRegex(
            ProjectLoadFailureException,
            'schema version 3',
        ):
            project.load_from_json(future_path)

        self.assert_seed_unchanged(project, snapshot)

if __name__ == '__main__':
    unittest.main()
