from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


MODULE_PATH = (
    Path(__file__).resolve().parents[1] / "分析CAD目录内容指纹V18.py"
)
SPEC = importlib.util.spec_from_file_location("cad_prefilter_v18", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def fingerprint(
    texts: list[tuple[str, int]],
    *,
    blocks: list[dict[str, object]] | None = None,
    layers: list[str] | None = None,
    proxies: int = 0,
    skipped: int = 0,
    entities: int = 100,
) -> dict[str, object]:
    return {
        "export_status": "success",
        "entity_count": entities,
        "text_occurrence_count": sum(count for _, count in texts),
        "unique_text_record_count": len(texts),
        "layout_block_reference_count": sum(
            int(item.get("layout_reference_count", 0))
            for item in blocks or []
        ),
        "definition_block_reference_count": 1 if blocks else 0,
        "proxy_entity_count": proxies,
        "skipped_object_error_count": skipped,
        "layers": layers or ["0", "S-TEXT"],
        "block_records": blocks or [],
        "text_records": [
            {
                "text": text,
                "count": count,
                "x": index,
                "y": index + 1,
                "origin": "layout-direct",
            }
            for index, (text, count) in enumerate(texts)
        ],
    }


class ContentFingerprintV18Tests(unittest.TestCase):
    def test_promotes_repeated_device_layout_evidence(self) -> None:
        result = MODULE.analyze_fingerprint(
            fingerprint(
                [
                    ("二层阻尼器平面布置图", 1),
                    ("VFD-1 X向 共12套", 2),
                ]
            )
        )
        self.assertEqual(result["content_scan_decision"], "promoted_primary")
        self.assertEqual(result["absence_proven"], "false")

    def test_promotes_repeated_device_named_blocks(self) -> None:
        result = MODULE.analyze_fingerprint(
            fingerprint(
                [("二层梁平法施工图", 1)],
                blocks=[
                    {
                        "name": "VFD-350",
                        "effective_name": "VFD-350",
                        "layer": "S-DAMPER",
                        "layout_reference_count": 8,
                    }
                ],
            )
        )
        self.assertEqual(result["content_scan_decision"], "promoted_primary")

    def test_keeps_general_notes_as_reference(self) -> None:
        result = MODULE.analyze_fingerprint(
            fingerprint(
                [
                    ("黏滞阻尼器设计说明", 1),
                    ("阻尼器技术要求及检验要求", 1),
                ]
            )
        )
        self.assertEqual(result["content_scan_decision"], "reference_hit")

    def test_negative_is_not_absence_proof(self) -> None:
        result = MODULE.analyze_fingerprint(
            fingerprint([("三层梁配筋图", 1), ("混凝土强度等级C35", 1)])
        )
        self.assertEqual(result["content_scan_decision"], "content_negative")
        self.assertEqual(result["absence_proven"], "false")

    def test_empty_or_bad_coverage_is_unresolved(self) -> None:
        result = MODULE.analyze_fingerprint(
            fingerprint([], blocks=[], proxies=5, skipped=3)
        )
        self.assertEqual(result["content_scan_decision"], "content_unresolved")

    def test_many_object_errors_are_unresolved(self) -> None:
        result = MODULE.analyze_fingerprint(
            fingerprint([("梁图", 1)], skipped=30, entities=100)
        )
        self.assertEqual(result["content_scan_decision"], "content_unresolved")


if __name__ == "__main__":
    unittest.main()
