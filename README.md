# Fabric Conditioning Tracker

Tracks every fabric specimen through the lab's 24-hour conditioning rooms. Each physical piece gets a permanent number and a Data Matrix label. Room scanners record when it goes IN and comes OUT.

- **MacBook Neo (host):** PostgreSQL database, FastAPI server, staff web app, and label printing. Runs on port **8120**.
- **Three Raspberry Pi 5 room stations:** a full-screen scanner page (PPE, Shared, Footwear) with an Eyoyo USB scanner. They talk to the Mac over the lab network.
- **Brother QL-810W printer:** connected to the Mac only, printing 62 × 29 mm DK-1209 labels.

## Documentation

All documentation is in **[docs/](docs/README.md)**. Start with:

- **[Quickstart](docs/QUICKSTART.md):** step-by-step setup of the Mac, each Pi and the printer, then everyday use.
- **[Printer setup and labels](docs/PRINTER_AND_LABELS.md):** USB or Wi-Fi, AirPrint, what to plug in, and which label rolls work.
- **[Staff guide](docs/STAFF_GUIDE.md):** importing prep sheets, printing, scanning, corrections.

## Repository layout

| Folder | Contents |
| --- | --- |
| `backend/` | FastAPI app (`app/`), database migrations, PostgreSQL integration tests |
| `frontend/` | React staff app and room scanner (`src/`), Playwright browser tests |
| `deploy/macos/` | Mac host control (`host.py`), app builder, LaunchDaemon installer |
| `deploy/scanner-kiosk/` | Pi station installer, USB/NFC input service, watchdog, systemd units |
| `scripts/` | Pi deployment, station package build, test and verification scripts |
| `docs/` | All documentation, test results and raw test data |
| `samples/` | Fake prep sheets for trying the importer |
| `presentation/` | Course presentation material |
| `output/` | Generated locally (app bundle, Pi package, reports); not in Git |
