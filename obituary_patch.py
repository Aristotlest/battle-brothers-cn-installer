"""Narrow, lossless BB player obituary operand-order postprocessor.

Only the encrypted instruction block of getObituaryInfo may change. This is
an independent format reader, not a general Squirrel bytecode editor. Unknown
formats must fail closed rather than produce a seemingly successful install.
"""
import struct


_KEY = (238473842, 20047425, 14005, 978629342)
_MASK = 0xffffffff
_HEADER = b'\xfa\xfaRIQS' + struct.pack('<iii', 1, 4, 4)
_SOURCE = 'scripts/entity/tactical/player'
_FUNCTION = 'getObituaryInfo'
_MAX_FILE = 16 * 1024 * 1024
_MAX_COUNT = 1_000_000
_MAX_DEPTH = 64


def _crypt(data, decrypt=False):
    """BB XXTEA: little-endian words, no padding, trailing 0..3 bytes clear."""
    end = len(data) & ~3
    if end < 8:
        return data
    values = list(struct.unpack('<%dI' % (end // 4), data[:end]))
    count = len(values)
    rounds = 6 + 52 // count
    total = (rounds * 0x9e3779b9) & _MASK if decrypt else 0

    def mix(z, y, p, e):
        return (((z >> 5 ^ y << 2) + (y >> 3 ^ z << 4)) ^
                ((total ^ y) + (_KEY[(p & 3) ^ e] ^ z)))

    if decrypt:
        y = values[0]
        for _ in range(rounds):
            e = total >> 2 & 3
            for p in range(count - 1, -1, -1):
                z = values[(p - 1) % count]
                values[p] = (values[p] - mix(z, y, p, e)) & _MASK
                y = values[p]
            total = (total - 0x9e3779b9) & _MASK
    else:
        z = values[-1]
        for _ in range(rounds):
            total = (total + 0x9e3779b9) & _MASK
            e = total >> 2 & 3
            for p in range(count):
                y = values[(p + 1) % count]
                values[p] = (values[p] + mix(z, y, p, e)) & _MASK
                z = values[p]
    return struct.pack('<%dI' % count, *values) + data[end:]


class _Reader:
    def __init__(self, data):
        if not isinstance(data, bytes) or len(data) > _MAX_FILE:
            raise ValueError('Expected bounded bytecode bytes')
        self.data = data
        self.pos = 0
        self.nodes = 0
        self.budget = 2_000_000
        self.targets = []

    def take(self, size):
        if size < 0 or size > len(self.data) - self.pos:
            raise ValueError('Truncated or invalid bytecode size')
        start = self.pos
        self.pos += size
        return self.data[start:self.pos]

    def tag(self, expected=b'TRAP'):
        if self.take(len(expected)) != expected:
            raise ValueError('Unexpected bytecode tag')

    def integer(self):
        return struct.unpack('<i', self.take(4))[0]

    def count(self):
        value = self.integer()
        self.budget -= value
        if not 0 <= value <= _MAX_COUNT or self.budget < 0:
            raise ValueError('Bytecode count exceeds bounds')
        return value

    def obj(self):
        kind = self.integer()
        if kind == 0x08000010:
            size = self.count()
            try:
                return _crypt(self.take(size), decrypt=True).decode('utf-8')
            except UnicodeDecodeError as exc:
                raise ValueError('Invalid UTF-8 bytecode string') from exc
        if kind == 0x05000002:
            return self.integer()
        if kind == 0x05000004:
            return struct.unpack('<f', self.take(4))[0]
        if kind == 0x01000001:
            return None
        raise ValueError('Unsupported bytecode object type')

    def function(self, depth=0):
        self.nodes += 1
        if depth > _MAX_DEPTH or self.nodes > 8192:
            raise ValueError('Bytecode function tree exceeds bounds')
        self.tag()
        source, name = self.obj(), self.obj()
        self.tag()
        nl, np, no, nv, ni, nd, nc, nf = (self.count() for _ in range(8))
        self.tag()
        literals = [self.obj() for _ in range(nl)]
        self.tag()
        for _ in range(np):
            self.obj()
        self.tag()
        for _ in range(no):
            self.integer()
            self.obj()
            self.obj()
        self.tag()
        locals_ = []
        for _ in range(nv):
            local = self.obj()
            pos, start, end = struct.unpack('<III', self.take(12))
            locals_.append((local, pos, start, end))
        self.tag()
        self.take(8 * ni)
        self.tag()
        self.take(4 * nd)
        self.tag()
        offset = self.pos
        raw = _crypt(self.take(8 * nc), decrypt=True)
        self.tag()
        for _ in range(nf):
            self.function(depth + 1)
        stack = self.integer()
        generator = self.take(1)
        varparams = self.integer()
        if not 0 <= stack <= 255 or generator not in (b'\x00', b'\x01') or varparams not in (0, 1):
            raise ValueError('Invalid function stack or flags')
        for local, pos, start, end in locals_:
            if not isinstance(local, str) or pos >= stack or not 0 <= start <= end <= nc:
                raise ValueError('Invalid local variable metadata')
        if name == _FUNCTION:
            if stack < 8 or generator != b'\x00' or varparams != 0:
                raise ValueError('Unsupported obituary stack or flags')
            self.targets.append(dict(source=source, name=name, literals=literals,
                                     locals=locals_, offset=offset, raw=raw,
                                     stack=stack, generator=generator,
                                     varparams=varparams))


def patch_player(data):
    """Return (bytes, report); raise ValueError for unsupported/malformed input.

    Report keys: status, function, changed_instructions, instruction_index.
    No input or output files are opened, and no upstream code is imported.
    """
    reader = _Reader(data)
    reader.tag(_HEADER)
    reader.function()
    reader.tag(b'LIAT')
    if reader.pos != len(data):
        raise ValueError('Trailing bytecode data')
    if len(reader.targets) != 1:
        raise ValueError('Expected exactly one getObituaryInfo function')
    target = reader.targets[0]
    if target['source'] not in (_SOURCE, _SOURCE + '.nut'):
        raise ValueError('Unsupported getObituaryInfo source')
    if 'KilledBy' not in target['literals']:
        raise ValueError('Missing KilledBy obituary literal')
    raw = bytearray(target['raw'])
    ops = list(struct.iter_unpack('<iBBBB', raw))
    matches = []
    for i in range(len(ops) - 4):
        literal, opcode, dest, second, method = ops[i]
        if (opcode, dest, second) != (4, 5, 6):
            continue
        literals = target['literals']
        if not (0 <= literal < len(literals) and method < len(literals)):
            continue
        if literals[literal] != '被' or literals[method] != 'getKilledName':
            continue
        if ops[i + 1:i + 4] != [(6, 7, 6, 2, 7), (6, 6, 6, 7, 1),
                                  (6, 17, 5, 5, 0)]:
            continue
        final = ops[i + 4]
        if final in ((5, 17, 4, 4, 0), (4, 17, 4, 5, 0)):
            matches.append((i + 4, final == (4, 17, 4, 5, 0)))
    if len(matches) != 1:
        raise ValueError('Expected exactly one supported obituary instruction chain')
    index, fixed = matches[0]
    for name, register, first, last in (('killedBy', 4, index, index),
                                        ('_killer', 2, index - 4, index - 2)):
        locals_ = [local for local in target['locals'] if local[0] == name]
        if len(locals_) != 1:
            raise ValueError('Missing or ambiguous obituary local: ' + name)
        _, pos, start, end = locals_[0]
        if pos != register or not (start <= first <= last <= end):
            raise ValueError('Unsupported obituary local register or lifetime: ' + name)
    if fixed:
        return data, dict(status='already_fixed', function=_FUNCTION,
                          changed_instructions=0, instruction_index=index)
    struct.pack_into('<iBBBB', raw, index * 8, 4, 17, 4, 5, 0)
    start = target['offset']
    patched = data[:start] + _crypt(bytes(raw)) + data[start + len(raw):]
    return patched, dict(status='patched', function=_FUNCTION,
                         changed_instructions=1, instruction_index=index)
