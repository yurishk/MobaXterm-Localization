"""Version-gated, independent-language PE resource localization."""
import hashlib
import json
import re
import struct
from pathlib import Path

from dfm import DFM, encode_stringtable, stringtable
from pe_resources import read_resources

PROJECT = Path(__file__).resolve().parents[1]
TOKEN = re.compile(r'%(?:host|port|user|pass)\b|%(?:\d+:)?[-+0]*(?:\d+|\*)?(?:\.(?:\d+|\*))?[a-zA-Z%]')
LOCALE = re.compile(r'[a-z]{2,3}(?:-[A-Za-z0-9]{2,8})*\Z')

def load_json(path):
    def unique(pairs):
        result = {}
        for k, v in pairs:
            if k in result:
                raise ValueError(f'Duplicate JSON key: {k!r}')
            result[k] = v
        return result
    return json.loads(Path(path).read_text(encoding='utf-8-sig'), object_pairs_hook=unique)

def save_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

def sha256(data):
    return hashlib.sha256(data).hexdigest()

def visible(prop):
    # Input values, Items.Strings, font names, event handlers and protocol tokens
    # deliberately stay unchanged, even when their source text is in a dictionary.
    return prop.endswith('Caption') or prop in ('Hint', 'Title')

def check_translation(source, target):
    if not isinstance(source, str) or not isinstance(target, str):
        raise ValueError('Translation keys and values must be strings')
    if '\ufffd' in target or '\0' in target:
        raise ValueError(f'Invalid character in translation: {source!r}')
    target.encode('utf-16le')
    if target and TOKEN.findall(source) != TOKEN.findall(target):
        raise ValueError(f'Format/command placeholders changed: {source!r}')

def load_language(path, profile=None):
    language = load_json(path)
    if language.get('schema_version') != 1 or not LOCALE.fullmatch(language.get('locale', '')):
        raise ValueError('Expected schema_version=1 and a locale such as zh-CN or ja-JP')
    if not isinstance(language.get('strings'), dict) or not isinstance(language.get('overrides', {}), dict):
        raise ValueError('strings and overrides must be JSON objects')
    if profile and language.get('source_sha256') != profile['source_sha256']:
        raise ValueError('Language catalog targets a different source; regenerate and review it')
    for source, target in language['strings'].items():
        check_translation(source, target)
    for anchor, entry in language.get('overrides', {}).items():
        if not isinstance(entry, dict) or set(entry) != {'source', 'target'}:
            raise ValueError(f'Invalid context override: {anchor}')
        check_translation(entry['source'], entry['target'])
    return language

def profile_for(source):
    digest = sha256(Path(source).read_bytes())
    profiles = load_json(PROJECT / 'config/versions.json')['versions']
    for profile in profiles.values():
        if profile['source_sha256'] == digest:
            return profile
    raise ValueError(f'Unsupported source SHA-256: {digest}. No file was modified.')

def catalog(source):
    entries = []
    for key, data in read_resources(source).items():
        if data.startswith(b'TPF0'):
            for value in DFM(data).values:
                if visible(value['prop']) and value['text']:
                    entries.append({'anchor': '|'.join(('DFM', str(key[1]), value['path'], value['prop'])),
                                    'source': value['text'], 'resource': list(key)})
        elif key[0] == 6:
            for index, text in enumerate(stringtable(data)):
                if text:
                    entries.append({'anchor': f'STRINGTABLE|{key[1]}|{index}',
                                    'source': text, 'resource': list(key)})
    return entries

def choose(language, anchor, source):
    override = language.get('overrides', {}).get(anchor)
    if override:
        if override['source'] != source:
            raise ValueError(f'Stale context override: {anchor}')
        return override['target'] or source
    return language['strings'].get(source) or source

def plan(source, language):
    updates, applied, remaining = {}, [], []
    known_anchors = set()
    for key, data in read_resources(source).items():
        if data.startswith(b'TPF0'):
            form = DFM(data)
            changes = []
            for value in form.values:
                if not visible(value['prop']) or not value['text']:
                    continue
                anchor = '|'.join(('DFM', str(key[1]), value['path'], value['prop']))
                known_anchors.add(anchor)
                text = value['text']
                target = choose(language, anchor, text)
                entry = {'anchor': anchor, 'source': text}
                if text != target:
                    check_translation(text, target)
                    changes.append((value, target))
                    applied.append({**entry, 'target': target})
                else:
                    remaining.append(entry)
            if changes:
                updates[key] = form.replace(changes)
        elif key[0] == 6:
            strings = stringtable(data)
            translated = []
            for index, text in enumerate(strings):
                anchor = f'STRINGTABLE|{key[1]}|{index}'
                known_anchors.add(anchor)
                target = choose(language, anchor, text)
                check_translation(text, target)
                translated.append(target)
                if target != text:
                    applied.append({'anchor': anchor, 'source': text, 'target': target})
                elif text:
                    remaining.append({'anchor': anchor, 'source': text})
            if translated != strings:
                updates[key] = encode_stringtable(translated)
    unknown = set(language.get('overrides', {})) - known_anchors
    if unknown:
        raise ValueError(f'Unknown context override: {sorted(unknown)[0]}')
    return updates, applied, remaining

def sections(data):
    pe = struct.unpack_from('<I', data, 60)[0]
    count = struct.unpack_from('<H', data, pe + 6)[0]
    optsize = struct.unpack_from('<H', data, pe + 20)[0]
    result = {}
    for index in range(count):
        p = pe + 24 + optsize + index * 40
        name = data[p:p+8].rstrip(b'\0').decode('ascii')
        size, raw = struct.unpack_from('<II', data, p + 16)
        result[name] = data[raw:raw+size] if size else b''
    return result

def remove_signature(path):
    data = bytearray(Path(path).read_bytes())
    opt = struct.unpack_from('<I', data, 60)[0] + 24
    dd = opt + (96 if struct.unpack_from('<H', data, opt)[0] == 0x10b else 112)
    pos, size = struct.unpack_from('<II', data, dd + 32)
    if pos and pos + size == len(data):
        del data[pos:]
    struct.pack_into('<II', data, dd + 32, 0, 0)
    struct.pack_into('<I', data, opt + 64, 0)
    Path(path).write_bytes(data)

def verify(source, output, updates):
    before, after = read_resources(source), read_resources(output)
    if set(before) != set(after):
        raise ValueError('Resource inventory changed')
    for key, data in before.items():
        if after[key] != updates.get(key, data):
            raise ValueError(f'Unexpected resource modification: {key}')
    a, b = sections(Path(source).read_bytes()), sections(Path(output).read_bytes())
    if set(a) != set(b):
        raise ValueError('PE section inventory changed')
    hashes = {}
    for name, data in a.items():
        if name != '.rsrc':
            if b[name] != data:
                raise ValueError(f'Executable section changed: {name}')
            hashes[name] = sha256(data)
    return hashes
