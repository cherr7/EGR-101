# On-device AI model evaluation

**Decision (Nov 12, 2026): not used.** The prep-sheet importer stays rule-based. Apple's on-device model can't be trusted with sample names, counts or comment hints, and isn't part of the app.

## What was tested

- **Model:** Apple Foundation Models on-device model (`instruct_3b`), through the `fm` command-line tool.
- **Machine:** the host MacBook Neo (A18 Pro, 8 GB, macOS 27.2).
- **Data:** the team's real 2026 prep workbooks, compared with what the importer reads from them.
- **Privacy:** nothing left the Mac. Job filenames and sample names are left out of this file because the repo is public.

The question: could the model help where the importer has to guess, by reading names, counts and free-text comments?

## Results

### General speed and memory

| Measure | Result |
| --- | --- |
| Time to first word, short prompt | about 0.35–0.6 s |
| Time to first word, 2,000-token prompt (about one prep sheet) | about 2.4 s |
| Writing speed, long answers | about 40–45 tokens/s |
| Memory locked at first use | about 2 GB that macOS can't page out |
| Free memory during long answers | dropped to 30–65 MB |

The largest prompt tested was about 3,800 tokens. The model's context window is about 4,096 tokens, so a big workbook won't fit in one prompt.

### Prep-sheet tasks

| Task | Result | Pass? |
| --- | --- | --- |
| Sample names, 5 sheet sections | 20 of 42 matched. It invented 6 samples from an instruction row ("cut 7 yards of Sample A-F…"). It wrote bare letters (`A`) where the importer writes `Sample A`. | No |
| Pieces per sample | 2 of 5 correct | No |
| Spare pieces | 2 of 5 correct. It made up numbers in 3 of 5. | No |
| Same answer on 3 runs, names / pieces / spares | 3 of 5, 4 of 5, 0 of 5 | No |
| Sheet type (prep sheet, training matrix, results, reference) | 6 of 8. It called two prep sheets "results sheets" because their header says "Total Number of Specimens to be **Tested**". | No |
| Database slowdown while the model runs | None. PostgreSQL stayed under 1 ms (median) during a 2,225-token run. | Yes |

### Comment hints (the one idea that looked promising)

The idea: flag comments that may change counts on the import screen. The bar was ≥ 95% of count-changing comments caught, with the same answer every run.

- **Data:** 183 comment cells from 15 workbooks, 58 distinct comments. Each was hand-labelled *before* the model was run.
- **Settings:** temperature 0, each comment run 3 times.

| Measure | Result |
| --- | --- |
| Count-changing comments caught | **30%** (9 of 30 distinct; 19 of 77 cells) |
| False "may change counts" hints | 4 distinct comments |
| Same answer on all 3 runs | 100% |

**Comments it missed**, with the model calling them "just information":
- most sample ranges (`A-F`, `Sample A-C`, `Samples L1-L4`)
- shared-sample notes ("Shares samples with … Prep not needed")
- notes that remove rows ("doesn't need its own line")
- missing-sample notes

**Comments it wrongly flagged:** roll and bin numbers (`Physical Sample Box(s)/Roll(s): 9121763 (2-F)`) were flagged as changing counts. It seems to treat long numbers as counts.

Repeatable but mostly wrong is worse than useless here, so the idea was dropped.

## Why the importer stays rule-based

Names and counts become permanent specimen records and printed labels, so a plausible wrong number is the worst kind of error. The rules either read a value correctly or raise a warning for a person to check. The model's errors raised no warning.

## Worth retrying only if

- A newer model passes the same tests, and the comment-hint test reaches ≥ 95% on count-changing comments, with no invented samples on instruction rows.
- Rerun on the same hand-labelled set, with no names or filenames in the repo.
