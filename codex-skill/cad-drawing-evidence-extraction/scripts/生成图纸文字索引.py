#!/usr/bin/env python3
"""从图框归属CSV生成可回查的逐图文字索引。"""

from __future__ import annotations

import argparse
import csv
import re
from collections import defaultdict
from pathlib import Path


DEFAULT_KEYWORDS = (
    "隔震",
    "支座",
    "阻尼器",
    "BRB",
    "预埋件",
    "抗震",
    "混凝土",
    "钢筋",
    "填充墙",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="从图框候选和文字归属CSV生成可审计的Markdown索引"
    )
    parser.add_argument("--frames", type=Path, required=True, help="图框候选CSV")
    parser.add_argument("--texts", type=Path, required=True, help="文字按图框归属CSV")
    parser.add_argument("--output", type=Path, required=True, help="输出Markdown")
    parser.add_argument(
        "--keyword",
        action="append",
        dest="keywords",
        help="附加或替代检索关键词；可重复传入",
    )
    return parser.parse_args()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def clean_mtext(value: str) -> str:
    value = value.replace("\\P", " ")
    value = re.sub(r"\\[ACcFfHhLlOoPpQqTtWw][^;{}\\]*;", "", value)
    value = value.replace("\\L", "").replace("\\l", "")
    value = value.replace("{", "").replace("}", "")
    return " ".join(value.split())


def title_candidates(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    candidates: list[dict[str, str]] = []
    for row in rows:
        if row.get("entity_type") != "DBText" or row.get("origin") != "direct":
            continue
        text = (row.get("text") or "").strip()
        if len(text) < 5 or len(text) > 80 or "说明" not in text:
            continue
        if re.fullmatch(r"[\d.×xX -]+", text):
            continue
        candidates.append(row)
    return candidates


def format_coordinate(row: dict[str, str]) -> str:
    try:
        return f"({float(row.get('x') or 0):.2f}, {float(row.get('y') or 0):.2f})"
    except ValueError:
        return f"({row.get('x', '')}, {row.get('y', '')})"


def build_index(
    frames: list[dict[str, str]],
    rows: list[dict[str, str]],
    keywords: tuple[str, ...],
) -> str:
    by_frame: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        frame_id = (row.get("frame_id") or "").strip()
        if frame_id:
            by_frame[frame_id].append(row)

    lines = [
        "# 图纸文字索引",
        "",
        "## 使用边界",
        "",
        "本索引来自CAD API文字及图框归属证据。关键词命中只表示原文出现，不证明构件存在、方案采用或规范符合。",
        "",
        "## 图纸概览",
        "",
        "| 图框 ID | 归属文字数 | 直接文字 | 长 MTEXT | 图名候选 | 关键词命中 |",
        "|---|---:|---:|---:|---|---|",
    ]
    details: list[tuple[str, list[dict[str, str]], list[dict[str, str]], list[dict[str, str]], dict[str, int]]] = []
    for frame in frames:
        frame_id = (frame.get("frame_id") or "").strip()
        if not frame_id:
            continue
        current = by_frame.get(frame_id, [])
        direct = [row for row in current if row.get("origin") == "direct"]
        long_mtext = [
            row
            for row in current
            if row.get("entity_type") == "MText" and len(row.get("text") or "") >= 1000
        ]
        titles = title_candidates(current)
        title_text = "；".join(
            dict.fromkeys((row.get("text") or "").strip() for row in titles[:3])
        ) or "未自动定位"
        keyword_counts = {
            keyword: sum(
                (row.get("text") or "").upper().count(keyword.upper()) for row in current
            )
            for keyword in keywords
        }
        hit_summary = "、".join(
            f"{keyword}({count})" for keyword, count in keyword_counts.items() if count
        ) or "无"
        lines.append(
            f"| {frame_id} | {len(current)} | {len(direct)} | {len(long_mtext)} | "
            f"{title_text} | {hit_summary} |"
        )
        details.append((frame_id, current, titles, long_mtext, keyword_counts))

    lines.extend(["", "## 逐图可追溯记录", ""])
    for frame_id, _current, titles, long_mtext, keyword_counts in details:
        lines.extend([f"### {frame_id}", ""])
        title_line = "；".join(
            f"`{(row.get('text') or '').strip()}`（句柄 `{row.get('handle', '')}`）"
            for row in titles[:5]
        ) or "未自动定位"
        lines.append(f"- 图名候选：{title_line}")
        lines.append(
            "- 关键词原文命中数："
            + "；".join(f"{keyword}={count}" for keyword, count in keyword_counts.items())
        )
        if long_mtext:
            lines.append("- 长说明文字：")
            for row in long_mtext:
                value = clean_mtext(row.get("text") or "")
                preview = value[:170] + ("…" if len(value) > 170 else "")
                lines.append(
                    f"  - `{preview}`（句柄 `{row.get('handle', '')}`，坐标 "
                    f"{format_coordinate(row)}）"
                )
        else:
            lines.append("- 长说明文字：未检出。")
        lines.append("")

    unassigned = sum(not (row.get("frame_id") or "").strip() for row in rows)
    lines.extend(
        [
            "## 未归属记录",
            "",
            f"共 {unassigned} 条。未归属文字不参与逐图关键词统计，仍应在输入CSV中保留。",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    args = parse_args()
    frames = read_csv(args.frames)
    rows = read_csv(args.texts)
    keywords = tuple(dict.fromkeys(args.keywords or DEFAULT_KEYWORDS))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(build_index(frames, rows, keywords), encoding="utf-8")
    print(f"frames={len(frames)} texts={len(rows)} output={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
