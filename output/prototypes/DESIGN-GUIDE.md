# Fabric workflow exploration

These prototypes explore alternatives rather than commit the project to a new design. They use simulated specimens and local state. They never connect to the tracker database. The existing React/FastAPI/PostgreSQL application remains the implemented software prototype.

Open the design lab at `http://localhost:8090` while its preview server is running. The scanner and correction previews have a named design picker below the active example. The arrangement preview lets you change devices, failure conditions, and actions.

## What can change

The number of apps, platform, device, screen structure, action sequence, direction selection, default scope, correction permissions, review process, and deployment can all be reconsidered. A scanner-only appliance, a Pi kiosk, a staff printing utility, and a supervisor portal can share one authoritative service without sharing one user interface.

Separate interfaces do not automatically require separate databases or separate backend services. Clients can have different shells and permissions while using the same registration identities, sessions, movement requests, and audit history. A native printing client should ask the identity service for registered labels and use its local printer driver; it should not invent specimen IDs or distribute database credentials.

## What still needs to be true

- A label resolves to a specific registered specimen or group; reprinting does not create a new identity.
- The system identifies the actual specimens moved, including partial withdrawals.
- Entry and removal form continuous conditioning periods; elapsed residence does not establish environmental compliance.
- A successful recording is distinguishable from a scan, a draft, a request for review, and an unknown transaction outcome.
- Corrections preserve the original observation and distinguish claimed effective time from the time the correction was recorded.
- A repeated or retried operation must not accidentally create another movement.
- Group operations and corrections need defined behavior when records change concurrently.

The document's latest unavailable-server behavior remains the reference: **NOT AVAILABLE**, without confirmed offline movements. An optional offline-draft arrangement in the exploration is a possible policy extension; its drafts are explicitly unconfirmed and would need reconciliation rules before implementation.

## Original targets to consider when choosing

The outline includes one scan with no typed entries for a routine stack movement, groups of up to 20, at least 99% first-scan accuracy, visible successful recording within one second of the scan trigger, at least 24 continuous conditioning hours, at least five years of audit history, initial cost below $5,000, and consumables at most $1 per stack.

Some explored workflows deliberately trade routine speed for review: scan-then-act, guided stages, individually collected baskets, and multi-stack staging add actions before the commit. One-second server response after pressing Confirm is a different measure from one-second recording after the first scan. That distinction needs an explicit decision if a review-heavy design is selected. First-read accuracy, physical label performance, retention operations, and actual cost cannot be established by these simulations.

## The important functional choices

| Choice | Alternatives | Consequence |
| --- | --- | --- |
| Direction | Persistent mode, one-use mode, fixed IN/OUT scanner, command label, physical switch, suggested next action | Changes routine action count and the chance of recording the wrong direction. Inferring the opposite direction makes accidental repeats particularly dangerous. |
| Commit point | Immediate scan, scan then action, review then confirm, collected basket, multi-stack tray | Changes speed, confirmation friction, and what the operator can inspect before a write. Staging needs clear pending states and cancellation. |
| Group definition | Any member represents the registered stack, numbered selection, individually scanned collection | Changes what labels staff need and how precisely partial movements can be specified. Physical regrouping is a separate identity problem. |
| Room identity | Installed station, destination token, handheld room selection | Changes how a room is established. Handhelds need protection against a stale or incorrectly selected room. |
| Operator identity | Station only, badge per action, badge per shift | Changes attribution and operator friction. Session/badge expiry and unattended devices need policies. |
| Correction target | Event, session, specimen, selected group, project, investigation case | Changes what staff navigate and what can be changed together. The saved correction still needs an exact impact list. |
| Editing model | Immediate save, staged cells, guided flow, before/after review, submitted request | Changes error detection and whether changes are committed or merely proposed. |
| Authority | Supervisor directly, operator requests a fix, independent second approval | Changes permissions, turnaround, and the records needed for requests, approvals, rejections, and self-approval prevention. |
| Failure handling | Local display, unsent draft, unknown submitted outcome, rejected stale preview | These are different states. A lost response is not proof that a write failed; an unsent draft is not a committed movement. |
| Printing | Browser PDF, manufacturer's software using CSV, native local utility, managed print station | Changes printer compatibility and workflow. All routes should reuse authoritative registered identities. |

## Starting combinations to try

These are discussion bundles, not a selected final architecture. The named picker gives the exact available design names.

1. **Touchscreen room station + direct supervisor editing.** Compare persistent mode with a mode that resets after each scan; pair it with a specimen timeline or before/after review. This is close to the existing prototype and exposes the direction-mode decision clearly.
2. **Dedicated IN/OUT scanners + desktop supervisor console.** A physical lane selects direction, so routine work needs little screen interaction. Compare that benefit against extra equipment, staff positioning, and how exceptions leave the room device.
3. **Partial-withdrawal station + reconciliation worksheet.** Compare a numbered specimen grid with an individual scan basket. Pair it with room reconciliation to see whether staff naturally think in specimen numbers, labels, or what's physically present.
4. **Operator capture + reviewed corrections.** Staff flag incidents from a handheld or kiosk; supervisors review before/after impact on a desktop. A request must remain visibly separate from a saved correction.
5. **Evidence-backed corrections + independent approval.** Explore the extra review cost when changing conditioning times has significant consequences. This requires workflow and permission features beyond the current backend.
6. **Staff printing utility + room kiosk + supervisor portal.** Three purpose-specific interfaces share identities and records. Printing can use a local driver while scans and corrections still use the central API.

## Common trials

Use these scenarios to compare designs, rather than judge only the first successful screen.

| Trial | What to observe |
| --- | --- |
| Bring a 20-specimen stack IN | Can staff tell the direction, room, group, and whether the database actually confirmed it? |
| Scan the same member again | Does it keep the original entry time, or could the design inadvertently record an OUT? |
| Remove specimens 2 and 5 | Does the visible selection match exactly what will move? What happens after completion? |
| Remove specimens before 24 hours | Does the warning identify the affected specimens and record actual removal after acknowledgement? A blocked screen cannot prevent physical removal. |
| Scan a mixed-location group | Does the screen require a deliberate subset, rather than guessing that all specimens move together? |
| Scan an unknown label | Is lookup failure distinct from success? Is intake or repair a separate authorized action? |
| Lose service before submission | Are movements disabled? Can the operator distinguish an available local screen from an available recording service? |
| Lose the response after submission | Is the outcome unknown until reconciled? Can retry reuse the submitted request instead of generating a second movement? |
| Change records while a basket is staged | Does a stale selection reject cleanly without moving only part of the intended group? |
| Scan OUT from the wrong room | Is the mismatch clear? Does the UI avoid quietly changing the recorded room? |
| Change 09:12 to a claimed 08:45 | Are original observation, effective time, recording time, reason, and actor all distinguishable? |
| Discover a missed IN | Does the new record openly identify a retrospective claim instead of appearing to be a contemporaneous scan? |
| Undo after another movement | Can staff inspect the impact, and is undo blocked when later changes make it unsafe? |
| Correct several specimens together | Is scope explicit, and is the update atomic when one record has changed? |
| Request a correction from a room station | Is submission merely a request? Who can approve it, and can the requester approve their own change? |
| Reprint a label | Do the same specimen identities survive the different printing workflow? |

## Reading the implementation implications

Each catalogue entry separates its benefit, risk, and backend implications. Many layouts can use the current endpoints with frontend changes. Some concepts introduce capabilities that are **not currently implemented**, such as multi-stack atomic transactions, badge authentication, correction requests, two-person approval, evidence attachment, command interpretation, or offline draft reconciliation.

A working interaction here demonstrates the experience. It does not establish hardware compatibility, production authorization, concurrency correctness, physical scan accuracy, or a five-year retention operation. Those require implementation and validation after choosing a direction.

## A useful review sequence

First shortlist two or three scanner concepts and two or three correction concepts. Run the same ordinary, partial, early, stale, and unavailable scenarios in each. Then choose a device arrangement and identify which policy changes that combination needs. The strongest final design may combine a scanner flow from one concept, a selection control from another, and a correction workflow from a third.

<!-- CATALOGUE -->

## Complete concept catalogue

### Scanner designs

#### Persistent mode

- **Device:** Landscape touchscreen + USB scanner
- **Functionality:** The current plan's persistent direction with whole-stack default; scope is explicit and resets to stack after a confirmed movement.
- **Benefit:** Fast when an operator processes a continuous run in one direction.
- **Risk:** The previous operator's direction may remain armed. A large persistent direction indicator is essential. Repeat scans remain no-ops rather than toggles.
- **Implementation:** None beyond the planned movement endpoint and same-request reconciliation.
- **Try:** IN Begin conditioning → Simulate a specimen scan → Simulate a specimen scan → OUT Remove specimens → Simulate a specimen scan → Acknowledge the early-exit warning and record OUT

#### Reset after scan

- **Device:** Landscape touchscreen + USB scanner
- **Functionality:** Requires a fresh direction choice before every subsequent movement, instead of retaining or automatically resetting to IN.
- **Benefit:** Reduces inherited direction mistakes between operators.
- **Risk:** Adds a touch per stack. Resetting to an unarmed state is deliberate; an automatic opposite direction or default-IN reset could make an accidental repeat a real movement.
- **Implementation:** Client workflow only. Existing movement/idempotency rules remain necessary.
- **Try:** IN Arm one movement → Simulate next specimen → Simulate next specimen → OUT Arm one movement → Simulate next specimen → Acknowledge the early-exit warning and record OUT

#### Scan then act

- **Device:** Landscape touchscreen or desktop + scanner
- **Functionality:** A scan performs lookup only. The direction action submits the movement after its identity is visible.
- **Benefit:** The operator sees the identified stack before choosing what should happen.
- **Risk:** One extra interaction per movement; a retained preview must be clearly tied to the next action.
- **Implementation:** Uses planned lookup followed by movement; no new server capability.
- **Try:** Simulate label lookup → Check IN Start a continuous session → Check IN Start a continuous session → Check OUT Close the current session → Acknowledge the early-exit warning and record OUT

#### Guided movement

- **Device:** Shared touchscreen for occasional users
- **Functionality:** Separates every decision into a sequential stage and supports numbered selection in the review stage.
- **Benefit:** Makes the order clear for students and infrequent operators.
- **Risk:** Slower for experienced operators; returning to an earlier stage must discard stale selections.
- **Implementation:** Client workflow using existing lookup, versions, and movement endpoint.
- **Try:** Into the room → Simulate a label scan → Confirm reviewed movement → Record another movement → Out of the room → Simulate a label scan → Confirm reviewed movement → Acknowledge the early-exit warning and record OUT

#### Dedicated lanes

- **Device:** Two scanners or two dedicated physical stations
- **Functionality:** Direction comes from scanner/station identity rather than a shared touch mode.
- **Benefit:** Removes the direction choice when physical traffic is separated.
- **Risk:** Two ordinary USB keyboard/HID scanners cannot inherently be distinguished by a browser. This requires separate stations, programmed prefixes, or a native capture service; labels must not be scanned in the wrong lane.
- **Implementation:** Dedicated stations already fit the endpoint. Distinguishing two scanners in one browser needs device routing or a native/prefix bridge.
- **Try:** Simulate entry scanner → Simulate entry scanner → Simulate exit scanner → Acknowledge the early-exit warning and record OUT

#### Scanner command cards

- **Device:** Scanner-only station with small status display
- **Functionality:** Moves mode, scope, selection, and early-exit acknowledgment into scannable commands, avoiding required touch input.
- **Benefit:** Works with gloves and scanner-only hardware.
- **Risk:** Command labels must be visually distinct and validated. The operator still needs a readable display for exact selection and incomplete-conditioning warnings.
- **Implementation:** Client command parser, reserved command namespace, and configuration for printed cards; movement API can remain unchanged.
- **Try:** IN MODE:IN → Simulate specimen label → Simulate specimen label → OUT MODE:OUT → Simulate specimen label → Acknowledge the early-exit warning and record OUT

#### Physical direction buttons

- **Device:** Pi + USB scanner + external button unit
- **Functionality:** Direction is armed by GPIO/HID hardware rather than an on-screen control.
- **Benefit:** Tactile operation with gloves and less touchscreen dependence.
- **Risk:** Requires a local hardware bridge, reliable button debouncing, and visible armed-direction feedback; disconnected hardware must never leave an invisible armed state.
- **Implementation:** Local GPIO/HID event bridge sends direction intent to the client; existing movement endpoint can be reused.
- **Try:** IN Physical entry button → Simulate scanner trigger → OUT Physical exit button → Simulate scanner trigger → Acknowledge the early-exit warning and record OUT

#### State-suggested action

- **Device:** Touchscreen or handheld
- **Functionality:** Derives a proposed direction from recorded state but never moves on scan alone. Mixed groups require an exact selection.
- **Benefit:** Avoids an inherited direction mode and makes current state visible.
- **Risk:** A repeat lookup after success proposes the opposite action. Explicit confirmation is mandatory; an automatic state toggle would be unsafe with repeated scans or stale location records.
- **Implementation:** Existing lookup and versioned movement endpoint; suggestion logic belongs in the client.
- **Try:** Simulate specimen lookup → Confirm proposed ${inCount===0?'IN':'OUT'} → Simulate specimen lookup → Confirm proposed ${inCount===0?'IN':'OUT'} → Acknowledge the early-exit warning and record OUT

#### Room inventory board

- **Device:** Desktop or larger shared touchscreen
- **Functionality:** Organizes actions around current location and explicit specimen selection rather than scanner direction mode.
- **Benefit:** Makes partial movements and mixed stacks easy to see.
- **Risk:** Twenty specimens fit; real rooms with many stacks need search/grouping. Selected specimens spanning different locations are rejected rather than silently filtered.
- **Implementation:** Current stack-detail and selection movement APIs are sufficient for this bounded design; a full room inventory view would need an efficient inventory query.
- **Try:** Scan a stack label → Bring selected IN → Select specimen 02 → Select specimen 05 → Remove selected OUT → Acknowledge the early-exit warning and record OUT

#### Numbered specimen grid

- **Device:** Landscape touchscreen for frequent partial checkout
- **Functionality:** Exact selection is the primary workflow instead of the exception to whole-stack scanning.
- **Benefit:** Fast and auditable when operators usually remove only a few specimens.
- **Risk:** Selection must not persist invisibly between unrelated stacks; all selected IDs and versions must be checked atomically.
- **Implementation:** Existing selected-specimen movement capability; preserve version checks and all-or-none updates.
- **Try:** Select specimen 02 → Select specimen 05 → IN → Record selected specimens → OUT → Record selected specimens → Acknowledge the early-exit warning and record OUT

#### Individual scan basket

- **Device:** USB scanner + compact touch or desktop screen
- **Functionality:** Several label scans define the exact movement scope without tapping specimen numbers.
- **Benefit:** The physical specimens in hand directly define the selection.
- **Risk:** An accidentally added specimen needs a visible removal control. v1 only permits specimens from one fixed stack; cross-stack baskets need a larger backend contract.
- **Implementation:** Existing selected-ID API for a single stack. Cross-stack basket batching would require a new atomic batch endpoint.
- **Try:** Simulate next individual label → Simulate next individual label → IN → Confirm basket movement → Simulate next individual label → OUT → Confirm basket movement → Acknowledge the early-exit warning and record OUT

#### Multi-stack staging tray

- **Device:** Desktop + scanner or larger touchscreen
- **Functionality:** Processes cotton's 20 specimens and nylon's 12 as one reviewed simulated batch.
- **Benefit:** Reduces repeated confirmation work for a cart of stacks.
- **Risk:** Atomicity across multiple stacks is a new requirement. A failure or mixed-location stack must reject the whole batch, and an uncertain response must reconcile one batch request.
- **Implementation:** New transactional multi-stack batch endpoint with shared request identity, per-stack versions, exact scope, and batch-level early-exit acknowledgment; the existing single-stack endpoint does not provide this behavior.
- **Try:** Scan cotton stack · 20 → Scan nylon stack · 12 → IN → Confirm all staged stacks → Scan cotton stack · 20 → Scan nylon stack · 12 → OUT → Confirm all staged stacks → Acknowledge the early-exit warning and record OUT

#### Destination token pairing

- **Device:** Portable scanner across room doors
- **Functionality:** The room/outside token supplies destination intent rather than a preconfigured persistent station direction.
- **Benefit:** Portable operation can make the destination explicit without navigating a room selector.
- **Risk:** Room tokens can be confused or copied; checkout still must match the room currently holding every selected specimen.
- **Implementation:** Token namespace and room/station mapping in the client; a truly roaming station needs authenticated destination authorization and station identity policy.
- **Try:** Scan shared-room token → Simulate specimen label → Confirm destination pairing → Scan outside-room token → Simulate specimen label → Confirm destination pairing → Acknowledge the early-exit warning and record OUT

#### Handheld camera

- **Device:** Phone or handheld touchscreen
- **Functionality:** Uses a camera capture/review cycle with thumb-reachable controls rather than USB HID typing.
- **Benefit:** Useful when the operator has no fixed workstation or dedicated scanner.
- **Risk:** Real camera access and QR decoding need secure-context/browser support, suitable lighting, and readable labels. This prototype does not request camera access.
- **Implementation:** Camera/QR client integration; existing lookup and movement endpoint can remain unchanged.
- **Try:** Capture simulated label → IN → Confirm movement → Capture simulated label → OUT → Confirm movement → Acknowledge the early-exit warning and record OUT

#### Minimal scanner console

- **Device:** Scanner-only station with compact display
- **Functionality:** Direction travels with each command, avoiding a retained mode; the display focuses on plain-text request and receipt state.
- **Benefit:** Compact and predictable for trained users or programmable scanners.
- **Risk:** Ordinary specimen labels do not contain a direction. A prefix-programmed scanner, command bridge, or operator command entry is required; error states must remain readable.
- **Implementation:** Local combined-command parser or device bridge; planned movement API receives parsed explicit direction and scope.
- **Try:** Simulate IN + label → Simulate IN + label → Simulate OUT + label → Acknowledge the early-exit warning and record OUT

#### Operator badge then scan

- **Device:** Shared touchscreen/scanner with badge reader
- **Functionality:** Routine movements are attributed to a person instead of only to the room station.
- **Benefit:** Adds operator accountability and can tailor permissions or operator defaults.
- **Risk:** Shared/lost badges and unattended unlocked stations weaken attribution. Badge presence alone is not strong authentication.
- **Implementation:** Routine operator authentication, badge registry, session expiry/lock rules, and named actor on movement events; the current plan only requires named supervisor corrections.
- **Try:** Simulate Avery Chen’s badge → IN → Simulate specimen scan → Simulate specimen scan → OUT → Simulate specimen scan → Acknowledge the early-exit warning and record OUT → Lock station

### Correction designs

#### Specimen timeline

- **Device:** Desktop or tablet
- **Functionality:** The session and original scan are visible together while editing one specimen.
- **Benefit:** Makes the difference between an observation and a correction easy to inspect.
- **Risk:** Slow when many specimens need the same correction.
- **Implementation:** Existing timestamp correction supports the concept; specimen-specific selection and timeline are presentation changes. The simulated stale-edit guard additionally needs correction-version preconditions; current correction requests do not carry an expected version.
- **Try:** Select specimen 02 or 03. → Enter a reason, then Save timestamp correction. → Set entry to11:00 to see future-time rejection, or exit08:30 to see chronology rejection. → Select Stale edit or Unavailable in the external demo harness and try saving.

#### Staged spreadsheet ledger

- **Device:** Desktop keyboard
- **Functionality:** Four per-row edits share a staged transaction with one reason.
- **Benefit:** Efficient for corrections affecting several records at once.
- **Risk:** Cell editing can obscure the impact unless staging is mandatory.
- **Implementation:** Policy/interface extension: existing API edits one session at a time; needs a transactional bulk correction endpoint and row versions. The simulated stale-edit guard additionally needs correction-version preconditions; current correction requests do not carry an expected version.
- **Try:** Change one proposed time and enter a reason. → Stage 4 cell edits; confirm the table says Staged with0 committed. → Commit 4 staged edits and inspect the immutable original 09:12 row. → Reset, stage again, select Stale edit, then commit to see0 specimens changed.

#### Guided correction wizard

- **Device:** Touchscreen or desktop
- **Functionality:** Progressive three-step flow separates entry, review, and recording.
- **Benefit:** Reduces accidental correction for infrequent supervisors.
- **Risk:** Adds steps to repetitive work.
- **Implementation:** Existing correction API; guidance and review stages can remain local UI state. The simulated stale-edit guard additionally needs correction-version preconditions; current correction requests do not carry an expected version.
- **Try:** Choose a correction type and enter a reason. → Review this correction to move from Identify to Review. → Confirm reviewed correction; observeRecord step and retained original scan. → Try Unavailable or Stale edit before final confirmation.

#### Before and after comparison

- **Device:** Desktop or wide tablet
- **Functionality:** A review-first diff makes the precise changed field the dominant view.
- **Benefit:** Useful when an approver needs to understand the change quickly.
- **Risk:** Less efficient for many small edits.
- **Implementation:** Existing correction API with current stack version included in the reviewed request. The simulated stale-edit guard additionally needs correction-version preconditions; current correction requests do not carry an expected version.
- **Try:** Compare observed 09:12 with claimed 08:45 and recording 10:05. → Enter a reason and apply the reviewed difference. → Reset and enter a future time to test rejection.

#### Exception triage inbox

- **Device:** Desktop
- **Functionality:** Work begins from reported exceptions rather than searching raw specimen history.
- **Benefit:** Directs supervisor attention to unresolved problems.
- **Risk:** Requires ownership rules so multiple reviewers do not duplicate work.
- **Implementation:** Concept extension: persisted exception cases, status, ownership, and resolution references; correction uses the existing core service. The simulated stale-edit guard additionally needs correction-version preconditions; current correction requests do not carry an expected version.
- **Try:** Select CT-105 missing entry or CT-106 room discrepancy. → Enter a resolution reason, then resolve the selected exception. → Observe only the selected ticket’s status change and retained scan history. → Try Stale edit or Unavailable and confirm no case resolves.

#### Undo impact preview

- **Device:** Desktop or tablet
- **Functionality:** Eligibility and consequences are shown before the reversal.
- **Benefit:** Makes an all-or-none undo understandable.
- **Risk:** An undo can be blocked by one later event and requires a different correction path.
- **Implementation:** Existing guarded undo; preview endpoint or read-only impact calculation would improve the UI. The simulated stale-edit guard additionally needs correction-version preconditions; current correction requests do not carry an expected version.
- **Try:** Enter a reason and Preview undo impact. → Confirm undo of 20 sessions; inspect retained original IN event. → Reset and select Intervening movement in the harness. → Try preview again: specimen 03 blocks the entire undo.

#### Missed entry reconstruction

- **Device:** Desktop or tablet
- **Functionality:** Three parallel accounts distinguish claimed time, existing observations, and recording time.
- **Benefit:** Avoids turning an operator account into a fabricated scanner event.
- **Risk:** Claimed residence time needs a reliable documented basis.
- **Implementation:** Existing missed-session correction supports source and recorded_at; evidence references would be an extension. The simulated stale-edit guard additionally needs correction-version preconditions; current correction requests do not carry an expected version.
- **Try:** Enter a reason explaining the missed scan. → Build reconstructed session to inspect the claim/source separation. → Record reconstructed session; confirm source missed entry and recorded 10:05. → Enter exit earlier than entry to see chronology rejection.

#### Atomic group time adjustment

- **Device:** Touchscreen tablet or desktop
- **Functionality:** Selection and group impact are visible before a single all-or-none write.
- **Benefit:** Handles stack-wide transcription errors with consistent reasoning.
- **Risk:** An overly broad selection can alter specimens with different physical histories.
- **Implementation:** Concept extension: transactional bulk-session correction with expected versions for every selected specimen. The simulated stale-edit guard additionally needs correction-version preconditions; current correction requests do not carry an expected version.
- **Try:** Clear selection, then select specimens01–05, or choose Select all. → Enter a shared reason and preview the selected adjustments. → Commit selected time adjustments and inspect original 09:12 history. → Stage a group, select Stale edit, then commit to see entire batch rejection.

#### Two person approval queue

- **Device:** Desktop
- **Functionality:** Requires two distinct approvals and records each review separately.
- **Benefit:** Adds deliberate scrutiny to consequential corrections.
- **Risk:** Slower resolution and reviewer availability dependencies.
- **Implementation:** Explicit policy concept: needs approval records, distinct reviewer enforcement, pending statuses, and atomic final approval; current backend has a single supervisor correction. The simulated stale-edit guard additionally needs correction-version preconditions; current correction requests do not carry an expected version.
- **Try:** Enter a rationale and approve as Eva Morales. → Inspect pending state: effective entry still 09:12. → Approve independently as Noah Chen; confirm correction recorded 10:11 with both reviews retained. → Use Stale edit before final approval to see0 changes.

#### Operator request and supervisor review

- **Device:** Operator tablet plus supervisor desktop
- **Functionality:** Request recorded 10:05is a different event from approval/correction recorded 10:11.
- **Benefit:** Operators can capture context without permission to alter sessions.
- **Risk:** Requests need routing and a clear pending state.
- **Implementation:** Concept extension: request entities, operator capture authorization, reviewer actions, and request-to-correction links.
- **Try:** Enter an operator account and Submit correction request. → Inspect the request lifecycle and unchanged original 09:12effective time. → Approve request as supervisor to record the separate 10:11 correction. → Select Unavailable before either action and verify no new event records.

#### Room reconciliation worksheet

- **Device:** Tablet carried between rooms
- **Functionality:** The starting point is a shelf check across three rooms, not a timestamp form.
- **Benefit:** Finds mistakes that ordinary scan history will not reveal.
- **Risk:** Requires precise specimen identification during the physical check.
- **Implementation:** Concept extension: reconciliation-check records and verified findings; room/session correction can use existing core correction behavior. The simulated stale-edit guard additionally needs correction-version preconditions; current correction requests do not carry an expected version.
- **Try:** Check the physical verification for specimen 19. → Choose its confirmed room, enter a reason, and review the mismatch. → Record verified room correction; inspect original Shared 09:12 history. → Try without physical verification to see rejection.

#### Project case workbench

- **Device:** Desktop
- **Functionality:** Case context remains available as a correction is recorded.
- **Benefit:** Supports complex discrepancies spanning several people or observations.
- **Risk:** A case workspace can be excessive for simple one-time errors.
- **Implementation:** Concept extension: case records, linked notes, and correction associations; core correction API remains reusable. The simulated stale-edit guard additionally needs correction-version preconditions; current correction requests do not carry an expected version.
- **Try:** Switch between Chronology, Operator notes, and Case actions. → Enter a rationale and Record correction in this case. → Inspect Case actions and immutable audit to see the new event.

#### Handheld incident capture

- **Device:** Handheld touch device or scanner-equipped phone
- **Functionality:** A narrow touch flow captures a request and never applies a correction.
- **Benefit:** Preserves details where the problem is noticed.
- **Risk:** Captured requests can remain unresolved without a review queue.
- **Implementation:** Concept extension: persisted incident/request capture; no supervisor correction permission on this device.
- **Try:** Choose Wrong room or Missed IN scan. → Keep demo specimen UL1:P:0007-03 or enter another recognized numbered code. → Enter a short account and Capture incident for review. → Confirm original effective entry stays 09:12 and request is recorded 10:05.

#### Keyboard command palette

- **Device:** Desktop keyboard; scanner-compatible identifier input
- **Functionality:** Intent is expressed through a keyboard grammar with explicit preview and execution.
- **Benefit:** Fast for experienced operators and accessibility-friendly keyboard work.
- **Risk:** Command interpretation must remain unambiguous and previewed.
- **Implementation:** Concept extension: strict client/server command parser translating to existing correction/undo APIs, with expected versions. The simulated stale-edit guard additionally needs correction-version preconditions; current correction requests do not carry an expected version.
- **Try:** Use a sample command or type “correct #03 entered 08:45”. → Enter a reason and Dry-run this command; Ctrl + Enter also triggers review. → Execute reviewed command and inspect retained original log. → Choose Undo last movement with Intervening movement to see atomic block. → Enter “correct #03 entered 11:00” to test future-time rejection.

#### Read only kiosk handoff

- **Device:** Room touchscreen, USB scanner, or scanner-only request capture
- **Functionality:** The kiosk produces a review reference while leaving all effective session records read-only.
- **Benefit:** Keeps correction requests available where the scanner already lives.
- **Risk:** Physical or digital tickets need a dependable handoff process.
- **Implementation:** Concept extension: request capture and ticket/reference generation; no kiosk correction permission.
- **Try:** Choose a reason category; scan or type UL1:P:0007-03. → Create supervisor handoff ticket, or press Enter in the scan input after selecting a reason. → Scanner-only trial: reset, scan UL1:P:0007-03 and Enter, scan WHY:LATE and Enter, then scan SEND and Enter. → Inspect ticket CT-104 and distinct observed09:12,claimed08:45,request-recorded 10:05times. → Select Unavailable and ensure a ticket is not issued.

#### Evidence backed correction case

- **Device:** Desktop or tablet
- **Functionality:** Evidence selection is part of the reviewed correction flow rather than an optional note.
- **Benefit:** Makes the reasoning and supporting material traceable.
- **Risk:** Evidence collection adds work and needs retention policies if implemented.
- **Implementation:** Concept extension: evidence metadata, references or attachments, authorization, and correction links; existing correction API stores the reason. The simulated stale-edit guard additionally needs correction-version preconditions; current correction requests do not carry an expected version.
- **Try:** Select supporting evidence and optionally attach the simulated operator note. → Enter an interpretation and Link evidence and review. → Record supported correction; inspect linked evidence names in the immutable audit addition. → Clear all evidence to see validation rejection.

### Tool arrangements

#### Unified web workspace

- **Device:** Shared desktop or touchscreen browser, Staff label printer
- **Functionality:** One application and one shared workstation expose every role's screens.
- **Benefit:** One installation and one navigation model.
- **Risk:** The shared workstation can interrupt every task; a busy room screen may expose too many controls.
- **Implementation:** No required domain change. Role authorization is enforced by the existing service.
- **Try:** Fail the shared client and preview each task → Print registered labels → Compare this arrangement with role-specific apps

#### Three role-specific web apps

- **Device:** Staff prep browser, Room touchscreen browser, Supervisor desktop browser, Staff label printer
- **Functionality:** Prep/printing, scanning and audit work have separate applications and release cycles.
- **Benefit:** Each role gets a focused surface without duplicating records.
- **Risk:** Three deployments need consistent API contracts and permissions.
- **Implementation:** No separate database required. Keep compatible authenticated API contracts.
- **Try:** Fail only the room app → Preview supervisor correction while scanning is unavailable → Disconnect the central service link

#### Local Pi kiosk + staff portal

- **Device:** Pi touchscreen with locally installed interface, Staff desktop portal, USB scanner, Staff label printer
- **Functionality:** The room screen can cold-start locally while staff work remains in a separate web portal.
- **Benefit:** A network failure still leaves a usable local unavailable screen.
- **Risk:** A local screen is not a functioning database connection; installed assets must be updated.
- **Implementation:** None for online use. Optional offline capture needs reconciliation and asserted-device-time provenance.
- **Try:** Disconnect the central link and inspect local-screen status → Preview an unavailable scan → Enable the optional capture extension and compare its unconfirmed outcome

#### Headless scanners + supervisor console

- **Device:** Fixed scanner appliance with IN/OUT controls and status indicator, Staff prep desktop, Supervisor console, Staff label printer
- **Functionality:** Routine scanning has no browser or touchscreen; exception editing happens elsewhere.
- **Benefit:** Few controls at the door; a failed scanner does not disable supervisor work.
- **Risk:** Partial selection and detailed errors need clear command-card/status conventions or a nearby console.
- **Implementation:** Existing movement API plus device authentication; no database credentials on appliances.
- **Try:** Fail the scanner appliance → Preview a correction from the supervisor console → Disconnect the API and check that the indicator cannot claim success

#### Native print utility + web tools

- **Device:** Native staff prep/print utility, Room web scanner, Staff audit browser, Printer driver on staff PC
- **Functionality:** Printing integrates with the staff PC's driver while scanning and audit remain web applications.
- **Benefit:** Better local printer integration and reprinting of already registered files.
- **Risk:** Native packaging and updates become another maintenance task; cached files must preserve identity/version.
- **Implementation:** Use existing prep/label endpoints. Native clients call the API rather than receiving database credentials.
- **Try:** Disconnect the central link and reprint a saved registered file → Compare cached reprinting with blocked new movement → Fail only the room scanner

#### Managed handheld + desktop review

- **Device:** Installed handheld scanner app, Staff prep/review desktop, Camera or built-in barcode reader, Staff label printer
- **Functionality:** The scanner moves with the worker; printing and audit editing stay on a desktop.
- **Benefit:** Selection and scanning can happen beside the rack.
- **Risk:** Device charging, room binding and managed updates need explicit procedures.
- **Implementation:** Existing API plus handheld/device authentication. Queueing is a separate optional extension.
- **Try:** Fail the handheld while desktop review remains available → Preview a normal scan → Compare optional unconfirmed capture during a link failure

#### Operator requests + approval portal

- **Device:** Operator room app, Staff prep app, Separate supervisor approval portal, Staff label printer
- **Functionality:** Operators can describe mistakes immediately, but a separate approval step applies corrections.
- **Benefit:** The exception and its approval are attributable without exposing direct editing to operators.
- **Risk:** Pending corrections need ownership and may delay usable audit records.
- **Implementation:** Add durable correction requests and approval/rejection workflow; canonical session updates occur only on approval.
- **Try:** Submit a correction request and inspect the unchanged record → Approve the pending request → Fail the room app while the approval portal remains available

#### Site server + central reporting

- **Device:** Site staff workspace, Locally installed room kiosk, Site authoritative server/database, Central read-only reporting portal, Staff label printer
- **Functionality:** A WAN outage can leave site-local recording operational while central reporting becomes historical.
- **Benefit:** Room operations depend on the site network rather than the external reporting link.
- **Risk:** The site server requires operational ownership; central copies must expose freshness and avoid dual writers.
- **Implementation:** Site-host the canonical service and add read-only reporting delivery with globally unique identities and provenance.
- **Try:** Disconnect the site-to-central link and preview a confirmed local scan → Open the historical central report → Fail the authoritative site database and compare the resulting write failure
