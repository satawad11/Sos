# Firmware 0.8 verification

- Pending alert ID and delivered/accepted states survive reboot. Closed, false-alarm and cancelled results clear the record. A timeout retains the record for troubleshooting/recovery.
- Network reception and staff acceptance use different display messages.
- Main-task watchdog allows 120 seconds for blocking radio calls. Reset cause and available heap are reported once per boot on FPort 5 and saved as `device_diagnostic` audit events.
- Mains changes must remain stable for three seconds. Battery readings still require calibration against the physical board and a multimeter; the existing low-battery indicator remains enabled.
- Invalid CONFIG values are rejected. Applied version and values are printed on USB; subsequent normal uplinks report the applied configuration version.
- USB command `DIAG` prints reset cause, heap, battery, mains, join and configuration state and briefly exercises LEDs/buzzer. Existing three-short-press mode sends TEST rather than an emergency case.

Before patient deployment, bench-test power loss before delivery, after delivery, after acceptance, and after server closure. Confirm the original case remains identifiable, a closed case stays closed, configuration survives restart, and TEST does not create a real emergency. Test the two-minute watchdog, stuck button, actual battery under load and mains switchover on each supported hardware variant. A successful build is not evidence that these hardware tests passed.
