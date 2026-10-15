"""Prove a bounded signed-offset switch table before reusing upstream landing pads."""
import re
import struct


def writes(ins, register):
    aliases = {'x' + register[1:], 'w' + register[1:]}
    return any(ins.reg_name(r) in aliases for r in ins.regs_access()[1])


def signed_jump_range(instructions, image, text_end, ro_start, data_start, all_instructions=None):
    if not instructions or (instructions[-1].mnemonic, instructions[-1].op_str) != ('br', 'x18'):
        return None
    tail = list(instructions[-256:])
    loads = [(i, re.fullmatch(r'(x\d+), \[(x\d+), x18, lsl #2\]', ins.op_str))
             for i, ins in enumerate(tail) if ins.mnemonic == 'ldrsw']
    loads = [(i, match) for i, match in loads if match]
    if not loads:
        return None
    index, match = loads[-1]
    loaded_reg, base_reg = match.groups()
    if loaded_reg == base_reg or index + 1 >= len(tail) or tail[index + 1].mnemonic != 'add':
        return None
    if tail[index + 1].op_str not in (f'x18, {loaded_reg}, {base_reg}', f'x18, {base_reg}, {loaded_reg}'):
        return None
    if any(writes(ins, 'x18') for ins in tail[index + 2:-1]):
        return None
    # Require a dominating unsigned bound and a preceding W18 write (upper bits zero).
    bound_index = compare_index = None
    for j in range(index - 1, 0, -1):
        ins = tail[j]
        if writes(ins, 'x18'):
            return None
        if ins.mnemonic == 'b.hi':
            compare_index = next((k for k in range(j - 1, -1, -1)
                                  if 'nzcv' in [tail[k].reg_name(r) for r in tail[k].regs_access()[1]]), None)
            if compare_index is None or tail[compare_index].mnemonic != 'cmp':
                return None
            previous = tail[compare_index]
            cmp_match = re.fullmatch(r'w18, #(0x[0-9a-f]+|\d+)', previous.op_str)
            if cmp_match:
                if any(writes(p, 'x18') for p in tail[compare_index + 1:j]):
                    return None
                bound_index, bound = j, int(cmp_match.group(1), 0)
                break
    if bound_index is None or bound > 4095:
        return None
    producer = next((ins for ins in reversed(tail[:compare_index]) if writes(ins, 'x18')), None)
    if producer is None or not producer.op_str.startswith('w18,'):
        return None
    first, last = tail[bound_index].address + 4, tail[-1].address
    for ins in tail[bound_index + 1:-1]:
        if ins.mnemonic in ('b', 'bl', 'br', 'blr', 'ret', 'cbz', 'cbnz', 'tbz', 'tbnz') or ins.mnemonic.startswith('b.'):
            return None
    # The W18 producer and guard form one straight-line region. Incoming edges
    # from either side of the branch must not bypass the producer or comparison.
    producer_start = producer.address
    for ins in tail:
        if producer_start < ins.address < first and ins.address != tail[bound_index].address:
            if ins.mnemonic in ('b', 'bl', 'br', 'blr', 'ret', 'cbz', 'cbnz', 'tbz', 'tbnz') or ins.mnemonic.startswith('b.'):
                return None
    if producer_start < tail[bound_index].operands[-1].imm <= last:
        return None
    for ins in (instructions if all_instructions is None else all_instructions):
        if ins.mnemonic in ('b', 'bl', 'cbz', 'cbnz', 'tbz', 'tbnz') or ins.mnemonic.startswith('b.'):
            target = ins.operands[-1].imm
            if producer_start < target <= last and not producer_start <= ins.address <= last:
                return None
    adjustment, base = 0, None
    for ins in reversed(tail[:index]):
        if ins.mnemonic in ('bl', 'blr'):
            return None
        if not writes(ins, base_reg):
            continue
        add = re.fullmatch(rf'{base_reg}, {base_reg}, #(0x[0-9a-f]+|\d+)', ins.op_str)
        if ins.mnemonic == 'add' and add:
            adjustment += int(add.group(1), 0)
        elif ins.mnemonic in ('adrp', 'adr') and ins.op_str.startswith(base_reg + ','):
            base = ins.operands[1].imm + adjustment
            break
        else:
            return None
    if base is None or not ro_start <= base or base + (bound + 1) * 4 > min(data_start, len(image)):
        return None
    targets = [base + struct.unpack_from('<i', image, base + i * 4)[0] for i in range(bound + 1)]
    if any(t % 4 or not 0 <= t < text_end for t in targets):
        return None
    low, high = min(targets), max(targets)
    if high - low > 1024**2:
        return None
    # Upstream maps target = base + 4*i to a register-preserving landing pad.
    # A conservative dense range includes all proven destinations, without table mutation.
    return low, (high - low) // 4 + 1
