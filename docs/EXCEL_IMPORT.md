# Excel prep-sheet import

How prep-sheet workbooks become stacks and labels, and the verification run against the real workbooks.

The working pipeline has been exercised against all four supplied Excel workbooks, using the real import/review/registration APIs and PostgreSQL. Every worksheet was inspected. The supplied PDF email and Word results template were checked as reference inputs and rejected as Excel orders.

This verification uses only a separate `fabric_pipeline_test` database. It does not call the running app on port 8120, alter its database, submit printer jobs, or modify the six source files. Generated label PDFs stay in memory. A private aggregate report is written under `output/qa/`, which is excluded from Git.

## Verified results

| Source workbook | Worksheets inspected | Prep candidates | Aggregate board candidates | Reference worksheets |
| --- | ---: | ---: | ---: | ---: |
| FPA 2112 - Dec2025-FilterMethod.xlsm | 3 | 30 | 0 | 2 |
| NFPA 1970 (71) (Garments,Aluminized,MB,Hoods) - May2025.xlsm | 6 | 82 | 0 | 2 |
| Main PPE Prep Board.xlsm | 9 | 0 | 730 | 7 |
| PPE Test Training Matrix - XXX.xlsx | 10 | 0 | 0 | 10 |
| Total | 28 | 112 | 730 | 21 |

Candidates are review rows, not an assertion that those many physical stacks currently exist. The two board worksheets can describe combined work and produce separate AR/laundered review candidates. Their aggregate quantities are deliberately not assigned as physical specimen counts.

The verification completed these checks:

- Compared all 112 prep conditioning counts and treatment strings with their original worksheet cells, including named subdivisions and panel dimensions.
- Compared 842 candidates' LabWare and Oracle/project references with the source cells. Both references are retained; Oracle/project is the primary project field when present, otherwise LabWare is used.
- Confirmed all seven nonempty imported drafts blocked registration before required review fields were completed. The training matrix and other reference worksheets created no specimen candidates.
- Filled missing project/material fields with explicit TEST values in the isolated database. For the board, one example from each of its two work worksheets received an explicitly synthetic count of one; this does not interpret the original aggregate quantity.
- Registered every prep test/treatment candidate, the two reviewed board examples, and five deliberate repeat-batch examples: 119 DEMO-marked test stacks containing 709 test specimens.
- Generated 709 PDF label pages and decoded every embedded Data Matrix image back to the exact registered specimen ID. Generated and decoded another 709 pages for reprinting; all IDs remained unchanged.
- Verified each decoded label contains only its specimen's label code, and checked first/last member lookup against the API's registered stack membership.

These counts come from a run before the switch to sequential label codes; rerun `scripts/verify-real-workbooks.py` to reproduce them with the current labels.
- Repeated file imports 35 times and commits 12 times without duplicating the associated registration. Five explicitly new batches with identical prep values received distinct specimen IDs.
- Confirmed no conditioning sessions, printer jobs, or unmarked production stacks were created by the verification.
- Compared SHA-256 hashes before and after reading all six source files; their contents remained unchanged.

The 709 specimens are verification data, not a proposed inventory for the real projects.

## Filled-in job prep sheets

Nine filled-in job prep sheets (2112, 1506, 1850 Seams/Water Pen and 1970 families) were then run through the same script with `--job-sheets`. Together with the four files above: 64 worksheets, 491 prep rows compared with their source cells, 516 registered verification stacks, and 2,697 label pages decoded twice with unchanged IDs.

Those sheets exposed layouts the blank templates did not have. The importer now handles them:

| Layout in a filled job sheet | Importer behavior |
| --- | --- |
| A "Note to Lab" row between the job details and the Test header | Job details are read from the row under the `LabWareDx`/`Oracle` labels, never from the note. |
| 1850 Seams template with a blank "# of samples" heading | Recognized by its Test, Conditions and conditioning columns. |
| The ordered work is on a later worksheet (e.g. Moisture Barriers) | Every worksheet with ordered tests is selected by default; a blank template still selects its first worksheet. |
| Tests with a blank "# of samples" (tested total 0) | Left out of a sheet that orders other tests, with a sheet note. They are kept for blank templates. |
| `Formula Map 2025` and other versioned reference sheets | Treated as reference sheets. |
| An instruction merged across Conditions and count, e.g. "cut 7 yards… for AITEX" | Not a specimen row. |
| Comments such as `Sample A-C`, `Samples OS1-OS2, TB1-TB2`, `Sample 3-Sample 20`, `A-V` | Expanded into one material reference per sample when the number of IDs equals "# of samples". Otherwise the IDs are listed in a review warning (e.g. `I, J, K, L` for 2 samples). Bare numbers become `Sample 3`. |
| "Shares samples with … Additional Prep not needed." | Warning plus `shares_samples`, so the same pieces are not labelled twice. |
| A treatment that is only a number (e.g. `1`) | Warning to confirm the condition. |
| "Sample Released" (K3) | Kept as `sample_released` source metadata. |

A registered job can exceed 100 stacks (the 1970 example registers 122), so a label preview or print job now accepts up to 1,000 stacks. The 5,000-label cap is unchanged.

## How the supplied prep sheets map

The FPA/NFPA templates use these preparation fields:

| Source field | Application behavior |
| --- | --- |
| Test, column A | Copied to the test field, including merged-cell/continued-treatment rows. |
| Conditions, column C | Copied as the exact treatment text; no treatment sequence is inferred. |
| Specimens per sample for Conditioning, column D | Supplies the physical pieces per reviewed material group. The tested-total column B does not replace it. |
| Number of samples, column E | Produces separate review rows when it clearly specifies multiple samples; the actual material identifiers still require review. |
| Hoods Sample Number, column E | Retained as the material/sample reference when explicitly supplied. |
| Sample Type, column F | Retained as context, rather than invented as a unique material identifier. |
| LabWare, Oracle/project, standard, NW/AR context | Stored separately with source filename, worksheet, and cell provenance. NW/AR project regime is not substituted for the treatment. |

For example, `6 Warp, 6 Fill` produces 12 conditioning pieces and retains the subdivision information. `4 Contact, 4 Spaced` produces eight. Source descriptors `1 24x18` and `6 15x15` now produce one and six panels respectively: the dimensions describe each piece and are never multiplied into its count. Their original descriptors remain in the source metadata.

This verification found that panel-size notation needed that targeted parser improvement. Ambiguous inputs such as `3 or 6`, `24 hours`, an uncached formula, or an aggregate board quantity still require an explicit reviewed count.

The conditioning-count column is intentionally used so additional pieces specified for conditioning are not dropped merely because a different number will eventually be tested. The existing synthetic regression also checks a tested total of ten against a conditioning total of twelve.

Nine filled-in job prep sheets (2112, 1506, 1850 and 1970 families) confirm this reading. Across 42 active test blocks, column B is the number *tested*, computed from D and E with a spare allowance:

| Column B formula shape | Meaning |
| --- | --- |
| `D*E`, `E*(D+D_next)` | No spares; every conditioned piece is tested. The second D is the next treatment row of the same test block (for example AR and 5L). |
| `(D-1)*E`, `E*(D-1)` (operand order varies; it is always D-1, never E-1) | One spare piece per sample. |
| `(D-2)*E` | Two spares per sample (ASTM 1506 colorfastness: 7 cut, 5 tested). |
| `IF($H$3="NW",10*E,4*E)` and similar constants | Standard-mandated tested count, independent of D (for example flame NW: 6 warp + 6 fill cut, 10 tested). |
| `0` | Column E is blank, so the test is not ordered in this job. It is a template row, not an unmultiplied count. |

Spares are never written as a number in these sheets. When B is a formula, the importer records `test_conditioned_pieces`, `test_tested_total` and `implied_spare_pieces` on each row for review. B was never larger than conditioned pieces (sum of D for the block × E), with one expected exception: Trap Tear D lists panels (`1 24x18`, `3 15x15`) that are cut into several test specimens after conditioning. Because B spans every treatment row in the block and subtracts spares, it is never a per-stack count. Conditioned pieces per stack are therefore D for that treatment row, once per sample in E.

## What still requires review

All 112 prep candidates in the supplied templates lack the project/material identifiers needed to issue real labels. The verification supplies TEST placeholders only after proving that registration is blocked without them. It does not infer customer identifiers from sample types or create plausible replacements.

The prep board supplies actual project context, but all 730 candidates lack a physical material identifier and a trustworthy per-stack conditioning count; three also lack a usable treatment. Existing board times are not converted into historical ENTRY/EXIT observations. Only one explicitly synthetic review example per board worksheet is registered during verification.

Staff therefore still choose the relevant worksheets/tests, remove unneeded template rows, enter the actual material references, and resolve physical counts. That is the editable review step already implemented in the app. A completed preparation order can proceed directly once those fields are present and correct.

The training matrix is a qualification reference, not an intake list. The email PDF and Word datasheet remain background examples. Word completion, email ingestion, network-drive monitoring, and training/results management are not part of this verification or the current intake implementation.

## Repeat the verification

The native local PostgreSQL host must already be running, and the project's Python dependencies must be installed. The script can use the existing native host configuration without printing credentials. It does not start services.

    .venv/bin/python scripts/verify-real-workbooks.py --run

By default it reads the six named files from the current user's Downloads directory. Another directory and aggregate-report location can be supplied. Filled-in job prep sheets carry real job numbers in their filenames, so they are passed as a private folder instead of being listed in this repository:

    .venv/bin/python scripts/verify-real-workbooks.py --run --downloads /path/to/private/source-files --report /path/to/private/summary.json
    .venv/bin/python scripts/verify-real-workbooks.py --run --job-sheets /path/to/private/job-prep-sheets

If PostgreSQL refuses to start with "postmaster became multithreaded during startup", set a locale first, e.g. `export LC_ALL=en_US.UTF-8`.

`--run` is required because the script resets its dedicated verification database. It permits only local PostgreSQL and the exact name `fabric_pipeline_test`. An existing nonempty database must carry the script's ownership marker before it can be reset; an unmarked nonempty database is refused. The normal app database and the ordinary backend test database are never selected.

For a separately configured local PostgreSQL installation, `PIPELINE_DATABASE_URL` may supply that dedicated database connection. Keep credentials in the environment rather than command-line arguments. The same exact database-name and local-host checks still apply.

The aggregate report records worksheet coverage, missing-field counts, identity/count checks, and pass/fail results. It contains no specimen IDs or customer field values. Original workbooks, generated PDFs, and decoded code images are not copied into the repository.

## Limits of this evidence

The checks prove the software path from these workbooks through reviewed SQL records to downloadable label PDFs. They decode the barcode images embedded in those PDFs; they do not prove physical printer scaling, roll compatibility, cutter behavior, fabric adhesion, or Eyoyo first-read performance. Those remain physical acceptance checks using the actual printer, stock, and scanner.

The importer supports the supplied template families and straightforward five-column tables. Unrelated future layouts may need another mapping. It reads cached Excel formula values and never runs macros or recalculates formulas; unresolved source values remain visible for review.
