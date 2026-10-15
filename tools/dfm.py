"""Lossless scanner for Delphi binary form resources (TPF0)."""
import struct

class DFM:
    def __init__(self, data):
        self.data, self.pos, self.values = data, 4, []
        if data[:4] != b'TPF0':
            raise ValueError('Not a binary Delphi form')
        self.object('')
        if self.pos != len(data):
            raise ValueError(f'Trailing data at {self.pos}/{len(data)}')

    def take(self, n):
        p = self.pos
        self.pos += n
        if self.pos > len(self.data):
            raise ValueError(f'Truncated DFM at {p}')
        return self.data[p:self.pos]

    def num(self, fmt):
        return struct.unpack(fmt, self.take(struct.calcsize(fmt)))[0]

    def short(self):
        return self.take(self.num('B')).decode('latin1')

    def props(self, path):
        while True:
            prop = self.short()
            if not prop:
                return
            self.value(path, prop)

    def object(self, parent):
        if self.data[self.pos] & 0xf0 == 0xf0:
            flags = self.num('B') & 0x0f
            if flags & 2:
                self.value(parent, '@childpos')
        cls, name = self.short(), self.short()
        path = parent + '/' + name
        self.props(path)
        while self.data[self.pos] != 0:
            self.object(path)
        self.pos += 1

    def value(self, path, prop):
        start = self.pos
        typ = self.num('B')
        text = None
        if typ in (0, 8, 9, 13):
            pass
        elif typ in (2, 3, 4, 5, 15, 16, 17, 19, 21):
            self.take({2:1, 3:2, 4:4, 5:10, 15:4, 16:8, 17:8, 19:8, 21:8}[typ])
        elif typ in (6, 7):
            text = self.short()
        elif typ in (12, 20):
            raw = self.take(self.num('<I'))
            text = raw.decode('utf-8' if typ == 20 else 'latin1')
        elif typ == 18:
            text = self.take(self.num('<I') * 2).decode('utf-16le')
        elif typ == 10:
            self.take(self.num('<I'))
        elif typ == 11:
            while self.short():
                pass
        elif typ == 1:
            i = 0
            while self.data[self.pos] != 0:
                self.value(path, f'{prop}[{i}]')
                i += 1
            self.pos += 1
        elif typ == 14:
            i = 0
            while self.data[self.pos] != 0:
                if self.data[self.pos] in (2, 3, 4):
                    self.value(path, f'{prop}[{i}].@order')
                if self.num('B') != 1:
                    raise ValueError('Expected collection item')
                self.props(path + f'/{prop}[{i}]')
                i += 1
            self.pos += 1
        else:
            raise ValueError(f'Unknown type {typ} at {start}, {path}.{prop}')
        if text is not None and typ != 7:
            self.values.append(dict(path=path, prop=prop, text=text, start=start, end=self.pos, typ=typ))

    def replace(self, changes):
        data = self.data
        for item, text in sorted(changes, key=lambda x:x[0]['start'], reverse=True):
            raw = text.encode('utf-16le')
            value = b'\x12' + struct.pack('<I', len(raw)//2) + raw
            data = data[:item['start']] + value + data[item['end']:]
        DFM(data)
        return data

def stringtable(data):
    p, out = 0, []
    for i in range(16):
        n = struct.unpack_from('<H', data, p)[0]
        p += 2
        out.append(data[p:p+n*2].decode('utf-16le'))
        p += n*2
    if any(data[p:]):
        raise ValueError('Invalid string table')
    return out

def encode_stringtable(strings):
    if len(strings) != 16:
        raise ValueError('A string table block must contain exactly 16 entries')
    out = b''
    for s in strings:
        b = s.encode('utf-16le')
        if len(b)//2 > 65535:
            raise ValueError('A string table entry exceeds 65535 UTF-16 code units')
        out += struct.pack('<H', len(b)//2) + b
    return out

if __name__ == '__main__':
    import sys, json
    from pe_resources import read_resources
    out = {}
    for key, data in read_resources(sys.argv[1]).items():
        if data.startswith(b'TPF0'):
            out[str(key[1])] = DFM(data).values
    print(json.dumps(out, ensure_ascii=False, indent=2))
