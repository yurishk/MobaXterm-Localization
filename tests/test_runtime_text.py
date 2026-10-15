"""Approved display literals must never overwrite instructions or exceed their slots."""
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from core import PROJECT, load_json, patch_literal_bytes, prepare_literal, runtime_plan, plan
from dfm import DFM


class RuntimeTextTests(unittest.TestCase):
    def toolbar_form(self):
        def short(s):
            raw = s.encode('ascii')
            return bytes([len(raw)]) + raw
        def value(s):
            return b'\x06' + short(s)
        return (b'TPF0' + short('TFormXterm') + short('FormXterm') + b'\0'
                + short('TPanel') + short('sPanel4') + b'\0'
                + short('TButton') + short('BtnNewSession')
                + short('Caption') + value('Session')
                + short('HelpKeyword') + value('Session')
                + short('Text') + value('Session')
                + b'\0\0\0\0')

    def test_toolbar_layout_does_not_restore_english(self):
        profile = load_json(PROJECT / 'config/versions.json')['versions']['26.5']
        language = {'source_sha256': profile['source_sha256'], 'strings': {'Session': '会话'}, 'overrides': {}}
        with patch('core.read_resources', return_value={(10, 'TFORMXTERM', 0): self.toolbar_form()}):
            updates, _, _ = plan('unused', language)
        values = {v['prop']: v['text'] for v in DFM(updates[(10, 'TFORMXTERM', 0)]).values}
        # The real layout routine restores Caption from HelpKeyword, not from the
        # static customization table. Replaying that assignment exposed the bug.
        after_layout = values['HelpKeyword']
        self.assertEqual(values['Caption'], '会话')
        self.assertEqual(after_layout, '会话')
        self.assertEqual(values['Text'], 'Session')

    def test_toolbar_caption_override_is_mirrored_to_restore_field(self):
        profile = load_json(PROJECT / 'config/versions.json')['versions']['26.5']
        language = {'source_sha256': profile['source_sha256'], 'strings': {'Session': '普通会话'},
                    'overrides': {'DFM|TFORMXTERM|/FormXterm/sPanel4/BtnNewSession|Caption':
                                  {'source': 'Session', 'target': '新建会话'}}}
        with patch('core.read_resources', return_value={(10, 'TFORMXTERM', 0): self.toolbar_form()}):
            updates, _, _ = plan('unused', language)
        values = {v['prop']: v['text'] for v in DFM(updates[(10, 'TFORMXTERM', 0)]).values}
        self.assertEqual(values['Caption'], '新建会话')
        self.assertEqual(values['HelpKeyword'], '新建会话')

    def fixture(self):
        slot = {'id': 'toolbar.home', 'offset': '0x10', 'source': 'Home', 'section': 'CODE'}
        data = b'PREFIX!!' + b'\xff'*4 + struct.pack('<I', 4) + b'Home\0' + b'INSTRUCTIONS'
        return slot, data

    def test_length_and_text_only_are_changed(self):
        slot, original = self.fixture()
        change = prepare_literal(original, slot, '家', 'cp936')
        result = patch_literal_bytes(original, [change])
        self.assertEqual(len(result), len(original))
        self.assertEqual(result[:12], original[:12])
        self.assertEqual(result[21:], original[21:])
        self.assertEqual(struct.unpack_from('<I', result, 12)[0], 2)
        self.assertEqual(result[16:21], '家'.encode('cp936') + b'\0'*3)

    def test_long_translation_is_rejected_without_truncation(self):
        slot, original = self.fixture()
        with self.assertRaisesRegex(ValueError, 'capacity'):
            prepare_literal(original, slot, '返回主页', 'cp936')

    def test_wrong_source_or_header_is_rejected(self):
        slot, original = self.fixture()
        for damaged in (original[:8]+b'\0'*4+original[12:], original.replace(b'Home', b'Gone')):
            with self.assertRaisesRegex(ValueError, 'Unexpected static string'):
                prepare_literal(damaged, slot, '主页', 'cp936')

    def test_unrepresentable_text_is_rejected(self):
        slot, original = self.fixture()
        with self.assertRaisesRegex(ValueError, 'cannot be encoded'):
            prepare_literal(original, slot, '主页', 'cp1252')

    def test_other_language_and_disabled_runtime_keep_english(self):
        slot, original = self.fixture()
        rules = {'literals': [slot], 'form_fields': []}
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp)/'source.exe'
            source.write_bytes(original)
            with patch('core.runtime_rules', return_value=rules), \
                 patch('core.section_for_offset', return_value='CODE'):
                changes, _, _ = runtime_plan(source, {'strings': {'Home': '主页'}})
                self.assertEqual(changes, [])
                changes, applied, _ = runtime_plan(source, {
                    'runtime_encoding': 'cp932', 'strings': {'Home': '戻る'}})
                self.assertEqual(applied[0]['target'], '戻る')
                self.assertEqual(patch_literal_bytes(original, changes)[16:20], '戻る'.encode('cp932'))

    def test_overlapping_or_resized_patches_are_rejected(self):
        slot, original = self.fixture()
        change = prepare_literal(original, slot, '主页', 'cp936')
        with self.assertRaises(ValueError):
            patch_literal_bytes(original, [change, change])
        with self.assertRaises(ValueError):
            patch_literal_bytes(original, [{**change, 'after': change['after']+b'x'}])


if __name__ == '__main__':
    unittest.main()
