"""Read PE resources without loading executable code; update a separate copy on Windows."""
import ctypes as C
import struct
from pathlib import Path

def read_resources(path):
    b = Path(path).read_bytes()
    pe = struct.unpack_from('<I', b, 60)[0]
    count = struct.unpack_from('<H', b, pe + 6)[0]
    optsize = struct.unpack_from('<H', b, pe + 20)[0]
    opt = pe + 24
    dd = opt + (96 if struct.unpack_from('<H', b, opt)[0] == 0x10b else 112)
    root_rva = struct.unpack_from('<I', b, dd + 16)[0]
    sections = [struct.unpack_from('<IIII', b, opt + optsize + i * 40 + 8) for i in range(count)]
    def offset(rva):
        for vs, va, size, raw in sections:
            if va <= rva < va + max(vs, size):
                return raw + rva - va
        raise ValueError('RVA outside sections')
    root = offset(root_rva)
    out = {}
    def walk(rel, keys):
        pos = root + rel
        named, ids = struct.unpack_from('<HH', b, pos + 12)
        for i in range(named + ids):
            name, value = struct.unpack_from('<II', b, pos + 16 + i * 8)
            if name & 0x80000000:
                p = root + (name & 0x7fffffff)
                n = struct.unpack_from('<H', b, p)[0]
                name = b[p+2:p+2+n*2].decode('utf-16le')
            k = keys + (name,)
            if value & 0x80000000:
                walk(value & 0x7fffffff, k)
            else:
                rva, size, cp, _ = struct.unpack_from('<IIII', b, root + value)
                p = offset(rva)
                out[k] = b[p:p+size]
    walk(0, ())
    return out

def update_resources(path, updates):
    k = C.WinDLL('kernel32', use_last_error=True)
    k.BeginUpdateResourceW.argtypes = [C.c_wchar_p, C.c_int]
    k.BeginUpdateResourceW.restype = C.c_void_p
    k.UpdateResourceW.argtypes = [C.c_void_p, C.c_void_p, C.c_void_p, C.c_ushort, C.c_void_p, C.c_uint]
    k.EndUpdateResourceW.argtypes = [C.c_void_p, C.c_int]
    h = k.BeginUpdateResourceW(str(Path(path).resolve()), False)
    if not h:
        raise C.WinError(C.get_last_error())
    def ident(x):
        return C.c_void_p(x) if isinstance(x, int) else C.cast(C.c_wchar_p(x), C.c_void_p)
    try:
        for (typ, name, lang), data in updates.items():
            buf = C.create_string_buffer(data)
            if not k.UpdateResourceW(h, ident(typ), ident(name), lang, buf, len(data)):
                raise C.WinError(C.get_last_error())
    except BaseException:
        k.EndUpdateResourceW(h, True)
        raise
    if not k.EndUpdateResourceW(h, False):
        raise C.WinError(C.get_last_error())

if __name__ == '__main__':
    import sys
    from collections import Counter
    r = read_resources(sys.argv[1])
    print('Resource types:', Counter(k[0] for k in r))
    for key, data in r.items():
        if data.startswith(b'TPF0'):
            print(key, len(data), repr(data[:70]))
