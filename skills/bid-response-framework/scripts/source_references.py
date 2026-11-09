"""Keep copied Word cross-references valid without colliding with our TOC."""
import re

from lxml import etree as ET

W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
R = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}'
REFERENCE = re.compile(r'^\s*(?:REF|PAGEREF|NOTEREF)\s+(?:"([^"]+)"|([^\s\\]+))', re.I)
INTERNAL_LINK = re.compile(r'^\s*HYPERLINK\s+\\l\s+(?:"([^"]+)"|([^\s\\]+))', re.I)
MISSING = '【待核对：原文交叉引用目标未收入本框架或无法唯一定位】'


def warning_run():
    run = ET.Element(W + 'r')
    ET.SubElement(run, W + 't').text = MISSING
    return run


def unwrap(element):
    """Freeze the original cached display, retaining its run formatting."""
    parent = element.getparent()
    index = parent.index(element)
    for child in list(element):
        parent.insert(index, child)
        index += 1
    parent.insert(index, warning_run())
    parent.remove(element)


def repair_references(source_elements, reserved_ids=0):
    # A decreasing/repeated source index starts another copy of original
    # content. References inside repeated forms must select their own target.
    # If a reordered plan makes that ambiguous, freeze rather than guess.
    elements = [(child, generation) for root, generation in source_elements for child in root.iter()]
    pending = {}
    targets = {}
    serial = 0
    for element, generation in elements:
        if element.tag == W + 'bookmarkStart':
            key = (generation, element.get(W + 'id'))
            pending.setdefault(key, []).append(element)
        elif element.tag == W + 'bookmarkEnd':
            key = (generation, element.get(W + 'id'))
            starts = pending.get(key, [])
            if not starts:
                element.getparent().remove(element)
                continue
            start = starts.pop(0)
            old_name = start.get(W + 'name')
            serial += 1
            name = 'BFSource' + str(serial)
            ident = str(reserved_ids + serial)
            start.set(W + 'id', ident)
            start.set(W + 'name', name)
            element.set(W + 'id', ident)
            targets.setdefault(old_name, []).append((generation, name))
    for starts in pending.values():
        for start in starts:
            start.getparent().remove(start)

    def target(name, generation):
        options = targets.get(name, [])
        local = [value for group, value in options if group == generation]
        if len(local) == 1:
            return local[0]
        return options[0][1] if len(options) == 1 else None

    def rewritten(instruction, generation):
        match = REFERENCE.match(instruction) or INTERNAL_LINK.match(instruction)
        if not match:
            return instruction
        number = 1 if match[1] is not None else 2
        destination = target(match[number], generation)
        if destination is None:
            return None
        a, b = match.span(number)
        return instruction[:a] + destination + instruction[b:]

    # Simple fields and hyperlink anchors are independent of the complex-field
    # stack. Snapshotting the elements also permits safe wrapper removal.
    for element, generation in elements:
        if element.tag == W + 'fldSimple':
            instruction = rewritten(element.get(W + 'instr', ''), generation)
            if instruction is None:
                unwrap(element)
            else:
                element.set(W + 'instr', instruction)
        elif element.tag == W + 'hyperlink' and element.get(W + 'anchor') and not element.get(R + 'id'):
            destination = target(element.get(W + 'anchor'), generation)
            if destination is None:
                unwrap(element)
            else:
                element.set(W + 'anchor', destination)

    stack = []

    def freeze(frame):
        run = frame['controls'][-1].getparent()
        run.getparent().insert(run.getparent().index(run) + 1, warning_run())
        for item in [*frame['instructions'], *frame['controls']]:
            item.getparent().remove(item)

    for element, generation in elements:
        if element.tag == W + 'fldChar':
            kind = element.get(W + 'fldCharType')
            if kind == 'begin':
                stack.append({'controls': [element], 'instructions': [],
                              'generation': generation, 'result': False})
            elif stack and kind == 'separate':
                stack[-1]['controls'].append(element)
                stack[-1]['result'] = True
            elif stack and kind == 'end':
                frame = stack.pop()
                frame['controls'].append(element)
                instructions = frame['instructions']
                instruction = ''.join(item.text or '' for item in instructions)
                replacement = rewritten(instruction, frame['generation'])
                if replacement is None:
                    freeze(frame)
                elif instructions and replacement != instruction:
                    instructions[0].text = replacement
                    for item in instructions[1:]:
                        item.text = ''
            elif kind in ('separate', 'end'):
                # The selected source range may start inside a complex field.
                element.getparent().remove(element)
        elif element.tag == W + 'instrText' and stack and not stack[-1]['result']:
            stack[-1]['instructions'].append(element)
    for frame in reversed(stack):
        freeze(frame)
