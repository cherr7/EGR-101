# UI text review

A deliberately harsh pass over every piece of text the staff app and room scanner show. The test for each line: **does someone at the bench need this to do the next thing?** If not, it goes. Reviewed 2026-11-12; all changes listed here are applied.

## What was wrong

1. **Implementation details shown to staff.** "Refreshes every 15 s", "refreshes every 10 seconds", "heartbeat every 5 seconds (offline after 15 s)", "The FastAPI server and PostgreSQL", "station-agent.py", "SHA-256", "checksum manifest", "America/New_York". Staff can't act on any of it. The numbers also drift from the code.
2. **Saying the same thing two or three times.** The New batch page named the four steps in the header, again in the stepper, and again in each card's subtitle. The upload card listed the file types three times. Restore had the "replaces the current database" warning twice.
3. **Reassurance nobody asked for.** "Macros are never run", "The app always keeps its local backup", "A copied file does not prove it has reached the cloud", "Original observations are retained", "Claimed movement time and actual recording time are stored separately". These are true. They're also design notes, not instructions.
4. **Audit-trail jargon in place of plain words.** "Observation", "claimed at", "deliberate reprint", "authoritative", "eligible", "residence time", "active dataset", "validated archive", "candidates", "pipeline gaps".
5. **Success messages that lecture.** "Registered 3 stacks. Their specimen numbers are now permanent." "Change saved. The original observation remains in the transaction history." Just say it worked.
6. **Raw data dumps.** The workbook "Source" popup printed every internal metadata key with its underscores replaced (`template row: false`, `piece groups: …`, `import kind: ppe_prep`).
7. **Wrong or stale text.** One health issue pointed to "the Stacks page", which no longer exists. The backup help said the kept-forever weekly backup is "each Monday's 2 a.m. backup", which is wrong if the hour is changed. One import warning appeared on every workbook ("Review physical material groups…"), so it carried no information.
8. **Duplicate controls.** "＋ New batch" appeared twice on the dashboard.

## Rules used

- Labels and buttons: say what happens ("Take out anyway", not "Override & record OUT").
- Subtitles only when they change what someone does. Most cards don't need one.
- Errors: what happened + what to do. No internal state ("the same saved request").
- Times are "Eastern" where a time zone matters at all.
- Words: **piece** in the bench workflow, **specimen** where the record is meant; **scan**, not observation/movement claim; **backup**, not checkpoint/archive/snapshot.
- Don't explain the safety design. It's in the docs ([STAFF_GUIDE.md](STAFF_GUIDE.md), [STATUS.md](STATUS.md)).

## Changes by screen

| Screen | Before | After |
| --- | --- | --- |
| Sidebar | "Database connected" | "Online" |
| Sidebar | "This host (open on other devices)" | "Open on other computers" |
| Offline banner | "NOT AVAILABLE The database cannot be reached. Changes and printing are unavailable." | "**Offline** Can't reach the database. Nothing can be saved or printed until it's back." |
| Dashboard | "Updated 10:42 · refreshes every 15 s" | "Updated 10:42" |
| Dashboard | Needs attention: "Registered pieces that haven't made it into a room yet. Pieces never scanned in have no room, so the room filter doesn't hide them." | "Registered pieces not in a room yet." |
| Dashboard | "No gaps ✓ / Every registered piece is labeled and has been scanned into a room." | "Nothing waiting." |
| Dashboard | "Possibly waiting on a bench. Scan them IN at the room station." | "Scan these IN at their room." |
| Dashboard | Batches subtitle "One batch per prep sheet. A batch closes when…" and a second "＋ New batch" | removed |
| Dashboard | "Pieces that have finished their 24 h. Longest-waiting first in each room." | "Finished 24 hours. Oldest first." |
| Dashboard | "Filter dashboard" | "Show only this batch" |
| New batch | Header "Prep sheet → confirm stacks → print labels → track in the rooms." | removed (the stepper says it) |
| New batch | Upload subtitle + ".xlsx, .xlsm, or .csv · macros are never run" | ".xlsx, .xlsm or .csv" |
| New batch | "Each row is one physical stack: one material, test, and treatment. Include every piece going into conditioning, extras too." | "One row per stack. Count every piece going into the room, spares included." |
| New batch | "Workbook review notes", "N workbook sheets inspected", "ppe prep · 12 candidates" | "Import notes", "Sheets (N)", "12 rows" / "not a prep sheet" |
| New batch | "All required values present", "Only rows needing attention" | "Ready to register", "Only rows with missing values" |
| New batch | "Register 3 stacks & continue →" | "Register 3 stacks" |
| New batch | "Registered 3 stacks. Their specimen numbers are now permanent." | "Registered 3 stacks." |
| New batch | Source popup: every metadata key | Named fields only (sheet, cells, sample type, comment, spares…), empty and internal values hidden |
| New batch | Print step subtitle | removed (the printer panel already explains the PDF route) |
| New batch | "Take the labeled stacks to their conditioning room and scan them IN at the door station." | "Scan the stacks IN at their room." |
| New batch | "New physical batch from this workbook" | "Register this sheet again as a new batch" |
| Print | "Print result unconfirmed. Check the printer before retrying." / "Check previous print job" / "I checked the queue; allow a deliberate reprint" | "No answer from the printer yet. Check whether labels came out before printing again." / "Check again" / "Nothing printed: allow reprint" |
| System health | "Live status of … Refreshes every 10 seconds." | removed |
| System health | "Checked on every refresh. Informational notes are listed last." | removed |
| System health | "The FastAPI server and PostgreSQL on this Mac." | removed |
| System health | "The kiosk page sends a heartbeat every 5 seconds (offline after 15 s). Each Pi reports its own health every 30 seconds." and the station-agent.py footnote | removed |
| System health | "Collecting — a point every 15 s", "OK (no under-voltage or throttling)", "Normal (no throttling)" | "Collecting…", "OK", "Normal" |
| System health issues | "Open the print history on the Stacks page" | "Check the Printing entries on Transactions." |
| System health issues | "No heartbeat since 2026-11-12T…", "Older station packages do not send heartbeats.", "Check fabric-scanner-agent.service on the Pi." | "Check the Pi's power and network.", "Check that the Pi is on.", "Reboot the Pi. If it keeps happening, reinstall it from the Mac." |
| Backups | "Latest successful checkpoint", "Daily schedule", "Additional copy: Destination not configured" | "Last backup", "Next backup", "Extra copy: Off" |
| Backups | "Database backup created. Download a copy or check the configured destination folder." | "Backup created." |
| Backups | "Backup settings saved. A destination folder is a local copy target; cloud synchronization is managed separately." | "Settings saved." |
| Backups | Two help paragraphs about OneDrive/SharePoint and "The app always keeps its local backup." | removed |
| Backups | "…except each Monday's 2 a.m. backup (Sunday night), which is kept forever." | "…except the first backup each Monday, which is kept forever." |
| Backups | Restore warning shown twice, "Validate backup archive", "Type RESTORE to replace the active dataset" | once, "Choose backup file", "Type RESTORE to confirm" |
| Backups | "Portable archives contain the database snapshot and a checksum manifest.", visible SHA-256 | removed (hash kept as a tooltip) |
| Specimens | "Times are displayed in America/New_York." | "Times are Eastern." |
| Specimens | "Selection is kept when filtering; only the checked specimen IDs will print." | removed |
| Specimens | "This specimen's observations & changes", "Movement claimed at X; recorded at Y.", "Permanent label ID", "Residence time" | "History", "Happened X; entered Y.", "Label code", "Time in room" |
| Transactions | "Change saved. The original observation remains…", "The entire transaction was undone. The original and undo both remain in history.", "Missed movement recorded. Claimed movement time…" | "Saved. The original stays in the history.", "Undone.", "Missed scan recorded." |
| Transactions | "Showing the latest 100 matching events. Original observations are retained." | "Latest 100." |
| Transactions | "Recorded time", "Actual movement time", "Affected numbered specimens" | "Entered", "Movement time", "Specimens" |
| Room scanner | "Response interrupted. This same request must be confirmed before another scan." | "Connection dropped. Checking whether the scan saved…" |
| Room scanner | "Undo was not applied: a specimen has intervening activity. Review the transaction log." | "Can't undo: a later scan includes these pieces." |
| Room scanner | Early checkout: "Override records their actual removal and incomplete conditioning." / "Override & record OUT" | "Taking them out now records them as not fully conditioned." / "Take out anyway" |
| Import warnings | "Review physical material groups and conditioning counts before registration. No scan times were imported." (on every import) | removed |
| Import warnings | "No importable preparation rows were found. Training matrices and result worksheets are reference documents, not specimen orders." | "No prep rows found in the selected sheets." |

## Left as is

- The three-step **Exit kiosk mode** confirmation. It's meant to be slow.
- Health issue titles ("Main disk is getting full", "PPE station is offline"). Short and actionable already.
- "“Submitted” means accepted by the Mac's print queue" under the printer table. It stops people treating Submitted as proof a label came out.
