"""Bounded BNSH/NVN inspection and local-only offline shader preparation.

Layout references (no SDK implementation is copied):
  maierfelix/bnsh-decoder BNSH.ksy (MIT, 2020 Felix Maier), and the locally
  validated BNSH 2.1.2 containers. See native/botw_graphics/THIRD_PARTY_LICENSE.txt.
A hash identifies original input; it does not assert that a translated shader
has correct resource bindings. Game code and translated outputs stay local.
"""
import argparse
import hashlib
import json
import os
import re
from pathlib import Path
import struct
import subprocess

MAGIC = b'BNSH\0\0\0\0'
STAGES = ('vertex', 'tess_control', 'tess_evaluation', 'geometry', 'fragment', 'compute')
MAX_CONTAINER_BYTES = 256 * 1024 * 1024


def parse_bnsh(data):
    """Read unrelocated file offsets, returning metadata without shader bytes."""
    data = bytes(data)
    if len(data) < 0x20 or data[:8] != MAGIC:
        raise ValueError('Invalid BNSH header bounds or magic')
    version = struct.unpack_from('<I', data, 8)[0]
    if data[12:14] != b'\xff\xfe':
        raise ValueError('Unsupported BNSH byte order')
    name, flags, first, relocation, size = struct.unpack_from('<IHHII', data, 0x10)
    if size != len(data) or size > MAX_CONTAINER_BYTES:
        raise ValueError('BNSH declared file size does not match input bounds')
    if flags:
        raise ValueError('BNSH is relocated; expected file-relative offsets')
    if version >> 16 != 2:
        raise ValueError('Unsupported BNSH major version')

    def read(offset, count, label):
        if offset < 0 or count < 0 or offset > size or count > size - offset:
            raise ValueError(label + ' exceeds BNSH file bounds')
        return data[offset:offset + count]

    def unpack(fmt, offset, label):
        return struct.unpack(fmt, read(offset, struct.calcsize(fmt), label))

    def dictionary(pointer):
        if not pointer:
            return []
        if read(pointer, 4, 'reflection dictionary') != b'_DIC':
            raise ValueError('Invalid BNSH reflection dictionary')
        count, = unpack('<I', pointer + 4, 'reflection dictionary count')
        if count > 4096:
            raise ValueError('BNSH reflection dictionary count exceeds limit')
        read(pointer + 0x18, count * 16, 'reflection dictionary nodes')
        result = []
        for i in range(count):
            text, = unpack('<Q', pointer + 0x20 + i * 16, 'reflection dictionary key')
            length, = unpack('<H', text, 'reflection string length')
            try:
                result.append(read(text + 2, length, 'reflection string').decode('utf-8'))
            except UnicodeDecodeError as exc:
                raise ValueError('Invalid reflection UTF-8') from exc
        return result

    def reflection(pointer):
        if not pointer:
            return {}
        result = {}
        for stage, record in zip(STAGES, unpack('<6Q', pointer, 'program reflection')):
            if not record:
                continue
            pointers = unpack('<5Q', record, 'stage reflection dictionaries')
            output, sampler, uniform, storage = unpack('<4I', record + 0x28, 'reflection resource indices')
            slots, = unpack('<Q', record + 0x38, 'reflection slot array')
            groups = {}
            for kind, names, base in zip(('inputs', 'outputs', 'samplers', 'uniforms', 'storage'),
                                         map(dictionary, pointers), (0, output, sampler, uniform, storage)):
                if base > 16384:
                    raise ValueError('Reflection resource index exceeds limit')
                groups[kind] = [{'name': name, 'slot': unpack('<i', slots + (base + i) * 4,
                                                             'reflection resource slot')[0]}
                                for i, name in enumerate(names)]
            result[stage] = groups
        return result

    if relocation:
        if read(relocation, 4, 'relocation table') != b'_RLT':
            raise ValueError('BNSH relocation table magic is invalid')
    if name:
        read(name, 1, 'file name')
        end = data.find(b'\0', name, min(size, name + 4096))
        if end < 0:
            raise ValueError('BNSH file name exceeds bounds')
        try:
            filename = data[name:end].decode('utf-8')
        except UnicodeDecodeError as exc:
            raise ValueError('BNSH file name is not UTF-8') from exc
    else:
        filename = ''
    if read(first, 4, 'grsc block') != b'grsc':
        raise ValueError('BNSH first block is not grsc')
    _, block_bytes = unpack('<II', first + 4, 'grsc block length')
    read(first, block_bytes, 'grsc block')
    api, api_version, code_type = unpack('<HHH', first + 16, 'target metadata')
    count, array, pool = unpack('<IQQ', first + 28, 'shader variations')
    if count > 100000:
        raise ValueError('BNSH shader variation count exceeds limit')
    read(array, count * 0x40, 'shader variation array')
    if pool:
        _, pool_bytes, pool_offset = unpack('<IIQ', pool, 'shader binary pool')
        read(pool_offset, pool_bytes, 'shader binary pool data')
    variations = []
    for index in range(count):
        _, _, program, _ = unpack('<QQQQ', array + index * 0x40, 'shader variation')
        if not program:
            raise ValueError('BNSH has no binary program for variation ' + str(index))
        read(program, 0x60, 'shader program')
        pointers = unpack('<6Q', program + 8, 'shader stages')
        shaders = []
        for stage, pointer in zip(STAGES, pointers):
            if not pointer:
                continue
            read(pointer, 0x40, 'NVN shader descriptor')
            address, control, code, code_size, control_size = unpack(
                '<QQQII', pointer, 'NVN shader descriptor')
            if address:
                raise ValueError('NVN shader address is relocated')
            ctl = read(control, control_size, 'NVN control section')
            binary = read(code, code_size, 'NVN GPU code')
            if len(ctl) < 4 or ctl[:4] != b'\x34\x12\x76\x98':
                raise ValueError('Invalid NVN control section magic')
            if len(binary) < 0x80 or binary[:4] != b'\x78\x56\x34\x12':
                raise ValueError('Invalid NVN GPU code header')
            shaders.append({'stage': stage, 'descriptor_offset': pointer,
                            'control_offset': control, 'control_bytes': control_size,
                            'code_offset': code, 'code_bytes': code_size,
                            'control_sha256': hashlib.sha256(ctl).hexdigest(),
                            'code_sha256': hashlib.sha256(binary).hexdigest()})
        if not shaders:
            raise ValueError('BNSH binary program has no shader stages')
        reflection_pointer, = unpack('<Q', program + 0x78, 'program reflection pointer')
        variations.append({'index': index, 'program_offset': program, 'shaders': shaders,
                           'reflection': reflection(reflection_pointer)})
    return {'name': filename, 'version': f'{version >> 16}.{version >> 8 & 255}.{version & 255}',
            'file_bytes': size, 'sha256': hashlib.sha256(data).hexdigest(),
            'target_api': api, 'target_api_version': api_version,
            'target_code_type': code_type, 'variations': variations}


def inspect_embedded_bnsh(data):
    """Locate complete BNSH files in a bounded local image, rejecting bad candidates."""
    result, cursor = [], 0
    while (cursor := data.find(MAGIC, cursor)) >= 0:
        if len(data) - cursor < 0x20:
            raise ValueError('Embedded BNSH header exceeds image bounds')
        size = struct.unpack_from('<I', data, cursor + 0x1c)[0]
        if size < 0x20 or size > len(data) - cursor or size > MAX_CONTAINER_BYTES:
            raise ValueError('Embedded BNSH declared size exceeds image bounds')
        result.append({'offset': cursor, **parse_bnsh(data[cursor:cursor + size])})
        cursor += size
    return result


def extract_shader_inputs(image, destination):
    """Write only verified NVN control/code spans into a fresh local directory."""
    containers = inspect_embedded_bnsh(image)
    if not containers:
        raise ValueError('No complete BNSH containers found')
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=False)
    records = []
    for container in containers:
        base = container['offset']
        for variation in container['variations']:
            for shader in variation['shaders']:
                row = {'container': container['name'], 'variation': variation['index'],
                       'reflection': variation['reflection'].get(shader['stage'], {}), **shader}
                for kind in ('control', 'code'):
                    filename = shader[kind + '_sha256'] + ('.control' if kind == 'control' else '.nvn')
                    start = base + shader[kind + '_offset']
                    payload = image[start:start + shader[kind + '_bytes']]
                    path = destination / filename
                    if not path.exists():
                        path.write_bytes(payload)
                    row[kind + '_file'] = filename
                records.append(row)
    (destination / 'inputs.json').write_text(json.dumps({'schema': 1, 'records': records}, indent=2) + '\n')
    return records


def texture_constraints(records):
    """Build explicit conditional type requirements, never fabricate cbuf data.

    For this audited NVN build the GPU c0 offsets 0x20/0x28 correspond exactly
    to the BNSH sampler slots 0/1 across every queried shader. Applying the same
    bank/offset ABI to another build requires a new audit. Metal's binding
    reflection must enforce the required type against the real NVN descriptor.
    """
    slots = {}
    for record in records:
        for sampler in record.get('reflection', {}).get('samplers', []):
            slot = sampler['slot']
            if slot == -1:  # Inactive resource, not a bound descriptor.
                continue
            if not isinstance(slot, int) or not 0 <= slot < 128:
                raise ValueError('Reflected texture slot exceeds supported range')
            slots.setdefault(slot, set()).add(sampler['name'])
    return [{'nvn_slot': slot, 'names': sorted(names), 'cbuf_index': 0,
             'cbuf_offset': 0x20 + slot * 8, 'runtime_required_type': '2d'}
            for slot, names in sorted(slots.items())]


def compile_shader_inputs(records, inputs, destination, compiler, *, require_runtime_2d=False):
    """Run the fixed external offline compiler; preserve every failure and log.

    Successful syntax conversion does not validate NVN bindings or semantics.
    This deliberately does not register generated shaders with the runtime.
    """
    destination, inputs, compiler = Path(destination), Path(inputs), Path(compiler).resolve()
    destination.mkdir(parents=True, exist_ok=False)
    results = []
    for identity in sorted({row['code_sha256'] for row in records}):
        source = inputs / (identity + '.nvn')
        glsl = destination / (identity + '.glsl')
        constraints = texture_constraints([r for r in records if r['code_sha256'] == identity]) if require_runtime_2d else []
        environment = dict(os.environ)
        environment.pop('BOTW_SHADER_2D_CBUFS', None)
        if constraints:
            environment['BOTW_SHADER_2D_CBUFS'] = ''.join(
                f";{q['cbuf_index']}:{q['cbuf_offset']};" for q in constraints)
        result = subprocess.run([str(compiler), str(source), str(glsl)], env=environment,
                                capture_output=True, text=True, timeout=120)
        (destination / (identity + '.log')).write_text(result.stdout + result.stderr)
        outputs = [glsl, glsl.with_suffix('.spv'), glsl.with_suffix('.metal')]
        ok = result.returncode == 0 and all(p.is_file() and p.stat().st_size for p in outputs)
        traces = {}
        for prefix, key in (('BOTW_ENV ', 'environment_queries'),
                            ('BOTW_UNRESOLVED_CBUF ', 'unresolved_cbuf'),
                            ('BOTW_UNRESOLVED_TEXTURE ', 'unresolved_texture'),
                            ('BOTW_STORAGE_DESCRIPTOR ', 'storage_descriptors'),
                            ('BOTW_TEXTURE_CONSTRAINT ', 'used_texture_constraints')):
            traces[key] = [json.loads(line[len(prefix):]) for line in result.stderr.splitlines()
                           if line.startswith(prefix)]
        results.append({'code_sha256': identity, 'exit_code': result.returncode,
                        'converted': bool(ok), 'nvn_bindings_validated': False, 'texture_type_constraints': constraints, **traces,
                        'outputs': [p.name for p in outputs if p.exists()]})
        # A partial run remains inspectable even if a later compiler process fails.
        (destination / 'translation.json').write_text(json.dumps({'schema': 1, 'results': results}, indent=2) + '\n')
    return results


def audit_storage_resources(msl, stage, reflection, descriptors):
    """Prove descriptor-name aliases against compiler IR and actual BNSH slots.

    This records bindings only. Dynamic memory accesses still require separate
    bounds validation before a shader may be registered.
    """
    base = {'vertex': 0x110, 'fragment': 0x510}.get(stage)
    if base is None:
        raise ValueError('Unsupported storage driver stage')
    reflected = {x['slot'] for x in reflection.get('storage', []) if x['slot'] >= 0}
    indices = {}
    for descriptor in descriptors:
        fields = ('descriptor_index', 'cbuf_index', 'cbuf_offset', 'count')
        if any(type(descriptor.get(x)) is not int for x in fields) or type(descriptor.get('is_written')) is not bool:
            raise ValueError('Invalid compiler storage descriptor')
        index, bank, offset, count = (descriptor[x] for x in fields)
        slot = (offset - base) // 16
        if bank != 0 or offset < base or (offset - base) % 16 or not 1 <= count <= 16 or not 0 <= index <= 16-count or not 0 <= slot <= 16-count:
            raise ValueError('Unsupported compiler storage descriptor address')
        for delta in range(count):
            if slot + delta not in reflected:
                raise ValueError('Storage descriptor slot absent from BNSH reflection')
            if index + delta in indices:
                raise ValueError('Duplicate compiler storage descriptor index')
            indices[index + delta] = (slot + delta, descriptor['is_written'])
    bindings = []
    for block, variable, index, metal in re.findall(r'device\s+(?:const\s+)?(\w+)\s*&\s*(ssbo(\d+)(?:_\d+)?)\s+\[\[buffer\((\d+)\)\]\]', msl):
        if int(index) not in indices:
            raise ValueError('Missing compiler storage descriptor for ' + variable)
        layout = re.search(r'\bstruct\s+' + re.escape(block) + r'\s*\{\s*(uint[24]?)\s+_m0\[1\];\s*\};', msl)
        if not layout:
            raise ValueError('Unknown storage buffer alias layout')
        stride = int(layout[1][-1]) * 4 if layout[1][-1].isdigit() else 4
        slot, written = indices[int(index)]
        accesses = re.findall(r'\b' + re.escape(variable) + r'\._m0\[([^\]]+)\]', msl)
        if len(re.findall(r'\b' + re.escape(variable) + r'\b', msl)) != len(accesses) + 1:
            raise ValueError('Unknown storage buffer alias use')
        bindings.append({'nvn_slot': slot, 'metal_slot': int(metal), 'stride': stride,
                         'is_written': written, 'dynamic_accesses': sum(not bool(re.fullmatch(r'\d+u?', x)) for x in accesses)})
    if len(re.findall(r'\bdevice\s+(?:const\s+)?\w+\s*[&*]\s*\w+\s+\[\[buffer\(\d+\)\]\]', msl)) != len(bindings):
        raise ValueError('Unknown Metal storage resource parameter')
    if msl.count('[[buffer(') and not bindings and indices:
        raise ValueError('Compiler storage descriptors have no recognized MSL aliases')
    driver_reads = set()
    for index in re.findall(r'\bc0(?:_\d+)?\.data\[([^\]]+)\]', msl):
        if not re.fullmatch(r'\d+u?', index):
            raise ValueError('Unresolved dynamic driver constant access')
        offset = int(index.rstrip('u')) * 4
        slot, field = divmod(offset-base, 16)
        if offset < base or slot not in reflected or slot not in {value[0] for value in indices.values()} or field not in (0, 4, 8):
            raise ValueError('Unknown storage driver constant field')
        driver_reads.add((offset, slot, {0: 'address_low', 4: 'address_high', 8: 'size'}[field]))
    for variable in re.findall(r'constant\s+\w+\s*&\s*(c0(?:_\d+)?)\s+\[\[buffer\(\d+\)\]\]', msl):
        count = len(re.findall(r'\b' + re.escape(variable) + r'\.data\[[^\]]+\]', msl))
        if len(re.findall(r'\b' + re.escape(variable) + r'\b', msl)) != count + 1:
            raise ValueError('Unknown storage driver variable use')
    return {'bindings': bindings, 'driver_reads': [{'bank': 0, 'offset': offset, 'nvn_slot': slot, 'field': field}
                                                  for offset, slot, field in sorted(driver_reads)],
            'access_bounds_validated': False}


def cancel_u32_storage_bases(value, bases, unsigned_variables):
    """Apply (unsigned_term + base) - base == unsigned_term modulo 2**32.

    Only the exact identity (including an optional all-ones base mask) is
    accepted; floating/signed terms and mismatched addresses are not guessed.
    """
    def outer(text):
        text = text.strip()
        while text.startswith('(') and text.endswith(')'):
            depth = 0
            for i, char in enumerate(text):
                depth += (char == '(') - (char == ')')
                if depth == 0:
                    break
            if i != len(text)-1:
                break
            text = text[1:-1].strip()
        return text

    def split(text, operator):
        depth = 0
        # A '+' inside a top-level bitwise/shift/comparison expression is
        # not its root operator. Decline that grammar rather than changing
        # C++ precedence while proving a uint32 cancellation.
        forbidden = '<>&|^=!?,:"' if operator in ('+', '-') else '|^=!?,:"' if operator == '&' else ''
        for char in text:
            depth += (char == '(') - (char == ')')
            if depth == 0 and char in forbidden:
                return None
        depth = 0
        for i in range(len(text)-1, -1, -1):
            depth += (text[i] == ')') - (text[i] == '(')
            if depth == 0 and text[i] == operator and i:
                return text[:i].strip(), text[i+1:].strip()
        return None

    def unsigned(term):
        if not re.fullmatch(r'[\w\s()+*/%<>&|^~,-]+', term):
            return False
        if any(not match[1] for match in re.finditer(r'(?<!\w)(?:0[xX][0-9a-fA-F]+|\d+)([uU]?)(?!\w)', term)):
            return False
        names = re.findall(r'\b[A-Za-z_]\w*\b', term)
        return all(name in unsigned_variables or name in {'uint', 'extract_bits'} for name in names)

    def rewrite(text, depth=0):
        if depth > 128:
            raise ValueError('Storage expression nesting exceeds limit')
        parts, at = [], 0
        while at < len(text):
            if text[at] != '(':
                parts.append(text[at]);at += 1;continue
            end, level = at+1, 1
            while end < len(text) and level:
                level += (text[end] == '(') - (text[end] == ')');end += 1
            if level:
                raise ValueError('Unbalanced storage expression')
            parts.append('(' + rewrite(text[at+1:end-1], depth+1) + ')');at = end
        result = ''.join(parts).strip()
        masked = split(outer(result), '&')
        if masked and outer(masked[0]) in bases and outer(masked[1]).lower() in ('4294967295u', '0xffffffffu'):
            return outer(masked[0])
        subtract = split(outer(result), '-')
        if subtract and outer(subtract[1]) in bases:
            base = outer(subtract[1]);add = split(outer(subtract[0]), '+')
            if add:
                term = add[0] if outer(add[1]) == base else add[1] if outer(add[0]) == base else None
                if term is None:
                    def addends(item):
                        pair = split(outer(item), '+')
                        return addends(pair[0]) + addends(pair[1]) if pair else [outer(item)]
                    terms = addends(subtract[0])
                    if terms.count(base) == 1:
                        terms.remove(base)
                        term = '+'.join('(' + item + ')' for item in terms)
                if term is not None:
                    if not unsigned(term):
                        raise ValueError('Storage address cancellation requires an unsigned term')
                    return 'uint(' + term + ')'
        return result
    return rewrite(value)


def build_dynamic_c1_probe(msl, control, code, relative_vertex_count):
    """Build an offline Metal probe for a finite, closed c1-index input set.

    Only single-assignment scalar expressions depending on relative VertexID
    and fixed original c1 words are accepted. This does not authorize a shader
    or prove its storage accesses; the runtime must enforce the input profile.
    """
    return _build_closed_index_probe(msl, control, code, relative_vertex_count, None, 'vertex')


def _build_closed_index_probe(msl, control, code, relative_vertex_count, target_expressions, relative_input):
    if type(relative_vertex_count) is not int or not 1 <= relative_vertex_count <= 64:
        raise ValueError('Invalid finite vertex input range')
    if len(control) < 0x708:
        raise ValueError('Missing original c1 bounds')
    start, end = struct.unpack_from('<II', control, 0x700)
    if start < 0x80 or start % 4 or end != len(code) or end < start or (end-start) % 4 or end-start > 65536:
        raise ValueError('Invalid original c1 bounds')
    kinds = {}
    for block, variable in re.findall(r'constant\s+(\w+)\s*&\s*(c1(?:_\d+)?)\s+\[\[buffer\(\d+\)\]\]', msl):
        layout = re.search(r'\bstruct\s+' + re.escape(block) + r'\s*\{\s*(uint|float)\s+data\[\d+\];\s*\};', msl)
        if not layout:
            raise ValueError('Unknown c1 probe layout')
        kinds[variable] = layout[1]
    expressions = list(dict.fromkeys(index for variable, index in re.findall(r'\b(c1(?:_\d+)?)\.data\[([^\]]+)\]', msl)
                                     if variable in kinds and not re.fullmatch(r'\d+u?', index))) if target_expressions is None else list(dict.fromkeys(target_expressions))
    if not expressions or len(expressions) > 64:
        raise ValueError('No bounded dynamic c1 expression set')
    completed, visiting, statements = set(), set(), []
    allowed = {'uint', 'int', 'float', 'as_type', 'extract_bits', 'abs', 'fast', 'clamp', 'trunc', 'max', 'min',
               'isnan', 'botw_relative_vertex'}

    def expression(value):
        def constant(match):
            variable, index = match[1], int(match[2])
            if variable not in kinds or index * 4 + 4 > end-start:
                raise ValueError('Probe c1 constant exceeds original bounds')
            bits, = struct.unpack_from('<I', code, start+index*4)
            literal = f'0x{bits:08x}u'
            return 'as_type<float>(' + literal + ')' if kinds[variable] == 'float' else literal
        value = re.sub(r'\b(c1(?:_\d+)?)\.data\[(\d+)u?\]', constant, value)
        basis = 'gl_VertexIndex\\s*-\\s*gl_BaseVertex' if relative_input == 'vertex' else 'gl_InstanceIndex\\s*-\\s*gl_BaseInstance'
        # A textual prefix is insufficient: a-b*2 is not (a-b)*2. Accept
        # only the whole expression or an explicitly parenthesized subtree.
        value = re.sub(r'\(\s*\b' + basis + r'\b\s*\)', '(botw_relative_vertex)', value)
        if re.fullmatch(r'\s*\b' + basis + r'\b\s*', value):
            value = 'botw_relative_vertex'
        if not re.fullmatch(r'[\w\s.+*/%<>=!&|^~?:(),-]+', value) or any(x in value for x in ('//', '/*', '*/')):
            raise ValueError('Unknown external input or probe expression grammar')
        if re.search(r'(?<![=!<>])=(?!=)|\+\+|--', value):
            raise ValueError('Side effect in probe expression')
        for identifier in re.findall(r'\b[A-Za-z_]\w*\b', value):
            if re.fullmatch(r'_\d+', identifier):
                variable(identifier)
            elif identifier not in allowed:
                raise ValueError('Unresolved external probe input: ' + identifier)
        return value

    def variable(name):
        if name in completed:
            return
        if name in visiting:
            raise ValueError('Cyclic probe assignment')
        assignments = re.findall(r'\b' + re.escape(name) + r'\s*\)*\s*(?:=(?!=)|[+*/%&|^\-]=|<<=|>>=|\+\+|--)|(?:\+\+|--)\s*\(*\s*\b' + re.escape(name) + r'\b', msl)
        definitions = re.findall(r'\b(uint|int|float)\s+' + re.escape(name) + r'\s*=\s*([^;]+);', msl)
        if len(assignments) != 1 or len(definitions) != 1:
            raise ValueError('Ambiguous scalar probe assignment: ' + name)
        definition = re.search(r'\b(uint|int|float)\s+' + re.escape(name) + r'\s*=', msl)
        prefix = msl[:definition.start()]
        if prefix.count('{') - prefix.count('}') != 1:
            raise ValueError('Conditional scalar probe assignment: ' + name)
        visiting.add(name)
        kind, value = definitions[0]
        value = expression(value)
        statements.append(f'    {kind} {name} = {value};\n')
        visiting.remove(name); completed.add(name)

    targets = [expression(value) for value in expressions]
    output = '#include <metal_stdlib>\nusing namespace metal;\n'
    output += 'kernel void botw_index_probe(device uint *out [[buffer(0)]],uint botw_relative_vertex [[thread_position_in_grid]]) {\n'
    output += f'    if (botw_relative_vertex >= {relative_vertex_count}u) return;\n' + ''.join(statements)
    output += ''.join(f'    out[botw_relative_vertex*{len(targets)}u+{i}u] = uint({value});\n'
                      for i, value in enumerate(targets)) + '}\n'
    return {'msl': output, 'expressions': expressions, 'relative_vertex_count': relative_vertex_count,
            'c1_words': (end-start)//4, 'relative_input': relative_input, 'conditional_probe_only': True}


def build_storage_index_probe(msl, control, code, stage, reflection, descriptors, relative_instance_count):
    """Probe finite storage indices after an exact uint32 address cancellation.

    Storage descriptor aliases and BNSH slots must already agree. A reachable
    attribute, uniform, memory value, vertex ID, or uncanceled guest base fails.
    """
    audit = audit_storage_resources(msl, stage, reflection, descriptors)
    reads = audit['driver_reads'];symbols = {}
    for row in reads:
        if row['field'] != 'address_low':
            raise ValueError('Storage probe requires a proven unsigned address-low field')
        symbols[row['offset']] = 'botw_storage_base_' + str(row['nvn_slot'])
    for symbol in symbols.values():
        if re.search(r'\b' + symbol + r'\b', msl):
            raise ValueError('Storage probe symbol collides with input')
    for block in re.findall(r'constant\s+(\w+)\s*&\s*c0(?:_\d+)?\s+\[\[buffer\(\d+\)\]\]', msl):
        layout = re.search(r'\bstruct\s+' + re.escape(block) + r'\s*\{\s*uint\s+data\[(\d+)\];\s*\};', msl)
        if not layout or any(row['offset']//4 >= int(layout[1]) for row in reads):
            raise ValueError('Storage address-low source is not uint32')
    source = re.sub(r'\bc0(?:_\d+)?\.data\[(\d+)u?\]',
                    lambda match: symbols[int(match[1])*4], msl)
    unsigned = set(re.findall(r'\buint\s+(_\d+)\b', source))
    bases = set(symbols.values())

    def normalize(value):
        return cancel_u32_storage_bases(value, bases, unsigned) if any(x in value for x in bases) else value
    source = re.sub(r'\b(uint|int|float)\s+(_\d+)\s*=\s*([^;]+);',
                    lambda match: match[1] + ' ' + match[2] + '=' + normalize(match[3]) + ';', source)
    aliases = {variable: int(metal) for variable, metal in re.findall(
        r'device\s+(?:const\s+)?\w+\s*&\s*(ssbo\d+(?:_\d+)?)\s+\[\[buffer\((\d+)\)\]\]', source)}
    bindings = {row['metal_slot']: row for row in audit['bindings']}
    expressions, accesses = [], []
    for access in re.finditer(r'\b(ssbo\d+(?:_\d+)?)\._m0\[', source):
        start, end, level = access.end(), access.end(), 1
        while end < len(source) and level:
            level += (source[end] == '[') - (source[end] == ']');end += 1
        if level:
            raise ValueError('Unbalanced storage access index')
        value = normalize(source[start:end-1])
        if value not in expressions:
            expressions.append(value)
        row = bindings[aliases[access[1]]]
        accesses.append({'expression_index': expressions.index(value), 'nvn_slot': row['nvn_slot'],
                         'stride': row['stride'], 'metal_slot': row['metal_slot']})
    if not accesses:
        raise ValueError('Storage probe has no accesses')
    result = _build_closed_index_probe(source, control, code, relative_instance_count, expressions, 'instance')
    result['relative_instance_count'] = result.pop('relative_vertex_count')
    result['accesses'] = accesses
    result['guest_base_cancellation_proven'] = True
    return result


def specialize_static_c1(msl, control, code):
    """Fold only provably static c1 reads from the original NVN constant region.

    The fixed MIT upstream dm/gfx/tools/dmsh_build.py documents control+0x700
    as c1's byte offset in the original NVN code blob, +0x704 as its end.
    No unbound bytes are supplied: dynamic/unrecognized reads fail closed.
    """
    declarations = list(re.finditer(r'constant\s+(\w+)\s*&\s*(c1(?:_\d+)?)\s+\[\[buffer\(\d+\)\]\]', msl))
    if not declarations:
        return msl, []
    if len(control) < 0x708:
        raise ValueError('Missing original c1 control bounds')
    start, end = struct.unpack_from('<II', control, 0x700)
    if start < 0x80 or start % 4 or end != len(code) or end < start or end - start > 65536:
        raise ValueError('Invalid original c1 code bounds')
    transformed, uses = msl, {}
    for declaration in declarations:
        block, variable = declaration[1], declaration[2]
        scalar = re.search(r'\bstruct\s+' + re.escape(block) + r'\s*\{\s*(uint|float)\s+data\[\d+\];\s*\};', msl)
        if not scalar:
            raise ValueError('Unknown c1 constant block layout')
        reads = list(re.finditer(r'\b' + re.escape(variable) + r'\.data\[(\d+)u?\]', msl))
        # One variable occurrence is the declaration; every other occurrence
        # must be a fixed, in-range data read. No pointer/address escapes.
        if len(re.findall(r'\b' + re.escape(variable) + r'\b', msl)) != len(reads) + 1:
            raise ValueError('Unresolved dynamic c1 index or variable use')
        for read in reads:
            offset = int(read[1]) * 4
            if offset + 4 > end - start:
                raise ValueError('Static c1 read exceeds original bounds')
            bits, = struct.unpack_from('<I', code, start + offset)
            kind = scalar[1]
            literal = f'0x{bits:08x}u'
            if kind == 'float':
                literal = 'as_type<float>(' + literal + ')'
            transformed = transformed.replace(read[0], literal)
            key = (offset, bits, kind)
            uses[key] = uses.get(key, 0) + 1
        # Remove exactly this resource parameter while preserving either the
        # preceding or following parameter separator of a valid signature.
        parameter = re.escape(declaration[0])
        transformed, removed = re.subn(parameter + r'\s*,\s*', '', transformed, count=1)
        if not removed:
            transformed, removed = re.subn(r',\s*' + parameter, '', transformed, count=1)
        if not removed:
            transformed, removed = re.subn(parameter, '', transformed, count=1)
        if removed != 1:
            raise ValueError('Cannot remove specialized c1 parameter')
    return transformed, [{'bank': 1, 'offset': offset, 'bits': f'{bits:08x}', 'type': kind, 'count': count}
                         for (offset, bits, kind), count in sorted(uses.items())]


def _remove_resource_parameter(source, parameter):
    escaped = re.escape(parameter)
    result, count = re.subn(escaped + r'\s*,\s*', '', source, count=1)
    if not count:
        result, count = re.subn(r',\s*' + escaped, '', source, count=1)
    if not count:
        result, count = re.subn(escaped, '', source, count=1)
    if count != 1:
        raise ValueError('Missing exact resource parameter')
    return result


def _array_reads(source, pattern):
    """Yield full balanced accesses, including an index containing brackets."""
    result = []
    for match in re.finditer(pattern, source):
        end, level = match.end(), 1
        while end < len(source) and level:
            level += (source[end] == '[') - (source[end] == ']')
            end += 1
        if level:
            raise ValueError('Unbalanced finite-profile array access')
        result.append((match.start(), end, source[match.end():end-1], match))
    return result


def _validate_finite_source(source):
    # This deliberately narrow compiler grammar has no local references,
    # pointers to elements, mutable helper calls, or additional functions.
    # The current font shaders use no remaining '&' after driver cancellation.
    stripped = re.sub(r'\bconstant\s+\w+\s*&\s*c(?:[3-9]|1[0-6])(?:_\d+)?\s+\[\[buffer\(\d+\)\]\]', '', source)
    if '&' in stripped:
        raise ValueError('Unproven finite source reference or address escape')
    no_using = re.sub(r'\busing\s+namespace\s+metal\s*;', '', source)
    if re.search(r'\b(?:thread|threadgroup|typedef|auto|decltype|template|namespace|using)\b', no_using):
        raise ValueError('Unproven finite source alias grammar')
    for line in source.splitlines():
        if line.lstrip().startswith('#') and not re.fullmatch(r'\s*#include\s+<(?:metal_stdlib|simd/simd\.h)>\s*', line):
            raise ValueError('Unknown finite source preprocessor directive')
    entry = re.search(r'\bvertex\s+\w+\s+(\w+)\s*\(', source)
    if not entry:
        raise ValueError('Missing finite vertex entry')
    calls = re.findall(r'\b([A-Za-z_]\w*)\s*(?:<[^<>]+>)?\s*\(', source)
    allowed = {'as_type', 'extract_bits', 'abs', 'fma', 'clamp', 'trunc', 'max', 'min', 'isnan',
               'if', 'buffer', 'attribute', 'user', entry[1]}
    allowed.update(scalar + width for scalar in ('uint', 'int', 'float', 'bool') for width in ('', '2', '3', '4'))
    if calls.count(entry[1]) != 1 or set(calls)-allowed:
        raise ValueError('Unknown or mutable finite source function')
    definitions = re.findall(r'(?:^|[;{}])\s*(?:(?:inline|static|constexpr|vertex|fragment|kernel)\s+)*\w+\s+(\w+)\s*\([^;{}]*\)\s*\{', source)
    if [name for name in definitions if name not in ('if', 'for', 'while', 'switch')] != [entry[1]]:
        raise ValueError('Unproven finite source helper definition')


def specialize_finite_storage_shader(msl, control, code, stage, reflection, descriptors):
    """Prepare a conditional shader whose actual MSL must be probed at load.

    The loader independently closes every dynamic c1/storage access over
    relative IDs and evaluates all finite inputs on Metal before registration.
    No claimed lengths or offline probe result in this manifest are trusted.
    """
    if stage != 'vertex':
        raise ValueError('Finite storage profiles require a vertex stage')
    audit = audit_storage_resources(msl, stage, reflection, descriptors)
    slots = {row['nvn_slot'] for row in audit['bindings']}
    if len(slots) != 1 or not audit['bindings'] or any(row['is_written'] for row in audit['bindings']):
        raise ValueError('Finite storage profiles require one reflected readonly storage slot')
    # These closed probes reject attributes, other memory, absolute IDs and
    # ambiguous assignments before any address algebra or source edits occur.
    build_dynamic_c1_probe(msl, control, code, 64)
    build_storage_index_probe(msl, control, code, stage, reflection, descriptors, 64)
    symbols = {row['offset']: 'botw_storage_base_' + str(row['nvn_slot']) for row in audit['driver_reads']}
    if any(re.search(r'\b' + value + r'\b', msl) for value in symbols.values()):
        raise ValueError('Generated storage symbol collides with source')
    source = re.sub(r'\bc0(?:_\d+)?\.data\[(\d+)u?\]', lambda match: symbols[int(match[1])*4], msl)
    unsigned = set(re.findall(r'\buint\s+(_\d+)\b', source))
    def normalize(value):
        return cancel_u32_storage_bases(value, set(symbols.values()), unsigned)
    source = re.sub(r'\b(uint|int|float)\s+(_\d+)\s*=\s*([^;]+);',
                    lambda match: match[1] + ' ' + match[2] + ' = ' + normalize(match[3]) + ';', source)
    for start, end, index, match in reversed(_array_reads(source, r'\b(ssbo\d+(?:_\d+)?)\._m0\[')):
        source = source[:start] + match[1] + '[' + normalize(index) + ']' + source[end:]
    if any(re.search(r'\b' + value + r'\b', source) for value in symbols.values()):
        raise ValueError('Uncanceled storage guest address')
    for declaration in list(re.finditer(r'constant\s+\w+\s*&\s*(c0(?:_\d+)?)\s+\[\[buffer\(\d+\)\]\]', source)):
        if len(re.findall(r'\b' + declaration[1] + r'\b', source)) != 1:
            raise ValueError('Remaining unknown driver constant use')
        source = _remove_resource_parameter(source, declaration[0])
    for declaration in list(re.finditer(r'device\s+(?:const\s+)?(\w+)\s*&\s*(ssbo\d+(?:_\d+)?)\s+\[\[buffer\((\d+)\)\]\]', source)):
        layout = re.search(r'\bstruct\s+' + re.escape(declaration[1]) + r'\s*\{\s*(uint[24]?)\s+_m0\[1\];\s*\};', source)
        if not layout:
            raise ValueError('Unknown finite storage pointer layout')
        source = source.replace(declaration[0], f'device const {layout[1]}* {declaration[2]} [[buffer({declaration[3]})]]', 1)
    if re.search(r'\bbotw_c1_words\b', source):
        raise ValueError('Generated c1 array collides with source')
    start, end = struct.unpack_from('<II', control, 0x700)
    for declaration in list(re.finditer(r'constant\s+(\w+)\s*&\s*(c1(?:_\d+)?)\s+\[\[buffer\(\d+\)\]\]', source)):
        block, variable = declaration[1], declaration[2]
        layout = re.search(r'\bstruct\s+' + re.escape(block) + r'\s*\{\s*(uint|float)\s+data\[\d+\];\s*\};', source)
        reads = _array_reads(source, r'\b' + re.escape(variable) + r'\.data\[')
        if not layout or len(re.findall(r'\b' + re.escape(variable) + r'\b', source)) != len(reads)+1:
            raise ValueError('Unknown finite c1 array use')
        for begin, finish, index, _ in reversed(reads):
            replacement = 'botw_c1_words[' + index + ']'
            if layout[1] == 'float':
                replacement = 'as_type<float>(' + replacement + ')'
            source = source[:begin] + replacement + source[finish:]
        source = _remove_resource_parameter(source, declaration[0])
    words = struct.unpack_from('<' + 'I'*((end-start)//4), code, start)
    entry = re.search(r'\bvertex\s+\w+\s+\w+\s*\(', source)
    source = source[:entry.start()] + 'constant uint botw_c1_words[' + str(len(words)) + '] = {' + ','.join(f'0x{x:08x}u' for x in words) + '};\n' + source[entry.start():]
    _validate_finite_source(source)
    return source, {'schema': 1, 'storage_slot': slots.pop(), 'storage_descriptors': descriptors,
                    'relative_vertex_inputs': 64, 'relative_instance_inputs': 64}


def bound_static_uniforms(msl):
    """Give fixed-index resources their exact scalar-array footprint.

    SPIRV-Cross can alias one original 64 KiB block as several scalar types.
    Clone each resource's type before narrowing, so a dynamic alias retains its
    full declaration and the runtime still requires that complete range.
    """
    transformed, ranges, blocks = msl, [], []
    for declaration in re.finditer(r'constant\s+(\w+)\s*&\s*(c(\d+)(?:_\d+)?)\s+\[\[buffer\((\d+)\)\]\]', msl):
        block, variable, bank, metal = declaration[1], declaration[2], int(declaration[3]), int(declaration[4])
        if not 3 <= bank <= 16:
            continue
        layout = re.search(r'\bstruct\s+' + re.escape(block) + r'\s*\{\s*(uint[234]?|float[234]?)\s+data\[(\d+)\];\s*\};', msl)
        if not layout:
            continue
        declared = int(layout[2])
        lanes = int(layout[1][-1]) if layout[1][-1].isdigit() else 1
        stride = (4 if lanes == 3 else lanes) * 4
        if not 1 <= declared <= 65536 // stride:
            raise ValueError('Uniform declaration exceeds supported bounds')
        indices = re.findall(r'\b' + re.escape(variable) + r'\.data\[([^\]]+)\]', msl)
        static = all(re.fullmatch(r'\d+u?', value) for value in indices)
        static &= len(re.findall(r'\b' + re.escape(variable) + r'\b', msl)) == len(indices) + 1
        fixed = [int(value.rstrip('u')) for value in indices if re.fullmatch(r'\d+u?', value)]
        if any(value >= declared for value in fixed):
            raise ValueError('Static uniform read exceeds original declaration bounds')
        required = max(fixed) + 1 if static and fixed else declared
        proof = 'fixed_indices' if static and fixed else 'declared_dynamic'
        if proof == 'fixed_indices':
            name = 'botw_ubo_' + str(metal)
            if re.search(r'\b' + name + r'\b', msl):
                raise ValueError('Generated uniform type collides with input source')
            blocks.append(f'struct {name} {{{layout[1]} data[{required}];}};\n')
            replacement = declaration[0].replace(block, name, 1)
            transformed = transformed.replace(declaration[0], replacement, 1)
        ranges.append({'metal_slot': metal, 'nvn_slot': bank - 3, 'bytes': required * stride, 'proof': proof})
    entry = re.search(r'\b(?:vertex|fragment)\s+\w+\s+\w+\s*\(', transformed)
    if blocks and not entry:
        raise ValueError('Missing entry for bounded uniform declarations')
    if blocks:
        transformed = transformed[:entry.start()] + ''.join(blocks) + transformed[entry.start():]
    return transformed, ranges


def shader_binding_map(msl, stage, reflection, storage_descriptors=()):
    """Match this compiler's named resources to explicit BNSH NVN slots.

    c[3+r] -> uniform slot r is the fixed NVN ABI documented by the upstream
    Maxwell reference reader. Every proposed slot must also occur in this
    program's actual BNSH reflection. Readonly pointer SSBOs additionally need
    the compiler IR's exact descriptor-table offset. Unknown c0/c1 fail.
    """
    entry = re.search(r'\b(vertex|fragment)\s+\w+\s+(\w+)\s*\(', msl)
    if not entry or entry[1] != stage:
        raise ValueError('Missing or mismatched Metal entry stage')
    end = msl.find('{', entry.end())
    if end < 0:
        raise ValueError('Metal entry body is missing')
    signature = msl[entry.start():end]
    uniforms = {x['slot'] for x in reflection.get('uniforms', []) if x['slot'] >= 0}
    samplers = {x['slot'] for x in reflection.get('samplers', []) if x['slot'] >= 0}
    result, covered, types = [], set(), []
    for space, readonly, scalar, pointer, variable, metal in re.findall(r'\b(constant|device)\s+(?:(const)\s+)?(\w+)\s*([&*])\s*(\w+)\s+\[\[buffer\((\d+)\)\]\]', signature):
        storage = re.fullmatch(r'ssbo(\d+)(?:_\d+)?', variable)
        if space == 'device' and readonly and pointer == '*' and scalar in ('uint', 'uint2', 'uint4') and storage:
            index = int(storage[1])
            matching = [x for x in storage_descriptors if type(x.get('descriptor_index')) is int and x['descriptor_index'] == index]
            if stage != 'vertex' or len(matching) != 1:
                raise ValueError('Missing unique readonly storage descriptor')
            descriptor = matching[0]
            slot = (descriptor['cbuf_offset']-0x110)//16
            if (descriptor['cbuf_index'] != 0 or descriptor['count'] != 1 or descriptor['is_written'] is not False
                    or not 0 <= slot < 16 or descriptor['cbuf_offset'] != 0x110+slot*16
                    or slot not in {x['slot'] for x in reflection.get('storage', [])}):
                raise ValueError('Readonly storage descriptor absent from BNSH reflection')
            result.append({'kind': 'storage', 'nvn_slot': slot, 'metal_slot': int(metal)})
            covered.add(('buffer', int(metal)))
            continue
        bank = re.fullmatch(r'c(\d+)(?:_\d+)?', variable)
        if space != 'constant' or readonly or pointer != '&' or not bank or not 3 <= int(bank[1]) <= 16:
            raise ValueError('Unknown private/driver constant or storage resource: ' + variable)
        slot = int(bank[1]) - 3
        if slot not in uniforms:
            raise ValueError('Uniform slot absent from BNSH reflection')
        result.append({'kind': 'uniform', 'nvn_slot': slot, 'metal_slot': int(metal)})
        covered.add(('buffer', int(metal)))
    for kind, variable, metal in re.findall(r'\b(texture\w+|sampler)(?:<[^>]+>)?\s+(\w+)\s+\[\[(?:texture|sampler)\((\d+)\)\]\]', signature):
        name = re.fullmatch(r'(?:fs|vs)_tex0_([0-9a-f]+)(Smplr)?', variable)
        if not name:
            raise ValueError('Unknown translated texture resource: ' + variable)
        offset = int(name[1], 16)
        if offset < 0x20 or (offset - 0x20) % 8:
            raise ValueError('Unknown NVN texture constant offset')
        slot = (offset - 0x20) // 8
        if slot not in samplers:
            raise ValueError('Texture/sampler slot absent from BNSH reflection')
        binding_kind = 'sampler' if kind == 'sampler' else 'texture'
        if kind not in ('sampler', 'texture2d', 'texture2d_array'):
            raise ValueError('Texture type is not supported by this bundle schema')
        result.append({'kind': binding_kind, 'nvn_slot': slot, 'metal_slot': int(metal)})
        covered.add((binding_kind, int(metal)))
        if binding_kind == 'texture':
            types.append({'nvn_slot': slot, 'metal_slot': int(metal), 'type': kind[7:]})
    declared = {(kind, int(index)) for kind, index in re.findall(r'\[\[(buffer|texture|sampler)\((\d+)\)\]\]', signature)}
    if declared != covered:
        raise ValueError('Unrecognized Metal resource declaration')
    inputs = sorted({int(x) for x in re.findall(r'\[\[attribute\((\d+)\)\]\]', msl)})
    reflected_inputs = {x['slot'] for x in reflection.get('inputs', [])}
    if stage == 'vertex' and not set(inputs) <= reflected_inputs:
        raise ValueError('Vertex input absent from BNSH reflection')
    return {'entry': entry[2], 'bindings': result, 'texture_types': types, 'vertex_inputs': inputs}


def shader_scratch_proof(code, spirv, stage):
    """Admit the initial graphics-only, zero-declared/no-Private profile."""
    if stage not in ('vertex', 'fragment') or len(code) < 0x80:
        raise ValueError('Scratch proof requires bounded graphics NVN SPH')
    word0, low, high, crs = struct.unpack_from('<4I', code, 0x30)
    if (word0 >> 10) & 15 != (1 if stage == 'vertex' else 5):
        raise ValueError('Scratch proof NVN SPH stage mismatch')
    low, high, crs = low & 0xffffff, high & 0xffffff, crs & 0xffffff
    if len(spirv) < 20 or len(spirv) > 16 << 20 or len(spirv) % 4:
        raise ValueError('Scratch proof SPIR-V bounds')
    words = struct.unpack('<' + 'I' * (len(spirv) // 4), spirv)
    if words[0] != 0x07230203 or words[1] not in (0x10300, 0x10400) or not 0 < words[3] <= 1048576 or words[4]:
        raise ValueError('Scratch proof requires SPIR-V version 1.3 or 1.4')
    pos, entries, memory, shader, functions, private = 5, [], 0, 0, [], 0
    in_function, previous, starts, ends = False, 0, 0, 0
    while pos < len(words):
        count, op = words[pos] >> 16, words[pos] & 65535
        if not count or count > len(words)-pos or (op > 400 and op != 5380):
            raise ValueError('Scratch proof malformed or unsupported SPIR-V instruction')
        if op == 5380 and (count != 1 or not in_function or stage != 'fragment'):
            raise ValueError('Scratch proof demote requires a fragment function')
        args = words[pos+1:pos+count]
        if op == 14:
            if args != (0, 1):
                raise ValueError('Scratch proof requires Logical GLSL450 memory model')
            memory += 1
        if op == 17 and args == (1,):
            shader += 1
        if op == 15:
            if count < 4 or args[0] != (0 if stage == 'vertex' else 4) or not 0 < args[1] < words[3] or b'\0' not in spirv[(pos+3)*4:(pos+count)*4]:
                raise ValueError('Scratch proof SPIR-V entry/stage mismatch')
            entries.append(args[1])
        if op == 54:
            if count != 5 or in_function:
                raise ValueError('Scratch proof malformed function')
            functions.append(args[1]);in_function = True;starts += 1
        if op == 56:
            if count != 1 or not in_function or not 249 <= previous <= 255:
                raise ValueError('Scratch proof incomplete function')
            in_function = False;ends += 1
        if op == 59:
            if count < 4:
                raise ValueError('Scratch proof malformed variable')
            if args[2] == 6:
                private += 1
            if args[2] == 4 or args[2] > 12:
                raise ValueError('Scratch proof unsupported graphics storage class')
        previous = op;pos += count
    if len(entries) != 1 or memory != 1 or not shader or functions.count(entries[0]) != 1 or in_function or starts != ends:
        raise ValueError('Scratch proof incomplete graphics module')
    if low+high+crs//32 == 0 and private:
        raise ValueError('Scratch proof rejects zero-declared Private storage; implicit 128-byte local-memory fallback is not admitted')
    if low or high or crs or private:
        raise ValueError('Scratch proof nonzero local-memory range/initialization is unverified')
    return {'backend': 'metal_thread_private', 'proof': 'zero_declared_no_private',
            'sph_low': low, 'sph_high': high, 'sph_crs': crs, 'private_variables': private}


def stage_shader_bundle(inputs, converted, destination, scratch_spirv=None):
    """Prepare a private local bundle; unknown resources remain unsupported."""
    inputs, converted, destination = Path(inputs), Path(converted), Path(destination)
    records = json.loads((inputs / 'inputs.json').read_text())['records']
    translation = converted / 'translation.json'
    descriptor_audits = {row['code_sha256']: row.get('storage_descriptors', [])
                         for row in json.loads(translation.read_text())['results']} if translation.exists() else {}
    groups = {}
    for record in records:
        identity = (record['control_sha256'], record['code_sha256'], record['stage'])
        groups.setdefault(identity, []).append(record)
    entries, files, indices = [], {}, {}
    for identity, rows in sorted(groups.items()):
        control_hash, code_hash, stage = identity
        row = rows[0]
        # One code blob can have different control/c1 bounds. Keep each exact
        # identity's transformed MSL separate instead of overwriting an alias.
        msl_name = hashlib.sha256(':'.join(identity).encode()).hexdigest() + '.metal'
        entry = {'stage': stage, 'control_sha256': control_hash, 'code_sha256': code_hash,
                 'reflection': row.get('reflection', {})}
        for kind, directory, filename in (('control', inputs, control_hash + '.control'),
                                          ('code', inputs, code_hash + '.nvn'),
                                          ('msl', converted, code_hash + '.metal')):
            if not re.fullmatch(r'[0-9a-f]{64}\.(?:control|nvn|metal)', filename):
                raise ValueError('Unsafe shader input identity')
            payload = (directory / filename).read_bytes()
            digest = hashlib.sha256(payload).hexdigest()
            if kind != 'msl' and digest != entry[kind + '_sha256']:
                raise ValueError('Original shader SHA256 mismatch: ' + filename)
            target = msl_name if kind == 'msl' else filename
            files[target] = payload
            entry[kind] = {'path': target, 'bytes': len(payload), 'sha256': digest}
        if re.search(r'\bssbo\d+(?:_\d+)?\b', files[msl_name].decode('utf-8')):
            try:
                entry['storage_audit'] = audit_storage_resources(files[msl_name].decode('utf-8'), stage,
                                                                  row.get('reflection', {}), descriptor_audits.get(code_hash, []))
            except ValueError as exc:
                entry['storage_audit'] = {'access_bounds_validated': False, 'reason': str(exc)}
        try:
            source = files[msl_name].decode('utf-8')
            try:
                msl, constants = specialize_static_c1(source, files[control_hash + '.control'], files[code_hash + '.nvn'])
            except ValueError:
                msl, profile = specialize_finite_storage_shader(source, files[control_hash + '.control'], files[code_hash + '.nvn'],
                                                                stage, row.get('reflection', {}), descriptor_audits.get(code_hash, []))
                entry['draw_profile'] = profile
                constants = []
            msl, uniform_ranges = bound_static_uniforms(msl)
            maps = [shader_binding_map(msl, stage,
                                       candidate.get('reflection', {}), descriptor_audits.get(code_hash, [])) for candidate in rows]
            if any(mapping != maps[0] for mapping in maps):
                raise ValueError('Conflicting BNSH resource reflection for one exact identity')
            files[msl_name] = msl.encode('utf-8')
            entry['msl'].update(bytes=len(files[msl_name]), sha256=hashlib.sha256(files[msl_name]).hexdigest())
            entry.update({'status': 'ready', **maps[0], 'reflection': row.get('reflection', {}),
                          'constant_specializations': constants, 'uniform_ranges': uniform_ranges})
            if scratch_spirv is not None:
                spv = (Path(scratch_spirv) / (code_hash + '.spv')).read_bytes()
                proof = shader_scratch_proof(files[code_hash + '.nvn'], spv, stage)
                name = hashlib.sha256(spv).hexdigest() + '.spv'
                files[name] = spv
                proof['spirv'] = {'path': name, 'bytes': len(spv), 'sha256': hashlib.sha256(spv).hexdigest()}
                entry['scratch'] = proof
        except (ValueError, OSError) as exc:
            entry.update({'status': 'unsupported', 'reason': str(exc)})
        indices[identity] = len(entries)
        entries.append(entry)
    manifest = {'schema': 1, 'entries': entries,
                'stage_records': [{'entry': indices[(x['control_sha256'], x['code_sha256'], x['stage'])],
                                   'container': x['container'], 'variation': x['variation'], 'stage': x['stage']}
                                  for x in records]}
    destination.mkdir(parents=True, exist_ok=False)
    for name, data in files.items():
        (destination / name).write_bytes(data)
    (destination / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    return {'unique_entries': len(entries), 'stage_records': len(records),
            'ready': sum(x['status'] == 'ready' for x in entries),
            'unsupported': sum(x['status'] == 'unsupported' for x in entries)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', type=Path,
                        help='Local, unrelocated NSO memory image or BNSH file')
    parser.add_argument('--report', type=Path)
    parser.add_argument('--extract-dir', type=Path,
                        help='Fresh ignored directory for local shader inputs')
    parser.add_argument('--compiler', type=Path, help='Fixed, patched shader-compiler-cli binary')
    parser.add_argument('--compile-dir', type=Path, help='Fresh ignored directory for local converted output')
    parser.add_argument('--require-runtime-2d-textures', action='store_true',
                        help='Compile under explicit BNSH slot-based 2D requirements; runtime must check descriptors')
    parser.add_argument('--bundle-inputs', type=Path, help='Existing local extraction directory for private bundling')
    parser.add_argument('--bundle-compiled', type=Path, help='Existing local converted MSL directory for private bundling')
    parser.add_argument('--scratch-proof-directory', type=Path, help='Exact code-SHA keyed SPIR-V 1.3/1.4 graphics local-memory proofs')
    parser.add_argument('--bundle-dir', type=Path, help='Fresh ignored directory for the private runtime bundle')
    args = parser.parse_args(argv)
    bundle_args = (args.bundle_inputs, args.bundle_compiled, args.bundle_dir)
    if any(bundle_args):
        if not all(bundle_args) or any((args.image, args.report, args.extract_dir, args.compiler,
                                       args.compile_dir, args.require_runtime_2d_textures)):
            parser.error('Bundling requires exactly --bundle-inputs, --bundle-compiled and --bundle-dir')
        print(json.dumps(stage_shader_bundle(*bundle_args, scratch_spirv=args.scratch_proof_directory), sort_keys=True))
        return 0
    if args.scratch_proof_directory:
        parser.error('--scratch-proof-directory requires bundling')
    if not args.image or not args.report:
        parser.error('--image and --report are required for inspection/extraction')
    if bool(args.compiler) != bool(args.compile_dir) or (args.compiler and not args.extract_dir):
        parser.error('--compiler and --compile-dir require --extract-dir')
    image = args.image.read_bytes()
    containers = inspect_embedded_bnsh(image)
    if not containers:
        parser.error('No complete BNSH containers found')
    report = {'schema': 1, 'image_sha256': hashlib.sha256(image).hexdigest(),
              'containers': containers}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + '\n')
    stages = sum(len(v['shaders']) for c in containers for v in c['variations'])
    print(f'Validated {len(containers)} BNSH containers, {stages} shader stage records', flush=True)
    if args.extract_dir:
        records = extract_shader_inputs(image, args.extract_dir)
        if args.compiler:
            results = compile_shader_inputs(records, args.extract_dir, args.compile_dir, args.compiler,
                                            require_runtime_2d=args.require_runtime_2d_textures)
            converted = sum(row['converted'] for row in results)
            print(f'Converted {converted}/{len(results)} unique code blobs; NVN bindings remain unvalidated')
            return int(converted != len(results))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
