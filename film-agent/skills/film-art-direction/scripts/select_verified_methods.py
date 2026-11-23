"""Find reviewed film methods by narrative need and observed scene conditions."""

import argparse
import json
from pathlib import Path
import unicodedata


REFERENCE = Path(__file__).resolve().parents[1] / "references" / "verified-methods.json"


def normalize(value):
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def keyword(value):
    normalized = normalize(value)
    if not normalized:
        raise argparse.ArgumentTypeError("关键词不能为空。")
    return normalized


def condition_matches(definition, observed):
    return observed.intersection(
        normalize(value) for value in [definition["key"], *definition["keywords"]]
    )


def need_keywords(row):
    labels = [row["id"], row["method_id"], row["title"], *row["related_method_ids"],
              *row["keywords"], *row["directions"]]
    return list(dict.fromkeys(labels))


def matching_rows(rows, needs):
    return [row for row in rows if needs.issubset({normalize(label) for label in need_keywords(row)})]


def unmatched_need_terms(rows, needs):
    vocabulary = {normalize(label) for row in rows for label in need_keywords(row)}
    return sorted(needs - vocabulary)


def list_methods(rows, needs):
    return {
        "needs": sorted(needs),
        "unmatched_need_terms": unmatched_need_terms(rows, needs),
        "methods": [
            {
                "id": row["id"],
                "title": row["title"],
                "directions": row["directions"],
                "classification_status": row["classification_status"],
                "need_keywords": need_keywords(row),
                "scope": row["scope"],
                "mechanism_scope": row["mechanism_scope"],
                "adaptable_parameters": row["adaptable_parameters"],
                "common_scene_uses": row["common_scene_uses"],
                "required_conditions": row["required_conditions"],
                "avoid_conditions": row["avoid_conditions"],
            }
            for row in matching_rows(rows, needs)
        ],
    }


def select(rows, needs, observed):
    candidates = []
    excluded = []
    recognized_conditions = set()
    for row in matching_rows(rows, needs):
        satisfied = []
        unmet = []
        conflicts = []
        for definition in row["required_conditions"]:
            matches = condition_matches(definition, observed)
            if matches:
                recognized_conditions.update(matches)
                satisfied.append({**definition, "matched_keywords": sorted(matches)})
            else:
                unmet.append(definition)
        for definition in row["avoid_conditions"]:
            matches = condition_matches(definition, observed)
            if matches:
                recognized_conditions.update(matches)
                conflicts.append({**definition, "matched_keywords": sorted(matches)})

        result = {
            **row,
            "matched_conditions": satisfied,
            "unmet_conditions": unmet,
            "conflicting_conditions": conflicts,
        }
        if conflicts:
            excluded.append(result)
        else:
            candidates.append(result)

    candidates.sort(key=lambda row: (
        row["priority"] == "low",
        len(row["unmet_conditions"]),
        -len(row["matched_conditions"]),
        row["id"],
    ))
    return {
        "needs": sorted(needs),
        "unmatched_need_terms": unmatched_need_terms(rows, needs),
        "provided_conditions": sorted(observed),
        "unrecognized_conditions": sorted(observed - recognized_conditions),
        "candidates": candidates,
        "excluded_due_to_conflict": excluded,
        "interpretation": "缺失条件未获确认；匹配条件来自调用者。候选仅限单场试片证据，仍须审看当前镜头和后镜。",
    }


def main():
    parser = argparse.ArgumentParser(
        description="按剧情关键词查找单场已审方法。仅精确匹配词表，无自然语言语义检索。",
    )
    parser.add_argument("--list", action="store_true", dest="list_only",
                        help="列出可用需求词与条件键、别名。可选 --need 过滤。")
    parser.add_argument("--need", action="append", type=keyword, default=[],
                        help="剧情需求或已登记的表达方向。重复指定时，候选须同时匹配所有词。")
    parser.add_argument("--conditions", action="append", type=keyword, default=[],
                        help="调用方已确认的可见条件关键词或 key。每个参数提供一项，可重复。")
    args = parser.parse_args()
    if not args.list_only and not args.need:
        parser.error("检索须提供 --need，浏览词表请用 --list。")
    if args.list_only and args.conditions:
        parser.error("--conditions 用于检索候选；--list 可用 --need 过滤词表。")
    data = json.loads(REFERENCE.read_text(encoding="utf-8"))
    if data["schema_version"] != 1:
        parser.error(f"不支持的证据版本 {data['schema_version']}")
    if any(row["scope"] != "single-scene-tested" for row in data["methods"]):
        parser.error("证据项超出本筛选器支持的单场验证范围。")
    if args.list_only:
        result = list_methods(data["methods"], set(args.need))
    else:
        result = select(data["methods"], set(args.need), set(args.conditions))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
