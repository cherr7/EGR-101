# Documentation

## Set up and use

| Guide | Read it when |
| --- | --- |
| [QUICKSTART.md](QUICKSTART.md) | Setting up the Mac, a Pi or the printer for the first time, or after a restart. Short numbered steps. |
| [PRINTER_AND_LABELS.md](PRINTER_AND_LABELS.md) | Connecting the Brother QL-810W (USB, Wi-Fi, AirPrint), choosing label rolls, label layout, print status. |
| [STAFF_GUIDE.md](STAFF_GUIDE.md) | Day-to-day work: import a prep sheet, register, print, scan IN/OUT, undo, correct, back up. |

## Reference

| Document | Contents |
| --- | --- |
| [MAC_HOST.md](MAC_HOST.md) | The Mac host in detail: data locations, start before login (LaunchDaemon), FileVault, sleep, network. |
| [PI_STATION.md](PI_STATION.md) | The Pi station in detail: architecture, installer, scanner pinning, NFC, startup chain, watchdog, maintenance. |
| [SCANNER_INPUT_PROTOCOL.md](SCANNER_INPUT_PROTOCOL.md) | The Pi's local scanner input service: endpoints, ownership, timestamps, Eyoyo key sequence. |
| [EXCEL_IMPORT.md](EXCEL_IMPORT.md) | How prep-sheet workbooks map to stacks, how column B and spares work, and the real-workbook verification. |
| [DATABASE.md](DATABASE.md) | The schema: tables, sequential keys, retry IDs, what is append-only, what backups keep. |
| [PRINTING_REVIEW.md](PRINTING_REVIEW.md) | Where label printing can go wrong (lost responses, duplicates, wrong roll, offline printer) and what guards each case. |
| [DEVICE_DISCOVERY.md](DEVICE_DISCOVERY.md) | How the Pis find the Mac by name (Tailscale), staff access through Cloudflare Tunnel + Access, the guest Wi-Fi test, the UL IT email draft. |
| [BACKUPS.md](BACKUPS.md) | Checkpoints, daily snapshots, extra copy destination, validate and restore. |
| [DEVELOPMENT.md](DEVELOPMENT.md) | Building, running the tests, screenshots, generated `output/` folders. |
| [SYSTEM_HEALTH.md](SYSTEM_HEALTH.md) | What the System health page checks and its warning rules. |
| [UPDATE_SYSTEM_PLAN.md](UPDATE_SYSTEM_PLAN.md) | Roadmap: one Mac app in /Applications that starts at boot, manages the Pis over Tailscale, updates everything with backup and rollback, and offers outside access with a passcode. |
| [AFM_EVALUATION.md](AFM_EVALUATION.md) | Apple's on-device AI model tested on the prep sheets and comments; not used, and why. |
| [UI_TEXT_REVIEW.md](UI_TEXT_REVIEW.md) | The review of on-screen wording and what was cut. |
| [FRIENDLY_URL_PLAN.md](FRIENDLY_URL_PLAN.md) | Earlier plan for a name like `http://conditioning/`; replaced by DEVICE_DISCOVERY.md, except the QR-code ideas. |
| [STATUS.md](STATUS.md) | What is done, what still needs physical testing, known gaps, audit history. |
| [TEST_RESULTS.md](TEST_RESULTS.md) | Results of the five Testing Plan tests; raw data is in [testing-data/](testing-data/). |
| [reference/Project Outline.pdf](reference/Project%20Outline.pdf) | The original project outline. |
