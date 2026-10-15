"""Packaging must not require optional release documentation."""
import contextlib
import io
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import localize
from core import sha256


class PackageTests(unittest.TestCase):
    def test_optional_documents_can_be_deleted(self):
        for include_docs in (False, True):
            with self.subTest(include_docs=include_docs), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                project = root / 'project'
                (project / 'config').mkdir(parents=True)
                (project / 'config/versions.json').write_text(
                    json.dumps({'release_locales': ['zh-CN']}), encoding='utf-8')
                docs = {
                    'docs/RELEASE_README.txt': '使用说明.txt',
                    'THIRD_PARTY_NOTICES.md': 'THIRD_PARTY_NOTICES.md',
                    'docs/KNOWN_ISSUES.md': 'KNOWN_ISSUES.md',
                    'docs/RELEASE_NOTES.md': None,
                }
                if include_docs:
                    for name in docs:
                        path = project / name
                        path.parent.mkdir(parents=True, exist_ok=True)
                        path.write_text('Optional document', encoding='utf-8')
                source = root / 'original.exe'
                source.write_bytes(b'original')
                plugin = root / 'CygUtils.plugin'
                plugin.write_bytes(b'official plugin fixture')
                profile = {'version': '26.5', 'plugins': {plugin.name: sha256(plugin.read_bytes())}}
                args = SimpleNamespace(source=source, locale='zh-CN', language=None,
                                       plugins_dir=root, output_dir=root / 'release', force=False)

                def fake_build(source, language, output):
                    output.write_bytes(b'localized fixture')
                    for suffix in ('.build.json', '.translations.json', '.remaining.json'):
                        output.with_suffix(suffix).write_text('{}', encoding='utf-8')
                    return {'locale': 'zh-CN'}

                with patch.object(localize, 'PROJECT', project), \
                     patch.object(localize, 'profile_for', return_value=profile), \
                     patch.object(localize, 'load_language', return_value={'locale': 'zh-CN'}), \
                     patch.object(localize, 'build', side_effect=fake_build), \
                     contextlib.redirect_stdout(io.StringIO()):
                    localize.package(args)
                archive = next(args.output_dir.glob('*.zip'))
                with zipfile.ZipFile(archive) as z:
                    self.assertIsNone(z.testzip())
                    names = {Path(name).name for name in z.namelist()}
                    self.assertIn('MobaXterm_26.5_zh-CN.exe', names)
                    self.assertIn(plugin.name, names)
                    self.assertIn('SHA256SUMS.txt', names)
                    for target in docs.values():
                        if target:
                            self.assertEqual(target in names, include_docs)
                self.assertEqual((args.output_dir / 'RELEASE_NOTES.md').is_file(), include_docs)


if __name__ == '__main__':
    unittest.main()
