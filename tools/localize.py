"""Extract, scaffold, check, build and package independent MobaXterm languages."""
import argparse
import os
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

from core import (PROJECT, LOCALE, catalog, load_json, load_language, plan, profile_for,
                  remove_signature, save_json, sha256, verify)
from pe_resources import update_resources

def copy_optional_document(source, destination):
    """Include release documentation when present; its absence never blocks packaging."""
    if source.is_file():
        shutil.copyfile(source, destination)

def build(source, language_path, output, force=False):
    if os.name != 'nt':
        raise ValueError('PE resource writing requires Windows')
    source, output = Path(source).resolve(), Path(output).resolve()
    if source == output:
        raise ValueError('Source and output must be different files')
    if output.exists() and not force:
        raise ValueError('Output exists; choose another path or use --force')
    profile = profile_for(source)
    language = load_language(language_path, profile)
    updates, applied, remaining = plan(source, language)
    if not applied:
        raise ValueError('No translated text applies to this source; fill in the language dictionary first')
    output.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix='.localize-', suffix='.exe', dir=output.parent)
    os.close(fd)
    temporary = Path(temp_name)
    try:
        shutil.copyfile(source, temporary)
        update_resources(temporary, updates)
        remove_signature(temporary)
        hashes = verify(source, temporary, updates)
        report = {'version': profile['version'], 'locale': language['locale'],
                  'source_sha256': profile['source_sha256'], 'output_sha256': sha256(temporary.read_bytes()),
                  'language_sha256': sha256(Path(language_path).read_bytes()),
                  'changed_resources': len(updates), 'translated_texts': len(applied),
                  'translated_form_texts': sum(x['anchor'].startswith('DFM|') for x in applied),
                  'translated_stringtable_entries': sum(x['anchor'].startswith('STRINGTABLE|') for x in applied),
                  'unchanged_entries_including_names_and_tokens': len(remaining),
                  'non_resource_section_sha256': hashes,
                  'validation': ['supported source hash', 'DFM round-trip', 'format placeholders',
                                 'resource inventory and unchanged resources', 'byte-identical non-resource sections'],
                  'signature': 'modified copy is unsigned', 'ui_testing': 'not performed by this build command'}
        os.replace(temporary, output)
    finally:
        if temporary.exists():
            temporary.unlink()
    save_json(output.with_suffix('.build.json'), report)
    save_json(output.with_suffix('.translations.json'), applied)
    save_json(output.with_suffix('.remaining.json'), remaining)
    return report

def package(args):
    source = args.source.resolve()
    profile = profile_for(source)
    if args.locale not in load_json(PROJECT/'config/versions.json')['release_locales']:
        raise ValueError('Official release packaging currently supports zh-CN only; use build for other languages')
    locale_path = args.language or PROJECT/'locales'/f'{args.locale}.json'
    language = load_language(locale_path, profile)
    if language['locale'] != args.locale:
        raise ValueError('Requested locale and dictionary locale differ')
    plugins = {}
    for name, expected in profile['plugins'].items():
        path = args.plugins_dir/name
        if not path.is_file() or sha256(path.read_bytes()) != expected:
            raise ValueError(f'Missing or mismatched official plugin: {name}')
        plugins[name] = path
    args.output_dir.mkdir(parents=True, exist_ok=True)
    stem = f"MobaXterm-Home-{profile['version']}-{args.locale}-portable"
    archive = args.output_dir/f'{stem}.zip'
    if archive.exists() and not args.force:
        raise ValueError('Release ZIP exists; use --force to rebuild')
    with tempfile.TemporaryDirectory(prefix='moba-release-') as stage:
        root = Path(stage)/stem
        root.mkdir()
        exe = root/f"MobaXterm_{profile['version']}_{args.locale}.exe"
        report = build(source, locale_path, exe)
        # Detailed translation inventories are development outputs, not runtime files.
        exe.with_suffix('.translations.json').unlink()
        exe.with_suffix('.remaining.json').unlink()
        for name, path in plugins.items():
            shutil.copyfile(path, root/name)
        copy_optional_document(PROJECT/'docs/RELEASE_README.txt', root/'使用说明.txt')
        copy_optional_document(PROJECT/'THIRD_PARTY_NOTICES.md', root/'THIRD_PARTY_NOTICES.md')
        copy_optional_document(PROJECT/'docs/KNOWN_ISSUES.md', root/'KNOWN_ISSUES.md')
        checksums = ''.join(f'{sha256(p.read_bytes())}  {p.name}\n' for p in sorted(root.iterdir()) if p.is_file())
        (root/'SHA256SUMS.txt').write_text(checksums, encoding='utf-8')
        with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as z:
            for path in sorted(root.iterdir()):
                z.write(path, f'{stem}/{path.name}')
    with zipfile.ZipFile(archive) as z:
        if z.testzip() is not None:
            raise ValueError('ZIP CRC verification failed')
    checksum_file = archive.with_suffix('.zip.sha256')
    checksum_file.write_text(f'{sha256(archive.read_bytes())}  {archive.name}\n', encoding='utf-8')
    save_json(archive.with_suffix('.build.json'), report)
    copy_optional_document(PROJECT/'docs/RELEASE_NOTES.md', args.output_dir/'RELEASE_NOTES.md')
    print(f'Release ZIP: {archive.resolve()}')
    print(f'SHA-256: {sha256(archive.read_bytes())}')

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    for name in ('extract', 'init-language', 'check', 'build', 'package'):
        p = sub.add_parser(name)
        p.add_argument('--source', type=Path, required=True, help='Unmodified supported official executable')
        if name in ('extract', 'init-language', 'build'):
            p.add_argument('--output', type=Path, required=True)
        if name == 'init-language':
            p.add_argument('--locale', required=True)
            p.add_argument('--name', required=True)
        if name in ('check', 'build', 'package'):
            p.add_argument('--language', type=Path, help='Independent language JSON; no Chinese fallback')
            p.add_argument('--locale', default='zh-CN')
        if name in ('extract', 'init-language', 'build', 'package'):
            p.add_argument('--force', action='store_true')
        if name == 'package':
            p.add_argument('--plugins-dir', type=Path, required=True)
            p.add_argument('--output-dir', type=Path, default=PROJECT/'release-assets')
    args = parser.parse_args(argv)
    profile = profile_for(args.source)
    if args.command in ('extract', 'init-language'):
        if args.output.exists() and not args.force:
            raise ValueError('Output exists; choose another path or use --force')
        entries = catalog(args.source)
        if args.command == 'extract':
            save_json(args.output, {'version': profile['version'], 'source_sha256': profile['source_sha256'], 'entries': entries})
        else:
            if not LOCALE.fullmatch(args.locale):
                raise ValueError('Invalid locale identifier')
            save_json(args.output, {'schema_version': 1, 'locale': args.locale, 'name': args.name,
                                   'source_sha256': profile['source_sha256'],
                                   'strings': {s: '' for s in sorted({e['source'] for e in entries})}, 'overrides': {}})
        print(f'Written: {args.output}')
    elif args.command == 'package':
        package(args)
    else:
        language_path = args.language or PROJECT/'locales'/f'{args.locale}.json'
        language = load_language(language_path, profile)
        if args.command == 'check':
            updates, applied, remaining = plan(args.source, language)
            print(f"{language['locale']}: {len(applied)} translated entries in {len(updates)} resources; {len(remaining)} unchanged entries (includes names and tokens)")
        else:
            report = build(args.source, language_path, args.output, args.force)
            print(f"Built {report['locale']}: {report['translated_texts']} translated entries -> {args.output}")
    return 0

if __name__ == '__main__':
    try:
        sys.exit(main())
    except (ValueError, OSError, KeyError, TypeError) as error:
        print(f'Error: {error}', file=sys.stderr)
        sys.exit(1)
