"""Generated minimal ASL fixtures; no vendor tables or machine dumps."""
import io
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import acpi_lock_order as tool

IASL = os.environ.get("IASL", shutil.which("iasl"))
EXEC = os.environ.get("ACPIEXEC", shutil.which("acpiexec"))


def fixture(bridge="LPCB", padding=0, prefix="", locals_used=""):
    return '''DefinitionBlock ("", "DSDT", 2, "TEST  ", "LOCKTEST", 0) {
Device (PC00) { Name (_ADR, Zero)
Device (BRIDGE) { Name (_ADR, Zero)
Device (EC0) { Name (_HID, "TEST0001")
Name (PAD0, Buffer (PADDING) {PADBYTES})
Mutex (ECMX, 0)
Name (PRES, One) Name (ODVA, Zero) Name (SNAP, Zero)
Method (GBAP, 0, Serialized) {
 Acquire (ECMX, 0xFFFF) Local0 = PRES Release (ECMX) Return (Local0)
}
Method (_Q08, 0, NotSerialized) {
 PREFIX
 Local0 = GBAP ()
 LOCALS
 Acquire (ECMX, 0xFFFF)
 If ((GBAP () == One)) { ODVA = 42 } Else { ODVA = Zero }
 Release (ECMX)
}
Method (_Q09, 0, NotSerialized) {
 Acquire (ECMX, 0xFFFF)
 Local0 = GBAP ()
 If ((Local0 == One)) { ODVA = 43 } Else { ODVA = Zero }
 Release (ECMX)
}
Method (RUN, 0, NotSerialized) {
 _Q08 () SNAP = ODVA _Q09 () Return (Package () {SNAP, ODVA})
}
}}}}
'''.replace("BRIDGE", bridge).replace("PADDING", str(padding)).replace("PADBYTES", ",".join(["0xA5"] * padding)).replace("PREFIX", prefix).replace("LOCALS", locals_used)


class FormatTests(unittest.TestCase):
    def test_package_boundaries(self):
        for body in (0, 1, 62, 63, 64, 4093, 4094, 4095, 1048572, 1048575, 268435450):
            encoded = tool.encode_pkg(body)
            length, width = tool.read_pkg(encoded, 0)
            self.assertEqual(length, body + width)
            self.assertEqual(width, len(encoded))
        with self.assertRaises(tool.Unsupported):
            tool.encode_pkg(1 << 28)

    def test_table_rejections(self):
        raw = bytearray(36)
        raw[:4] = b"DSDT"
        struct.pack_into("<I", raw, 4, 36)
        raw[9] = (-sum(raw)) & 255
        tool.validate_table(raw)
        for invalid in (b"", raw[:-1], b"SSDT" + raw[4:], raw[:-1] + b"\x01"):
            with self.assertRaises(tool.Unsupported):
                tool.validate_table(invalid)
        struct.pack_into("<I", raw, 24, 0xFFFFFFFF)
        raw[9] = 0
        raw[9] = (-sum(raw)) & 255
        with self.assertRaises(tool.Unsupported):
            tool.rewrite_table(raw, "", Path("."), "unused")

    def test_comments_and_strings_do_not_change_structure(self):
        source = fixture(prefix='Store ("} { Method (FAKE)", Debug) /* } { */ // }')
        a, b, changed = tool.analyze(source)
        self.assertEqual(a.path, ("PC00", "LPCB", "EC0_", "_Q08"))
        self.assertIn('"} { Method (FAKE)"', changed["_Q08"])
        self.assertIn("Local1 = GBAP ()", changed["_Q08"])

    def test_exhausted_locals_refused(self):
        source = fixture(locals_used=" ".join(f"Local{i} = Zero" for i in range(8)))
        with self.assertRaisesRegex(tool.Unsupported, "unused local"):
            tool.analyze(source)

    def test_additional_locking_refused(self):
        for prefix in ("Acquire (ECMX, 0xFFFF)", "Acquire (OTHER, 0xFFFF)"):
            with self.assertRaisesRegex(tool.Unsupported, "locking"):
                tool.analyze(fixture(prefix=prefix))

    def test_additional_call_under_lock_refused(self):
        source = fixture().replace("Release (ECMX)\n}", "GBAP () Release (ECMX)\n}", 1)
        with self.assertRaisesRegex(tool.Unsupported, "Additional GBAP"):
            tool.analyze(source)

    def test_conditional_lock_sequence_refused(self):
        source = fixture().replace("Acquire (ECMX, 0xFFFF)\n If", "If (One) { Acquire (ECMX, 0xFFFF)\n If", 1)
        source = source.replace("Release (ECMX)\n}", "Release (ECMX) }\n}", 1)
        with self.assertRaisesRegex(tool.Unsupported, "Conditional lock"):
            tool.analyze(source)

    def test_changed_signatures_and_sequences_refused(self):
        for source in (fixture().replace("GBAP, 0, Serialized", "GBAP, 0, NotSerialized"),
                       fixture().replace("GBAP () == One", "GBAP () == Zero"),
                       fixture().replace("_Q09, 0, NotSerialized", "_Q09, 1, NotSerialized")):
            with self.assertRaises(tool.Unsupported):
                tool.analyze(source)

    def test_conditional_container_refused(self):
        source = fixture().replace("Device (PC00)", "If (One) { Device (PC00)", 1) + "}"
        with self.assertRaisesRegex(tool.Unsupported, "container"):
            tool.analyze(source)

    def test_ambiguous_groups_refused(self):
        source = fixture().replace("Device (EC0)", "Device (EC1)", 1)
        source = fixture()[:-1] + source[source.index("Device (PC00)"):]
        with self.assertRaises(tool.Unsupported):
            tool.analyze(source)

    def test_cpio_extracts_exact_payload(self):
        archive = tool.make_cpio(b"example-table")
        self.assertEqual(len(archive) % 512, 0)
        if shutil.which("bsdtar"):
            with tempfile.NamedTemporaryFile() as f:
                f.write(archive)
                f.flush()
                extracted = subprocess.check_output(["bsdtar", "-xOf", f.name, "kernel/firmware/acpi/DSDT.aml"])
                self.assertEqual(extracted, b"example-table")
        self.assertEqual(archive, tool.make_cpio(b"example-table"))

    def test_guard_survives_python_optimization(self):
        result = subprocess.run([sys.executable, "-O", "-c",
                                 "import acpi_lock_order as t; t.require(False, 'rejected')"], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Unsupported: rejected", result.stderr)


@unittest.skipUnless(IASL, "iasl required for compiler integration")
class CompilerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def compile(self, source):
        path = self.dir / "fixture.dsl"
        path.write_text(source)
        tool.run_iasl(IASL, ["-oa", path])
        data = path.with_suffix(".aml").read_bytes()
        source = tool.disassemble(data, self.dir, "original", IASL)
        return data, source

    def test_models_offsets_and_package_growth(self):
        for bridge, padding in (("LPCB", 0), ("LPC0", 37), ("ISA0", 3900), ("LPCB", 4050), ("LPC0", 65500)):
            with self.subTest(bridge=bridge, padding=padding):
                original, source = self.compile(fixture(bridge, padding))
                fixed, manifest, diff = tool.rewrite_table(original, source, self.dir, IASL)
                self.assertNotEqual(tool.sha(original), tool.sha(fixed))
                self.assertTrue(manifest["unrelated_bytes_preserved"])
                self.assertTrue(manifest["full_disassembly_verified"])
                self.assertIn("Local1 = GBAP", diff)
                fixed_source = tool.disassemble(fixed, self.dir, "fixed-again", IASL)
                with self.assertRaises(tool.Unsupported):
                    tool.analyze(fixed_source)

    def test_enclosing_package_length_field_grows(self):
        original, source = self.compile(fixture(padding=3848))
        original_query = tool.analyze(source)[0]
        old_widths = [x[3] for x in tool.locate(original, original_query)[:-1]]
        fixed, _, _ = tool.rewrite_table(original, source, self.dir, IASL)
        fixed_source = tool.disassemble(fixed, self.dir, "grown", IASL)
        nodes = tool.parse_nodes(fixed_source)[1]
        fixed_query = next(n for n in nodes if n.kind == "Method" and n.name == "_Q08")
        new_widths = [x[3] for x in tool.locate(fixed, fixed_query)[:-1]]
        self.assertGreater(sum(new_widths), sum(old_widths))

    @unittest.skipUnless(EXEC, "acpiexec required for execution test")
    def test_original_and_corrected_behavior(self):
        original, source = self.compile(fixture())
        fixed, _, _ = tool.rewrite_table(original, source, self.dir, IASL)
        for name, data in (("before", original), ("after", fixed)):
            path = self.dir / (name + ".aml")
            path.write_bytes(data)
            result = subprocess.run([EXEC, "-di", "-b", "execute \\PC00.LPCB.EC0.RUN", str(path)],
                                    capture_output=True, text=True, timeout=10)
            log = result.stdout + result.stderr
            self.assertNotIn("ACPI Error", log)
            self.assertIn("[Integer] = 000000000000002A", log)
            self.assertIn("[Integer] = 000000000000002B", log)

    def test_roundtrip_mismatch_refused(self):
        original, source = self.compile(fixture())
        with patch.object(tool, "single_method", return_value=b"wrong"):
            with self.assertRaisesRegex(tool.Unsupported, "exactly"):
                tool.rewrite_table(original, source, self.dir, IASL)

    def test_last_incrementable_oem_revision(self):
        original, source = self.compile(fixture().replace('"LOCKTEST", 0)', '"LOCKTEST", 0xFFFFFFFE)', 1))
        fixed, manifest, _ = tool.rewrite_table(original, source, self.dir, IASL)
        tool.validate_table(fixed)
        self.assertEqual(manifest["oem_revision"], 0xFFFFFFFF)

    def test_cli_build_verify_and_no_clobber(self):
        original, _ = self.compile(fixture())
        path = self.dir / "input.aml"
        path.write_bytes(original)
        output = self.dir / "result"
        with patch("sys.stdout", new_callable=io.StringIO):
            self.assertEqual(tool.main(["--iasl", IASL, "build", "--input", str(path), "--output", str(output)]), 0)
            self.assertEqual(tool.main(["verify", "--input", str(output / "DSDT-fixed.aml"),
                                        "--manifest", str(output / "manifest.json")]), 0)
        self.assertEqual(output.stat().st_mode & 0o777, 0o700)
        self.assertTrue(all(p.stat().st_mode & 0o777 == 0o600 for p in output.iterdir()))
        self.assertTrue(json.loads((output / "manifest.json").read_text())["experimental"])
        with self.assertRaises(SystemExit):
            tool.main(["--iasl", IASL, "build", "--input", str(path), "--output", str(output)])
        with self.assertRaises(SystemExit):
            tool.main(["verify", "--input", str(path), "--manifest", str(output / "manifest.json")])


if __name__ == "__main__":
    unittest.main()
