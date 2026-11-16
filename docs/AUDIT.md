# Implementation audit and validation

Date: 2026-11-16. This is a code and evidence review performed during preparation of the public tool. It is not an independent third-party review or a hardware certification.

## Scope

Reviewed the initial host-specific builder, boot-entry installer/remover, AML transformation, package-length updates, output integrity, test methodology and publication contents. The public tool was then exercised against the same privately held real DSDT, followed by generated public fixtures and a fresh private interpreter run of the exact portable-tool output.

The real input is an HP Dragonfly 13.5 G4 DSDT, BIOS V90 01.13.01. The live failure had opposing `GBAP` and `ECMX` owners and waiters. The public repository does not include that table, kernel memory captures or system logs.

## Findings and changes

| Finding | Resolution | Status |
| --- | --- | --- |
| The original builder used one table hash and fixed parent offsets. | Public code locates named declaration chains, refuses ambiguity, resizes enclosing lengths, and validates complete output disassembly. | Fixed for supported layouts |
| Python `assert` guarded important validation in the original builder. | Production checks use explicit exceptions, including under `python -O`. The local builder and installer were hardened too. | Fixed |
| Reusing `_Q08`'s `Local0` would be unsafe on variants that use it later. | Public code selects an unused local from `Local0` through `Local7`, or refuses the transformation. The original host-specific method had no later dependency on its overwritten local. | Fixed for portability |
| Simple brace counting could mistake comments or strings for structure. | Position-preserving masking excludes both from declaration and transformation analysis. Tests cover misleading braces and method text in strings/comments. | Fixed |
| A superficially matching lock sequence might be nested under another acquisition or retain another `GBAP` call while holding `ECMX`. | Each supported query must have exactly one explicit `ECMX` acquire/release pair and one `GBAP` call in that critical section. Conditional lock sequences and additional locking are refused. | Fixed by narrower acceptance |
| Growing a method can also grow an ancestor's package-length field. | Ancestors are updated from the inside out, propagating both payload and length-field changes. An integration test exercises an actual enclosing length-field width increase. | Fixed |
| Unrelated code could change during full-table recompilation. | Only the two query methods are compiled. Original methods must reproduce their input bytes; the rest of the table is retained, reverse-reconstructed and checked by complete disassembly. No compiler errors are suppressed. | Addressed |
| Local removal depended on the source archive still being present and intact. | Removal now operates on the exact installed config block before consulting build inputs. A write-intercepted test covers unavailable source data and a changed destination file. The public tool does not install anything. | Fixed locally; avoided publicly |
| Large offline test counts could imply hardware validation. | README, manifests and this audit explicitly distinguish emulation, model exploration, real-table builds and physical-machine testing. | Documentation corrected |
| Publishing the original working directory would expose machine-specific firmware and configuration. | The repository contains original code, generated-fixture definitions, documentation and a small aggregate validation record. No vendor tables, generated overrides, complete logs, boot configs, credentials or bundled third-party binaries are included. | Publication contents reviewed |

## Transformation checks

1. Validate the DSDT signature, length, checksum and incrementable OEM revision.
2. Identify one `_Q08` / `_Q09` / serialized zero-argument `GBAP` group in a common namespace.
3. Require the specific supported direct lock/call sequences and an unused scratch local for `_Q08`.
4. Compile the original query bodies in isolation and require byte-for-byte equality with the corresponding original AML methods.
5. Compile the corrected query bodies. Locate their original named containers and update all affected package lengths, including length-field width changes.
6. Update only the table length, OEM revision and checksum in the header.
7. Reverse the enumerated edits and require exact recovery of the original table.
8. Disassemble the complete corrected table and require exact agreement with the intended source below the table-header comments.
9. Construct a deterministic, uncompressed `newc` archive with fixed relative paths and root ownership.
10. Write results to a new private output directory. The tool does not select a boot entry, edit an initramfs or change a security setting.

The declaration locator is deliberately narrow; it is not a general AML interpreter. Candidate discovery alone is not trusted. Unique containment, original byte reproduction and complete corrected-source comparison are mandatory checks that remain enabled under Python optimization.

## Public test suite

The unittest suite covers malformed/truncated/checksum-invalid inputs, OEM revision overflow, AML package-size boundaries, comments/strings, exhausted locals, altered method signatures, extra locking/calls, conditional containers, ambiguous groups, archive extraction, compiler round-trip rejection, private output permissions, no-clobber behavior and exact-table verification.

Compiler integration fixtures vary namespace names and physical table sizes. They exercise an enclosing package-length field growing during the rewrite. An interpreter fixture checks sequential behavior before and after the correction. A finite scheduling model covers nine combinations of query and battery tasks. The model reproduces the original cycle in eight applicable combinations; the ninth, two `_Q09` tasks alone, has no opposing order and correctly has no deadlock in either version.

CI runs these tests with Python 3.10, 3.12 and 3.14 and distribution ACPICA tools. The fixture source is generated by the tests; it is not extracted vendor firmware.

## Private real-table validation

The exact public-tool output was built with Python optimization enabled, compared to the table used in a fresh private interpreter run, and tested as follows:

| Check | Result |
| --- | --- |
| Original `_Q08` / `_Q09` byte reproduction | Exact |
| Whole corrected disassembly | Exactly the intended two-method change and OEM revision increment |
| Original/fixed table sizes | 366,786 / 366,789 bytes |
| Sequential interpreter cases | 49,152 total: 24,576 baseline and 24,576 corrected |
| Checked state assertions | 516,096; zero failures |
| Checked notifications | 69,632; counts and values match expectations |
| Concurrent corrected query evaluations | 6,400 across 16 threads |
| Additional concurrent direct `GBAP` calls | 3,200 |
| Corrected finite-model reachable states | 3,786; no deadlock in the model |
| Host-specific override hardware boot | **Passed:** selected the test entry, kernel loaded the matching corrected DSDT |
| Exact public-builder output hardware boot | **Not yet tested** |
| Initial battery and AC reporting | **Observed:** battery present at 97%, adapter online; deeper accuracy and transition checks pending |
| Charger disconnect and reconnect | **Passed:** AC and discharge status updated, then AC returned online |
| Actual charging start/end and suspend/resume | **Not yet tested** |
| Long-term recurrence prevention | **Not established** |

The emulation covers query type, cache flags, cached/reported presence, region availability, notification enablement, large/zero power values and optional external methods present/absent. EC fields are emulated and external consumers are stubs. Unchanged helper methods and both corrected query methods match the real table bytes in that execution harness.

Earlier host-specific packaging tests additionally ran Linux 7.2.14's early CPIO scanner in userspace against the real initramfs layout and verified existing early firmware access. Those tests do not constitute a boot test of the public builder's output or a test of every bootloader.

## First physical boot (2026-11-16)

The Dragonfly booted the separate host-specific override. The active DSDT hash matched that artifact and the kernel reported a physical table override. Battery presence and AC online were readable, UPower and logind were active, and no ACPI errors or hung-task warnings appeared in the first minute. The sysfs battery reported 97% and “Not charging”; UPower reported fully charged. A subsequent disconnect changed AC to offline and battery state to discharging; reconnect changed AC back online and restored the prior status. Reads completed and no ACPI errors or stuck tasks appeared. Actual charging start/end and battery accuracy remain untested.

The host-specific `_Q08` uses `Local0`; the public builder uses a previously unused `Local1` on this firmware. Their table bytes and hashes differ. The successful boot therefore does not validate the public builder’s exact output on hardware.

## Remaining limits

- Moving the presence read before the following EC lock changes the timing of the snapshot. Sequential behavior is tested; concurrent physical state transitions still need hardware validation.
- A complete DSDT override carries platform data from its source firmware. Do not move generated tables between machines or reuse them after BIOS changes.
- Incrementing the OEM revision can supersede a future vendor table. There is no pre-kernel firmware-version gate. Keep the override opt-in and remove it before a BIOS update.
- No code forcibly unwinds an already-established kernel deadlock. An affected session needs recovery separately.
- This correction addresses the observed `GBAP` inversion only. It does not modify the separately reported `GFVE` pattern or promise to fix unrelated ACPI hangs.
- Unsupported layouts are intentionally rejected. No bypass of the checks is offered.

See [BOOT-TEST.md](BOOT-TEST.md) for a staged physical-machine test and rollback procedure. Compatibility reports should clearly separate a recognized pattern, successful local build, successful boot and recurrence testing.
