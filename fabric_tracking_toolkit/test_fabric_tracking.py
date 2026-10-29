import json
import tempfile
import unittest
from pathlib import Path
import sample_barcode

class FabricTrackingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.request = {
            "schema_version": "1.0",
            "project_id": "2908458D1",
            "jobs": [{
                "job_ref": "J001", "project_id": "2908458D1",
                "test": "Flame Resistance Test", "condition": "AR",
                "stack_id": "STACK-A", "stack_size": 3,
                "samples": [
                    {"sample_no": 1, "sample_label": "A1", "external_sample_id": "ID-001"},
                    {"sample_no": 2, "sample_label": "A2", "external_sample_id": "ID-002"},
                    {"sample_no": 3, "sample_label": "A3", "external_sample_id": "ID-003"}
                ]
            }]
        }
        self.response_path = self.root / "returned.json"
        self.response_path.write_text(json.dumps(self.request), encoding="utf-8")
        self.manifest_path = self.root / "manifest.json"
        self.log_path = self.root / "scan_log.csv"
        sample_barcode.import_returned_ids(str(self.response_path), str(self.manifest_path))

    def tearDown(self):
        self.tmp.cleanup()

    def test_scan_any_member_logs_whole_stack(self):
        result = sample_barcode.scan("ID-002", "IN", str(self.manifest_path), str(self.log_path))
        self.assertTrue(result["logged"])
        self.assertEqual(len(result["samples"]), 3)
        import csv
        with self.log_path.open(newline="", encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
        self.assertEqual(len(rows), 3)
        self.assertEqual({r["direction"] for r in rows}, {"IN"})
        self.assertEqual(len({r["timestamp"] for r in rows}), 1)

    def test_duplicate_direction_suppressed(self):
        sample_barcode.scan("ID-001", "IN", str(self.manifest_path), str(self.log_path))
        result = sample_barcode.scan("ID-003", "IN", str(self.manifest_path), str(self.log_path))
        self.assertFalse(result["logged"])

    def test_out_after_in_logs_all(self):
        sample_barcode.scan("ID-001", "IN", str(self.manifest_path), str(self.log_path))
        result = sample_barcode.scan("ID-003", "OUT", str(self.manifest_path), str(self.log_path))
        self.assertTrue(result["logged"])
        import csv
        with self.log_path.open(newline="", encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
        self.assertEqual(len(rows), 6)
        self.assertEqual(len({r["timestamp"] for r in rows[3:]}), 1)

    def test_duplicate_external_ids_rejected(self):
        bad = json.loads(self.response_path.read_text())
        bad["jobs"][0]["samples"][1]["external_sample_id"] = "ID-001"
        p = self.root / "bad.json"
        p.write_text(json.dumps(bad))
        with self.assertRaises(ValueError):
            sample_barcode.import_returned_ids(str(p), str(self.root / "bad_manifest.json"))

if __name__ == "__main__":
    unittest.main()


class PrepReaderTests(unittest.TestCase):
    def test_fictional_nfpa_sheet_extracts_expected_jobs(self):
        from prep_reader import read_file

        workbook = Path(
            "web_runs/38816c4c2f424d5d92503e22ace8dd04/prep_sheet.xlsm"
        )
        if not workbook.exists():
            self.skipTest("Fictional NFPA workbook is not present")

        result = read_file(str(workbook), sheet="NFPA 2112")
        jobs = result["jobs"]

        # There are 23 substantive test rows, plus the incomplete CTT Prep row.
        self.assertEqual(len(jobs), 24)
        self.assertEqual(result["project_id"], "TEST-2908458D1")

        # The first job has two conditions, one explicit sample count, and
        # remains a single job.
        first = jobs[0]
        self.assertEqual(first["test"], "Flame")
        self.assertEqual(first["condition"], "AR, 100L")
        self.assertEqual(first["stack_size"], 2)
        self.assertEqual(first["source"]["source_row"], 5)

        # Incomplete CTT Prep must not be treated as ready for ID handoff.
        ctt = jobs[-1]
        self.assertEqual(ctt["test"], "CTT Prep")
        self.assertEqual(ctt["status"], "review_required")
        self.assertIsNone(ctt["stack_size"])
        self.assertEqual(ctt["condition"], "")

    def test_incomplete_job_is_not_ready_for_id_handoff(self):
        from prep_reader import read_file

        # A tiny workbook verifies that a test name without a condition or
        # explicit sample count remains review_required.
        from openpyxl import Workbook

        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "incomplete.xlsx"
            wb = Workbook()
            ws = wb.active
            ws.title = "Prep"
            ws.append(["LabWareDx", None, None])
            ws.append(["TEST-12345", None, None])
            ws.append(["Test", "Conditions", "# of samples"])
            ws.append(["CTT Prep", None, None])
            wb.save(path)
            wb.close()

            result = read_file(str(path), sheet="Prep")
            self.assertEqual(len(result["jobs"]), 1)
            self.assertEqual(result["jobs"][0]["status"], "review_required")


class IdHandoffTests(unittest.TestCase):
    def test_only_ready_complete_jobs_are_exported(self):
        from sample_barcode import export_id_request

        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / "extraction.json"
            output = Path(td) / "id_request.json"

            source.write_text(json.dumps({
                "project_id": "TEST-12345",
                "jobs": [
                    {
                        "status": "ready_for_id_handoff",
                        "project_id": "TEST-12345",
                        "test": "Flame",
                        "condition": "AR",
                        "stack_size": 2,
                    },
                    {
                        "status": "review_required",
                        "project_id": "TEST-12345",
                        "test": "CTT Prep",
                        "condition": "",
                        "stack_size": None,
                    },
                    {
                        "status": "ready_for_id_handoff",
                        "project_id": "TEST-12345",
                        "test": "Heat",
                        "condition": "",
                        "stack_size": 3,
                    },
                ],
            }), encoding="utf-8")

            result = export_id_request(str(source), str(output))

            self.assertEqual(len(result["jobs"]), 1)
            self.assertEqual(result["jobs"][0]["test"], "Flame")
            self.assertEqual(len(result["jobs"][0]["samples"]), 2)
            self.assertTrue(any("CTT Prep" in w for w in result["warnings"]))
            self.assertTrue(any("Heat" in w for w in result["warnings"]))

    def test_no_eligible_jobs_produces_empty_request_with_warning(self):
        from sample_barcode import export_id_request

        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / "extraction.json"
            output = Path(td) / "id_request.json"
            source.write_text(json.dumps({
                "project_id": "TEST-12345",
                "jobs": [{
                    "status": "review_required",
                    "test": "CTT Prep",
                    "condition": "",
                    "stack_size": None,
                }],
            }), encoding="utf-8")

            result = export_id_request(str(source), str(output))

            self.assertEqual(result["jobs"], [])
            self.assertTrue(any("No eligible jobs" in w for w in result["warnings"]))


class StrictIdImportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

        self.request = {
            "schema_version": "1.0",
            "project_id": "TEST-12345",
            "jobs": [{
                "job_ref": "J001",
                "project_id": "TEST-12345",
                "test": "Flame",
                "condition": "AR",
                "stack_id": "STACK-001",
                "stack_size": 2,
                "samples": [
                    {"sample_no": 1, "sample_label": "A1", "external_sample_id": None},
                    {"sample_no": 2, "sample_label": "A2", "external_sample_id": None},
                ],
                "status": "awaiting_external_ids",
            }],
        }

        self.response = json.loads(json.dumps(self.request))
        self.response["jobs"][0]["samples"][0]["external_sample_id"] = "EXT-001"
        self.response["jobs"][0]["samples"][1]["external_sample_id"] = "EXT-002"

        self.request_path = self.root / "request.json"
        self.response_path = self.root / "response.json"
        self.manifest_path = self.root / "manifest.json"
        self.write_json(self.request_path, self.request)
        self.write_json(self.response_path, self.response)

    def tearDown(self):
        self.tmp.cleanup()

    @staticmethod
    def write_json(path, data):
        path.write_text(json.dumps(data), encoding="utf-8")

    def import_response(self):
        return sample_barcode.import_returned_ids(
            str(self.response_path),
            str(self.manifest_path),
            str(self.request_path),
        )

    def test_valid_response_imports_all_samples(self):
        manifest = self.import_response()
        self.assertEqual(len(manifest["samples"]), 2)
        self.assertEqual(
            {s["sample_id"] for s in manifest["samples"]},
            {"EXT-001", "EXT-002"},
        )

    def test_missing_sample_is_rejected(self):
        self.response["jobs"][0]["samples"].pop()
        self.write_json(self.response_path, self.response)

        with self.assertRaisesRegex(ValueError, "sample rows|sample count"):
            self.import_response()

    def test_unknown_extra_job_is_rejected(self):
        extra = json.loads(json.dumps(self.response["jobs"][0]))
        extra["job_ref"] = "J999"
        self.response["jobs"].append(extra)
        self.write_json(self.response_path, self.response)

        with self.assertRaisesRegex(ValueError, "Unknown extra jobs"):
            self.import_response()

    def test_changed_stack_size_is_rejected(self):
        self.response["jobs"][0]["stack_size"] = 3
        self.write_json(self.response_path, self.response)

        with self.assertRaisesRegex(ValueError, "stack_size"):
            self.import_response()

    def test_duplicate_sample_ids_are_rejected(self):
        self.response["jobs"][0]["samples"][1]["external_sample_id"] = "EXT-001"
        self.write_json(self.response_path, self.response)

        with self.assertRaisesRegex(ValueError, "Duplicate sample ID"):
            self.import_response()


class StrictIdImportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

        self.request = {
            "schema_version": "1.0",
            "project_id": "TEST-12345",
            "jobs": [{
                "job_ref": "J001",
                "project_id": "TEST-12345",
                "test": "Flame",
                "condition": "AR",
                "stack_id": "STACK-001",
                "stack_size": 2,
                "samples": [
                    {"sample_no": 1, "sample_label": "A1", "external_sample_id": None},
                    {"sample_no": 2, "sample_label": "A2", "external_sample_id": None},
                ],
                "status": "awaiting_external_ids",
            }],
        }

        self.response = json.loads(json.dumps(self.request))
        self.response["jobs"][0]["samples"][0]["external_sample_id"] = "EXT-001"
        self.response["jobs"][0]["samples"][1]["external_sample_id"] = "EXT-002"

        self.request_path = self.root / "request.json"
        self.response_path = self.root / "response.json"
        self.manifest_path = self.root / "manifest.json"
        self.write_json(self.request_path, self.request)
        self.write_json(self.response_path, self.response)

    def tearDown(self):
        self.tmp.cleanup()

    @staticmethod
    def write_json(path, data):
        path.write_text(json.dumps(data), encoding="utf-8")

    def import_response(self):
        return sample_barcode.import_returned_ids(
            str(self.response_path),
            str(self.manifest_path),
            str(self.request_path),
        )

    def test_valid_response_imports_all_samples(self):
        manifest = self.import_response()
        self.assertEqual(len(manifest["samples"]), 2)
        self.assertEqual(
            {s["sample_id"] for s in manifest["samples"]},
            {"EXT-001", "EXT-002"},
        )

    def test_missing_sample_is_rejected(self):
        self.response["jobs"][0]["samples"].pop()
        self.write_json(self.response_path, self.response)

        with self.assertRaisesRegex(ValueError, "sample rows|sample count"):
            self.import_response()

    def test_unknown_extra_job_is_rejected(self):
        extra = json.loads(json.dumps(self.response["jobs"][0]))
        extra["job_ref"] = "J999"
        self.response["jobs"].append(extra)
        self.write_json(self.response_path, self.response)

        with self.assertRaisesRegex(ValueError, "Unknown extra jobs"):
            self.import_response()

    def test_changed_stack_size_is_rejected(self):
        self.response["jobs"][0]["stack_size"] = 3
        self.write_json(self.response_path, self.response)

        with self.assertRaisesRegex(ValueError, "stack_size"):
            self.import_response()

    def test_duplicate_sample_ids_are_rejected(self):
        self.response["jobs"][0]["samples"][1]["external_sample_id"] = "EXT-001"
        self.write_json(self.response_path, self.response)

        with self.assertRaisesRegex(ValueError, "Duplicate sample ID"):
            self.import_response()
