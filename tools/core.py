"""Version-gated, independent-language PE resource localization."""
import hashlib
import codecs
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
    encoding = language.get('runtime_encoding')
    if encoding is not None:
        if not isinstance(encoding, str) or not re.fullmatch(r'cp[0-9]+', encoding):
            raise ValueError('runtime_encoding must be a Windows code page such as cp936, or null')
        try:
            codecs.lookup(encoding)
        except LookupError as error:
            raise ValueError(f'Unknown runtime_encoding: {encoding}') from error
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

def runtime_rules(source_sha256):
    """Only a hash-matched version profile can enable extra display-text locations."""
    if not source_sha256:
        return {'literals': [], 'form_fields': []}
    profiles = load_json(PROJECT / 'config/versions.json')['versions']
    for profile in profiles.values():
        if profile['source_sha256'] == source_sha256 and profile.get('runtime_text'):
            rules = load_json(PROJECT / 'config' / profile['runtime_text'])
            if rules.get('source_sha256') != source_sha256 or rules.get('format') != 'delphi-ansi-static':
                raise ValueError('Runtime-text profile does not match the supported source')
            return rules
    return {'literals': [], 'form_fields': []}

def form_display_text(key, value, fields):
    if visible(value['prop']):
        return True
    anchor = '|'.join(('DFM', str(key[1]), value['path'], value['prop']))
    if anchor not in fields:
        return False
    if value['text'] != fields[anchor]['source']:
        raise ValueError(f'Unexpected initial display text: {anchor}')
    return True

def catalog(source):
    rules = runtime_rules(profile_for(source)['source_sha256'])
    fields = {entry['anchor']: entry for entry in rules['form_fields']}
    entries = []
    for key, data in read_resources(source).items():
        if data.startswith(b'TPF0'):
            for value in DFM(data).values:
                if form_display_text(key, value, fields) and value['text']:
                    entries.append({'anchor': '|'.join(('DFM', str(key[1]), value['path'], value['prop'])),
                                    'source': value['text'], 'resource': list(key)})
        elif key[0] == 6:
            for index, text in enumerate(stringtable(data)):
                if text:
                    entries.append({'anchor': f'STRINGTABLE|{key[1]}|{index}',
                                    'source': text, 'resource': list(key)})
    for slot in rules['literals']:
        entries.append({'anchor': 'LITERAL|' + slot['id'], 'source': slot['source'],
                        'offset': slot['offset'], 'capacity_bytes': len(slot['source'].encode('ascii')),
                        'requires': 'runtime_encoding matching the target Windows ANSI code page'})
    return entries

def choose(language, anchor, source):
    override = language.get('overrides', {}).get(anchor)
    if override:
        if override['source'] != source:
            raise ValueError(f'Stale context override: {anchor}')
        return override['target'] or source
    return language['strings'].get(source) or source

def form_translation(language, anchor, source, fields):
    """A button's restore field must use the same contextual translation as its caption."""
    mirror = fields.get(anchor, {}).get('mirror_of')
    if not mirror:
        return choose(language, anchor, source)
    target = choose(language, mirror, source)
    if anchor in language.get('overrides', {}) and choose(language, anchor, source) != target:
        raise ValueError(f'Restore-field override conflicts with its caption: {anchor}; override {mirror} instead')
    return target

def verify_restore_fields(data, resource, fields):
    values = {'|'.join(('DFM', str(resource), v['path'], v['prop'])): v['text'] for v in DFM(data).values}
    for anchor, field in fields.items():
        if anchor in values and field.get('mirror_of'):
            if field['mirror_of'] not in values or values[anchor] != values[field['mirror_of']]:
                raise ValueError(f'Toolbar layout would restore a different caption: {anchor}')

def plan(source, language):
    updates, applied, remaining = {}, [], []
    rules = runtime_rules(language.get('source_sha256'))
    fields = {entry['anchor']: entry for entry in rules['form_fields']}
    known_anchors = {'LITERAL|' + entry['id'] for entry in rules['literals']}
    for key, data in read_resources(source).items():
        if data.startswith(b'TPF0'):
            form = DFM(data)
            changes = []
            for value in form.values:
                if not form_display_text(key, value, fields) or not value['text']:
                    continue
                anchor = '|'.join(('DFM', str(key[1]), value['path'], value['prop']))
                known_anchors.add(anchor)
                text = value['text']
                target = form_translation(language, anchor, text, fields)
                entry = {'anchor': anchor, 'source': text}
                if text != target:
                    check_translation(text, target)
                    changes.append((value, target))
                    applied.append({**entry, 'target': target})
                else:
                    remaining.append(entry)
            if changes:
                updates[key] = form.replace(changes)
            verify_restore_fields(updates.get(key, data), key[1], fields)
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

def prepare_literal(data, slot, target, encoding):
    """Replace only an approved AnsiString's length/data, inside its original capacity."""
    p = int(slot['offset'], 0)
    original = slot['source'].encode('ascii')
    expected = b'\xff'*4 + struct.pack('<I', len(original)) + original + b'\0'
    if p < 8 or data[p-8:p+len(original)+1] != expected:
        raise ValueError(f'Unexpected static string record: {slot["id"]}')
    check_translation(slot['source'], target)
    try:
        translated = target.encode(encoding)
    except UnicodeEncodeError as error:
        raise ValueError(f'{slot["id"]} cannot be encoded using {encoding}') from error
    if b'\0' in translated or len(translated) > len(original):
        raise ValueError(f'{slot["id"]}: translation requires {len(translated)} bytes, '
                         f'capacity is {len(original)}; use a shorter LITERAL context override')
    before = data[p-4:p+len(original)+1]
    after = struct.pack('<I', len(translated)) + translated + bytes(len(original)+1-len(translated))
    return {'offset': p-4, 'before': before, 'after': after,
            'anchor': 'LITERAL|' + slot['id'], 'source': slot['source'], 'target': target,
            'encoding': encoding, 'section': slot['section']}

def runtime_plan(source, language):
    rules = runtime_rules(language.get('source_sha256'))
    changes, applied, remaining = [], [], []
    data = Path(source).read_bytes() if rules['literals'] else b''
    encoding = language.get('runtime_encoding')
    for slot in rules['literals']:
        # Detect outdated offsets or accidental edits to the reviewed profile.
        prepare_literal(data, slot, slot['source'], 'ascii')
        p = int(slot['offset'], 0)
        if section_for_offset(data, p-8, len(slot['source'])+9) != slot['section']:
            raise ValueError(f'Static string is outside its approved section: {slot["id"]}')
        anchor = 'LITERAL|' + slot['id']
        target = choose(language, anchor, slot['source']) if encoding else slot['source']
        if target != slot['source']:
            change = prepare_literal(data, slot, target, encoding)
            changes.append(change)
            applied.append({'anchor': anchor, 'source': slot['source'], 'target': target,
                            'file_offset': slot['offset'], 'encoding': encoding})
        else:
            remaining.append({'anchor': anchor, 'source': slot['source']})
    return changes, applied, remaining

def patch_literal_bytes(data, changes):
    result = bytearray(data)
    end = -1
    for change in sorted(changes, key=lambda x: x['offset']):
        p = change['offset']
        before, after = change['before'], change['after']
        if p < end or len(before) != len(after) or result[p:p+len(before)] != before:
            raise ValueError('Static-string patches overlap, resize a slot, or do not match the source')
        result[p:p+len(before)] = after
        end = p + len(before)
    return bytes(result)

def section_for_offset(data, offset, size):
    pe = struct.unpack_from('<I', data, 60)[0]
    count = struct.unpack_from('<H', data, pe+6)[0]
    optsize = struct.unpack_from('<H', data, pe+20)[0]
    for index in range(count):
        p = pe+24+optsize+index*40
        length, raw = struct.unpack_from('<II', data, p+16)
        if raw <= offset and offset+size <= raw+length:
            return data[p:p+8].rstrip(b'\0').decode('ascii')
    raise ValueError('Static string is outside initialized PE sections')

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

def verify(source, output, updates, literal_changes=()):
    before, after = read_resources(source), read_resources(output)
    if set(before) != set(after):
        raise ValueError('Resource inventory changed')
    for key, data in before.items():
        if after[key] != updates.get(key, data):
            raise ValueError(f'Unexpected resource modification: {key}')
    expected = patch_literal_bytes(Path(source).read_bytes(), literal_changes)
    a, b = sections(expected), sections(Path(output).read_bytes())
    if set(a) != set(b):
        raise ValueError('PE section inventory changed')
    hashes = {}
    for name, data in a.items():
        if name != '.rsrc':
            if b[name] != data:
                raise ValueError(f'Executable section changed: {name}')
            hashes[name] = sha256(data)
    return hashes
