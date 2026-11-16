# Boot test and recovery

An ACPI override changes firmware code executed inside the kernel. The builder validates the transformation offline; physical-machine behavior still needs testing. Start with a separate menu entry, a known normal entry and saved work.

The [Linux kernel documents initrd table overrides](https://cdn.kernel.org/doc/html/latest/admin-guide/acpi/initrd_table_override.html). The generated uncompressed archive must precede the normal initramfs. Do not replace the normal initramfs or load a table built for another machine.

## Before the test

1. Confirm that you can select the normal entry at boot. Keep a recovery USB available if you normally rely on an unattended boot.
2. Record your model and BIOS version locally. Rebuild after a firmware change; do not reuse an older override.
3. Review `changes.patch` and `manifest.json`. Both query handlers should evaluate `GBAP` before acquiring `ECMX`; `GBAP` must remain serialized.
4. Confirm `CONFIG_ACPI_TABLE_UPGRADE=y` and that your security configuration allows overrides. Do not weaken security controls just to try this tool.
5. Add only a test entry. Do not change the default, kernel command line, root/encryption arguments or normal kernel/initramfs pairing.

## Limine

Copy the archive to the same boot filesystem as your existing initramfs. Clone the existing Linux entry under a new name and insert the override as its **first** `module_path`. Keep all existing paths and command-line arguments from your own configuration.

```text
/Linux (ACPI lock-order test)
    protocol: linux
    kernel_path: YOUR_EXISTING_KERNEL_PATH
    module_path: YOUR_OVERRIDE_ARCHIVE_PATH
    module_path: YOUR_EXISTING_INITRAMFS_PATH
    cmdline: YOUR_EXISTING_KERNEL_COMMAND_LINE
```

These are placeholders, not a configuration to paste unchanged. If the normal entry has multiple modules, keep all of them in their original order after the new archive. Limine 12.9.6 concatenates Linux modules in configuration order with four-byte alignment; see its [implementation](https://github.com/limine-bootloader/limine/blob/v12.9.6/common/protos/linux_x86.c#L472). Other bootloader versions need their own verification.

## GRUB and other loaders

Use a custom test entry based on your existing working Linux entry. The initrd list must put the generated archive first, followed by all existing early microcode and initramfs images in their original order. GRUB's [Linux boot instructions](https://www.gnu.org/software/grub/manual/grub/html_node/GNU_002fLinux.html) describe the `linux` and `initrd` commands. Configuration generation and paths vary by distribution; this project deliberately does not rewrite them automatically.

## After boot

First verify the active DSDT against the local manifest using the command in the README. If it does not match, stop: the override was not loaded as intended. Review the boot log for table-upgrade messages and ACPI errors.

Then check, in order:

1. Battery presence, charge percentage, charging/discharging state and AC adapter status.
2. Charger connect and disconnect while observing those values.
3. Normal charging start/end transitions.
4. One controlled suspend/resume cycle, then additional cycles if the first is clean.
5. Normal use long enough to cover the machine's previous recurrence interval.

Stop if readings are wrong, new ACPI errors appear, temperature/fan behavior changes or tasks become stuck. Do not use a series of timed battery reads to probe an already-deadlocked machine: uninterruptible readers can accumulate even after their timeout wrappers exit.

## Rollback

Restart and select the normal entry, which must not reference the generated archive. Once running normally, remove the test entry and its archive. Preserve other configuration changes; do not blindly restore an old whole-file backup over newer edits.

A firmware-level lock cycle already established in memory will not be unwound by removing the archive or restarting a battery application. Save work before any restart. If normal shutdown is blocked, recovery may require a forced restart.

Remove the test entry **before** a BIOS update, even if the override worked previously. The archive is tied to its source firmware table. The manifest is a local integrity record, not a signature or a boot-time firmware gate.
