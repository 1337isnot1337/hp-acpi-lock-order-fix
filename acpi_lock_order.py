#!/usr/bin/env python3
"""Build a local ACPI override for a recognized GBAP/ECMX lock inversion.

No kernel memory, firmware, boot configuration, or system files are written.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import difflib
import hashlib
import json
from pathlib import Path
import re
import shutil
import stat
import struct
import subprocess
import tempfile


class Unsupported(ValueError):
    """Input cannot be safely handled by this narrow transformation."""


def require(condition, message):
    if not condition:
        raise Unsupported(message)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def validate_table(data):
    require(36 <= len(data) <= 16 * 1024 * 1024, "Invalid table size")
    require(data[:4] == b"DSDT", "Input must be a single DSDT, not an acpidump or another table")
    require(struct.unpack_from("<I", data, 4)[0] == len(data), "Table length mismatch")
    require(sum(data) % 256 == 0, "Invalid ACPI checksum")


def mask_noncode(source):
    """Hide comments/strings while preserving character positions and newlines."""
    token = r'"(?:\\.|[^"\\])*"|/\*[\s\S]*?\*/|//[^\n]*'
    return re.sub(token, lambda m: re.sub(r"[^\n]", " ", m[0]), source)


def matching(mask, start, opening="(", closing=")"):
    depth = 1
    for pos in range(start + 1, len(mask)):
        depth += (mask[pos] == opening) - (mask[pos] == closing)
        if depth == 0:
            return pos
    raise Unsupported("Unbalanced disassembly")


def canonical_name(name):
    name = re.sub(r"\s", "", name)
    absolute = name.startswith("\\")
    name = name.lstrip("\\")
    up = len(name) - len(name.lstrip("^"))
    name = name[up:]
    parts = name.split(".") if name else []
    require(all(re.fullmatch(r"[A-Z_][A-Z0-9_]{0,3}", p) for p in parts), "Unsupported ACPI name: " + name)
    return absolute, up, tuple(p.ljust(4, "_") for p in parts)


def resolve(name, parent):
    absolute, up, parts = canonical_name(name)
    require(up <= len(parent), "ACPI parent prefix exceeds scope")
    return (() if absolute else parent[:len(parent) - up]) + parts


def path_text(parts):
    return "\\" + ".".join(parts)


@dataclass
class Node:
    kind: str
    name: str
    args: list[str]
    start: int
    brace: int
    end: int
    parents: list
    path: tuple


def parse_nodes(source):
    mask = mask_noncode(source)
    nodes = []
    pattern = r"\b(DefinitionBlock|Scope|Device|Method|Processor|PowerResource|ThermalZone)\s*\("
    for m in re.finditer(pattern, mask):
        paren = mask.index("(", m.start())
        close = matching(mask, paren)
        brace = close + 1
        while brace < len(mask) and mask[brace].isspace():
            brace += 1
        require(brace < len(mask) and mask[brace] == "{", "Expected declaration body")
        end = matching(mask, brace, "{", "}") + 1
        args = [s.strip() for s in source[paren + 1:close].split(",")]
        parents = [n for n in nodes if n.brace < m.start() < end <= n.end]
        parent_path = parents[-1].path if parents else ()
        path = () if m[1] == "DefinitionBlock" else resolve(args[0], parent_path)
        nodes.append(Node(m[1], args[0], args, m.start(), brace, end, parents, path))
    return mask, nodes


def read_pkg(data, pos):
    require(0 <= pos < len(data), "Package outside table")
    width = 1 + (data[pos] >> 6)
    require(width == 1 or not data[pos] & 0x30, "Reserved AML package-length bits are set")
    require(pos + width <= len(data), "Truncated AML package length")
    length = data[pos] & (15 if width > 1 else 63)
    for i in range(width - 1):
        length |= data[pos + i + 1] << (4 + 8 * i)
    require(length >= width, "Invalid AML package length")
    return length, width


def encode_pkg(body_size):
    require(body_size >= 0, "Negative AML package body size")
    for width in range(1, 5):
        length = body_size + width
        if length < (64 if width == 1 else 1 << (4 + 8 * (width - 1))):
            if width == 1:
                return bytes([length])
            return bytes([((width - 1) << 6) | (length & 15)]) + bytes(
                (length >> (4 + 8 * i)) & 255 for i in range(width - 1))
    raise Unsupported("AML package exceeds format limit")


def read_name(data, pos):
    start = pos
    if data[pos] == 0x5C:
        pos += 1
    while pos < len(data) and data[pos] == 0x5E:
        pos += 1
    prefix = data[start:pos].decode("ascii")
    require(pos < len(data), "Truncated AML name")
    if data[pos] == 0x2E:
        count, pos = 2, pos + 1
    elif data[pos] == 0x2F:
        require(pos + 1 < len(data), "Truncated MultiName")
        count, pos = data[pos + 1], pos + 2
        require(count > 0, "Empty MultiName")
    else:
        count = 1
    end = pos + count * 4
    require(end <= len(data), "Truncated NameSeg")
    names = [data[p:p + 4].decode("ascii") for p in range(pos, end, 4)]
    require(all(re.fullmatch(r"[A-Z_][A-Z0-9_]{3}", n) for n in names), "Invalid NameSeg")
    return canonical_name(prefix + ".".join(names)), end


OPS = {"Scope": b"\x10", "Device": b"\x5b\x82", "Method": b"\x14",
       "Processor": b"\x5b\x83", "PowerResource": b"\x5b\x84", "ThermalZone": b"\x5b\x85"}


def declarations(data, node):
    op = OPS[node.kind]
    found = []
    start = 36
    while (pos := data.find(op, start)) >= 0:
        start = pos + 1
        try:
            length, width = read_pkg(data, pos + len(op))
            name, body = read_name(data, pos + len(op) + width)
            end = pos + len(op) + length
            if name == canonical_name(node.name) and body < end <= len(data):
                found.append((pos, end, pos + len(op), width))
        except (Unsupported, UnicodeDecodeError, IndexError):
            continue
    return found


def locate(data, node):
    chain = [p for p in node.parents if p.kind != "DefinitionBlock"] + [node]
    options = [[]]
    for item in chain:
        require((item.kind in OPS and item.kind != "Method") or item is node, "Nested method unsupported")
        candidates = declarations(data, item)
        options = [path + [candidate] for path in options for candidate in candidates
                   if not path or path[-1][0] < candidate[0] < candidate[1] <= path[-1][1]]
        require(len(options) <= 1000, "Ambiguous declaration search")
    require(len(options) == 1, "Cannot unambiguously locate " + path_text(node.path))
    return options[0]


def analyze(source):
    mask, nodes = parse_nodes(source)
    methods = {name: [n for n in nodes if n.kind == "Method" and n.path[-1] == name]
               for name in ("_Q08", "_Q09", "GBAP")}
    triples = [(a, b, g) for a in methods["_Q08"] for b in methods["_Q09"] for g in methods["GBAP"]
               if a.path[:-1] == b.path[:-1] == g.path[:-1]]
    require(len(triples) == 1, "Need exactly one _Q08/_Q09/GBAP group in the same scope")
    q08, q09, gbap = triples[0]
    for q in (q08, q09):
        require(len(q.args) == 3 and q.args[1:] == ["0", "NotSerialized"], "Unexpected query signature")
        # No hidden If/Else/Buffer packages may surround a declaration we resize.
        braces = [i for i, c in enumerate(mask[:q.start]) if c == "{" and matching(mask, i, "{", "}") >= q.end - 1]
        require(set(braces) == {p.brace for p in q.parents}, "Conditional or unsupported declaration container")
    require(len(gbap.args) == 3 and gbap.args[1:] == ["0", "Serialized"], "GBAP must be a zero-argument Serialized method")
    require(re.search(r"Acquire\s*\(\s*ECMX\s*,\s*0xFFFF\s*\)", mask[gbap.brace:gbap.end]), "GBAP does not acquire ECMX")
    changed = {}
    for q in (q08, q09):
        text = source[q.start:q.end]
        masked = mask_noncode(text)
        if q is q08:
            pattern = r"Acquire\s*\(\s*ECMX\s*,\s*0xFFFF\s*\)\s*If\s*\(\s*\(\s*GBAP\s*\(\s*\)\s*==\s*One\s*\)\s*\)"
            locals_used = set(re.findall(r"\bLocal[0-7]\b", masked))
            free = next(("Local" + str(i) for i in range(8) if "Local" + str(i) not in locals_used), None)
            require(free is not None, "_Q08 has no unused local for the battery-presence snapshot")
            replacement = f"{free} = GBAP ()\n                Acquire (ECMX, 0xFFFF)\n                If (({free} == One))"
        else:
            pattern = r"Acquire\s*\(\s*ECMX\s*,\s*0xFFFF\s*\)\s*(Local[0-7])\s*=\s*GBAP\s*\(\s*\)"
            replacement = None
        matches = list(re.finditer(pattern, masked))
        require(len(matches) == 1, "Unrecognized or already-fixed " + q.name + " lock sequence")
        m = matches[0]
        locks = list(re.finditer(r"\b(Acquire|Release)\s*\(\s*([^,)]+)", masked))
        require([(x[1], x[2].strip()) for x in locks] == [("Acquire", "ECMX"), ("Release", "ECMX")],
                q.name + " has additional or unsupported explicit locking")
        require(locks[0].start() == m.start(), "Rewrite does not begin at the only lock acquisition")
        require(sum(c == "{" for c in masked[:m.start()]) - sum(c == "}" for c in masked[:m.start()]) == 1,
                "Conditional lock sequence requires manual review")
        critical_calls = list(re.finditer(r"\bGBAP\s*\(\s*\)", masked[locks[0].end():locks[1].start()]))
        require(len(critical_calls) == 1, "Additional GBAP call under ECMX requires manual review")
        line_start = text.rfind("\n", 0, m.start()) + 1
        indent = re.match(r"[ \t]*", text[line_start:m.start()])[0]
        require("/*" not in text[m.start():m.end()] and "//" not in text[m.start():m.end()], "Comments inside rewrite span require manual review")
        if replacement is None:
            replacement = f"{m[1]} = GBAP ()\n                Acquire (ECMX, 0xFFFF)"
        replacement = replacement.replace("\n                ", "\n" + indent)
        changed[q.name] = text[:m.start()] + replacement + text[m.end():]
    return q08, q09, changed


def external_declarations(method, node):
    """Compiler-only placeholders; exact original byte round-trip is mandatory."""
    body = mask_noncode(method)[method.index("{") + 1:]
    names = {}
    token = r"[\\^]*(?:[A-Z_][A-Z0-9_]{0,3}\.)*[A-Z_][A-Z0-9_]{0,3}(?![A-Za-z0-9_])"
    for m in re.finditer(token, body):
        name = m[0]
        if m.start() and (body[m.start() - 1].isalnum() or body[m.start() - 1] == "_"):
            continue
        if name in ("One", "Zero", "Ones"):
            continue
        context = node.path if name.startswith("^") else node.path[:-1]
        path = resolve(name, context)
        kind = "MethodObj" if re.match(r"\s*\(", body[m.end():]) else "IntObj"
        names[path] = kind if kind == "MethodObj" else names.get(path, kind)
    for op, kind in (("Acquire", "MutexObj"), ("Release", "MutexObj"), ("Notify", "DeviceObj")):
        for m in re.finditer(op + r"\s*\(\s*(" + token + r")", body):
            names[resolve(m[1], node.path if m[1].startswith("^") else node.path[:-1])] = kind
    names[node.path[:-1]] = "DeviceObj"
    return names


def run_iasl(iasl, args):
    try:
        result = subprocess.run([iasl, *map(str, args)], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise Unsupported("iasl could not complete: " + str(e)) from e
    require(result.returncode == 0, "iasl failed; no override produced:\n" + (result.stdout + result.stderr)[-3000:])
    return result.stdout + result.stderr


def disassemble(data, directory, name, iasl):
    path = directory / (name + ".aml")
    path.write_bytes(data)
    run_iasl(iasl, ["-d", path])
    return path.with_suffix(".dsl").read_text()


def compile_methods(source, queries, directory, name, iasl):
    externals = {}
    for node in queries:
        for path, kind in external_declarations(source[node.name], node).items():
            prior = externals.get(path)
            require(prior in (None, "IntObj", kind) or kind == "IntObj", "Conflicting compiler placeholder types")
            if prior is None or kind != "IntObj":
                externals[path] = kind
    header = 'DefinitionBlock ("", "SSDT", 2, "FIXTOO", "LOCKTEST", 1) {\n'
    header += "\n".join("External (" + path_text(p) + ", " + t + ")" for p, t in sorted(externals.items()))
    text = header + "\nScope (" + path_text(queries[0].path[:-1]) + ") {\n" + "\n".join(source.values()) + "\n}}\n"
    # Keep the firmware's explicit Store/Divide encoding for exact baseline round-trip.
    text = re.sub(r"ODVA\s*=\s*\(\(BDC\s*\*\s*BDV\)\s*/\s*(0x[0-9A-Fa-f]+)\)",
                  r"Store (Divide (Multiply (BDC, BDV), \1, ), ODVA)", text)
    path = directory / (name + ".dsl")
    path.write_text(text)
    run_iasl(iasl, ["-oa", path])
    return path.with_suffix(".aml").read_bytes(), text


def single_method(data, name):
    node = Node("Method", name, [], 0, 0, 0, [], ())
    matches = declarations(data, node)
    require(len(matches) == 1, "Compiled method is ambiguous: " + name)
    return data[matches[0][0]:matches[0][1]]


def rewrite_table(original, source, directory, iasl):
    validate_table(original)
    require(struct.unpack_from("<I", original, 24)[0] < 0xFFFFFFFF, "OEM revision cannot be incremented")
    q08, q09, changed = analyze(source)
    queries = (q08, q09)
    before = {q.name: source[q.start:q.end] for q in queries}
    baseline, _ = compile_methods(before, queries, directory, "methods-original", iasl)
    compiled, _ = compile_methods(changed, queries, directory, "methods-fixed", iasl)
    edits, containers, method_report = [], {}, []
    for q in queries:
        chain = locate(original, q)
        start, end, _, _ = chain[-1]
        require(original[start:end] == single_method(baseline, q.name), "Original " + q.name + " does not reproduce firmware bytes exactly")
        replacement = single_method(compiled, q.name)
        edits.append((start, end, replacement))
        containers.update({x[0]: x for x in chain[:-1]})
        method_report.append({"path": path_text(q.path), "old_size": end - start, "new_size": len(replacement), "baseline_roundtrip_exact": True})
    for start, end, pkg_pos, width in sorted(containers.values(), key=lambda x: x[1] - x[0]):
        old_length, _ = read_pkg(original, pkg_pos)
        delta = sum(len(data) - (b - a) for a, b, data in edits if start < a < b <= end)
        edits.append((pkg_pos, pkg_pos + width, encode_pkg(old_length - width + delta)))
    edits.sort()
    require(all(a[1] <= b[0] for a, b in zip(edits, edits[1:])), "Overlapping edits")
    fixed = bytearray(original)
    for a, b, data in reversed(edits):
        fixed[a:b] = data
    struct.pack_into("<I", fixed, 4, len(fixed))
    revision = struct.unpack_from("<I", original, 24)[0] + 1
    struct.pack_into("<I", fixed, 24, revision)
    fixed[9] = 0
    fixed[9] = (-sum(fixed)) & 255
    validate_table(fixed)
    reverse, delta = bytearray(fixed), 0
    mapped = []
    for a, b, data in edits:
        mapped.append((a + delta, a + delta + len(data), original[a:b]))
        delta += len(data) - (b - a)
    for a, b, data in reversed(mapped):
        reverse[a:b] = data
    reverse[:36] = original[:36]
    require(reverse == original, "Reverse reconstruction failed")
    expected = source
    for q in sorted(queries, key=lambda n: n.start, reverse=True):
        expected = expected[:q.start] + changed[q.name] + expected[q.end:]
    expected = re.sub(r'(DefinitionBlock\s*\([^\n]*,\s*)0x[0-9A-Fa-f]+(\))',
                      lambda m: m[1] + f"0x{revision:08X}" + m[2], expected, count=1)
    actual = disassemble(fixed, directory, "DSDT-fixed", iasl)
    require(expected[expected.index("DefinitionBlock"): ] == actual[actual.index("DefinitionBlock"): ],
            "Whole-table disassembly differs from the intended change; no override produced")
    manifest = {"format_version": 1, "experimental": True, "hardware_tested": False,
                "original_sha256": sha(original), "fixed_sha256": sha(fixed),
                "original_size": len(original), "fixed_size": len(fixed), "oem_revision": revision,
                "oem_id": original[10:16].decode("ascii", errors="replace"),
                "oem_table_id": original[16:24].decode("ascii", errors="replace"),
                "methods": method_report, "unrelated_bytes_preserved": True,
                "full_disassembly_verified": True}
    diff = "".join(difflib.unified_diff(source[source.index("DefinitionBlock"):].splitlines(True),
                                    expected[expected.index("DefinitionBlock"):].splitlines(True),
                                    fromfile="original/DSDT.dsl", tofile="fixed/DSDT.dsl"))
    return bytes(fixed), manifest, diff


def make_cpio(table):
    """Deterministic root-owned newc archive; no external archive utility needed."""
    output = bytearray()
    entries = [("kernel", b"", stat.S_IFDIR | 0o755),
               ("kernel/firmware", b"", stat.S_IFDIR | 0o755),
               ("kernel/firmware/acpi", b"", stat.S_IFDIR | 0o755),
               ("kernel/firmware/acpi/DSDT.aml", table, stat.S_IFREG | 0o644),
               ("TRAILER!!!", b"", 0)]
    for ino, (name, data, mode) in enumerate(entries):
        fields = (ino, mode, 0, 0, 2 if stat.S_ISDIR(mode) else 1, 0, len(data), 0, 0, 0, 0, len(name) + 1, 0)
        output += b"070701" + "".join(f"{x:08x}" for x in fields).encode()
        output += name.encode() + b"\0"
        output += b"\0" * (-len(output) % 4)
        output += data
        output += b"\0" * (-len(output) % 4)
    output += b"\0" * (-len(output) % 512)
    return bytes(output)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iasl", default="iasl", help="ACPICA iasl executable")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("inspect", "build", "verify"):
        sub = commands.add_parser(name)
        sub.add_argument("--input", required=True, type=Path, help="A single raw DSDT .aml file")
        if name == "build":
            sub.add_argument("--output", required=True, type=Path, help="New private output directory; must not exist")
        if name == "verify":
            sub.add_argument("--manifest", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        original = args.input.read_bytes()
        validate_table(original)
        if args.command == "verify":
            manifest = json.loads(args.manifest.read_text())
            require(manifest.get("format_version") == 1 and sha(original) == manifest.get("fixed_sha256"), "Corrected table is not active or manifest does not match")
            print("Exact corrected table verified; hardware behavior still requires testing.")
            return 0
        iasl = shutil.which(args.iasl)
        require(iasl is not None, "Install ACPICA iasl (acpica-tools on Debian/Ubuntu; acpica on Arch)")
        if args.command == "build":
            require(not args.output.exists(), "Output directory already exists; refusing to overwrite")
        with tempfile.TemporaryDirectory(prefix="acpi-lock-order-") as tmp:
            directory = Path(tmp)
            source = disassemble(original, directory, "DSDT", iasl)
            q08, q09, _ = analyze(source)
            if args.command == "inspect":
                print(json.dumps({"recognized": True, "original_sha256": sha(original),
                                  "scope": path_text(q08.path[:-1]), "methods": [path_text(q.path) for q in (q08, q09)],
                                  "build_validation_required": True}, indent=2))
                return 0
            fixed, manifest, diff = rewrite_table(original, source, directory, iasl)
            archive = make_cpio(fixed)
            manifest["archive_sha256"] = sha(archive)
            manifest["archive_blake2b"] = hashlib.blake2b(archive).hexdigest()
            manifest["iasl_version"] = run_iasl(iasl, ["-v"]).strip()
            args.output.mkdir(mode=0o700, parents=True)
            for name, data in (("DSDT-fixed.aml", fixed), ("acpi-lock-order.cpio", archive),
                               ("manifest.json", (json.dumps(manifest, indent=2) + "\n").encode()),
                               ("changes.patch", diff.encode())):
                path = args.output / name
                path.write_bytes(data)
                path.chmod(0o600)
            print(json.dumps(manifest, indent=2))
            print("Prepared locally. Nothing installed; no boot configuration changed.")
        return 0
    except (Unsupported, OSError, ValueError) as e:
        parser.exit(2, f"Refused: {e}\n")


if __name__ == "__main__":
    raise SystemExit(main())
