# HP ACPI lock-order workaround

Build a machine-specific Linux ACPI override for the `GBAP` / `ECMX` deadlock found in some HP firmware.

**Experimental. A related host-specific override has booted successfully; the public builder’s exact output and long-term prevention have not yet been verified on hardware.** This is a targeted workaround for a recognized firmware pattern. It does not fix every ACPI hang, and it does not flash your BIOS.

## The problem

The firmware's serialized battery method `GBAP` can acquire the embedded-controller mutex `ECMX`. The `_Q08` and `_Q09` controller event handlers acquire those locks in the opposite order. Two workers can end up waiting on each other indefinitely:

```text
battery worker:       owns GBAP → waits for ECMX
controller worker:    owns ECMX → waits for GBAP
```

Battery readers and other controller users then pile up in uninterruptible sleep. The machine can show a very high load average even when little CPU work is happening.

The underlying failure is discussed in [kernel bug 221842](https://www.mail-archive.com/acpi-bugzilla%40lists.sourceforge.net/msg52308.html) and an [October 23 Linux ACPI follow-up](https://lore-kernel.gnuweeb.org/linux-acpi/6adbd0f1.963fedab.b2414.0d2b@mx.google.com/). The latter explicitly proposes evaluating `GBAP` before taking `ECMX` in these handlers.

## What this tool changes

It moves the relevant `GBAP` call before `Acquire (ECMX, 0xFFFF)` in `_Q08` and `_Q09`. It retains `GBAP`'s serialization. `_Q08` uses an otherwise unused local variable for the result.

Each person builds from **their own DSDT**. There are no downloaded replacement firmware tables and no model-specific byte offsets in the tool. It locates the declarations, compiles only the two affected methods, updates the enclosing package lengths and table header, and verifies the result. The original methods must reproduce their input bytes exactly. The corrected table's complete disassembly must match the intended source change.

If any of those checks fails, it refuses the build. Unsupported layouts need review; there is no force option.

## Requirements

- Linux, Python 3.10 or newer, and ACPICA's `iasl` compiler/disassembler.
- `CONFIG_ACPI_TABLE_UPGRADE=y` in the kernel used for the test boot.
- A boot configuration that permits ACPI table overrides. Kernel lockdown can prevent them; this project does not disable Secure Boot or lockdown.
- Access to the boot menu and a normal boot entry that does not load the override.

Install the distribution's ACPICA tools, for example:

```bash
# Debian / Ubuntu
sudo apt install acpica-tools

# Arch Linux
sudo pacman -S acpica
```

## Build locally

Capture only the DSDT. Do not dump or publish the `MSDM` table, which can contain a Windows product key.

```bash
git clone https://github.com/1337isnot1337/hp-acpi-lock-order-fix.git
cd hp-acpi-lock-order-fix

mkdir -m 700 "$HOME/acpi-lock-order-input"
sudo cat /sys/firmware/acpi/tables/DSDT > "$HOME/acpi-lock-order-input/DSDT.aml"

python3 acpi_lock_order.py inspect \
  --input "$HOME/acpi-lock-order-input/DSDT.aml"

python3 acpi_lock_order.py build \
  --input "$HOME/acpi-lock-order-input/DSDT.aml" \
  --output "$HOME/acpi-lock-order-build"
```

Run the Python tool as your regular user. Only the table capture may need root. The output directory must not already exist. For a compiler outside `PATH`, put `--iasl /path/to/iasl` before the subcommand.

The private output directory contains:

| File | Purpose |
| --- | --- |
| `DSDT-fixed.aml` | Your corrected table |
| `acpi-lock-order.cpio` | Uncompressed early initrd containing that table |
| `changes.patch` | Exact source-level change for review |
| `manifest.json` | Input/output hashes, table identity, revision and validation results |

**These outputs contain your firmware data. Keep them local.** Nothing is uploaded, installed, or enabled by the tool.

## Test boot and rollback

Follow [the boot and recovery instructions](docs/BOOT-TEST.md). Add an opt-in test entry that loads the generated archive **before** the existing initramfs. Keep the normal entry and default intact.

After a successful test boot, capture the active DSDT to a new local file and verify it against your manifest:

```bash
sudo cat /sys/firmware/acpi/tables/DSDT > "$HOME/acpi-lock-order-input/active.aml"
python3 acpi_lock_order.py verify \
  --input "$HOME/acpi-lock-order-input/active.aml" \
  --manifest "$HOME/acpi-lock-order-build/manifest.json"
```

This verifies the active table's hash. It does not establish that battery reporting, thermal behavior, charging transitions or suspend/resume work correctly. Test those deliberately, then observe normal use over time. Restarting with the normal entry omits the override.

**Remove the override entry before any BIOS update.** A higher OEM revision in a local override can supersede a future vendor table. This project has no firmware-version gate that runs before the kernel loads an override.

## Compatibility and evidence

The tool recognizes one `_Q08` / `_Q09` / serialized `GBAP` group in the same namespace, with the specific direct lock/call sequences described above. It supports named enclosing scopes and devices, and resizes AML package lengths including width changes. It rejects conditional declaration containers, ambiguous groups, altered signatures, exhausted local variables and bytecode that does not round-trip exactly.

Successful recognition is not a hardware compatibility guarantee. The real-table build was validated offline on an HP Dragonfly 13.5 G4, BIOS V90 01.13.01. A separately built host-specific override using the same lock-order change booted on that machine; it uses a different scratch local from the public builder’s output. Other paths and table sizes are covered by generated fixtures; they are not reports of tests on other physical laptops.

See [the detailed audit](docs/AUDIT.md) for findings, fixes, test coverage and remaining limits. See [contributing](CONTRIBUTING.md) for useful reports without exposing private firmware data.

## Development

```bash
python3 -m unittest discover -s tests -v
```

Compiler tests require `iasl`; the interpreter test also requires `acpiexec`. They are included in the distribution packages above. Tests generate minimal ASL fixtures locally rather than bundling vendor firmware. The finite scheduling model is an abstraction of the relevant locks, not a machine-checked proof about the entire firmware or kernel.

Code is MIT licensed. Generated firmware tables retain their original ownership and are not distributed by this repository.
