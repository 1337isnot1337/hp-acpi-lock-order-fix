# Contributing

Useful compatibility reports include the laptop model, BIOS version, kernel version, `iasl` version, recognized namespace, whether the build passed, whether the corrected table was active after boot, and results for battery/charger/suspend/recurrence tests.

Distinguish an offline build from a hardware test. State the observation interval for a claim that hangs stopped. Include failures too.

Do not attach full ACPI dumps, generated tables, private build directories, complete boot configurations or unreviewed system logs to a public issue. In particular, do not publish an `MSDM` table or OEM Windows key. Review excerpts for serial numbers, identifiers, addresses and private paths before posting.

For an unsupported pattern, provide a minimal, manually reviewed source excerpt around the relevant lock/call sequence. Do not add a force flag or disable baseline round-trip/full-disassembly validation to make a build pass.

Run the compiler/interpreter suite when changing transformations, package-length handling or archive construction:

```bash
python3 -m unittest discover -s tests -v
```

Generated fixtures belong in tests. Real firmware captures stay private.
