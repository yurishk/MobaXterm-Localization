"""Regression tests for language isolation and lossless resource edits; no proprietary fixtures."""
import json
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools'))
from core import check_translation, choose, load_json, load_language, plan, profile_for
from dfm import DFM, encode_stringtable, stringtable

def short(s):
    raw = s.encode('ascii')
    return bytes([len(raw)]) + raw

def ansi(s):
    return b'\x06' + short(s)

def form():
    return (b'TPF0' + short('TTest') + short('Test')
            + short('Caption') + ansi('Cancel')
            + short('Text') + ansi('Cancel')
            + short('Hint') + ansi('Value: %s')
            + short('Left') + b'\x03' + struct.pack('<h', 123)
            + b'\0\0')

class ResourceTests(unittest.TestCase):
    def test_unicode_replacement_preserves_input_and_layout(self):
        source = DFM(form())
        caption = next(v for v in source.values if v['prop'] == 'Caption')
        updated = source.replace([(caption, '取消 🐱')])
        values = {v['prop']:v['text'] for v in DFM(updated).values}
        self.assertEqual(values['Caption'], '取消 🐱')
        self.assertEqual(values['Text'], 'Cancel')
        self.assertEqual(updated[caption['start']+len(b'\x12'+struct.pack('<I', 5)+'取消 🐱'.encode('utf-16le')):], form()[caption['end']:])

    def test_stringtable_round_trip_and_zero_padding(self):
        strings = ['取消', 'Continue', '😀'] + ['']*13
        data = encode_stringtable(strings)
        self.assertEqual(stringtable(data + b'\0'*34), strings)
        with self.assertRaises(ValueError):
            stringtable(data + b'x')

    def test_invalid_stringtable_length(self):
        with self.assertRaises(ValueError):
            encode_stringtable(['too few'])
        with self.assertRaises(ValueError):
            encode_stringtable(['x'*65536]+['']*15)

    def test_invalid_dfm_fails(self):
        with self.assertRaises(ValueError):
            DFM(b'WRONG')

class LanguageTests(unittest.TestCase):
    def test_placeholders_are_preserved_in_order(self):
        check_translation('Server %s: %d', '服务器 %s：%d')
        check_translation('%host %port %user %pass %%', '%host %port %user %pass %%')
        for source, target in [('Server %s: %d', '服务器 %d：%s'),
                               ('Connect %host:%port', '连接 %主机:%port')]:
            with self.assertRaises(ValueError):
                check_translation(source, target)

    def test_invalid_unicode_rejected(self):
        for target in ['bad\ufffd', 'bad\0']:
            with self.assertRaises(ValueError):
                check_translation('source', target)

    def test_empty_and_missing_targets_use_english(self):
        language = {'strings': {'Cancel': ''}, 'overrides': {}}
        self.assertEqual(choose(language, 'a', 'Cancel'), 'Cancel')
        self.assertEqual(choose(language, 'a', 'Continue'), 'Continue')

    def test_context_overrides_and_stale_source(self):
        language = {'strings': {'Cancel': '取消'}, 'overrides': {'a': {'source': 'Cancel', 'target': 'キャンセル'}}}
        self.assertEqual(choose(language, 'a', 'Cancel'), 'キャンセル')
        with self.assertRaises(ValueError):
            choose(language, 'a', 'Continue')

    def test_duplicate_json_keys_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/'duplicate.json'
            path.write_text('{"strings": {"Cancel": "one", "Cancel": "two"}}', encoding='utf-8')
            with self.assertRaises(ValueError):
                load_json(path)

    def test_language_profile_mismatch_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/'ja-JP.json'
            path.write_text(json.dumps({'schema_version': 1, 'locale': 'ja-JP', 'source_sha256': 'wrong',
                                        'strings': {}, 'overrides': {}}), encoding='utf-8')
            with self.assertRaises(ValueError):
                load_language(path, {'source_sha256': 'expected'})

    def test_unknown_source_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/'untrusted.exe'
            path.write_bytes(b'not a supported program')
            with self.assertRaises(ValueError):
                profile_for(path)

    def test_other_language_never_loads_chinese_or_changes_inputs(self):
        resources = {(10, 'TTEST', 0): form(), (6, 1, 0): encode_stringtable(['Continue']+['']*15)}
        language = {'strings': {'Cancel': 'キャンセル'}, 'overrides': {}}
        with patch('core.read_resources', return_value=resources):
            updates, applied, remaining = plan('unused', language)
        result = {v['prop']:v['text'] for v in DFM(updates[(10, 'TTEST', 0)]).values}
        self.assertEqual(result['Caption'], 'キャンセル')
        self.assertEqual(result['Text'], 'Cancel')
        self.assertNotIn((6, 1, 0), updates)
        self.assertTrue(any(x['source'] == 'Continue' for x in remaining))
        self.assertEqual(len(applied), 1)

    def test_unknown_context_override_rejected(self):
        language = {'strings': {}, 'overrides': {'DFM|TYPO': {'source': 'Cancel', 'target': '取消'}}}
        with patch('core.read_resources', return_value={(10, 'TTEST', 0): form()}):
            with self.assertRaises(ValueError):
                plan('unused', language)

if __name__ == '__main__':
    unittest.main()
