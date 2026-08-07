#!/usr/bin/env python3
"""从 CADSYMBOLEXPORT6 输出中识别阻尼器块实例并生成可追溯计数。

本脚本不修改 DWG。它依赖实例句柄/路径而不是文字出现次数，并允许接入
既有图框候选和文字归属 CSV 来区分布置图、图例、说明和节点大样。
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from statistics import median
from typing import Any, Iterable


DAMPER_TERMS = re.compile(
    r"阻尼器|减震器|消能器|耗能器|黏滞|粘滞|屈曲约束支撑|防屈曲支撑|"
    r"\bVFD\b|\bLVFD\b|\bBRB\b|\bFBRB\b|\bMYD\b|\bXNQD\b",
    re.IGNORECASE,
)
MODEL_TOKEN = re.compile(
    r"(?<![A-Z0-9])(?:(?:L?VFD|F?BRB|MYD|XNQD|VAD)"
    r"[-_－—]?[A-Z0-9]+(?:[-_－—+×xX*][A-Z0-9.]+)*|"
    r"FD[-_－—][A-Z0-9]+(?:[×xX*][-+]?[0-9.]+)*|"
    r"SD[-_－—][A-Z0-9]+(?:型)?(?:[-_－—+×xX*][A-Z0-9.]+)*)(?![A-Z0-9])",
    re.IGNORECASE,
)
EXACT_LAYOUT_MARKER = re.compile(
    r"^(?:L?VFD|F?BRB|MYD|XNQD|VAD)$",
    re.IGNORECASE,
)
LAYOUT_TERMS = re.compile(
    r"(?:阻尼器|消能器|减震器|耗能器).{0,8}(?:平面)?布置图|"
    r"(?:平面)?布置图.{0,8}(?:阻尼器|消能器|减震器|耗能器)",
)
GENERIC_PLAN_TERMS = re.compile(r"(?:平面)?布置图")
STRUCTURAL_PLAN_TERMS = re.compile(r"(?:结构平面(?:布置)?图|结构布置图)")
BEAM_PLAN_TERMS = re.compile(r"(?:梁(?:平法|配筋|平面|布置).{0,4}图|梁平法施工图)")
COLUMN_PLAN_TERMS = re.compile(
    r"(?:(?:柱|墙柱)(?:平法|配筋|平面|布置).{0,4}图|柱平法施工图)"
)
WALL_PLAN_TERMS = re.compile(
    r"(?:剪力墙|墙体|墙身)(?:平法|配筋|平面|布置).{0,6}图"
)
SLAB_PLAN_TERMS = re.compile(r"(?:板(?:平法|配筋|平面|布置).{0,4}图|板平法施工图)")
STANDARD_FLOOR_TERMS = re.compile(r"标准层")
LEGEND_TERMS = re.compile(r"布置位置示意|位置示意|图例|阻尼器型式|符号说明")
TABLE_TERMS = re.compile(r"数量及参数表|参数及数量表|性能参数表|参数表|数量表")
DETAIL_TERMS = re.compile(r"大样|节点|预埋件|连接详图|安装详图|断面图")
NOTE_TERMS = re.compile(r"设计说明|技术说明|施工说明|维护|包装|运输|贮存")
DAMPER_TABLE_ANCHOR = re.compile(
    r"(?:阻尼器|减震器|消能器|耗能器).{0,12}(?:型式|数量|楼层)"
)
FLOOR_LABEL = re.compile(
    r"^(?:(?:B?\d+|RF)F|屋面(?:层)?|机房(?:层)?|"
    r"地下[一二三四五六七八九十百\d]+层|地上[一二三四五六七八九十百\d]+层|"
    r"负[一二三四五六七八九十百\d]+层)$",
    re.IGNORECASE,
)
X_DIRECTION = re.compile(r"^[XＸ]\s*(?:向|方向)$", re.IGNORECASE)
Y_DIRECTION = re.compile(r"^[YＹ]\s*(?:向|方向)$", re.IGNORECASE)
TOTAL_LABEL = re.compile(r"^合计$")
MODEL_TABLE_ANCHOR = re.compile(
    r"(?:黏滞|粘滞|阻尼器|减震器|消能器|耗能器).{0,16}"
    r"(?:数量及参数表|参数及数量表|数量表|参数表)"
)
MODEL_HEADER = re.compile(r"(?:阻尼器|减震器|消能器|耗能器).{0,8}(?:规格)?型号|规格型号")
QUANTITY_HEADER = re.compile(r"^数量(?:[（(].*?[）)])?$")
DIRECTION_CELL = re.compile(
    r"^[XＸYＹ](?:向|方向)(?:阻尼器|减震器|消能器|耗能器)?$",
    re.IGNORECASE,
)
PLACEMENT_WALL_LABEL = re.compile(
    r"^(?:黏滞|粘滞)?(?:阻尼器|减震器|消能器|耗能器)(?:支撑)?墙$"
)
FLOOR_NUMBER_TEXT = r"[一二三四五六七八九十百\d]+"
FLOOR_RANGE_SEPARATOR = r"[~～至—\-]"
FLOOR_TOKEN_TEXT = (
    r"(?:地下[一二三四五六七八九十百\d]+层|"
    r"地上[一二三四五六七八九十百\d]+层|"
    r"负[一二三四五六七八九十百\d]+层|"
    rf"{FLOOR_NUMBER_TEXT}{FLOOR_RANGE_SEPARATOR}{FLOOR_NUMBER_TEXT}层|"
    r"[一二三四五六七八九十百\d]+层|屋面层|机房层)"
)
FLOOR_LAYOUT_BLOCK = re.compile(
    rf"(?P<floor>{FLOOR_TOKEN_TEXT}).{{0,12}}"
    r"(?:阻尼器|减震器|消能器|耗能器)",
    re.IGNORECASE,
)
FLOOR_PLAN_TITLE = re.compile(
    rf"(?P<floor>{FLOOR_TOKEN_TEXT}).{{0,16}}"
    r"(?:结构)?平面(?:布置)?图",
    re.IGNORECASE,
)
PARAMETER_BLOCK = re.compile(
    r"(?:阻尼器|减震器|消能器|耗能器).{0,8}(?:性能)?参数"
)


@dataclass(frozen=True)
class Frame:
    frame_id: str
    space: str
    min_x: float
    min_y: float
    max_x: float
    max_y: float

    @property
    def area(self) -> float:
        return max(0.0, self.max_x - self.min_x) * max(0.0, self.max_y - self.min_y)

    @property
    def width(self) -> float:
        return max(0.0, self.max_x - self.min_x)

    @property
    def height(self) -> float:
        return max(0.0, self.max_y - self.min_y)

    def contains(self, space: str, x: float, y: float) -> bool:
        return (
            space == self.space
            and self.min_x <= x <= self.max_x
            and self.min_y <= y <= self.max_y
        )

    def overlap_area(
        self, space: str, min_x: float, min_y: float, max_x: float, max_y: float
    ) -> float:
        if space != self.space:
            return 0.0
        width = max(0.0, min(self.max_x, max_x) - max(self.min_x, min_x))
        height = max(0.0, min(self.max_y, max_y) - max(self.min_y, min_y))
        return width * height


@dataclass(frozen=True)
class FrameText:
    frame_id: str
    text: str
    x: float | None
    y: float | None
    handle: str
    block_path: str
    layer: str
    source: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="识别并统计 V6 中望 CAD 块实例中的阻尼器候选"
    )
    parser.add_argument("symbol_json", type=Path, help="CADSYMBOLEXPORT6 JSON")
    parser.add_argument(
        "--frames",
        type=Path,
        help="可选：图框候选 CSV，字段含 frame_id/space/region_min_x...；缺失时使用 min_x...",
    )
    parser.add_argument(
        "--frame-texts",
        type=Path,
        help="可选：文字按图框归属 CSV，用于识别布置图/图例/说明/大样角色",
    )
    parser.add_argument(
        "--oriented-texts",
        type=Path,
        help=(
            "可选：CADORIENTEDTEXTEXPORT7 JSON。仅用布置图内精确产品标记的"
            "世界方向核对 X/Y；方向不明确时保持待确认"
        ),
    )
    parser.add_argument(
        "--text-json",
        type=Path,
        help=(
            "可选：CADTEXTEXPORT5 JSON。用于识别楼层平面标题，并把相邻图纸中"
            "重复展示的同一楼层布置块按块定义去重"
        ),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="输出目录，必须位于本机非同步工作目录",
    )
    parser.add_argument("--prefix", default=None, help="输出文件前缀")
    parser.add_argument(
        "--min-score",
        type=int,
        default=7,
        help="进入自动计数的最低分；默认 7",
    )
    return parser.parse_args()


def read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8-sig") as stream:
        value = json.load(stream)
    if not isinstance(value, dict) or not isinstance(value.get("records"), list):
        raise ValueError(f"V6 JSON 缺少 records 数组：{path}")
    return value


def normalize_direction_text(value: str) -> str:
    text = compact_text(value).upper().replace("Ｘ", "X").replace("Ｙ", "Y")
    return text[:1] if text[:1] in {"X", "Y"} else ""


def classify_world_axis(axis_x: Any, axis_y: Any) -> tuple[str, float | None]:
    """把接近 WCS X/Y 的文字轴保守分类；斜向或无效轴不猜。

    当前证据是产品标记文字自身的世界方向。20°阈值允许常见的整体建筑
    小角度旋转；若建筑轴与 WCS 大角度偏转，应另行读取轴网方向，不能仅靠
    本函数把两个正交簇强行命名为 X/Y。
    """
    x = optional_float(axis_x)
    y = optional_float(axis_y)
    if x is None or y is None:
        return "", None
    length = (x * x + y * y) ** 0.5
    if length <= 1e-9:
        return "", None
    abs_x = abs(x / length)
    abs_y = abs(y / length)
    threshold = 0.9396926207859084  # cos(20°)
    if abs_x >= threshold:
        angle = math.degrees(math.acos(min(1.0, abs_x)))
        return "X", angle
    if abs_y >= threshold:
        angle = math.degrees(math.acos(min(1.0, abs_y)))
        return "Y", angle
    return "", min(
        math.degrees(math.acos(min(1.0, abs_x))),
        math.degrees(math.acos(min(1.0, abs_y))),
    )


def read_oriented_layout_markers(
    path: Path | None,
    frames: list[Frame],
    frame_texts: dict[str, list[FrameText]],
) -> list[dict[str, Any]]:
    if path is None:
        return []
    source = read_json(path)
    frame_roles = {
        frame.frame_id: classify_frame(frame_texts.get(frame.frame_id, []))
        for frame in frames
    }
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for record in source["records"]:
        marker = compact_text(normalize_text(record.get("text"))).upper()
        if not EXACT_LAYOUT_MARKER.fullmatch(marker):
            continue
        record_key = str(record.get("record_key") or "").strip()
        if not record_key or record_key in seen:
            continue
        seen.add(record_key)
        space = str(record.get("space") or "")
        x = float(record.get("x") or 0.0)
        y = float(record.get("y") or 0.0)
        frame = assign_frame(frames, space, x, y)
        frame_id = frame.frame_id if frame else ""
        role, role_evidence = frame_roles.get(frame_id, ("unknown", ""))
        direction, axis_angle = classify_world_axis(
            record.get("world_axis_x"), record.get("world_axis_y")
        )
        if role != "layout":
            decision = "excluded_non_layout"
        elif direction:
            decision = "direction_classified"
        else:
            decision = "direction_ambiguous"
        rows.append(
            {
                "decision": decision,
                "marker": marker,
                "direction": direction,
                "record_key": record_key,
                "handle": str(record.get("handle") or ""),
                "block_path": str(record.get("block_path") or ""),
                "space": space,
                "frame_id": frame_id,
                "frame_role": role,
                "frame_role_evidence": role_evidence,
                "x": x,
                "y": y,
                "world_rotation_radians": record.get("world_rotation_radians"),
                "world_axis_x": record.get("world_axis_x"),
                "world_axis_y": record.get("world_axis_y"),
                "axis_angle_to_wcs_degrees": axis_angle,
            }
        )
    return rows


def get_float(row: dict[str, str], primary: str, fallback: str) -> float:
    value = row.get(primary)
    if value in (None, ""):
        value = row.get(fallback)
    return float(value or 0.0)


def read_frames(path: Path | None) -> list[Frame]:
    if path is None:
        return []
    frames: list[Frame] = []
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            frames.append(
                Frame(
                    frame_id=row["frame_id"],
                    space=row.get("space", "*Model_Space"),
                    min_x=get_float(row, "region_min_x", "min_x"),
                    min_y=get_float(row, "region_min_y", "min_y"),
                    max_x=get_float(row, "region_max_x", "max_x"),
                    max_y=get_float(row, "region_max_y", "max_y"),
                )
            )
    return frames


def optional_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def read_frame_texts(path: Path | None) -> dict[str, list[FrameText]]:
    result: dict[str, list[FrameText]] = defaultdict(list)
    if path is None:
        return result
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            frame_id = (row.get("frame_id") or "").strip()
            text = (row.get("text") or "").strip()
            if frame_id and text:
                result[frame_id].append(
                    FrameText(
                        frame_id=frame_id,
                        text=text,
                        x=optional_float(row.get("x")),
                        y=optional_float(row.get("y")),
                        handle=(row.get("handle") or "").strip(),
                        block_path=(row.get("block_path") or "").strip(),
                        layer=(row.get("layer") or "").strip(),
                        source=(row.get("source") or row.get("origin") or "").strip(),
                    )
                )
    return result


def classify_frame(texts: Iterable[FrameText]) -> tuple[str, str]:
    joined = "\n".join(item.text for item in texts)
    # 明确的阻尼器布置标题优先于同图中的“图例/型式/示意”等局部文字。
    # 一张施工图可以同时包含布置区和局部图例，不能因局部词把整图降为图例。
    if LAYOUT_TERMS.search(joined):
        return "layout", first_match(LAYOUT_TERMS, joined)
    # 结构、梁、柱、板视图必须先于通用“布置图”分类，否则“墙柱平面布置图”
    # 会被误认为阻尼器专项布置图。结构平面可作为计数主视图；梁/柱/板图只
    # 登记跨视图候选，不能在未完成物理设备去重时直接与结构平面累加。
    if BEAM_PLAN_TERMS.search(joined):
        return "beam_plan", first_match(BEAM_PLAN_TERMS, joined)
    if COLUMN_PLAN_TERMS.search(joined):
        return "column_plan", first_match(COLUMN_PLAN_TERMS, joined)
    if WALL_PLAN_TERMS.search(joined):
        return "wall_plan", first_match(WALL_PLAN_TERMS, joined)
    if SLAB_PLAN_TERMS.search(joined):
        return "slab_plan", first_match(SLAB_PLAN_TERMS, joined)
    if STRUCTURAL_PLAN_TERMS.search(joined) and DAMPER_TERMS.search(joined):
        return "structural_plan", first_match(STRUCTURAL_PLAN_TERMS, joined)
    if GENERIC_PLAN_TERMS.search(joined) and DAMPER_TERMS.search(joined):
        return "layout", first_match(GENERIC_PLAN_TERMS, joined)
    if LEGEND_TERMS.search(joined):
        return "legend", first_match(LEGEND_TERMS, joined)
    if DETAIL_TERMS.search(joined):
        return "detail", first_match(DETAIL_TERMS, joined)
    if TABLE_TERMS.search(joined):
        return "table", first_match(TABLE_TERMS, joined)
    if NOTE_TERMS.search(joined):
        return "note", first_match(NOTE_TERMS, joined)
    return "unknown", ""


def first_match(pattern: re.Pattern[str], text: str) -> str:
    match = pattern.search(text)
    return match.group(0) if match else ""


def assign_frame(
    frames: list[Frame], space: str, x: float, y: float
) -> Frame | None:
    matches = [frame for frame in frames if frame.contains(space, x, y)]
    if not matches:
        return None
    return min(matches, key=lambda frame: frame.area)


def assign_frame_for_record(
    frames: list[Frame], record: dict[str, Any]
) -> tuple[Frame | None, float, float, str]:
    """用可见几何范围定位图框，插入点仅作为无范围时的兜底。

    设计院块的基点经常远离实际图形。直接使用 BlockReference.Position 会把
    图面内容误判为框外；V6 的世界范围更接近用户看到的对象位置。
    """
    space = str(record.get("space") or "")
    insertion_x = float(record.get("x") or 0.0)
    insertion_y = float(record.get("y") or 0.0)
    values = [
        optional_float(record.get(name))
        for name in ("min_x", "min_y", "max_x", "max_y")
    ]
    if bool(record.get("bounds_valid")) and all(value is not None for value in values):
        min_x, min_y, max_x, max_y = (float(value) for value in values)
        if max_x >= min_x and max_y >= min_y:
            center_x = (min_x + max_x) / 2.0
            center_y = (min_y + max_y) / 2.0
            overlaps = [
                (frame.overlap_area(space, min_x, min_y, max_x, max_y), frame)
                for frame in frames
            ]
            positive = [item for item in overlaps if item[0] > 0]
            if positive:
                _, frame = max(positive, key=lambda item: (item[0], -item[1].area))
                return frame, center_x, center_y, "world_bounds_overlap"
            frame = assign_frame(frames, space, center_x, center_y)
            if frame is not None:
                return frame, center_x, center_y, "world_bounds_center"
            return None, center_x, center_y, "world_bounds_center_unframed"

    frame = assign_frame(frames, space, insertion_x, insertion_y)
    return frame, insertion_x, insertion_y, "insertion_point"


def normalize_text(value: Any) -> str:
    if isinstance(value, list):
        value = " ".join(str(item) for item in value)
    return re.sub(r"\s+", " ", str(value or "")).strip()


def semantic_text(record: dict[str, Any]) -> str:
    return " | ".join(
        value
        for value in (
            normalize_text(record.get("block_name")),
            normalize_text(record.get("effective_name")),
            normalize_text(record.get("name_path")),
            normalize_text(record.get("layer")),
            normalize_text(record.get("attributes")),
            normalize_text(record.get("definition_texts")),
        )
        if value
    )


def model_hint(text: str) -> str:
    matches = [match.group(0).upper() for match in MODEL_TOKEN.finditer(text)]
    if matches:
        return matches[0]
    return ""


def exact_layout_markers(record: dict[str, Any]) -> list[str]:
    values = record.get("definition_texts")
    if not isinstance(values, list):
        return []
    markers: list[str] = []
    for value in values:
        text = normalize_text(value).upper()
        if EXACT_LAYOUT_MARKER.fullmatch(text):
            markers.append(text)
    return markers


def floor_key_from_text(value: Any, pattern: re.Pattern[str]) -> str:
    match = pattern.search(compact_text(normalize_text(value)))
    return compact_text(match.group("floor")) if match else ""


def floor_sort_key(value: str) -> tuple[int, int, str]:
    text = compact_text(value)
    if text in {"屋面", "屋面层"}:
        return (2, 10000, text)
    if text in {"机房", "机房层"}:
        return (2, 10001, text)
    negative = text.startswith("地下") or text.startswith("负")
    core = re.sub(r"^(?:地下|地上|负)", "", text).removesuffix("层")
    range_match = re.fullmatch(
        rf"(?P<start>{FLOOR_NUMBER_TEXT}){FLOOR_RANGE_SEPARATOR}"
        rf"(?P<end>{FLOOR_NUMBER_TEXT})",
        core,
    )
    if range_match:
        core = range_match.group("start")
    if core.isdigit():
        number = int(core)
    else:
        digits = {
            "零": 0,
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
        if core == "十":
            number = 10
        elif "十" in core:
            left, right = core.split("十", 1)
            number = (digits.get(left, 1) * 10) + digits.get(right, 0)
        else:
            number = digits.get(core, 9999)
    return (0 if negative else 1, -number if negative else number, text)


def floor_number(value: str) -> int | None:
    core = compact_text(value)
    if core.isdigit():
        return int(core)
    digits = {
        "零": 0,
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
    if core == "十":
        return 10
    if "十" in core:
        left, right = core.split("十", 1)
        return (digits.get(left, 1) * 10) + digits.get(right, 0)
    return digits.get(core)


def floor_applicability_count(value: str) -> int:
    text = compact_text(value).removesuffix("层")
    match = re.fullmatch(
        rf"(?P<start>{FLOOR_NUMBER_TEXT}){FLOOR_RANGE_SEPARATOR}"
        rf"(?P<end>{FLOOR_NUMBER_TEXT})",
        text,
    )
    if not match:
        return 1
    start = floor_number(match.group("start"))
    end = floor_number(match.group("end"))
    if start is None or end is None or end < start:
        return 1
    return end - start + 1


def read_floor_plan_titles(path: Path | None) -> list[dict[str, Any]]:
    if path is None:
        return []
    source = read_json(path)
    results: list[dict[str, Any]] = []
    for record in source["records"]:
        floor = floor_key_from_text(record.get("text"), FLOOR_PLAN_TITLE)
        if not floor:
            continue
        results.append(
            {
                "floor": floor,
                "text": normalize_text(record.get("text")),
                "x": float(record.get("x") or 0.0),
                "y": float(record.get("y") or 0.0),
                "space": str(record.get("space") or "*Model_Space"),
                "handle": str(
                    record.get("handle")
                    or record.get("entity_handle")
                    or record.get("record_key")
                    or ""
                ),
            }
        )
    return results


def read_oriented_wall_labels(path: Path | None) -> list[dict[str, Any]]:
    """读取墙式阻尼器的逐位置文字证据。

    这条路线只在布置容器本身明确带有楼层和阻尼器语义时使用。普通说明中
    出现“阻尼器支撑墙”不会自动进入计数。
    """
    if path is None:
        return []
    source = read_json(path)
    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    for record in source["records"]:
        text = compact_text(normalize_text(record.get("text")))
        if not PLACEMENT_WALL_LABEL.fullmatch(text):
            continue
        record_key = str(record.get("record_key") or "").strip()
        if not record_key or record_key in seen:
            continue
        seen.add(record_key)
        root_handle = record_key.split("/", 1)[0]
        direction, axis_angle = classify_world_axis(
            record.get("world_axis_x"), record.get("world_axis_y")
        )
        results.append(
            {
                "decision": (
                    "direction_classified" if direction else "direction_ambiguous"
                ),
                "marker": text,
                "direction": direction,
                "record_key": record_key,
                "root_instance_handle": root_handle,
                "handle": str(record.get("handle") or ""),
                "block_path": str(record.get("block_path") or ""),
                "space": str(record.get("space") or ""),
                "x": float(record.get("x") or 0.0),
                "y": float(record.get("y") or 0.0),
                "world_rotation_radians": record.get("world_rotation_radians"),
                "world_axis_x": record.get("world_axis_x"),
                "world_axis_y": record.get("world_axis_y"),
                "axis_angle_to_wcs_degrees": axis_angle,
            }
        )
    return results


def record_center_y(record: dict[str, Any]) -> float:
    min_y = optional_float(record.get("min_y"))
    max_y = optional_float(record.get("max_y"))
    if bool(record.get("bounds_valid")) and min_y is not None and max_y is not None:
        return (min_y + max_y) / 2.0
    return float(record.get("y") or 0.0)


def parameter_models_by_floor(
    records: list[dict[str, Any]], titles: list[dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    """把每张平面图旁的单表型号按最近楼层标题关联。

    这里只提取型号，不从无数量列的性能参数表臆造数量。若同表出现多个型号，
    调用方会保留为多型号待确认。
    """
    candidates: list[dict[str, Any]] = []
    for record in records:
        if str(record.get("root_instance_handle") or "") != str(
            record.get("instance_handle") or ""
        ):
            continue
        name = " | ".join(
            normalize_text(record.get(field))
            for field in ("block_name", "effective_name", "name_path")
        )
        if not PARAMETER_BLOCK.search(name):
            continue
        values = record.get("definition_texts")
        if not isinstance(values, list):
            continue
        models = sorted(
            {
                model_hint(compact_text(normalize_text(value)))
                for value in values
                if model_hint(compact_text(normalize_text(value)))
            }
        )
        if not models:
            continue
        candidates.append(
            {
                "instance_handle": str(record.get("instance_handle") or ""),
                "models": models,
                "y": record_center_y(record),
            }
        )

    result: dict[str, dict[str, Any]] = {}
    for title in titles:
        same_space = candidates
        if not same_space:
            continue
        closest = min(same_space, key=lambda item: abs(item["y"] - title["y"]))
        current = result.get(title["floor"])
        distance = abs(closest["y"] - title["y"])
        if current is None or distance < current["distance"]:
            result[title["floor"]] = {
                **closest,
                "distance": distance,
                "title_handle": title["handle"],
            }
    return result


def analyze_deduplicated_floor_layouts(
    records: list[dict[str, Any]],
    text_json: Path | None,
    oriented_texts: Path | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """按楼层块定义去重相邻图纸中的重复展示，并统计逐位置方向。

    有些设计院在 N+1 层平面继续展示 N 层阻尼器，以表达上下层连接关系。
    同一块定义因此可能出现两次。计数主键采用“楼层 + definition_handle”，
    而不是根插入句柄；同时保留所有展示句柄用于回查。
    """
    titles = read_floor_plan_titles(text_json)
    wall_rows = read_oriented_wall_labels(oriented_texts)
    wall_by_root: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in wall_rows:
        wall_by_root[row["root_instance_handle"]].append(row)

    parameter_by_floor = parameter_models_by_floor(records, titles)
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        if str(record.get("root_instance_handle") or "") != str(
            record.get("instance_handle") or ""
        ):
            continue
        names = " | ".join(
            normalize_text(record.get(field))
            for field in ("block_name", "effective_name", "name_path")
        )
        floor = floor_key_from_text(names, FLOOR_LAYOUT_BLOCK)
        if not floor:
            continue
        values = record.get("definition_texts")
        if not isinstance(values, list):
            continue
        placement_labels = [
            compact_text(normalize_text(value))
            for value in values
            if PLACEMENT_WALL_LABEL.fullmatch(
                compact_text(normalize_text(value))
            )
        ]
        if not placement_labels:
            continue
        definition_key = str(record.get("definition_handle") or "").strip()
        if not definition_key:
            definition_key = (
                str(record.get("geometry_signature") or "")
                + "|"
                + str(record.get("block_name") or "")
            )
        grouped[(floor, definition_key)].append(
            {**record, "_placement_label_count": len(placement_labels)}
        )

    results: list[dict[str, Any]] = []
    selected_direction_rows: list[dict[str, Any]] = []
    titles_by_floor: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for title in titles:
        titles_by_floor[title["floor"]].append(title)

    for (floor, definition_key), references in sorted(grouped.items()):
        title_candidates = titles_by_floor.get(floor, [])

        def reference_distance(record: dict[str, Any]) -> float:
            root = str(record.get("instance_handle") or "")
            roots_rows = wall_by_root.get(root, [])
            evidence_y = (
                median(row["y"] for row in roots_rows)
                if roots_rows
                else record_center_y(record)
            )
            if not title_candidates:
                return 0.0
            return min(abs(evidence_y - title["y"]) for title in title_candidates)

        canonical = min(
            references,
            key=lambda record: (
                reference_distance(record),
                str(record.get("instance_handle") or ""),
            ),
        )
        canonical_handle = str(canonical.get("instance_handle") or "")
        canonical_rows = wall_by_root.get(canonical_handle, [])
        direction_counts = Counter(
            row["direction"]
            for row in canonical_rows
            if row["decision"] == "direction_classified"
        )
        ambiguous_count = sum(
            row["decision"] == "direction_ambiguous"
            for row in canonical_rows
        )
        placement_count = int(canonical["_placement_label_count"])
        applicable_floor_count = floor_applicability_count(floor)
        oriented_count = len(canonical_rows)
        if oriented_count == placement_count and ambiguous_count == 0:
            status = "deduplicated_floor_layout_consistent"
        elif oriented_count == placement_count:
            status = "direction_unresolved"
        else:
            status = "placement_evidence_mismatch"

        parameter = parameter_by_floor.get(floor, {})
        models = list(parameter.get("models") or [])
        model = models[0] if len(models) == 1 else ""
        title = (
            min(
                title_candidates,
                key=lambda item: abs(
                    (
                        median(row["y"] for row in canonical_rows)
                        if canonical_rows
                        else record_center_y(canonical)
                    )
                    - item["y"]
                ),
            )
            if title_candidates
            else None
        )
        result = {
            "floor": floor,
            "status": status,
            "definition_handle": str(canonical.get("definition_handle") or ""),
            "block_name": str(canonical.get("block_name") or ""),
            "canonical_root_handle": canonical_handle,
            "presentation_root_handles": ";".join(
                sorted(str(record.get("instance_handle") or "") for record in references)
            ),
            "presentation_reference_count": len(references),
            "placement_label": (
                canonical_rows[0]["marker"]
                if canonical_rows
                else "阻尼器支撑墙"
            ),
            "placement_count": placement_count,
            "applicable_floor_count": applicable_floor_count,
            "expanded_quantity_candidate": (
                placement_count * applicable_floor_count
            ),
            "oriented_label_count": oriented_count,
            "x_quantity": direction_counts.get("X", 0),
            "y_quantity": direction_counts.get("Y", 0),
            "expanded_x_quantity": (
                direction_counts.get("X", 0) * applicable_floor_count
            ),
            "expanded_y_quantity": (
                direction_counts.get("Y", 0) * applicable_floor_count
            ),
            "ambiguous_direction_count": ambiguous_count,
            "model": model,
            "model_candidates": ";".join(models),
            "parameter_table_handle": str(parameter.get("instance_handle") or ""),
            "title_text": str(title.get("text") if title else ""),
            "title_handle": str(title.get("handle") if title else ""),
            "title_x": title.get("x") if title else None,
            "title_y": title.get("y") if title else None,
            "canonical_evidence_y": (
                median(row["y"] for row in canonical_rows)
                if canonical_rows
                else record_center_y(canonical)
            ),
            "dedupe_basis": (
                f"同一楼层与块定义 {definition_key} 仅计一次；"
                f"{len(references)}个根插入均保留为展示证据"
            ),
        }
        results.append(result)
        for row in canonical_rows:
            selected_direction_rows.append(
                {
                    **row,
                    "floor": floor,
                    "model": model,
                    "canonical_root_handle": canonical_handle,
                    "definition_handle": result["definition_handle"],
                }
            )

    results.sort(key=lambda row: floor_sort_key(str(row["floor"])))
    selected_direction_rows.sort(
        key=lambda row: (
            floor_sort_key(str(row["floor"])),
            str(row["direction"]),
            str(row["record_key"]),
        )
    )
    return results, selected_direction_rows


def is_dimensioned_detail(record: dict[str, Any]) -> bool:
    """识别带尺寸标注的说明/大样容器，而不是布置图中的单个产品符号。"""
    signature = str(record.get("geometry_signature") or "")
    return bool(re.search(r"(?:^|;)[A-Za-z0-9_]*Dimension=", signature))


def direct_block_semantic(record: dict[str, Any]) -> bool:
    """只检查当前块名，不把祖先 name_path 的语义继承给普通子构件。"""
    value = " ".join(
        [
            normalize_text(record.get("block_name")),
            normalize_text(record.get("effective_name")),
        ]
    )
    return bool(DAMPER_TERMS.search(value))


def record_bounds_area(record: dict[str, Any]) -> float | None:
    if not bool(record.get("bounds_valid")):
        return None
    values = [
        optional_float(record.get(name))
        for name in ("min_x", "min_y", "max_x", "max_y")
    ]
    if any(value is None for value in values):
        return None
    min_x, min_y, max_x, max_y = (float(value) for value in values)
    if max_x <= min_x or max_y <= min_y:
        return None
    return (max_x - min_x) * (max_y - min_y)


def semantic_leaf_symbol_evidence(
    records: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """识别“阻尼器布置容器块”中的同构叶子设备实例。

    一些设计院把整层布置做成一个含阻尼器语义的大块，单台设备再做成同名
    匿名子块。此时叶子块没有文字、型号或专用图层。为避免把任意语义子块
    都当成设备，本规则同时要求：

    - 当前块和直接父块的块名均含阻尼器语义；
    - 当前块无任何子实例；
    - 同一父块下至少两个叶子具有相同非空几何签名；
    - 叶子世界范围面积不超过父容器的 5%；
    - 父块和叶子均不是带尺寸的大样。

    图纸角色仍由后续逻辑限定为阻尼器布置图或含阻尼器的结构平面图。
    """
    by_key = {
        str(record.get("instance_key") or ""): record
        for record in records
        if str(record.get("instance_key") or "")
    }
    children_by_parent: dict[str, list[dict[str, Any]]] = defaultdict(list)
    child_count: Counter[str] = Counter()
    for record in records:
        parent_key = str(record.get("parent_instance_key") or "")
        if parent_key:
            children_by_parent[parent_key].append(record)
            child_count[parent_key] += 1

    result: dict[str, dict[str, Any]] = {}
    for parent_key, children in children_by_parent.items():
        parent = by_key.get(parent_key)
        if (
            parent is None
            or not direct_block_semantic(parent)
            or is_dimensioned_detail(parent)
        ):
            continue
        parent_area = record_bounds_area(parent)
        if parent_area is None:
            continue

        signature_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for child in children:
            child_key = str(child.get("instance_key") or "")
            signature = str(child.get("geometry_signature") or "")
            if (
                not child_key
                or child_count[child_key] > 0
                or not signature
                or not direct_block_semantic(child)
                or is_dimensioned_detail(child)
            ):
                continue
            signature_groups[signature].append(child)

        for signature, siblings in signature_groups.items():
            if len(siblings) < 2:
                continue
            for child in siblings:
                child_area = record_bounds_area(child)
                if child_area is None:
                    continue
                area_ratio = child_area / parent_area
                if area_ratio > 0.05:
                    continue
                child_key = str(child.get("instance_key") or "")
                result[child_key] = {
                    "semantic_sibling_count": len(siblings),
                    "semantic_parent_key": parent_key,
                    "semantic_parent_block": str(parent.get("block_name") or ""),
                    "semantic_leaf_area_ratio": area_ratio,
                    "semantic_leaf_signature": signature,
                }
    return result


def analyze_records(
    records: list[dict[str, Any]],
    frames: list[Frame],
    frame_texts: dict[str, list[FrameText]],
    min_score: int,
) -> list[dict[str, Any]]:
    signature_frequency = Counter(
        str(record.get("geometry_signature") or "")
        for record in records
        if record.get("geometry_signature")
    )
    frame_roles = {
        frame.frame_id: classify_frame(frame_texts.get(frame.frame_id, []))
        for frame in frames
    }
    semantic_leaf_evidence = semantic_leaf_symbol_evidence(records)

    candidates: list[dict[str, Any]] = []
    for record in records:
        instance_key = str(record.get("instance_key") or "")
        text = semantic_text(record)
        semantic_hit = bool(DAMPER_TERMS.search(text))
        layer_hit = bool(DAMPER_TERMS.search(normalize_text(record.get("layer"))))
        model = model_hint(text)
        markers = exact_layout_markers(record)
        marker_types = Counter(markers)
        marker_count = len(markers)
        marker_type = (
            next(iter(marker_types))
            if len(marker_types) == 1
            else "/".join(sorted(marker_types))
        )
        signature = str(record.get("geometry_signature") or "")
        repeated = bool(signature and signature_frequency[signature] >= 2)
        dimensioned_detail = is_dimensioned_detail(record)
        leaf_evidence = semantic_leaf_evidence.get(instance_key)
        semantic_leaf_symbol = leaf_evidence is not None

        if (
            not semantic_hit
            and not layer_hit
            and not model
            and not marker_count
            and not semantic_leaf_symbol
        ):
            continue

        insertion_x = float(record.get("x") or 0.0)
        insertion_y = float(record.get("y") or 0.0)
        space = str(record.get("space") or "")
        frame, evidence_x, evidence_y, location_method = assign_frame_for_record(
            frames, record
        )
        frame_id = frame.frame_id if frame else ""
        role, role_evidence = frame_roles.get(frame_id, ("unknown", ""))

        score = 0
        reasons: list[str] = []
        if model:
            score += 5
            reasons.append(f"型号语义:{model}")
        if semantic_hit:
            score += 3
            reasons.append("块名/属性/块内文字含阻尼器语义")
        if layer_hit:
            score += 2
            reasons.append("图层含阻尼器语义")
        if marker_count:
            score += 5
            marker_summary = "/".join(
                f"{name}×{count}" for name, count in sorted(marker_types.items())
            )
            reasons.append(f"块内精确布置标记:{marker_summary}")
        if semantic_leaf_symbol:
            score += 7
            reasons.append(
                "语义容器内同构叶子设备:"
                f"同父签名实例×{leaf_evidence['semantic_sibling_count']}，"
                f"叶子/父块面积比={leaf_evidence['semantic_leaf_area_ratio']:.6f}"
            )
        if repeated:
            score += 2
            reasons.append(f"几何签名重复:{signature_frequency[signature]}")
        if dimensioned_detail:
            score -= 8
            reasons.append("块内含尺寸标注，按局部说明/大样容器排除")
        if role in {"layout", "structural_plan"}:
            score += 5
            reasons.append(
                "位于阻尼器专项布置图"
                if role == "layout"
                else "位于含阻尼器标记的结构平面图"
            )
        elif role in {"legend", "detail", "table", "note"}:
            score -= 6
            reasons.append(f"位于非计数图纸角色:{role}")
        elif role in {"beam_plan", "column_plan", "wall_plan", "slab_plan"}:
            reasons.append(f"位于跨视图候选:{role}，未完成物理设备去重")
        elif frames and frame is None:
            score -= 2
            reasons.append("未归属已识别图框")
        if location_method.startswith("world_bounds"):
            reasons.append("按世界范围定位图框")

        if dimensioned_detail:
            decision = "excluded_detail_container"
        elif role in {"legend", "detail", "table", "note"}:
            decision = "excluded_nonlayout"
        elif role in {"layout", "structural_plan"} and not (
            model or layer_hit or marker_count or semantic_leaf_symbol
        ):
            decision = "excluded_context_container"
            reasons.append("仅说明文字命中，不作为产品布置实例")
        elif role in {"layout", "structural_plan"} and score >= min_score:
            decision = "counted"
        elif not frames:
            decision = "candidate_needs_frame"
        else:
            decision = "manual_review"

        count_value = marker_count if decision == "counted" and marker_count else 1
        candidates.append(
            {
                "decision": decision,
                "score": score,
                "model_hint": model,
                "marker_type": marker_type,
                "marker_count": marker_count,
                "count_value": count_value,
                "instance_key": instance_key,
                "instance_handle": str(record.get("instance_handle") or ""),
                "root_instance_handle": str(record.get("root_instance_handle") or ""),
                "parent_instance_key": str(record.get("parent_instance_key") or ""),
                "block_name": str(record.get("block_name") or ""),
                "effective_name": str(record.get("effective_name") or ""),
                "name_path": str(record.get("name_path") or ""),
                "space": space,
                "layer": str(record.get("layer") or ""),
                "x": evidence_x,
                "y": evidence_y,
                "insertion_x": insertion_x,
                "insertion_y": insertion_y,
                "evidence_x": evidence_x,
                "evidence_y": evidence_y,
                "location_method": location_method,
                "min_x": optional_float(record.get("min_x")),
                "min_y": optional_float(record.get("min_y")),
                "max_x": optional_float(record.get("max_x")),
                "max_y": optional_float(record.get("max_y")),
                "frame_id": frame_id,
                "frame_role": role,
                "frame_role_evidence": role_evidence,
                "geometry_signature": signature,
                "signature_frequency": signature_frequency[signature] if signature else 0,
                "dimensioned_detail": dimensioned_detail,
                "semantic_leaf_symbol": semantic_leaf_symbol,
                "semantic_sibling_count": (
                    leaf_evidence["semantic_sibling_count"] if leaf_evidence else 0
                ),
                "semantic_parent_key": (
                    leaf_evidence["semantic_parent_key"] if leaf_evidence else ""
                ),
                "semantic_parent_block": (
                    leaf_evidence["semantic_parent_block"] if leaf_evidence else ""
                ),
                "semantic_leaf_area_ratio": (
                    leaf_evidence["semantic_leaf_area_ratio"] if leaf_evidence else None
                ),
                "reasons": "；".join(reasons),
                "semantic_preview": text[:300],
            }
        )

    # 带尺寸的局部说明/大样容器及其后代都不计入布置数量。
    detail_container_keys = {
        row["instance_key"]
        for row in candidates
        if row["decision"] == "excluded_detail_container"
    }
    for row in candidates:
        if any(
            row["instance_key"].startswith(container_key + "/")
            for container_key in detail_container_keys
        ):
            row["decision"] = "excluded_detail_member"
            row["reasons"] += "；父实例是带尺寸的局部说明/大样容器"

    # 若父子实例都命中同一语义，优先保留更深、定位更具体的实例。
    candidate_keys = {row["instance_key"] for row in candidates}
    for row in candidates:
        prefix = row["instance_key"] + "/"
        descendants = [
            key for key in candidate_keys if key.startswith(prefix) and key != row["instance_key"]
        ]
        if descendants and row["decision"] in {"counted", "manual_review", "candidate_needs_frame"}:
            row["decision"] = "excluded_parent_container"
            row["count_value"] = 0
            row["reasons"] += "；存在更具体的语义子实例"

    # 完全重叠的同构实例只计一次。该规则只处理具有有效世界边界、相同图框、
    # 相同块名和相同几何签名的精确重复展示；不同位置、不同型号或只有坐标
    # 接近的实例均不会在这里合并。
    exact_placement_groups: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(
        list
    )
    for row in candidates:
        if row["decision"] != "counted" or not row.get("geometry_signature"):
            continue
        bounds = [row.get(name) for name in ("min_x", "min_y", "max_x", "max_y")]
        if any(value is None for value in bounds):
            continue
        placement_key = (
            row.get("frame_id") or "",
            row.get("space") or "",
            normalize_text(row.get("block_name")),
            normalize_text(row.get("effective_name")),
            row.get("geometry_signature") or "",
            *(round(float(value), 6) for value in bounds),
        )
        exact_placement_groups[placement_key].append(row)
    for group in exact_placement_groups.values():
        if len(group) < 2:
            continue
        canonical = min(group, key=lambda row: str(row.get("instance_key") or ""))
        duplicate_keys = sorted(
            str(row.get("instance_key") or "")
            for row in group
            if row is not canonical
        )
        canonical["reasons"] += (
            f"；发现{len(group)}个完全重叠同构实例，仅保留此实例计数，"
            f"其余句柄路径:{'/'.join(duplicate_keys)}"
        )
        for row in group:
            if row is canonical:
                continue
            row["decision"] = "excluded_exact_duplicate_placement"
            row["count_value"] = 0
            row["reasons"] += (
                "；与实例"
                f"{canonical['instance_key']}在同一图框具有相同块名、几何签名和世界边界"
            )

    return sorted(
        candidates,
        key=lambda row: (
            row["frame_id"],
            row["decision"],
            row["model_hint"],
            row["instance_key"],
        ),
    )


def compact_text(value: str) -> str:
    return re.sub(r"\s+", "", value or "")


def row_quantity(row: dict[str, Any]) -> int:
    if row.get("decision") != "counted":
        return 0
    try:
        return max(1, int(row.get("count_value") or 1))
    except (TypeError, ValueError):
        return 1


def row_model_label(row: dict[str, Any]) -> str:
    if row.get("model_hint"):
        return str(row["model_hint"])
    if row.get("marker_type"):
        return f"{row['marker_type']}（图面通用标记，未分型）"
    return "(型号未解析)"


def parse_quantity(value: str) -> int | None:
    text = compact_text(value).replace(",", "").replace("，", "")
    match = re.fullmatch(r"([0-9]+)(?:\.0+)?(?:套|个|只)?", text)
    if not match:
        return None
    return int(match.group(1))


def is_floor_label(value: str) -> bool:
    return bool(FLOOR_LABEL.fullmatch(compact_text(value)))


def table_group_for_anchor(
    entries: list[FrameText], anchor: FrameText, frame: Frame
) -> list[FrameText]:
    if anchor.block_path:
        same_block = [
            item for item in entries if item.block_path == anchor.block_path
        ]
        if len(same_block) >= 4:
            return same_block
    if anchor.x is None or anchor.y is None:
        return []
    x_radius = max(frame.width * 0.12, 1.0)
    y_radius = max(frame.height * 0.20, 1.0)
    return [
        item
        for item in entries
        if item.x is not None
        and item.y is not None
        and abs(item.x - anchor.x) <= x_radius
        and abs(item.y - anchor.y) <= y_radius
    ]


def closest_quantity_cell(
    entries: list[FrameText],
    target_x: float,
    target_y: float,
    x_tolerance: float,
    y_tolerance: float,
    excluded_handles: set[str] | None = None,
) -> tuple[FrameText, int] | None:
    excluded_handles = excluded_handles or set()
    matches: list[tuple[float, FrameText, int]] = []
    for item in entries:
        if item.x is None or item.y is None or item.handle in excluded_handles:
            continue
        quantity = parse_quantity(item.text)
        if quantity is None:
            continue
        dx = abs(item.x - target_x)
        dy = abs(item.y - target_y)
        if dx <= x_tolerance and dy <= y_tolerance:
            matches.append((dx + dy * 3.0, item, quantity))
    if not matches:
        return None
    _, item, quantity = min(matches, key=lambda match: match[0])
    return item, quantity


def parse_floor_quantity_tables(
    frame: Frame, entries: list[FrameText]
) -> list[dict[str, Any]]:
    anchors = [
        item
        for item in entries
        if DAMPER_TABLE_ANCHOR.search(compact_text(item.text))
        and item.x is not None
        and item.y is not None
    ]
    parsed_candidates: list[dict[str, Any]] = []
    for anchor in anchors:
        group = table_group_for_anchor(entries, anchor, frame)
        x_headers = [
            item
            for item in group
            if X_DIRECTION.fullmatch(compact_text(item.text))
            and item.x is not None
            and item.y is not None
        ]
        y_headers = [
            item
            for item in group
            if Y_DIRECTION.fullmatch(compact_text(item.text))
            and item.x is not None
            and item.y is not None
        ]
        header_pairs = [
            (x_header, y_header)
            for x_header in x_headers
            for y_header in y_headers
            if 1e-6
            < abs(x_header.x - y_header.x)
            <= max(frame.width * 0.05, 1.0)
        ]
        if not header_pairs:
            continue
        x_header, y_header = min(
            header_pairs,
            key=lambda pair: (
                abs(pair[0].y - pair[1].y) * 10.0
                + abs((pair[0].x + pair[1].x) / 2.0 - anchor.x)
                + abs((pair[0].y + pair[1].y) / 2.0 - anchor.y)
            ),
        )
        column_spacing = abs(x_header.x - y_header.x)
        header_y = (x_header.y + y_header.y) / 2.0
        total_headers = [
            item
            for item in group
            if TOTAL_LABEL.fullmatch(compact_text(item.text))
            and item.x is not None
            and item.y is not None
            and item.x > max(x_header.x, y_header.x)
            and abs(item.y - header_y)
            <= max(frame.height * 0.01, column_spacing * 0.5, 1.0)
        ]
        if not total_headers:
            continue
        total_header = min(
            total_headers,
            key=lambda item: (
                abs(item.y - header_y),
                abs(item.x - max(x_header.x, y_header.x) - column_spacing),
            ),
        )

        # 同一说明页常并排、上下放置多张数量表。以 X/Y/合计三列表头
        # 确定横向范围，并以同列下一张表标题作为纵向下边界，避免跨表混入。
        x_min = min(x_header.x, y_header.x) - column_spacing * 1.6
        x_max = total_header.x + column_spacing * 0.8
        lower_anchors = [
            item
            for item in anchors
            if item is not anchor
            and item.y is not None
            and item.x is not None
            and item.y < anchor.y
            and x_min <= item.x <= x_max
        ]
        lower_anchor_y = (
            max(item.y for item in lower_anchors)
            if lower_anchors
            else anchor.y - max(frame.height * 0.20, 1.0)
        )
        upper_y = anchor.y + max(frame.height * 0.03, 1.0)
        group = [
            item
            for item in entries
            if item.x is not None
            and item.y is not None
            and x_min <= item.x <= x_max
            and lower_anchor_y < item.y <= upper_y
        ]

        floor_x_tolerance = max(frame.width * 0.05, column_spacing * 1.5, 1.0)
        floor_entries = [
            item
            for item in group
            if is_floor_label(item.text)
            and item.x is not None
            and item.y is not None
            and abs(item.x - anchor.x) <= floor_x_tolerance
        ]
        if not floor_entries:
            continue

        y_values = sorted({item.y for item in floor_entries})
        y_gaps = [
            right - left
            for left, right in zip(y_values, y_values[1:])
            if right - left > 1e-6
        ]
        row_tolerance = max(
            1.0,
            min(
                max(frame.height * 0.01, 1.0),
                median(y_gaps) * 0.20 if y_gaps else max(frame.height * 0.005, 1.0),
            ),
        )
        column_tolerance = max(
            column_spacing * 0.45, frame.width * 0.005, 1.0
        )

        floor_rows: list[dict[str, Any]] = []
        for floor in sorted(floor_entries, key=lambda item: item.y, reverse=True):
            x_cell = closest_quantity_cell(
                group,
                x_header.x,
                floor.y,
                column_tolerance,
                row_tolerance,
            )
            excluded = {x_cell[0].handle} if x_cell and x_cell[0].handle else set()
            y_cell = closest_quantity_cell(
                group,
                y_header.x,
                floor.y,
                column_tolerance,
                row_tolerance,
                excluded,
            )
            if x_cell is None or y_cell is None:
                continue
            floor_rows.append(
                {
                    "floor": compact_text(floor.text),
                    "x_quantity": x_cell[1],
                    "y_quantity": y_cell[1],
                    "row_total": x_cell[1] + y_cell[1],
                    "floor_handle": floor.handle,
                    "x_handle": x_cell[0].handle,
                    "y_handle": y_cell[0].handle,
                    "y": floor.y,
                }
            )
        if not floor_rows:
            continue

        total_value: int | None = None
        total_handle = ""
        floor_column_x = median(item.x for item in floor_entries)
        total_labels = [
            item
            for item in group
            if TOTAL_LABEL.fullmatch(compact_text(item.text))
            and item.x is not None
            and item.y is not None
            and abs(item.x - floor_column_x) <= floor_x_tolerance
            and item.y < min(floor.y for floor in floor_entries)
        ]
        for total_label in sorted(total_labels, key=lambda item: item.y):
            total_cell = closest_quantity_cell(
                group,
                total_header.x,
                total_label.y,
                column_tolerance,
                row_tolerance,
            )
            if total_cell is not None:
                total_value = total_cell[1]
                total_handle = total_cell[0].handle
                break

        parsed_candidates.append(
            {
                "frame_id": frame.frame_id,
                "anchor_text": anchor.text,
                "anchor_handle": anchor.handle,
                "table_block_path": anchor.block_path,
                "x_header_handle": x_header.handle,
                "y_header_handle": y_header.handle,
                "total_header_handle": total_header.handle,
                "floor_rows": floor_rows,
                "table_total": total_value,
                "table_total_handle": total_handle,
            }
        )
    return parsed_candidates


def parse_floor_quantity_table(
    frame: Frame, entries: list[FrameText]
) -> dict[str, Any] | None:
    parsed_candidates = parse_floor_quantity_tables(frame, entries)
    if not parsed_candidates:
        return None
    return max(
        parsed_candidates,
        key=lambda candidate: (
            len(candidate["floor_rows"]),
            candidate["table_total"] is not None,
        ),
    )


def reconcile_floor_quantities(
    rows: list[dict[str, Any]],
    frames: list[Frame],
    frame_texts: dict[str, list[FrameText]],
) -> list[dict[str, Any]]:
    frame_by_id = {frame.frame_id: frame for frame in frames}
    counted_by_frame: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row["decision"] == "counted" and row["frame_id"]:
            counted_by_frame[row["frame_id"]].append(row)

    results: list[dict[str, Any]] = []
    for frame_id, counted in sorted(counted_by_frame.items()):
        frame = frame_by_id.get(frame_id)
        if frame is None:
            continue
        table = parse_floor_quantity_table(
            frame, frame_texts.get(frame_id, [])
        )
        if table is None:
            continue
        position_count = sum(row_quantity(row) for row in counted)
        floor_rows = table["floor_rows"]
        nonzero_rows = [row for row in floor_rows if row["row_total"] > 0]
        row_sum = sum(row["row_total"] for row in floor_rows)
        table_total = table["table_total"]
        total_consistent = table_total is None or table_total == row_sum
        repeated_floor_match = (
            len(nonzero_rows) >= 2
            and all(row["row_total"] == position_count for row in nonzero_rows)
        )
        single_floor_match = (
            len(nonzero_rows) == 1
            and nonzero_rows[0]["row_total"] == position_count
        )

        if repeated_floor_match and total_consistent:
            status = "shared_layout_consistent"
            reconciled_quantity = table_total if table_total is not None else row_sum
            conclusion = (
                f"{position_count}个唯一几何位置与"
                f"{len(nonzero_rows)}个非零楼层的每层数量一致"
            )
        elif single_floor_match and total_consistent:
            status = "single_floor_consistent"
            reconciled_quantity = table_total if table_total is not None else row_sum
            conclusion = "唯一几何位置与单个非零楼层数量一致"
        else:
            status = "mismatch"
            reconciled_quantity = None
            conclusion = "几何位置、楼层行或表格合计不能形成一致关系"

        results.append(
            {
                **table,
                "position_count": position_count,
                "models": dict(
                    Counter(
                        {
                            label: sum(
                                row_quantity(row)
                                for row in counted
                                if row_model_label(row) == label
                            )
                            for label in {row_model_label(row) for row in counted}
                        }
                    )
                ),
                "nonzero_floor_count": len(nonzero_rows),
                "floor_row_sum": row_sum,
                "status": status,
                "reconciled_quantity_candidate": reconciled_quantity,
                "conclusion": conclusion,
            }
        )
    return results


def extract_building_ids(value: str) -> tuple[str, ...]:
    """从图名/表名提取带 #（或缺字形后显示为 |）的楼栋编号。"""
    text = compact_text(value)
    if not re.search(r"宿舍|楼|建筑|食堂|教学|实训|研发|体育馆", text):
        return ()
    result: list[str] = []
    for match in re.finditer(r"(?<!\d)(\d{1,3})\s*[#＃|｜]", text):
        value = str(int(match.group(1)))
        if value not in result:
            result.append(value)
    return tuple(result)


def frame_standard_layout_identity(
    entries: list[FrameText],
) -> tuple[tuple[str, ...], str]:
    """返回标准层结构平面图明确写出的楼栋集合和标题证据。"""
    candidates: list[tuple[tuple[str, ...], str]] = []
    for item in entries:
        text = compact_text(item.text)
        if not STANDARD_FLOOR_TERMS.search(text) or not STRUCTURAL_PLAN_TERMS.search(
            text
        ):
            continue
        building_ids = extract_building_ids(text)
        if building_ids:
            candidates.append((building_ids, item.text))
    if not candidates:
        return (), ""
    return max(candidates, key=lambda item: (len(item[0]), -len(item[1])))


def reconcile_shared_building_standard_layouts(
    rows: list[dict[str, Any]],
    frames: list[Frame],
    frame_texts: dict[str, list[FrameText]],
) -> list[dict[str, Any]]:
    """调和跨图框的“多栋共用标准层平面图”和楼层数量表。

    图面几何只来自标准层结构平面图；楼层范围和每层 X/Y 数量来自明确的
    数量表。表格只用于验证和展开已经识别出的模板，不能补齐图面漏识别。
    """
    counted_by_frame: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row["decision"] == "counted" and row["frame_id"]:
            counted_by_frame[row["frame_id"]].append(row)

    tables: list[dict[str, Any]] = []
    for frame in frames:
        for table in parse_floor_quantity_tables(
            frame, frame_texts.get(frame.frame_id, [])
        ):
            building_ids = extract_building_ids(table["anchor_text"])
            if not building_ids:
                continue
            tables.append({**table, "building_ids": building_ids})

    results: list[dict[str, Any]] = []
    for frame_id, counted in sorted(counted_by_frame.items()):
        building_ids, title = frame_standard_layout_identity(
            frame_texts.get(frame_id, [])
        )
        if not building_ids:
            continue
        position_count = sum(row_quantity(row) for row in counted)
        matches = [
            table
            for table in tables
            if set(building_ids).issubset(set(table["building_ids"]))
        ]
        if not matches:
            results.append(
                {
                    "layout_frame_id": frame_id,
                    "layout_title": title,
                    "building_ids": building_ids,
                    "building_count": len(building_ids),
                    "position_count_per_floor_per_building": position_count,
                    "table_frame_id": "",
                    "table_anchor_text": "",
                    "floors": [],
                    "floor_count": 0,
                    "table_row_total": None,
                    "table_total_per_building": None,
                    "status": "shared_layout_table_missing",
                    "expanded_quantity_candidate": None,
                    "conclusion": "标准层模板已识别，但未找到覆盖该楼栋集合的明确楼层数量表",
                }
            )
            continue
        table = min(
            matches,
            key=lambda item: (
                set(item["building_ids"]) != set(building_ids),
                len(item["building_ids"]),
                -len(item["floor_rows"]),
            ),
        )
        nonzero_rows = [
            floor for floor in table["floor_rows"] if floor["row_total"] > 0
        ]
        row_totals = {floor["row_total"] for floor in nonzero_rows}
        floor_row_sum = sum(floor["row_total"] for floor in table["floor_rows"])
        table_total = table["table_total"]
        table_internal_consistent = table_total is None or table_total == floor_row_sum
        per_floor_match = (
            bool(nonzero_rows)
            and row_totals == {position_count}
            and table_internal_consistent
        )
        if per_floor_match:
            expanded = position_count * len(nonzero_rows) * len(building_ids)
            status = "shared_building_standard_floor_consistent"
            conclusion = (
                f"{len(building_ids)}栋共用标准层模板；每栋每层{position_count}个，"
                f"表列{len(nonzero_rows)}层且逐层一致"
            )
        else:
            expanded = None
            status = "shared_building_standard_floor_mismatch"
            conclusion = (
                "标准层模板位置数与匹配数量表的逐层数量或表内合计不一致，"
                "不生成展开总数"
            )
        results.append(
            {
                "layout_frame_id": frame_id,
                "layout_title": title,
                "building_ids": building_ids,
                "building_count": len(building_ids),
                "position_count_per_floor_per_building": position_count,
                "table_frame_id": table["frame_id"],
                "table_anchor_text": table["anchor_text"],
                "table_anchor_handle": table["anchor_handle"],
                "floors": [floor["floor"] for floor in nonzero_rows],
                "floor_count": len(nonzero_rows),
                "table_row_total": (
                    next(iter(row_totals)) if len(row_totals) == 1 else None
                ),
                "table_total_per_building": table_total,
                "status": status,
                "expanded_quantity_candidate": expanded,
                "conclusion": conclusion,
            }
        )
    return results


def parse_model_quantity_tables(
    frames: list[Frame],
    frame_texts: dict[str, list[FrameText]],
) -> list[dict[str, Any]]:
    """解析“型号—数量”表，只接受同一图框、同行、同数量列的坐标证据。"""
    results: list[dict[str, Any]] = []
    for frame in frames:
        entries = [
            item
            for item in frame_texts.get(frame.frame_id, [])
            if item.x is not None and item.y is not None
        ]
        anchors = [
            item for item in entries if MODEL_TABLE_ANCHOR.search(compact_text(item.text))
        ]
        model_headers = [
            item for item in entries if MODEL_HEADER.search(compact_text(item.text))
        ]
        quantity_headers = [
            item for item in entries if QUANTITY_HEADER.fullmatch(compact_text(item.text))
        ]
        direction_cells = [
            item for item in entries if DIRECTION_CELL.fullmatch(compact_text(item.text))
        ]
        model_cells = [
            (item, model_hint(compact_text(item.text)))
            for item in entries
            if model_hint(compact_text(item.text))
        ]
        if not anchors or not model_headers or not quantity_headers or not model_cells:
            continue

        anchor = min(anchors, key=lambda item: abs(item.y - model_headers[0].y))
        model_header = min(
            model_headers,
            key=lambda item: abs(item.y - anchor.y) + abs(item.x - anchor.x),
        )
        quantity_header = min(
            quantity_headers,
            key=lambda item: abs(item.y - model_header.y),
        )
        column_distance = abs(quantity_header.x - model_header.x)
        row_tolerance = max(frame.height * 0.008, 1.0)
        column_tolerance = max(frame.width * 0.025, column_distance * 0.20, 1.0)

        parsed_models: list[dict[str, Any]] = []
        used_model_handles: set[str] = set()
        for model_cell, model in sorted(model_cells, key=lambda item: item[0].y, reverse=True):
            if model_cell.handle in used_model_handles:
                continue
            quantity_cell = closest_quantity_cell(
                entries,
                quantity_header.x,
                model_cell.y,
                column_tolerance,
                row_tolerance,
            )
            if quantity_cell is None:
                continue
            same_row_directions = [
                item
                for item in direction_cells
                if abs(item.y - model_cell.y) <= row_tolerance
            ]
            direction_cell = (
                min(
                    same_row_directions,
                    key=lambda item: (
                        abs(item.y - model_cell.y),
                        abs(item.x - model_cell.x),
                    ),
                )
                if same_row_directions
                else None
            )
            parsed_models.append(
                {
                    "model": model,
                    "quantity": quantity_cell[1],
                    "model_handle": model_cell.handle,
                    "quantity_handle": quantity_cell[0].handle,
                    "direction": (
                        normalize_direction_text(direction_cell.text)
                        if direction_cell is not None
                        else ""
                    ),
                    "direction_text": (
                        direction_cell.text if direction_cell is not None else ""
                    ),
                    "direction_handle": (
                        direction_cell.handle if direction_cell is not None else ""
                    ),
                    "y": model_cell.y,
                }
            )
            used_model_handles.add(model_cell.handle)
        if not parsed_models:
            continue

        total_value: int | None = None
        total_label_handle = ""
        total_value_handle = ""
        for label in entries:
            if not TOTAL_LABEL.fullmatch(compact_text(label.text)):
                continue
            total_cell = closest_quantity_cell(
                entries,
                quantity_header.x,
                label.y,
                column_tolerance,
                row_tolerance,
            )
            if total_cell is not None:
                total_value = total_cell[1]
                total_label_handle = label.handle
                total_value_handle = total_cell[0].handle
                break

        model_sum = sum(item["quantity"] for item in parsed_models)
        results.append(
            {
                "frame_id": frame.frame_id,
                "anchor_text": anchor.text,
                "anchor_handle": anchor.handle,
                "model_header_handle": model_header.handle,
                "quantity_header_handle": quantity_header.handle,
                "models": parsed_models,
                "model_sum": model_sum,
                "table_total": total_value,
                "total_label_handle": total_label_handle,
                "total_value_handle": total_value_handle,
            }
        )
    return results


def reconcile_model_quantities(
    rows: list[dict[str, Any]],
    frames: list[Frame],
    frame_texts: dict[str, list[FrameText]],
    direction_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    geometry_count = sum(row_quantity(row) for row in rows)
    classified_direction_rows = [
        row
        for row in direction_rows
        if row["decision"] == "direction_classified"
    ]
    ambiguous_direction_rows = [
        row
        for row in direction_rows
        if row["decision"] == "direction_ambiguous"
    ]
    layout_direction_count = len(classified_direction_rows) + len(
        ambiguous_direction_rows
    )
    layout_direction_counts = Counter(
        row["direction"] for row in classified_direction_rows
    )
    results: list[dict[str, Any]] = []
    for table in parse_model_quantity_tables(frames, frame_texts):
        table_total = table["table_total"]
        model_sum = table["model_sum"]
        table_direction_counts = Counter()
        for model_row in table["models"]:
            if model_row["direction"]:
                table_direction_counts[model_row["direction"]] += model_row["quantity"]
            model_row["layout_direction_count"] = layout_direction_counts.get(
                model_row["direction"], 0
            )
            model_row["direction_match"] = bool(
                model_row["direction"]
                and model_row["layout_direction_count"] == model_row["quantity"]
            )
        table_internal_consistent = table_total is None or table_total == model_sum
        geometry_consistent = (
            geometry_count > 0
            and table_internal_consistent
            and geometry_count == (table_total if table_total is not None else model_sum)
        )
        direction_evidence_present = layout_direction_count > 0
        direction_complete = bool(
            direction_evidence_present
            and not ambiguous_direction_rows
            and layout_direction_count == geometry_count
            and table_direction_counts
            and layout_direction_counts == table_direction_counts
        )
        if geometry_consistent and direction_complete:
            status = "direction_model_table_consistent"
            candidate = table_total if table_total is not None else model_sum
            conclusion = (
                f"布置图{geometry_count}个精确标记均有可分类的世界方向，"
                f"方向计数{dict(sorted(layout_direction_counts.items()))}与参数表"
                f"{dict(sorted(table_direction_counts.items()))}一致"
            )
        elif geometry_consistent and direction_evidence_present:
            status = "direction_unresolved"
            candidate = table_total if table_total is not None else model_sum
            conclusion = (
                f"总数{geometry_count}与型号表合计一致，但布置方向计数"
                f"{dict(sorted(layout_direction_counts.items()))}、方向不明确"
                f"{len(ambiguous_direction_rows)}个，与参数表方向数量"
                f"{dict(sorted(table_direction_counts.items()))}未形成完整一致关系；"
                "不确认方向—型号拆分"
            )
        elif geometry_consistent:
            status = "model_table_total_consistent"
            candidate = table_total if table_total is not None else model_sum
            conclusion = (
                f"布置图精确标记共{geometry_count}个，与型号表合计{candidate}一致；"
                "未提供单个标记方向证据，型号拆分仅来自参数表"
            )
        else:
            status = "mismatch"
            candidate = None
            conclusion = (
                f"布置图精确标记{geometry_count}个、型号行合计{model_sum}、"
                f"表格合计{table_total if table_total is not None else '未找到'}，未形成一致关系"
            )
        results.append(
            {
                **table,
                "geometry_count": geometry_count,
                "layout_direction_count": layout_direction_count,
                "layout_direction_counts": dict(layout_direction_counts),
                "ambiguous_direction_count": len(ambiguous_direction_rows),
                "table_direction_counts": dict(table_direction_counts),
                "direction_confirmed": direction_complete,
                "status": status,
                "reconciled_quantity_candidate": candidate,
                "conclusion": conclusion,
            }
        )
    return results


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = [
        "decision",
        "score",
        "model_hint",
        "marker_type",
        "marker_count",
        "count_value",
        "instance_key",
        "instance_handle",
        "root_instance_handle",
        "parent_instance_key",
        "block_name",
        "effective_name",
        "name_path",
        "space",
        "layer",
        "x",
        "y",
        "insertion_x",
        "insertion_y",
        "evidence_x",
        "evidence_y",
        "location_method",
        "min_x",
        "min_y",
        "max_x",
        "max_y",
        "frame_id",
        "frame_role",
        "frame_role_evidence",
        "geometry_signature",
        "signature_frequency",
        "dimensioned_detail",
        "semantic_leaf_symbol",
        "semantic_sibling_count",
        "semantic_parent_key",
        "semantic_parent_block",
        "semantic_leaf_area_ratio",
        "reasons",
        "semantic_preview",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def write_reconciliation_csv(
    path: Path, reconciliations: list[dict[str, Any]]
) -> None:
    fields = [
        "frame_id",
        "status",
        "position_count",
        "models",
        "anchor_text",
        "anchor_handle",
        "table_block_path",
        "floor",
        "x_quantity",
        "y_quantity",
        "row_total",
        "floor_handle",
        "x_handle",
        "y_handle",
        "table_total",
        "table_total_handle",
        "nonzero_floor_count",
        "floor_row_sum",
        "reconciled_quantity_candidate",
        "conclusion",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for result in reconciliations:
            for floor_row in result["floor_rows"]:
                writer.writerow(
                    {
                        "frame_id": result["frame_id"],
                        "status": result["status"],
                        "position_count": result["position_count"],
                        "models": "; ".join(
                            f"{model}={count}"
                            for model, count in sorted(result["models"].items())
                        ),
                        "anchor_text": result["anchor_text"],
                        "anchor_handle": result["anchor_handle"],
                        "table_block_path": result["table_block_path"],
                        "floor": floor_row["floor"],
                        "x_quantity": floor_row["x_quantity"],
                        "y_quantity": floor_row["y_quantity"],
                        "row_total": floor_row["row_total"],
                        "floor_handle": floor_row["floor_handle"],
                        "x_handle": floor_row["x_handle"],
                        "y_handle": floor_row["y_handle"],
                        "table_total": result["table_total"],
                        "table_total_handle": result["table_total_handle"],
                        "nonzero_floor_count": result["nonzero_floor_count"],
                        "floor_row_sum": result["floor_row_sum"],
                        "reconciled_quantity_candidate": result[
                            "reconciled_quantity_candidate"
                        ],
                        "conclusion": result["conclusion"],
                    }
                )


def write_shared_standard_layout_csv(
    path: Path, reconciliations: list[dict[str, Any]]
) -> None:
    fields = [
        "layout_frame_id",
        "layout_title",
        "building_ids",
        "building_count",
        "position_count_per_floor_per_building",
        "table_frame_id",
        "table_anchor_text",
        "table_anchor_handle",
        "floors",
        "floor_count",
        "table_row_total",
        "table_total_per_building",
        "status",
        "expanded_quantity_candidate",
        "conclusion",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for result in reconciliations:
            writer.writerow(
                {
                    **result,
                    "building_ids": ",".join(result["building_ids"]),
                    "floors": ",".join(result["floors"]),
                }
            )


def write_model_reconciliation_csv(
    path: Path, reconciliations: list[dict[str, Any]]
) -> None:
    fields = [
        "frame_id",
        "status",
        "geometry_count",
        "layout_direction_count",
        "layout_direction_counts",
        "ambiguous_direction_count",
        "direction_confirmed",
        "direction",
        "direction_text",
        "direction_handle",
        "model",
        "quantity",
        "layout_direction_model_count",
        "direction_match",
        "model_handle",
        "quantity_handle",
        "model_sum",
        "table_total",
        "total_label_handle",
        "total_value_handle",
        "anchor_text",
        "anchor_handle",
        "reconciled_quantity_candidate",
        "conclusion",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for result in reconciliations:
            for model_row in result["models"]:
                writer.writerow(
                    {
                        "frame_id": result["frame_id"],
                        "status": result["status"],
                        "geometry_count": result["geometry_count"],
                        "layout_direction_count": result["layout_direction_count"],
                        "layout_direction_counts": "; ".join(
                            f"{direction}={count}"
                            for direction, count in sorted(
                                result["layout_direction_counts"].items()
                            )
                        ),
                        "ambiguous_direction_count": result[
                            "ambiguous_direction_count"
                        ],
                        "direction_confirmed": result["direction_confirmed"],
                        "direction": model_row["direction"],
                        "direction_text": model_row["direction_text"],
                        "direction_handle": model_row["direction_handle"],
                        "model": model_row["model"],
                        "quantity": model_row["quantity"],
                        "layout_direction_model_count": model_row[
                            "layout_direction_count"
                        ],
                        "direction_match": model_row["direction_match"],
                        "model_handle": model_row["model_handle"],
                        "quantity_handle": model_row["quantity_handle"],
                        "model_sum": result["model_sum"],
                        "table_total": result["table_total"],
                        "total_label_handle": result["total_label_handle"],
                        "total_value_handle": result["total_value_handle"],
                        "anchor_text": result["anchor_text"],
                        "anchor_handle": result["anchor_handle"],
                        "reconciled_quantity_candidate": result[
                            "reconciled_quantity_candidate"
                        ],
                        "conclusion": result["conclusion"],
                    }
                )


def write_direction_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = [
        "decision",
        "marker",
        "direction",
        "record_key",
        "handle",
        "block_path",
        "space",
        "frame_id",
        "frame_role",
        "frame_role_evidence",
        "x",
        "y",
        "world_rotation_radians",
        "world_axis_x",
        "world_axis_y",
        "axis_angle_to_wcs_degrees",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def write_floor_layout_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = [
        "floor",
        "status",
        "model",
        "model_candidates",
        "placement_label",
        "placement_count",
        "applicable_floor_count",
        "expanded_quantity_candidate",
        "x_quantity",
        "y_quantity",
        "expanded_x_quantity",
        "expanded_y_quantity",
        "ambiguous_direction_count",
        "oriented_label_count",
        "definition_handle",
        "block_name",
        "canonical_root_handle",
        "presentation_root_handles",
        "presentation_reference_count",
        "parameter_table_handle",
        "title_text",
        "title_handle",
        "title_x",
        "title_y",
        "canonical_evidence_y",
        "dedupe_basis",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def write_floor_direction_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = [
        "floor",
        "model",
        "decision",
        "direction",
        "marker",
        "record_key",
        "handle",
        "root_instance_handle",
        "canonical_root_handle",
        "definition_handle",
        "block_path",
        "space",
        "x",
        "y",
        "world_rotation_radians",
        "world_axis_x",
        "world_axis_y",
        "axis_angle_to_wcs_degrees",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def markdown_table(rows: list[list[str]]) -> str:
    if not rows:
        return "无\n"
    header = rows[0]
    body = rows[1:]
    lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join("---" for _ in header) + " |",
    ]
    lines.extend("| " + " | ".join(row) + " |" for row in body)
    return "\n".join(lines) + "\n"


def write_report(
    path: Path,
    drawing: str,
    symbol_json: Path,
    frame_path: Path | None,
    text_json_path: Path | None,
    oriented_text_path: Path | None,
    rows: list[dict[str, Any]],
    direction_rows: list[dict[str, Any]],
    reconciliations: list[dict[str, Any]],
    shared_standard_layouts: list[dict[str, Any]],
    model_reconciliations: list[dict[str, Any]],
    floor_layouts: list[dict[str, Any]],
    floor_direction_rows: list[dict[str, Any]],
) -> None:
    counted = [row for row in rows if row["decision"] == "counted"]
    decisions = Counter(row["decision"] for row in rows)
    models: Counter[str] = Counter()
    frames: Counter[str] = Counter()
    if floor_layouts:
        for row in floor_layouts:
            models[row["model"] or "(型号未解析)"] += int(row["placement_count"])
            frames[row["floor"]] += int(row["placement_count"])
    else:
        for row in counted:
            models[row_model_label(row)] += row_quantity(row)
            frames[row["frame_id"] or "(未归框)"] += row_quantity(row)

    parts = [
        f"# {Path(drawing).stem if drawing else symbol_json.stem}：阻尼器自动计数\n",
        "## 输入与方法\n",
        f"- 原图：`{drawing or '未记录'}`\n",
        f"- V6 实例证据：`{symbol_json}`\n",
        f"- 图框证据：`{frame_path if frame_path else '未提供'}`\n",
        f"- V5 楼层标题证据：`{text_json_path if text_json_path else '未提供'}`\n",
        f"- V7 单标记方向证据：`{oriented_text_path if oriented_text_path else '未提供'}`\n",
        "- 原图状态：只读，未修改\n",
        "- 计数主键：`instance_key`；嵌套实例使用完整句柄路径去重；"
        "成组块内的精确产品标记按标记个数展开\n",
        "\n## 自动计数结果\n",
    ]
    result_rows = [["型号", "计入实例数"]]
    result_rows.extend([[model, str(count)] for model, count in sorted(models.items())])
    parts.append(markdown_table(result_rows))
    if floor_layouts:
        parts.extend(
            [
                "\n## 相邻楼层重复展示去重\n",
                "- 本图采用“楼层 + 块定义句柄”作为设备组计数主键；同一楼层布置块在相邻图纸重复展示时只计一次，根插入句柄全部保留供回查。\n",
                "- 逐位置数量来自楼层阻尼器布置块内重复的“阻尼器支撑墙”标注；只有块名同时具备楼层与阻尼器语义时才启用，普通说明文字不会进入计数。\n",
            ]
        )
        layout_table = [
            [
                "楼层",
                "型号",
                "去重数量",
                "适用层数",
                "展开数量候选",
                "X向",
                "Y向",
                "方向不明",
                "展示次数",
                "块定义/计数根",
                "状态",
            ]
        ]
        for result in floor_layouts:
            layout_table.append(
                [
                    result["floor"],
                    result["model"] or "待确认",
                    str(result["placement_count"]),
                    str(result["applicable_floor_count"]),
                    str(result["expanded_quantity_candidate"]),
                    str(result["x_quantity"]),
                    str(result["y_quantity"]),
                    str(result["ambiguous_direction_count"]),
                    str(result["presentation_reference_count"]),
                    f"`{result['definition_handle'] or '—'}` / "
                    f"`{result['canonical_root_handle'] or '—'}`",
                    result["status"],
                ]
            )
        layout_table.append(
            [
                "合计",
                "—",
                str(sum(int(row["placement_count"]) for row in floor_layouts)),
                "—",
                str(
                    sum(
                        int(row["expanded_quantity_candidate"])
                        for row in floor_layouts
                    )
                ),
                str(sum(int(row["x_quantity"]) for row in floor_layouts)),
                str(sum(int(row["y_quantity"]) for row in floor_layouts)),
                str(
                    sum(
                        int(row["ambiguous_direction_count"])
                        for row in floor_layouts
                    )
                ),
                "—",
                "—",
                "—",
            ]
        )
        parts.append(markdown_table(layout_table))
        for result in floor_layouts:
            parts.extend(
                [
                    f"- {result['floor']}：标题 `{result['title_text'] or '未找到'}`；"
                    f"参数表根句柄 `{result['parameter_table_handle'] or '—'}`；"
                    f"展示根句柄 `{result['presentation_root_handles']}`。"
                    f"{result['dedupe_basis']}"
                    + (
                        f"标题明确适用于 {result['applicable_floor_count']} 层，"
                        f"展开数量候选为 {result['expanded_quantity_candidate']}。"
                        if int(result["applicable_floor_count"]) > 1
                        else ""
                    )
                    + "\n"
                ]
            )
    confirmed_direction_models = [
        (model_row["direction"], model_row["model"], model_row["quantity"])
        for result in model_reconciliations
        if result["status"] == "direction_model_table_consistent"
        for model_row in result["models"]
        if model_row["direction_match"]
    ]
    if confirmed_direction_models:
        parts.append("\n### 已确认的方向—型号拆分\n")
        parts.append(
            markdown_table(
                [["方向", "型号", "数量"]]
                + [
                    [f"{direction}向", model, str(quantity)]
                    for direction, model, quantity in confirmed_direction_models
                ]
            )
        )

    parts.append("\n## 多栋共用标准层模板调和\n")
    if not shared_standard_layouts:
        parts.append("- 未形成“标准层结构平面图—楼栋集合—楼层数量表”的跨图框匹配。\n")
    else:
        shared_rows = [
            [
                "布置图框",
                "楼栋",
                "每栋每层图面位置",
                "适用楼层",
                "每栋表内合计",
                "状态",
                "展开数量候选",
            ]
        ]
        for result in shared_standard_layouts:
            shared_rows.append(
                [
                    result["layout_frame_id"],
                    ",".join(result["building_ids"]),
                    str(result["position_count_per_floor_per_building"]),
                    ",".join(result["floors"]) or "未解析",
                    (
                        str(result["table_total_per_building"])
                        if result["table_total_per_building"] is not None
                        else "未找到"
                    ),
                    result["status"],
                    (
                        str(result["expanded_quantity_candidate"])
                        if result["expanded_quantity_candidate"] is not None
                        else "不生成"
                    ),
                ]
            )
        closed = [
            result
            for result in shared_standard_layouts
            if result["status"] == "shared_building_standard_floor_consistent"
            and result["expanded_quantity_candidate"] is not None
        ]
        if len(closed) == len(shared_standard_layouts):
            shared_rows.append(
                [
                    "合计",
                    "—",
                    "—",
                    "—",
                    "—",
                    "全部闭合",
                    str(
                        sum(
                            int(result["expanded_quantity_candidate"])
                            for result in closed
                        )
                    ),
                ]
            )
        parts.append(markdown_table(shared_rows))
        for result in shared_standard_layouts:
            parts.append(
                f"- {result['layout_frame_id']}：`{result['layout_title']}`；"
                f"匹配表 `{result['table_anchor_text'] or '未找到'}`；"
                f"{result['conclusion']}。\n"
            )

    parts.append("\n## 楼层适用范围与数量调和\n")
    if not reconciliations:
        parts.append(
            "- 未形成可解析的楼层/方向数量表，保持唯一几何位置数，不应用楼层倍数。\n"
        )
    else:
        for result in reconciliations:
            candidate = result["reconciled_quantity_candidate"]
            parts.extend(
                [
                    f"\n### {result['frame_id']}\n",
                    f"- 表格锚点：`{result['anchor_text']}`；句柄 `{result['anchor_handle'] or '—'}`；"
                    f"块路径 `{result['table_block_path'] or '—'}`\n",
                    f"- 唯一几何位置：{result['position_count']}\n",
                    f"- 非零楼层数：{result['nonzero_floor_count']}\n",
                    f"- 表格楼层行合计：{result['floor_row_sum']}\n",
                    f"- 表格合计：{result['table_total'] if result['table_total'] is not None else '未找到'}\n",
                    f"- 调和状态：`{result['status']}`\n",
                    f"- 数量调和候选：{candidate if candidate is not None else '不生成'}\n",
                    f"- 依据：{result['conclusion']}\n",
                ]
            )
            floor_rows = [["楼层", "X向", "Y向", "行合计", "证据句柄"]]
            for floor in result["floor_rows"]:
                floor_rows.append(
                    [
                        floor["floor"],
                        str(floor["x_quantity"]),
                        str(floor["y_quantity"]),
                        str(floor["row_total"]),
                        "/".join(
                            handle or "—"
                            for handle in (
                                floor["floor_handle"],
                                floor["x_handle"],
                                floor["y_handle"],
                            )
                        ),
                    ]
                )
            parts.append(markdown_table(floor_rows))

    parts.append("\n## 型号参数表与布置标记调和\n")
    if not model_reconciliations:
        parts.append("- 未形成可解析的“型号—数量”参数表调和证据。\n")
    else:
        for result in model_reconciliations:
            parts.extend(
                [
                    f"\n### {result['frame_id']}\n",
                    f"- 表格锚点：`{result['anchor_text']}`；句柄 `{result['anchor_handle'] or '—'}`\n",
                    f"- 布置图精确标记：{result['geometry_count']}\n",
                    f"- 有方向证据的布置标记：{result['layout_direction_count']}；"
                    f"方向不明确：{result['ambiguous_direction_count']}\n",
                    "- 布置方向计数："
                    + (
                        "；".join(
                            f"{direction}向={count}"
                            for direction, count in sorted(
                                result["layout_direction_counts"].items()
                            )
                        )
                        or "未形成"
                    )
                    + "\n",
                    f"- 型号行合计：{result['model_sum']}\n",
                    f"- 表格合计：{result['table_total'] if result['table_total'] is not None else '未找到'}\n",
                    f"- 调和状态：`{result['status']}`\n",
                    f"- 数量调和候选：{result['reconciled_quantity_candidate'] if result['reconciled_quantity_candidate'] is not None else '不生成'}\n",
                    f"- 依据：{result['conclusion']}\n",
                ]
            )
            model_rows = [
                [
                    "方向",
                    "型号",
                    "表格数量",
                    "布置方向数",
                    "逐向一致",
                    "证据句柄（方向/型号/数量）",
                ]
            ]
            for model_row in result["models"]:
                model_rows.append(
                    [
                        (
                            f"{model_row['direction']}向"
                            if model_row["direction"]
                            else "未提取"
                        ),
                        model_row["model"],
                        str(model_row["quantity"]),
                        str(model_row["layout_direction_count"]),
                        "是" if model_row["direction_match"] else "否",
                        "/".join(
                            handle or "—"
                            for handle in (
                                model_row["direction_handle"],
                                model_row["model_handle"],
                                model_row["quantity_handle"],
                            )
                        ),
                    ]
                )
            parts.append(markdown_table(model_rows))

    if floor_direction_rows:
        parts.append("\n## 楼层布置逐位置方向证据\n")
        floor_direction_counts = Counter(
            (row["floor"], row["direction"])
            for row in floor_direction_rows
            if row["decision"] == "direction_classified"
        )
        parts.append(
            "- 已按去重后的计数根保留逐位置证据："
            + "；".join(
                f"{floor}{direction}向={count}"
                for (floor, direction), count in sorted(
                    floor_direction_counts.items()
                )
            )
            + "。\n"
        )
        parts.append(
            "- 详细记录键、坐标、世界文字轴和偏轴角见同前缀的 `楼层阻尼器方向实例.csv`。\n"
        )

    parts.append("\n## 单个布置标记方向证据\n")
    classified = [
        row for row in direction_rows if row["decision"] == "direction_classified"
    ]
    ambiguous = [
        row for row in direction_rows if row["decision"] == "direction_ambiguous"
    ]
    if not direction_rows:
        if oriented_text_path:
            parts.append(
                "- 已提供 V7 方向导出，但未找到独立的精确 `VFD/BRB/MYD/XNQD/VAD`"
                "文字标记；本样本的方向结果来自上节去重后的“阻尼器支撑墙”逐位置证据。\n"
            )
        else:
            parts.append("- 未提供 V7 方向导出，不对布置标记作 X/Y 分类。\n")
    else:
        direction_counts = Counter(row["direction"] for row in classified)
        parts.append(
            "- 分类结果："
            + "；".join(
                f"{direction}向={count}"
                for direction, count in sorted(direction_counts.items())
            )
            + f"；方向不明确={len(ambiguous)}。\n"
        )
        direction_evidence = [
            ["方向", "记录键", "图框", "坐标", "世界文字轴", "偏轴角"]
        ]
        for row in classified + ambiguous:
            direction_evidence.append(
                [
                    f"{row['direction']}向" if row["direction"] else "待确认",
                    f"`{row['record_key']}`",
                    row["frame_id"] or "—",
                    f"({row['x']:.3f}, {row['y']:.3f})",
                    f"({float(row['world_axis_x']):.3f}, {float(row['world_axis_y']):.3f})",
                    (
                        f"{float(row['axis_angle_to_wcs_degrees']):.2f}°"
                        if row["axis_angle_to_wcs_degrees"] is not None
                        else "—"
                    ),
                ]
            )
        parts.append(markdown_table(direction_evidence))

    parts.extend(
        [
            "\n## 按图框统计\n",
            markdown_table(
                [["图框", "计入实例数"]]
                + [[frame, str(count)] for frame, count in sorted(frames.items())]
            ),
            "\n## 候选决策统计\n",
            markdown_table(
                [["状态", "数量"]]
                + [[decision, str(count)] for decision, count in sorted(decisions.items())]
            ),
            "\n## 逐实例证据\n",
        ]
    )
    evidence_rows = [["状态", "型号", "实例键", "图框/角色", "坐标", "依据"]]
    for row in rows:
        evidence_rows.append(
            [
                row["decision"],
                row_model_label(row),
                f"`{row['instance_key']}`",
                f"{row['frame_id'] or '—'}/{row['frame_role']}",
                f"({row['x']:.3f}, {row['y']:.3f})",
                (
                    f"计数权重×{row_quantity(row)}；"
                    if row["decision"] == "counted" and row_quantity(row) > 1
                    else ""
                )
                + row["reasons"].replace("|", "/"),
            ]
        )
    parts.append(markdown_table(evidence_rows))
    parts.extend(
        [
            "\n## 结论边界\n",
            "- `counted` 表示满足当前 API 和图纸角色规则的布置实例，不自动等于合同供货或生产放行数量。\n",
            "- `deduplicated_floor_layout_consistent` 表示楼层标题、楼层阻尼器块定义、"
            "逐位置标注和方向数量内部一致；相邻图纸中同一块定义的重复展示不会重复计数。\n",
            "- 墙式布置的逐位置计数依赖“阻尼器支撑墙”标注与楼层阻尼器容器同时成立；"
            "若一个支撑墙实际安装多台设备，必须由大样或数量表另行调和，不能按文字自动放大。\n",
            "- `shared_layout_consistent` 只表示“唯一位置 × 非零楼层”与表格内部一致，是设计数量候选，不是供货放行数量。\n",
            "- `shared_building_standard_floor_consistent` 仅在标准层图名明确楼栋集合、"
            "图面每层位置数与数量表逐层数量一致、表内合计一致时展开；表格不用于补齐漏识别的图面实例。\n",
            "- `model_table_total_consistent` 只表示布置标记总数与参数表型号数量合计一致；"
            "若平面标记未分型，型号拆分仍以参数表为证据。\n",
            "- `direction_model_table_consistent` 要求每个布置标记均有世界方向、"
            "X/Y 数量与参数表方向—型号—数量逐向一致；任何斜向不明或数量冲突均不确认拆分。\n",
            "- V7 当前仅把距 WCS X/Y 轴不超过 20°的标记轴分类。建筑轴大角度旋转时，"
            "须增加轴网方向证据，不能把两个正交簇直接猜成 X/Y。\n",
            "- `manual_review`、`candidate_needs_frame` 必须补充图框/图面证据后才能转为计入或排除。\n",
            "- 图例、说明、参数表和节点大样中的实例单列排除，不能混入布置数量。\n",
        ]
    )
    path.write_text("".join(parts), encoding="utf-8")


def main() -> int:
    args = parse_args()
    source = read_json(args.symbol_json)
    frames = read_frames(args.frames)
    frame_texts = read_frame_texts(args.frame_texts)
    direction_rows = read_oriented_layout_markers(
        args.oriented_texts, frames, frame_texts
    )
    records = source["records"]
    rows = analyze_records(records, frames, frame_texts, args.min_score)
    reconciliations = reconcile_floor_quantities(rows, frames, frame_texts)
    shared_standard_layouts = reconcile_shared_building_standard_layouts(
        rows, frames, frame_texts
    )
    model_reconciliations = reconcile_model_quantities(
        rows, frames, frame_texts, direction_rows
    )
    floor_layouts, floor_direction_rows = analyze_deduplicated_floor_layouts(
        records, args.text_json, args.oriented_texts
    )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    prefix = args.prefix or args.symbol_json.stem.replace(".cad_symbol_export_v6", "")
    csv_path = args.output_dir / f"{prefix}.阻尼器实例候选.csv"
    reconciliation_csv_path = args.output_dir / f"{prefix}.楼层数量调和.csv"
    shared_standard_layout_csv_path = (
        args.output_dir / f"{prefix}.多栋共用标准层调和.csv"
    )
    model_reconciliation_csv_path = args.output_dir / f"{prefix}.型号数量调和.csv"
    direction_csv_path = args.output_dir / f"{prefix}.阻尼器方向实例.csv"
    floor_layout_csv_path = args.output_dir / f"{prefix}.楼层布置去重.csv"
    floor_direction_csv_path = (
        args.output_dir / f"{prefix}.楼层阻尼器方向实例.csv"
    )
    report_path = args.output_dir / f"{prefix}.阻尼器自动计数.md"
    write_csv(csv_path, rows)
    write_reconciliation_csv(reconciliation_csv_path, reconciliations)
    write_shared_standard_layout_csv(
        shared_standard_layout_csv_path, shared_standard_layouts
    )
    write_model_reconciliation_csv(
        model_reconciliation_csv_path, model_reconciliations
    )
    write_direction_csv(direction_csv_path, direction_rows)
    write_floor_layout_csv(floor_layout_csv_path, floor_layouts)
    write_floor_direction_csv(floor_direction_csv_path, floor_direction_rows)
    write_report(
        report_path,
        str(source.get("drawing") or ""),
        args.symbol_json,
        args.frames,
        args.text_json,
        args.oriented_texts,
        rows,
        direction_rows,
        reconciliations,
        shared_standard_layouts,
        model_reconciliations,
        floor_layouts,
        floor_direction_rows,
    )
    floor_layout_count = sum(
        int(row["placement_count"]) for row in floor_layouts
    )
    expanded_floor_layout_count = sum(
        int(row["expanded_quantity_candidate"]) for row in floor_layouts
    )
    print(
        json.dumps(
            {
                "records": len(records),
                "damper_candidates": len(rows),
                "counted_records": sum(row["decision"] == "counted" for row in rows),
                "counted": sum(row_quantity(row) for row in rows),
                "floor_layout_groups": len(floor_layouts),
                "floor_layout_count": floor_layout_count,
                "expanded_floor_layout_count": expanded_floor_layout_count,
                "floor_direction_counts": dict(
                    Counter(
                        row["direction"]
                        for row in floor_direction_rows
                        if row["decision"] == "direction_classified"
                    )
                ),
                "reconciled_quantity_candidates": [
                    {
                        "frame_id": result["frame_id"],
                        "status": result["status"],
                        "quantity": result["reconciled_quantity_candidate"],
                    }
                    for result in reconciliations
                ],
                "shared_standard_layouts": [
                    {
                        "layout_frame_id": result["layout_frame_id"],
                        "building_ids": result["building_ids"],
                        "floors": result["floors"],
                        "status": result["status"],
                        "quantity": result["expanded_quantity_candidate"],
                    }
                    for result in shared_standard_layouts
                ],
                "model_reconciliations": [
                    {
                        "frame_id": result["frame_id"],
                        "status": result["status"],
                        "quantity": result["reconciled_quantity_candidate"],
                    }
                    for result in model_reconciliations
                ],
                "csv": str(csv_path),
                "reconciliation_csv": str(reconciliation_csv_path),
                "shared_standard_layout_csv": str(
                    shared_standard_layout_csv_path
                ),
                "model_reconciliation_csv": str(model_reconciliation_csv_path),
                "direction_csv": str(direction_csv_path),
                "floor_layout_csv": str(floor_layout_csv_path),
                "floor_direction_csv": str(floor_direction_csv_path),
                "report": str(report_path),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
