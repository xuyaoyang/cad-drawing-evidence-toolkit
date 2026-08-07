#!/usr/bin/env python3
"""把 V18 完整流程输出自动组织为跨 DWG/跨视图物理设备归一组。

V19 不读取或修改 DWG，只消费 V18/V16 已生成的 CSV/JSON。只有楼栋、楼层、
唯一主视图和主视图轴网均可确认时才调用 V12；其余组保留安全停止状态。
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import subprocess
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable


COMPOSITE_BUILDING = re.compile(
    r"(?<!\d)(0*\d{1,3})\s*[-－]\s*(0*\d{1,3})\s*[#＃]"
)
SIMPLE_BUILDING = re.compile(r"(?<![\d\-－])(0*\d{1,3})\s*[#＃]")
FLOOR_RANGE = re.compile(
    r"(?<![A-Za-z0-9])([Bb]?\d{1,3})\s*(?:[Ff]|层)?\s*"
    r"[~～至\-－]\s*([Bb]?\d{1,3})\s*(?:[Ff]|层)"
)
FLOOR_SINGLE = re.compile(r"(?<![A-Za-z0-9])([Bb]?\d{1,3})\s*(?:[Ff]|层)")
CHINESE_FLOOR = re.compile(r"([一二三四五六七八九十]{1,3})层")
AXIS_LABEL = re.compile(
    r"^(\d{1,3}(?:\s*[-－]\s*\d{1,3})*)\s*[-－]\s*"
    r"([0-9]{1,3}|[A-Za-z])$"
)
PLAIN_AXIS_LABEL = re.compile(r"^([0-9]{1,3}|[A-Za-z])$")
NAMESPACE_AXIS_LABEL = re.compile(r"^(.+?[-－])([0-9]{1,3}|[A-Za-z])$")

PRIMARY_ROLES = {"layout", "structural_plan"}
CROSS_ROLES = {"beam_plan", "column_plan", "wall_plan", "slab_plan"}
RELEVANT_ROLES = PRIMARY_ROLES | CROSS_ROLES
RELEVANT_DECISIONS = {"counted", "manual_review", "candidate_needs_frame"}


@dataclass
class DrawingArtifacts:
    source_path: Path
    copied_stem: str
    route_status: str
    content_decision: str
    analysis_dir: Path
    candidate_csv: Path
    frame_texts_csv: Path | None
    visibility_json: Path | None
    shared_layout_csv: Path | None
    rows: list[dict[str, str]]
    frame_texts: list[dict[str, str]]
    primitive_geometry_json: Path | None = None


@dataclass
class EvidenceUnit:
    artifact: DrawingArtifacts
    frame_id: str
    role: str
    buildings: tuple[str, ...]
    floors: tuple[str, ...]
    rows: list[dict[str, str]] = field(default_factory=list)

    @property
    def scope_key(self) -> str:
        return (
            "B:" + ",".join(self.buildings) + "|F:" + ",".join(self.floors)
        )

    @property
    def has_counted(self) -> bool:
        return any(row.get("decision") == "counted" for row in self.rows)

    @property
    def selected_rows(self) -> list[dict[str, str]]:
        if self.role in PRIMARY_ROLES:
            return [row for row in self.rows if row.get("decision") == "counted"]
        return [
            row
            for row in self.rows
            if row.get("decision") in RELEVANT_DECISIONS
            and row.get("semantic_leaf_symbol") == "True"
        ]

    @property
    def source_id(self) -> str:
        token = hashlib.sha1(
            (
                self.artifact.copied_stem
                + "|"
                + self.frame_id
                + "|"
                + self.role
                + "|"
                + self.scope_key
            ).encode("utf-8")
        ).hexdigest()[:10]
        return f"SRC-{token}"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="组织并运行 V19 跨 DWG 阻尼器证据组"
    )
    parser.add_argument("--v18-report", type=Path, required=True)
    parser.add_argument("--v16-root", type=Path, required=True)
    parser.add_argument("--v12-script", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--plan-only", action="store_true")
    return parser.parse_args()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def write_csv(path: Path, rows: Iterable[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def normalize_number(value: str) -> str:
    return str(int(value))


def normalize_building(value: str) -> str:
    return "-".join(
        normalize_number(part)
        for part in re.split(r"[-－]", value)
        if part.strip()
    )


def extract_buildings(text: str) -> tuple[str, ...]:
    values: set[str] = set()
    masked = list(text or "")
    for match in COMPOSITE_BUILDING.finditer(text or ""):
        values.add(
            f"{normalize_number(match.group(1))}-"
            f"{normalize_number(match.group(2))}"
        )
        for index in range(match.start(), match.end()):
            masked[index] = " "
    remaining = "".join(masked)
    for match in SIMPLE_BUILDING.finditer(remaining):
        values.add(normalize_number(match.group(1)))
    return tuple(sorted(values, key=natural_key))


def normalize_floor(value: str) -> str:
    value = value.upper()
    if value.startswith("B"):
        return f"B{int(value[1:])}F"
    return f"{int(value)}F"


def extract_floors(text: str) -> tuple[str, ...]:
    values: set[str] = set()
    masked = list(text or "")
    for match in FLOOR_RANGE.finditer(text or ""):
        start = normalize_floor(match.group(1))
        end = normalize_floor(match.group(2))
        values.add(start if start == end else f"{start}~{end}")
        for index in range(match.start(), match.end()):
            masked[index] = " "
    for match in FLOOR_SINGLE.finditer("".join(masked)):
        values.add(normalize_floor(match.group(1)))
    for match in CHINESE_FLOOR.finditer(text or ""):
        number = chinese_number(match.group(1))
        if number is not None:
            values.add(f"{number}F")
    for token, normalized in (
        ("屋面", "ROOF"),
        ("基础", "FOUNDATION"),
        ("标准层", "TYPICAL"),
    ):
        if token in (text or ""):
            values.add(normalized)
    return tuple(sorted(values, key=natural_key))


def chinese_number(value: str) -> int | None:
    digits = {
        "一": 1,
        "二": 2,
        "三": 3,
        "四": 4,
        "五": 5,
        "六": 6,
        "七": 7,
        "八": 8,
        "九": 9,
    }
    if value == "十":
        return 10
    if "十" in value:
        left, right = value.split("十", 1)
        tens = digits.get(left, 1) if left else 1
        ones = digits.get(right, 0) if right else 0
        return tens * 10 + ones
    return digits.get(value)


def natural_key(value: str) -> tuple[tuple[int, Any], ...]:
    return tuple(
        (0, int(part)) if part.isdigit() else (1, part)
        for part in re.split(r"(\d+)", value)
        if part != ""
    )


def row_scope_text(row: dict[str, str]) -> str:
    return " ".join(
        row.get(field) or ""
        for field in (
            "semantic_parent_block",
            "block_name",
            "effective_name",
            "name_path",
            "semantic_preview",
            "frame_role_evidence",
            "layer",
        )
    )


def choose_buildings(
    file_buildings: tuple[str, ...], row_buildings: tuple[str, ...]
) -> tuple[str, ...]:
    """文件名的明确楼栋范围优先，行内文本仅用于补缺。

    阻尼器型号、轴号或块内其他说明也可能带 ``3-8#`` 一类文本；若把这些
    内容与文件名楼栋并集，会产生并不存在的楼栋。文件名没有楼栋时才回退到
    行内/块内语义范围。
    """
    return file_buildings or row_buildings


def infer_file_role(name: str) -> str:
    if re.search(r"阻尼器.*(?:布置|平面)|(?:布置|平面).*阻尼器", name):
        return "layout"
    if "梁" in name:
        return "beam_plan"
    if "柱" in name:
        return "column_plan"
    if "墙" in name:
        return "wall_plan"
    if re.search(r"板|楼盖|叠合", name):
        return "slab_plan"
    if re.search(r"结构.*平面|平面.*结构", name):
        return "structural_plan"
    return "unknown"


def find_one(directory: Path, pattern: str) -> Path | None:
    matches = sorted(directory.glob(pattern))
    return matches[0] if matches else None


def load_artifacts(
    v18_report: Path, v16_root: Path
) -> tuple[list[DrawingArtifacts], list[dict[str, Any]]]:
    report_rows = read_csv(v18_report)
    eligible = {
        str(Path(row["source_path"]).resolve()).lower(): row
        for row in report_rows
        if row.get("route_status") == "selected"
        or row.get("content_scan_decision") == "promoted_primary"
    }
    manifest_path = v16_root / "input_manifest.csv"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"V16 input_manifest.csv 不存在：{manifest_path}")
    manifest_rows = read_csv(manifest_path)
    artifacts: list[DrawingArtifacts] = []
    issues: list[dict[str, Any]] = []
    for item in manifest_rows:
        source = Path(item.get("source_path") or "").resolve()
        report = eligible.get(str(source).lower())
        if report is None:
            continue
        stem = item.get("copied_stem") or ""
        analysis_dir = v16_root / "analysis" / stem
        candidate = find_one(analysis_dir, "*.阻尼器实例候选.csv")
        if candidate is None:
            issues.append(
                {
                    "source_path": str(source),
                    "status": "candidate_export_missing",
                    "reason": "阻尼器实例候选CSV不存在",
                }
            )
            continue
        frame_texts = find_one(analysis_dir, "*.文字按图框归属清单.csv")
        visibility = find_one(analysis_dir, "*.cad_visibility_export_v13.json")
        shared = find_one(analysis_dir, "*.多栋共用标准层调和.csv")
        primitive = (
            v16_root
            / "输出"
            / f"{stem}.cad_primitive_export_v10.json"
        )
        if not primitive.is_file():
            primitive = None
        artifacts.append(
            DrawingArtifacts(
                source_path=source,
                copied_stem=stem,
                route_status=report.get("route_status") or "",
                content_decision=report.get("content_scan_decision") or "",
                analysis_dir=analysis_dir,
                candidate_csv=candidate,
                frame_texts_csv=frame_texts,
                visibility_json=visibility,
                shared_layout_csv=shared,
                rows=read_csv(candidate),
                frame_texts=read_csv(frame_texts) if frame_texts else [],
                primitive_geometry_json=primitive,
            )
        )
    return artifacts, issues


def frame_text_for(artifact: DrawingArtifacts, frame_id: str) -> str:
    return " ".join(
        row.get("text") or ""
        for row in artifact.frame_texts
        if (row.get("frame_id") or "") == frame_id
    )


def build_units(
    artifacts: list[DrawingArtifacts],
) -> tuple[list[EvidenceUnit], list[dict[str, Any]]]:
    units: dict[
        tuple[str, str, str, tuple[str, ...], tuple[str, ...]], EvidenceUnit
    ] = {}
    unresolved: list[dict[str, Any]] = []
    for artifact in artifacts:
        file_role = infer_file_role(artifact.source_path.stem)
        file_buildings = extract_buildings(artifact.source_path.stem)
        for row in artifact.rows:
            decision = row.get("decision") or ""
            role = row.get("frame_role") or ""
            if role not in RELEVANT_ROLES:
                role = file_role
            if role not in RELEVANT_ROLES or decision not in RELEVANT_DECISIONS:
                continue
            if (
                role in CROSS_ROLES
                and row.get("semantic_leaf_symbol") != "True"
            ):
                continue
            if role in PRIMARY_ROLES and decision != "counted":
                continue
            frame_id = row.get("frame_id") or ""
            scope_text = row_scope_text(row)
            buildings = choose_buildings(
                file_buildings, extract_buildings(scope_text)
            )
            floors = extract_floors(scope_text)
            if not floors and frame_id:
                floors = extract_floors(frame_text_for(artifact, frame_id))
            if not buildings or not floors:
                unresolved.append(
                    {
                        "source_path": str(artifact.source_path),
                        "copied_stem": artifact.copied_stem,
                        "frame_id": frame_id,
                        "role": role,
                        "instance_key": row.get("instance_key") or "",
                        "buildings": ",".join(buildings),
                        "floors": ",".join(floors),
                        "status": "scope_unresolved",
                        "reason": (
                            "楼栋范围无法提取"
                            if not buildings
                            else "楼层范围无法提取"
                        ),
                    }
                )
                continue
            pseudo_frame = frame_id or (
                "NOFRAME-"
                + hashlib.sha1(
                    (
                        row.get("semantic_parent_key")
                        or row.get("parent_instance_key")
                        or row.get("instance_key")
                        or ""
                    ).encode("utf-8")
                ).hexdigest()[:10]
            )
            key = (
                artifact.copied_stem,
                pseudo_frame,
                role,
                buildings,
                floors,
            )
            if key not in units:
                units[key] = EvidenceUnit(
                    artifact=artifact,
                    frame_id=frame_id,
                    role=role,
                    buildings=buildings,
                    floors=floors,
                )
            units[key].rows.append(row)
    return list(units.values()), unresolved


def axis_evidence(unit: EvidenceUnit) -> dict[str, Any]:
    if not unit.frame_id or unit.artifact.frame_texts_csv is None:
        return {
            "ready": False,
            "mode": "missing_frame_texts",
            "building": "",
            "namespace": "",
            "x_count": 0,
            "y_count": 0,
        }
    by_building: dict[str, dict[str, set[str]]] = defaultdict(
        lambda: {"x": set(), "y": set()}
    )
    plain = {"x": set(), "y": set()}
    namespaces: dict[str, dict[str, set[str]]] = defaultdict(
        lambda: {"x": set(), "y": set()}
    )
    for row in unit.artifact.frame_texts:
        if (row.get("frame_id") or "") != unit.frame_id:
            continue
        text = (row.get("text") or "").strip()
        match = AXIS_LABEL.fullmatch(text)
        if match:
            building, axis = match.groups()
            axis = axis.upper()
            by_building[normalize_building(building)][
                "x" if axis.isdigit() else "y"
            ].add(axis)
            continue
        namespace_match = NAMESPACE_AXIS_LABEL.fullmatch(text)
        if namespace_match:
            namespace, axis = namespace_match.groups()
            axis = axis.upper()
            namespaces[namespace]["x" if axis.isdigit() else "y"].add(axis)
            continue
        plain_match = PLAIN_AXIS_LABEL.fullmatch(text)
        if plain_match:
            axis = plain_match.group(1).upper()
            plain["x" if axis.isdigit() else "y"].add(axis)

    prefixed = sorted(
        (
            (
                len(values["x"]) >= 3 and len(values["y"]) >= 2,
                len(values["x"]) + len(values["y"]),
                building,
                values,
            )
            for building, values in by_building.items()
        ),
        reverse=True,
    )
    if prefixed and prefixed[0][0]:
        _, _, building, values = prefixed[0]
        return {
            "ready": True,
            "mode": "building_prefixed",
            "building": building,
            "namespace": "",
            "x_count": len(values["x"]),
            "y_count": len(values["y"]),
        }

    if (
        len(unit.buildings) == 1
        and len(plain["x"]) >= 3
        and len(plain["y"]) >= 2
    ):
        return {
            "ready": True,
            "mode": "plain_single_building_normalized",
            "building": unit.buildings[0],
            "namespace": "",
            "x_count": len(plain["x"]),
            "y_count": len(plain["y"]),
        }

    namespace_candidates = sorted(
        (
            (
                len(values["x"]) >= 3 and len(values["y"]) >= 2,
                len(values["x"]) + len(values["y"]),
                namespace,
                values,
            )
            for namespace, values in namespaces.items()
        ),
        reverse=True,
    )
    if (
        len(unit.buildings) == 1
        and namespace_candidates
        and namespace_candidates[0][0]
    ):
        _, _, namespace, values = namespace_candidates[0]
        return {
            "ready": True,
            "mode": "namespace_suffix_single_scope",
            "building": unit.buildings[0],
            "namespace": namespace,
            "x_count": len(values["x"]),
            "y_count": len(values["y"]),
        }

    best_prefixed = prefixed[0] if prefixed else None
    best_namespace = namespace_candidates[0] if namespace_candidates else None
    x_counts = [len(plain["x"])]
    y_counts = [len(plain["y"])]
    if best_prefixed:
        x_counts.append(len(best_prefixed[3]["x"]))
        y_counts.append(len(best_prefixed[3]["y"]))
    if best_namespace:
        x_counts.append(len(best_namespace[3]["x"]))
        y_counts.append(len(best_namespace[3]["y"]))
    return {
        "ready": False,
        "mode": "insufficient_axis_labels",
        "building": best_prefixed[2] if best_prefixed else "",
        "namespace": best_namespace[2] if best_namespace else "",
        "x_count": max(x_counts),
        "y_count": max(y_counts),
    }


def axis_evidence_ready(unit: EvidenceUnit) -> bool:
    return bool(axis_evidence(unit)["ready"])


def normalized_frame_rows(unit: EvidenceUnit) -> list[dict[str, str]]:
    rows = [
        dict(row)
        for row in unit.artifact.frame_texts
        if (row.get("frame_id") or "") == unit.frame_id
    ]
    evidence = axis_evidence(unit)
    if evidence["mode"] not in {
        "plain_single_building_normalized",
        "namespace_suffix_single_scope",
    }:
        return rows
    building = str(evidence["building"])
    for row in rows:
        text = (row.get("text") or "").strip()
        if evidence["mode"] == "plain_single_building_normalized":
            match = PLAIN_AXIS_LABEL.fullmatch(text)
        else:
            match = NAMESPACE_AXIS_LABEL.fullmatch(text)
            if match and match.group(1) != evidence["namespace"]:
                match = None
        if not match:
            continue
        row["v20_axis_source_text"] = text
        row["v20_axis_normalized"] = "True"
        axis = match.group(1) if match.lastindex == 1 else match.group(2)
        row["text"] = f"{building}-{axis.upper()}"
    return rows


def select_primary(
    units: list[EvidenceUnit],
) -> tuple[EvidenceUnit | None, str]:
    structural = [
        unit
        for unit in units
        if unit.role == "structural_plan" and unit.has_counted
    ]
    layouts = [
        unit for unit in units if unit.role == "layout" and unit.has_counted
    ]
    if len(structural) == 1:
        return structural[0], ""
    if len(structural) > 1:
        return None, "multiple_structural_primary_views"
    if len(layouts) == 1:
        return layouts[0], ""
    if len(layouts) > 1:
        return None, "multiple_layout_primary_views"
    return None, "primary_layout_missing"


def include_parent_rows(
    all_rows: list[dict[str, str]], selected: list[dict[str, str]]
) -> list[dict[str, str]]:
    by_key = {
        row.get("instance_key") or "": row
        for row in all_rows
        if row.get("instance_key")
    }
    included: dict[str, dict[str, str]] = {}
    queue = list(selected)
    while queue:
        row = queue.pop()
        key = row.get("instance_key") or f"ROW-{id(row)}"
        if key in included:
            continue
        included[key] = row
        for field in ("semantic_parent_key", "parent_instance_key"):
            parent_key = row.get(field) or ""
            parent = by_key.get(parent_key)
            if parent is not None and parent_key not in included:
                queue.append(parent)
    return list(included.values())


def write_filtered_csv(path: Path, rows: list[dict[str, str]]) -> None:
    if not rows:
        raise ValueError(f"不能写空候选CSV：{path}")
    write_csv(path, rows, list(rows[0].keys()))


def prepare_group_manifest(
    group_id: str,
    scope_key: str,
    primary: EvidenceUnit,
    units: list[EvidenceUnit],
    group_dir: Path,
) -> Path:
    sources: list[dict[str, Any]] = []
    ordered = [primary] + [
        unit for unit in units if unit.source_id != primary.source_id
    ]
    for unit in ordered:
        selected = unit.selected_rows
        filtered = include_parent_rows(unit.artifact.rows, selected)
        candidate_out = group_dir / "inputs" / f"{unit.source_id}.candidate.csv"
        write_filtered_csv(candidate_out, filtered)
        frame_out: Path | None = None
        if unit.frame_id and unit.artifact.frame_texts:
            frame_rows = normalized_frame_rows(unit)
            if frame_rows:
                frame_out = (
                    group_dir / "inputs" / f"{unit.source_id}.frame_texts.csv"
                )
                frame_fields = list(
                    dict.fromkeys(
                        key for row in frame_rows for key in row.keys()
                    )
                )
                write_csv(frame_out, frame_rows, frame_fields)
        sources.append(
            {
                "source_id": unit.source_id,
                "view_type": unit.role,
                "candidate_csv": str(candidate_out),
                "frame_texts_csv": str(frame_out) if frame_out else None,
                "primitive_geometry_json": (
                    str(unit.artifact.primitive_geometry_json)
                    if unit.artifact.primitive_geometry_json
                    else None
                ),
                "visibility_json": (
                    str(unit.artifact.visibility_json)
                    if unit.artifact.visibility_json
                    else None
                ),
                "include_decisions": sorted(
                    {
                        row.get("decision") or ""
                        for row in selected
                        if row.get("decision")
                    }
                ),
                "semantic_leaf_only": all(
                    row.get("semantic_leaf_symbol") == "True"
                    for row in selected
                ),
            }
        )
    shared = primary.artifact.shared_layout_csv
    manifest = {
        "project_id": f"V19-{group_id}",
        "scope_key": scope_key,
        "scope_buildings": list(primary.buildings),
        "scope_floors": list(primary.floors),
        "primary_source_id": primary.source_id,
        "output_prefix": f"V19-{group_id}",
        "axis_fraction_tolerance": 0.08,
        "parent_normalized_tolerance": 0.02,
        "shared_layout_csv": str(shared) if shared else None,
        "sources": sources,
    }
    path = group_dir / f"V19-{group_id}.manifest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def parse_result(stdout: str) -> dict[str, Any] | None:
    for line in reversed(stdout.splitlines()):
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and "status" in value:
            return value
    return None


def run_v12(
    script: Path, manifest: Path, output_dir: Path, prefix: str
) -> tuple[dict[str, Any] | None, str]:
    process = subprocess.run(
        [
            sys.executable,
            str(script),
            str(manifest),
            "--output-dir",
            str(output_dir),
            "--prefix",
            prefix,
        ],
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )
    log = output_dir / f"{prefix}.execution.log"
    log.write_text(
        process.stdout + ("\nSTDERR:\n" + process.stderr if process.stderr else ""),
        encoding="utf-8",
    )
    if process.returncode != 0:
        return None, f"V12退出码{process.returncode}"
    result = parse_result(process.stdout)
    if result is None:
        return None, "V12未返回可解析状态"
    return result, ""


LOCATION_COMPLETE_STATUSES = {
    "single_primary_device_location_complete",
    "cross_view_quantity_closed",
}

LOCATION_REQUIRED_FIELDS = (
    "building_id",
    "floor",
    "axis_position_key",
    "primary_source_id",
    "primary_frame_id",
    "primary_instance_key",
    "primary_world_x",
    "primary_world_y",
)


def build_location_registry(
    group_rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    registry: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    for group in group_rows:
        device_path_text = str(group.get("physical_device_csv") or "")
        device_path = Path(device_path_text) if device_path_text else None
        device_rows = (
            read_csv(device_path)
            if device_path is not None and device_path.is_file()
            else []
        )
        for row in device_rows:
            registry.append(
                {
                    "registry_device_id": (
                        f"DL-{group['group_id']}-"
                        f"{row.get('physical_device_id') or ''}"
                    ),
                    "group_id": group["group_id"],
                    "scope_key": group["scope_key"],
                    "group_status": group["status"],
                    "source_file": group["primary_file"],
                    **row,
                }
            )
        if group["status"] not in LOCATION_COMPLETE_STATUSES:
            unresolved.append(
                {
                    "group_id": group["group_id"],
                    "scope_key": group["scope_key"],
                    "buildings": group["buildings"],
                    "floors": group["floors"],
                    "primary_file": group["primary_file"],
                    "status": group["status"],
                    "reason": group["reason"],
                    "located_device_count": len(device_rows),
                    "unresolved_occurrence_count": group.get(
                        "unresolved_occurrence_count", ""
                    ),
                }
            )

    missing_field_rows = [
        row
        for row in registry
        if any(row.get(field) in ("", None) for field in LOCATION_REQUIRED_FIELDS)
    ]
    duplicate_registry_id_count = len(registry) - len(
        {row["registry_device_id"] for row in registry}
    )
    metrics = {
        "located_device_count": len(registry),
        "unresolved_group_count": len(unresolved),
        "missing_location_row_count": len(missing_field_rows),
        "duplicate_registry_id_count": duplicate_registry_id_count,
    }
    return registry, unresolved, metrics


def main() -> int:
    args = parse_args()
    v18_report = args.v18_report.resolve()
    v16_root = args.v16_root.resolve()
    v12_script = args.v12_script.resolve()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    if not v12_script.is_file():
        raise FileNotFoundError(f"V12脚本不存在：{v12_script}")

    artifacts, load_issues = load_artifacts(v18_report, v16_root)
    units, unresolved_scope = build_units(artifacts)
    groups: dict[str, list[EvidenceUnit]] = defaultdict(list)
    for unit in units:
        groups[unit.scope_key].append(unit)

    group_rows: list[dict[str, Any]] = []
    group_payloads: list[dict[str, Any]] = []
    for scope_key in sorted(groups):
        group_units = groups[scope_key]
        group_id = hashlib.sha1(scope_key.encode("utf-8")).hexdigest()[:10]
        buildings = group_units[0].buildings
        floors = group_units[0].floors
        primary, primary_problem = select_primary(group_units)
        cross_units = [
            unit
            for unit in group_units
            if primary is None or unit.source_id != primary.source_id
        ]
        distinct_roles = sorted({unit.role for unit in group_units})
        status = ""
        reason = ""
        result: dict[str, Any] | None = None
        manifest_path: Path | None = None
        primary_axis = (
            axis_evidence(primary)
            if primary is not None
            else {
                "ready": False,
                "mode": "",
                "building": "",
                "namespace": "",
                "x_count": 0,
                "y_count": 0,
            }
        )

        if primary is None:
            status = primary_problem
            reason = "没有唯一可计数结构平面/阻尼器布置主视图"
        elif not primary_axis["ready"]:
            status = "primary_axis_evidence_missing"
            reason = (
                "主视图未形成V12要求的楼栋轴网证据："
                f"mode={primary_axis['mode']}，"
                f"数字轴={primary_axis['x_count']}，"
                f"字母轴={primary_axis['y_count']}"
            )
        else:
            group_dir = output_dir / "groups" / f"V19-{group_id}"
            manifest_path = prepare_group_manifest(
                group_id, scope_key, primary, group_units, group_dir
            )
            if args.plan_only:
                status = (
                    "single_primary_group_ready"
                    if not cross_units
                    else "cross_view_group_ready"
                )
                reason = "证据组已准备，按plan-only未运行V12"
            else:
                result, error = run_v12(
                    v12_script,
                    manifest_path,
                    group_dir,
                    f"V19-{group_id}",
                )
                if result is None:
                    status = "v12_execution_failed"
                    reason = error
                else:
                    status = str(result.get("status") or "v12_status_missing")
                    reason = (
                        "V12主视图设备定位已执行"
                        if not cross_units
                        else "V12跨视图物理设备归一已执行"
                    )

        row = {
            "group_id": group_id,
            "scope_key": scope_key,
            "buildings": ",".join(buildings),
            "floors": ",".join(floors),
            "source_count": len(group_units),
            "view_roles": ",".join(distinct_roles),
            "primary_source_id": primary.source_id if primary else "",
            "primary_file": (
                primary.artifact.source_path.name if primary else ""
            ),
            "primary_frame_id": primary.frame_id if primary else "",
            "primary_axis_mode": primary_axis["mode"],
            "primary_axis_building": primary_axis["building"],
            "primary_axis_namespace": primary_axis["namespace"],
            "primary_axis_x_count": primary_axis["x_count"],
            "primary_axis_y_count": primary_axis["y_count"],
            "status": status,
            "reason": reason,
            "physical_template_count": (
                result.get("physical_template_count", "") if result else ""
            ),
            "physical_device_count": (
                result.get("physical_device_count", "") if result else ""
            ),
            "occurrence_count": (
                result.get("occurrence_count", "") if result else ""
            ),
            "unresolved_occurrence_count": (
                result.get("unresolved_occurrence_count", "") if result else ""
            ),
            "primary_location_issue_count": (
                result.get("primary_location_issue_count", "") if result else ""
            ),
            "floor_scope_issue_count": (
                result.get("floor_scope_issue_count", "") if result else ""
            ),
            "location_missing_field_count": (
                result.get("location_missing_field_count", "") if result else ""
            ),
            "duplicate_device_id_count": (
                result.get("duplicate_device_id_count", "") if result else ""
            ),
            "floor_evidence_source": (
                result.get("floor_evidence_source", "") if result else ""
            ),
            "physical_device_csv": (
                result.get("physical_device_csv", "") if result else ""
            ),
            "manifest_path": str(manifest_path) if manifest_path else "",
            "report_path": str(result.get("report") or "") if result else "",
        }
        group_rows.append(row)
        group_payloads.append(
            {
                **row,
                "sources": [
                    {
                        "source_id": unit.source_id,
                        "source_path": str(unit.artifact.source_path),
                        "copied_stem": unit.artifact.copied_stem,
                        "frame_id": unit.frame_id,
                        "role": unit.role,
                        "selected_row_count": len(unit.selected_rows),
                        "route_status": unit.artifact.route_status,
                        "content_decision": unit.artifact.content_decision,
                    }
                    for unit in group_units
                ],
            }
        )

    fields = [
        "group_id",
        "scope_key",
        "buildings",
        "floors",
        "source_count",
        "view_roles",
        "primary_source_id",
        "primary_file",
        "primary_frame_id",
        "primary_axis_mode",
        "primary_axis_building",
        "primary_axis_namespace",
        "primary_axis_x_count",
        "primary_axis_y_count",
        "status",
        "reason",
        "physical_template_count",
        "physical_device_count",
        "occurrence_count",
        "unresolved_occurrence_count",
        "primary_location_issue_count",
        "floor_scope_issue_count",
        "location_missing_field_count",
        "duplicate_device_id_count",
        "floor_evidence_source",
        "physical_device_csv",
        "manifest_path",
        "report_path",
    ]
    write_csv(output_dir / "V19跨DWG证据组.csv", group_rows, fields)
    unresolved_fields = [
        "source_path",
        "copied_stem",
        "frame_id",
        "role",
        "instance_key",
        "buildings",
        "floors",
        "status",
        "reason",
    ]
    unresolved_rows = unresolved_scope + load_issues
    write_csv(
        output_dir / "V19未分组证据.csv",
        unresolved_rows,
        unresolved_fields,
    )
    location_registry, location_unresolved, location_metrics = (
        build_location_registry(group_rows)
    )
    location_fields = [
        "registry_device_id",
        "group_id",
        "scope_key",
        "group_status",
        "source_file",
        "physical_device_id",
        "physical_template_id",
        "building_id",
        "floor",
        "axis_position_key",
        "axis_x_low",
        "axis_x_high",
        "axis_x_fraction",
        "axis_y_low",
        "axis_y_high",
        "axis_y_fraction",
        "primary_source_id",
        "primary_frame_id",
        "primary_instance_key",
        "primary_world_x",
        "primary_world_y",
        "location_method",
        "floor_evidence_source",
        "location_status",
        "evidence_status",
    ]
    write_csv(
        output_dir / "V21阻尼器定位总表.csv",
        location_registry,
        location_fields,
    )
    location_unresolved_fields = [
        "group_id",
        "scope_key",
        "buildings",
        "floors",
        "primary_file",
        "status",
        "reason",
        "located_device_count",
        "unresolved_occurrence_count",
    ]
    write_csv(
        output_dir / "V21阻尼器定位未决.csv",
        location_unresolved,
        location_unresolved_fields,
    )
    if location_metrics["located_device_count"] == 0:
        location_registry_status = "device_location_registry_unavailable"
    elif (
        location_metrics["unresolved_group_count"]
        or location_metrics["missing_location_row_count"]
        or location_metrics["duplicate_registry_id_count"]
        or unresolved_rows
    ):
        location_registry_status = "device_location_registry_partial"
    else:
        location_registry_status = "device_location_registry_complete"
    location_payload = {
        "version": "V21",
        "status": location_registry_status,
        **location_metrics,
        "unresolved_scope_count": len(unresolved_scope),
        "load_issue_count": len(load_issues),
        "registry_csv": str(output_dir / "V21阻尼器定位总表.csv"),
        "unresolved_csv": str(output_dir / "V21阻尼器定位未决.csv"),
    }
    (output_dir / "V21阻尼器定位总表.json").write_text(
        json.dumps(location_payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    status_counts = Counter(row["status"] for row in group_rows)
    payload = {
        "version": "V19",
        "v18_report": str(v18_report),
        "v16_root": str(v16_root),
        "absence_proven": False,
        "group_count": len(group_rows),
        "status_counts": dict(status_counts),
        "unresolved_scope_count": len(unresolved_scope),
        "load_issue_count": len(load_issues),
        "groups": group_payloads,
        "unresolved": unresolved_rows,
        "v21_location_registry": location_payload,
    }
    v19_json_path = output_dir / "V19跨DWG证据组.json"
    v19_json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    v23_script = Path(__file__).with_name("生成阻尼器几何定位证据包V23.py")
    v23_output_dir = output_dir / "V23几何定位证据"
    v23_result: dict[str, Any] = {
        "status": "v23_generator_missing",
        "reason": str(v23_script),
    }
    if v23_script.is_file():
        completed = subprocess.run(
            [
                sys.executable,
                str(v23_script),
                str(v19_json_path),
                "--output-dir",
                str(v23_output_dir),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        parsed = parse_result(completed.stdout)
        if completed.returncode == 0 and parsed is not None:
            v23_result = parsed
        else:
            v23_result = {
                "status": "v23_generation_failed",
                "exit_code": completed.returncode,
                "stderr": completed.stderr.strip(),
            }
    payload["v23_evidence_package"] = v23_result
    v19_json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    location_lines = [
        "# V21 阻尼器定位总表",
        "",
        "## 结论",
        "",
        f"- 状态：`{location_registry_status}`。",
        f"- 已形成楼栋、楼层、轴间位置和主视图世界坐标的设备记录："
        f"{location_metrics['located_device_count']}。",
        f"- 未闭合证据组：{location_metrics['unresolved_group_count']}；"
        f"定位字段缺失行：{location_metrics['missing_location_row_count']}；"
        f"总表设备ID重复：{location_metrics['duplicate_registry_id_count']}。",
        f"- 未分组范围：{len(unresolved_scope)}；载入问题：{len(load_issues)}。",
        "- 只有 `device_location_registry_complete` 才表示本次API证据范围内"
        "每条定位记录均通过完整性门槛；仍不代表合同、供货或生产数量。",
        "",
        "## 定位字段",
        "",
        "- 每条记录包含楼栋、楼层、X/Y轴间及相对比例、主视图实例、"
        "主视图世界坐标和证据来源。",
        "- 单栋明确楼层可按范围展开；多栋共用图只有存在逐栋楼层调和证据"
        "才允许展开，防止重复或漏算。",
        "",
        "## 输出",
        "",
        f"- `{output_dir / 'V21阻尼器定位总表.csv'}`",
        f"- `{output_dir / 'V21阻尼器定位未决.csv'}`",
        f"- `{output_dir / 'V21阻尼器定位总表.json'}`",
    ]
    (output_dir / "V21阻尼器定位总表.md").write_text(
        "\n".join(location_lines) + "\n",
        encoding="utf-8",
    )
    lines = [
        "# V19 跨 DWG/跨视图阻尼器证据组",
        "",
        "## 结论",
        "",
        f"- 证据组：{len(group_rows)}；未分组记录：{len(unresolved_rows)}。",
        "- V19 只在楼栋、楼层、唯一主视图和主视图轴网均闭合时运行V12；"
        "只有主视图的范围也会生成V21设备定位记录。",
        "- 不同DWG或不同视图的单图数量不得相加；"
        "`absence_proven=false`。",
        "",
        "## 状态统计",
        "",
        "| 状态 | 组数 |",
        "| --- | ---: |",
    ]
    for status, count in sorted(status_counts.items()):
        lines.append(f"| `{status}` | {count} |")
    lines.extend(
        [
            "",
            "## 证据组",
            "",
            "| 组 | 楼栋 | 楼层 | 来源/角色 | 主视图 | 状态 | 模板/未归一 |",
            "| --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for row in group_rows:
        lines.append(
            f"| {row['group_id']} | {row['buildings']} | {row['floors']} | "
            f"{row['source_count']}/{row['view_roles']} | "
            f"{row['primary_file'] or '—'} | `{row['status']}` | "
            f"{row['physical_template_count'] or '—'}/"
            f"{row['unresolved_occurrence_count'] or '—'} |"
        )
    lines.extend(
        [
            "",
            "## 边界",
            "",
            "- `primary_layout_missing`：辅助视图有表达，但没有可计数主视图。",
            "- `primary_axis_evidence_missing`：有主视图，但轴网不足以建立物理位置。",
            "- `multiple_*_primary_views`：同一范围存在多个主视图，禁止自动选一个。",
            "- `single_primary_device_location_complete`：只有主视图，但单栋楼层"
            "与轴网定位已完整展开。",
            "- `device_location_floor_scope_unresolved`：楼层不能安全分配到楼栋，"
            "不得自动乘算。",
            "- `cross_view_identity_unresolved`：V12仍有跨视图出现无法映射到主视图。",
            "- `cross_view_identity_consistent_visibility_unverified`：身份已归一，"
            "但V13/V14可见性仍未闭合。",
            "- `cross_view_quantity_closed` 也只表示API证据范围内设计布置候选闭合，"
            "不是合同、供货或生产数量。",
            "",
            "## 输出",
            "",
            f"- `{output_dir / 'V19跨DWG证据组.csv'}`",
            f"- `{output_dir / 'V19未分组证据.csv'}`",
            f"- `{output_dir / 'V19跨DWG证据组.json'}`",
            f"- `{output_dir / 'V21阻尼器定位总表.csv'}`",
            f"- `{output_dir / 'V21阻尼器定位未决.csv'}`",
            f"- `{v23_output_dir / 'V23人工抽查索引.html'}`",
        ]
    )
    (output_dir / "V19跨DWG证据组.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "group_count": len(group_rows),
                "status_counts": dict(status_counts),
                "unresolved_scope_count": len(unresolved_scope),
                "load_issue_count": len(load_issues),
                "v21_location_registry_status": location_registry_status,
                "v23_evidence_package_status": v23_result.get("status"),
                "v23_evidence_package": str(v23_output_dir),
                "v24_review_package_status": v23_result.get("v24_status"),
                "v24_review_package": str(
                    v23_output_dir / "V24风险分层抽查"
                ),
                **location_metrics,
                "report": str(output_dir / "V19跨DWG证据组.md"),
                "location_report": str(
                    output_dir / "V21阻尼器定位总表.md"
                ),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
