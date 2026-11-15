"""Synthetic format fixtures only; no game bytecode or upstream dependencies."""
import importlib
import struct
import unittest


KEY = (238473842, 20047425, 14005, 978629342)
MASK = 0xffffffff


def crypt(block, decrypt=False):
    """Test-side XXTEA implementation, independent of the patcher's reader."""
    size = len(block) // 4 * 4
    if size < 8:
        return block
    words = list(struct.unpack('<%dI' % (size // 4), block[:size]))
    rounds = 6 + 52 // len(words)
    total = (rounds * 0x9e3779b9) & MASK if decrypt else 0
    for _ in range(rounds):
        if not decrypt:
            total = (total + 0x9e3779b9) & MASK
        selector = total >> 2 & 3
        positions = range(len(words) - 1, -1, -1) if decrypt else range(len(words))
        carry = words[0] if decrypt else words[-1]
        for p in positions:
            y = carry if decrypt else words[(p + 1) % len(words)]
            z = words[(p - 1) % len(words)] if decrypt else carry
            mix = (((z >> 5 ^ y << 2) + (y >> 3 ^ z << 4)) ^
                   ((total ^ y) + (KEY[(p & 3) ^ selector] ^ z)))
            words[p] = (words[p] - mix if decrypt else words[p] + mix) & MASK
            carry = words[p]
        if decrypt:
            total = (total - 0x9e3779b9) & MASK
    return struct.pack('<%dI' % len(words), *words) + block[size:]


def obj(value):
    if isinstance(value, str):
        value = value.encode('utf-8')
    if isinstance(value, bytes):
        return struct.pack('<Ii', 0x08000010, len(value)) + crypt(value)
    if value is None:
        return struct.pack('<I', 0x01000001)
    if isinstance(value, float):
        return struct.pack('<If', 0x05000004, value)
    return struct.pack('<Ii', 0x05000002, value)


def instruction(op, arg0, arg1, arg2, arg3):
    return struct.pack('<iBBBB', arg1, op, arg0, arg2, arg3)


def chain(fixed=False):
    return [instruction(4, 5, 0, 6, 1), instruction(7, 6, 6, 2, 7),
            instruction(6, 6, 6, 7, 1), instruction(17, 5, 6, 5, 0),
            instruction(17, 4, 4 if fixed else 5, 5 if fixed else 4, 0)]


def function(name='getObituaryInfo', source='scripts/entity/tactical/player',
             ops=None, literals=None, locals_=None, children=()):
    ops = chain() if ops is None else ops
    literals = ['被', 'getKilledName', 'KilledBy', 17, 1.5, None] if literals is None else literals
    locals_ = [('killedBy', 4, 0, len(ops)), ('_killer', 2, 0, len(ops))] if locals_ is None else locals_
    counts = (len(literals), 1, 1, len(locals_), 1, 2, len(ops), len(children))
    pieces = [b'TRAP', obj(source), obj(name), b'TRAP', struct.pack('<8i', *counts),
              b'TRAP', b''.join(map(obj, literals)), b'TRAP', obj('this'),
              b'TRAP', struct.pack('<I', 0), obj(0), obj('outer'), b'TRAP']
    pieces += [obj(n) + struct.pack('<III', pos, start, end) for n, pos, start, end in locals_]
    pieces += [b'TRAP', crypt(struct.pack('<ii', 10, 0)), b'TRAP',
               crypt(struct.pack('<ii', 0, 1)), b'TRAP']
    offset = sum(map(len, pieces))
    raw = b''.join(ops)
    pieces += [crypt(raw), b'TRAP', *children, struct.pack('<iBi', 10, 0, 0)]
    return b''.join(pieces), offset, raw


def closure(**kwargs):
    target, relative, raw = function(**kwargs)
    # Nested target and unrelated sibling exercise whole-tree preservation.
    sibling, _, _ = function(name='onDeath', ops=[instruction(0, 0, 0, 0, 0)])
    root, root_ops, root_raw = function(name='main', children=(target, sibling),
                                      ops=[instruction(0, 0, 0, 0, 0)])
    header = b'\xfa\xfaRIQS' + struct.pack('<iii', 1, 4, 4)
    start = len(header) + root_ops + len(root_raw) + 4 + relative
    return header + root + b'LIAT', start, raw


class ObituaryPatchTests(unittest.TestCase):
    def test_changes_only_final_add_operand_order(self):
        data, start, before = closure()
        try:
            module = importlib.import_module('obituary_patch')
        except ModuleNotFoundError:
            self.fail('patch_player is not implemented')
        patched, report = module.patch_player(data)
        self.assertEqual(report['status'], 'patched')
        self.assertEqual(report['function'], 'getObituaryInfo')
        self.assertEqual(report['changed_instructions'], 1)
        self.assertEqual(report['instruction_index'], 4)
        self.assertEqual(len(patched), len(data))
        self.assertEqual(patched[:start], data[:start])
        self.assertEqual(patched[start + len(before):], data[start + len(before):])
        after = crypt(patched[start:start + len(before)], decrypt=True)
        self.assertEqual(after, b''.join(chain(fixed=True)))
        self.assertEqual(before[:-8], after[:-8])
        # Squirrel ADD concatenates arg2 + arg1, not arg1 + arg2.
        registers = {4: '斩杀', 5: '被兽人'}
        arg1, op, arg0, arg2, arg3 = struct.unpack('<iBBBB', after[-8:])
        self.assertEqual(registers[arg2] + registers[arg1], '被兽人斩杀')

    def test_already_fixed_is_explicit_and_byte_identical(self):
        from obituary_patch import patch_player
        data, _, _ = closure(ops=chain(fixed=True))
        patched, report = patch_player(data)
        self.assertEqual(patched, data)
        self.assertEqual(report['status'], 'already_fixed')
        self.assertEqual(report['changed_instructions'], 0)
        self.assertEqual(report['function'], 'getObituaryInfo')

    def test_finds_semantic_chain_without_hardcoded_instruction_index(self):
        from obituary_patch import patch_player
        padding = [instruction(0, 0, 0, 0, 0)] * 3
        data, start, before = closure(ops=padding + chain() + padding)
        patched, report = patch_player(data)
        self.assertEqual(report['instruction_index'], 7)
        self.assertEqual(crypt(patched[start:start + len(before)], True),
                         b''.join(padding + chain(True) + padding))

    def test_requires_expected_player_source(self):
        from obituary_patch import patch_player
        for source in ('other/player', None, 'scripts/entity/tactical/player.nut.bak'):
            with self.subTest(source=source), self.assertRaises(ValueError):
                patch_player(closure(source=source)[0])
        patch_player(closure(source='scripts/entity/tactical/player.nut')[0])

    def test_requires_named_locals_at_live_registers(self):
        from obituary_patch import patch_player
        cases = [[], [('wrong', 4, 0, 5), ('_killer', 2, 0, 5)],
                 [('killedBy', 3, 0, 5), ('_killer', 2, 0, 5)],
                 [('killedBy', 4, 0, 5), ('_killer', 3, 0, 5)],
                 [('killedBy', 4, 0, 3), ('_killer', 2, 0, 5)],
                 [('killedBy', 4, 0, 5), ('_killer', 2, 1, 5)],
                 [('killedBy', 4, 0, 5), ('killedBy', 4, 0, 5), ('_killer', 2, 0, 5)]]
        for locals_ in cases:
            with self.subTest(locals_=locals_), self.assertRaises(ValueError):
                patch_player(closure(locals_=locals_)[0])

    def test_requires_killedby_literal_marker(self):
        from obituary_patch import patch_player
        with self.assertRaises(ValueError):
            patch_player(closure(literals=['被', 'getKilledName'])[0])

    def test_rejects_mutated_semantic_chain(self):
        from obituary_patch import patch_player
        # Mutate each operand and opcode individually, including the literal IDs.
        for i, packed in enumerate(chain()):
            fields = list(struct.unpack('<iBBBB', packed))
            for j in range(5):
                mutated = fields.copy()
                mutated[j] += 1
                ops = chain()
                ops[i] = struct.pack('<iBBBB', *mutated)
                with self.subTest(instruction=i, field=j), self.assertRaises(ValueError):
                    patch_player(closure(ops=ops)[0])

    def test_rejects_missing_or_ambiguous_matches(self):
        from obituary_patch import patch_player
        for kwargs in ({'name': 'onDeath'}, {'ops': []}, {'ops': chain() * 2},
                       {'ops': chain() + chain(True)}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                patch_player(closure(**kwargs)[0])
        child, _, _ = function()
        with self.assertRaises(ValueError):
            patch_player(closure(children=(child,))[0])

    def test_rejects_every_truncation_and_trailing_data(self):
        from obituary_patch import patch_player
        data, _, _ = closure()
        for size in range(len(data)):
            with self.subTest(size=size), self.assertRaises(ValueError):
                patch_player(data[:size])
        with self.assertRaises(ValueError):
            patch_player(data + b'junk')

    def test_rejects_corrupt_tags_counts_objects_and_strings(self):
        from obituary_patch import patch_player
        data, _, _ = closure()
        variants = [data.replace(b'TRAP', b'NOPE', 1), b'no' + data[2:],
                    data[:-4] + b'NOPE',
                    data.replace(struct.pack('<8i', 6, 1, 1, 2, 1, 2, 5, 0),
                                 struct.pack('<8i', -1, 1, 1, 2, 1, 2, 5, 0)),
                    data.replace(struct.pack('<8i', 6, 1, 1, 2, 1, 2, 5, 0),
                                 struct.pack('<8i', 2_000_000, 1, 1, 2, 1, 2, 5, 0)),
                    data.replace(obj('getObituaryInfo')[:4], b'BAD!', 1),
                    closure(literals=[b'\xff', 'getKilledName', 'KilledBy'])[0]]
        for variant in variants:
            with self.subTest(length=len(variant)), self.assertRaises(ValueError):
                patch_player(variant)

    def test_rejects_invalid_function_metadata(self):
        from obituary_patch import patch_player
        data, _, _ = closure()
        footer = struct.pack('<iBi', 10, 0, 0)
        for invalid in (struct.pack('<iBi', -1, 0, 0),
                        struct.pack('<iBi', 300, 0, 0),
                        struct.pack('<iBi', 7, 0, 0),
                        struct.pack('<iBi', 10, 2, 0),
                        struct.pack('<iBi', 10, 0, -1)):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                patch_player(data.replace(footer, invalid, 1))
        with self.assertRaises(ValueError):
            patch_player(closure(locals_=[('killedBy', 4, 0, 900),
                                         ('_killer', 2, 0, 5)])[0])

    def test_rejects_excessive_depth_and_input_size(self):
        from obituary_patch import patch_player
        child, _, _ = function()
        for _ in range(66):
            child, _, _ = function(name='wrapper', children=(child,))
        data = b'\xfa\xfaRIQS' + struct.pack('<iii', 1, 4, 4) + child + b'LIAT'
        with self.assertRaises(ValueError):
            patch_player(data)
        for invalid in (None, 'not bytes', b'x' * (16 * 1024 * 1024 + 1)):
            with self.subTest(kind=type(invalid)), self.assertRaises(ValueError):
                patch_player(invalid)


if __name__ == '__main__':
    unittest.main()
