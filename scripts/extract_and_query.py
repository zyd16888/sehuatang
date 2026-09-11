#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
从「番号 + 标题」列表中提取番号，并生成跨 collection 的 MongoDB 聚合查询
"""

import json
import re
from dataclasses import dataclass
from pathlib import Path

# ==================== 配置区 ====================
SCRIPT_DIR = Path(__file__).resolve().parent
INPUT_FILE = SCRIPT_DIR / "list.txt"
OUTPUT_DIR = SCRIPT_DIR / "generated"


@dataclass(frozen=True)
class CollectionSpec:
    name: str
    source_type: str
    number_field: str = "number"
    date_field: str = "post_time"
    normalized_number_field: str | None = None


COLLECTIONS = [
    CollectionSpec("hd_chinese_subtitles", "subtitle"),
    CollectionSpec("asia_codeless_originate", "uncensored"),
    CollectionSpec("EU_US_no_mosaic", "uncensored"),
    CollectionSpec("vegan_with_mosaic", "regular"),
    CollectionSpec("asia_mosaic_originate", "regular"),
    CollectionSpec("anime_originate", "regular"),
    CollectionSpec("vr_video", "regular"),
    CollectionSpec("4k_video", "regular"),
    CollectionSpec("domestic_original", "regular"),
    CollectionSpec("three_levels_photo", "regular"),
    CollectionSpec("korean_anchorman", "regular"),
    CollectionSpec(
        "javbee_items",
        "regular",
        number_field="code",
        date_field="date",
        normalized_number_field="code_normalized",
    ),
]
# subtitle/uncensored 由来源 collection 决定；regular 中标题含“破解”时升级为 cracked。
# ================================================


def extract_numbers(text: str) -> list[str]:
    """从文本中提取番号，兼容多种分隔符"""
    pattern = r"^([A-Z0-9]+[-\s]?\d+)"
    matches = re.findall(pattern, text, re.MULTILINE | re.IGNORECASE)

    cleaned = []
    for m in matches:
        num = re.sub(r"\s+", "", m.upper())
        num = re.sub(r"[－—–]", "-", num)
        cleaned.append(num)

    # 去重并保持顺序
    seen = set()
    result = []
    for n in cleaned:
        if n not in seen:
            seen.add(n)
            result.append(n)
    return result


def generate_priority_query(
    numbers: list[str],
    collections: list[CollectionSpec],
) -> str:
    """生成跨集合查询，保留中文字幕和无码，缺失时再降级。"""
    if not numbers:
        return "// 没有提取到任何番号"
    if not collections:
        return "// 没有配置任何 MongoDB collection"

    valid_source_types = {"subtitle", "uncensored", "regular"}
    collection_names = [spec.name for spec in collections]
    if len(collection_names) != len(set(collection_names)):
        raise ValueError("COLLECTIONS 中存在重复的 collection")
    for spec in collections:
        if not spec.name:
            raise ValueError("collection 名称不能为空")
        if spec.source_type not in valid_source_types:
            raise ValueError(
                f"collection {spec.name} 的类型 {spec.source_type} 不受支持"
            )
        if not spec.number_field or not spec.date_field:
            raise ValueError(f"collection {spec.name} 的字段配置不能为空")

    def render_number_match(spec: CollectionSpec) -> str:
        conditions = []
        if spec.normalized_number_field:
            normalized_numbers = [re.sub(r"[^A-Z0-9]", "", n.upper()) for n in numbers]
            field_js = json.dumps(spec.normalized_number_field, ensure_ascii=False)
            values_js = json.dumps(normalized_numbers, ensure_ascii=False)
            conditions.append(f'    {{ {field_js}: {{ $in: {values_js} }} }}')

        field_js = json.dumps(spec.number_field, ensure_ascii=False)
        for number in numbers:
            parts = re.split(r"[- ]", number, maxsplit=1)
            if len(parts) == 2:
                prefix, suffix = parts
                regex = f'"^{prefix}[- ]?{suffix}"'
            else:
                regex = f'"^{number}"'
            conditions.append(
                f'    {{ {field_js}: {{ $regex: {regex}, $options: "i" }} }}'
            )
        return "{\n  $or: [\n" + ",\n".join(conditions) + "\n  ]\n}"

    match_variables = []
    match_declarations = []
    match_variable_by_fields = {}
    for spec in collections:
        fields = (spec.number_field, spec.normalized_number_field)
        variable_name = match_variable_by_fields.get(fields)
        if variable_name is None:
            variable_name = f"numberMatch{len(match_variable_by_fields) + 1}"
            match_variable_by_fields[fields] = variable_name
            match_declarations.append(
                f"const {variable_name} = {render_number_match(spec)};"
            )
        match_variables.append(variable_name)

    first_spec = collections[0]

    def render_canonical_fields(spec: CollectionSpec, indent: str) -> list[str]:
        fields = []
        if spec.number_field != "number":
            source = f'"${spec.number_field}"'
            if spec.normalized_number_field:
                source = (
                    f'{{ $ifNull: ["${spec.number_field}", '
                    f'"${spec.normalized_number_field}"] }}'
                )
            fields.append(f"{indent}number: {source}")
        if spec.date_field != "post_time":
            fields.append(f'{indent}post_time: "${spec.date_field}"')
        return fields

    union_stages = []
    for spec, match_variable in zip(collections[1:], match_variables[1:]):
        collection_js = json.dumps(spec.name, ensure_ascii=False)
        source_type_js = json.dumps(spec.source_type, ensure_ascii=False)
        add_fields = render_canonical_fields(spec, "            ")
        add_fields.extend(
            [
                f"            source_collection: {collection_js}",
                f"            source_type: {source_type_js}",
            ]
        )
        add_fields_js = ",\n".join(add_fields)
        union_stages.append(
            f"""  {{
    $unionWith: {{
      coll: {collection_js},
      pipeline: [
        {{ $match: {match_variable} }},
        {{
          $addFields: {{
{add_fields_js}
          }}
        }}
      ]
    }}
  }}"""
        )

    union_body = ",\n".join(union_stages)
    if union_body:
        union_body += ",\n"

    first_collection_js = json.dumps(first_spec.name, ensure_ascii=False)
    first_source_type_js = json.dumps(first_spec.source_type, ensure_ascii=False)
    match_declarations_js = "\n\n".join(match_declarations)
    first_add_fields = render_canonical_fields(first_spec, "      ")
    first_add_fields.extend(
        [
            f"      source_collection: {first_collection_js}",
            f"      source_type: {first_source_type_js}",
        ]
    )
    first_add_fields_js = ",\n".join(first_add_fields)

    query = f"""// 需要 MongoDB 4.4+（使用 $unionWith）
{match_declarations_js}

db.getCollection({first_collection_js}).aggregate([
  // 1. 每个 collection 先过滤目标番号，再合并结果
  {{ $match: {match_variables[0]} }},
  {{
    $addFields: {{
{first_add_fields_js}
    }}
  }},
{union_body}

  // 2. 来源集合决定中文字幕/无码，其他集合再按标题识别破解
  {{
    $addFields: {{
      version_type: {{
        $switch: {{
          branches: [
            {{
              case: {{ $eq: ["$source_type", "subtitle"] }},
              then: "subtitle"
            }},
            {{
              case: {{ $eq: ["$source_type", "uncensored"] }},
              then: "uncensored"
            }},
            {{
              case: {{
                $regexMatch: {{
                  input: {{ $ifNull: ["$title", ""] }},
                  regex: "破解",
                  options: "i"
                }}
              }},
              then: "cracked"
            }}
          ],
          default: "regular"
        }}
      }}
    }}
  }},

  // 3. 中文字幕与无码同为最高级，破解次之，普通最低
  {{
    $addFields: {{
      version_priority: {{
        $switch: {{
          branches: [
            {{
              case: {{ $in: ["$version_type", ["subtitle", "uncensored"]] }},
              then: 1
            }},
            {{
              case: {{ $eq: ["$version_type", "cracked"] }},
              then: 2
            }}
          ],
          default: 3
        }}
      }}
    }}
  }},

  // 4. 每个番号的每种版本只保留最新一条
  {{
    $sort: {{
      number: 1,
      version_priority: 1,
      post_time: -1
    }}
  }},
  {{
    $group: {{
      _id: {{
        number: "$number",
        version_type: "$version_type"
      }},
      doc: {{ $first: "$$ROOT" }}
    }}
  }},

  // 5. 同一番号有中文字幕和无码时各保留一条
  {{
    $group: {{
      _id: "$_id.number",
      versions: {{ $push: "$doc" }}
    }}
  }},
  {{
    $set: {{
      preferred_versions: {{
        $filter: {{
          input: "$versions",
          as: "version",
          cond: {{
            $in: [
              "$$version.version_type",
              ["subtitle", "uncensored"]
            ]
          }}
        }}
      }},
      cracked_versions: {{
        $filter: {{
          input: "$versions",
          as: "version",
          cond: {{ $eq: ["$$version.version_type", "cracked"] }}
        }}
      }},
      regular_versions: {{
        $filter: {{
          input: "$versions",
          as: "version",
          cond: {{ $eq: ["$$version.version_type", "regular"] }}
        }}
      }}
    }}
  }},

  // 6. 两个最高类别都没有时，才回退到破解，再回退普通
  {{
    $set: {{
      selected_versions: {{
        $cond: [
          {{ $gt: [{{ $size: "$preferred_versions" }}, 0] }},
          "$preferred_versions",
          {{
            $cond: [
              {{ $gt: [{{ $size: "$cracked_versions" }}, 0] }},
              {{ $slice: ["$cracked_versions", 1] }},
              {{ $slice: ["$regular_versions", 1] }}
            ]
          }}
        ]
      }}
    }}
  }},
  {{ $unwind: "$selected_versions" }},
  {{ $replaceRoot: {{ newRoot: "$selected_versions" }} }},

  // 7. 只返回需要的字段和来源信息
  {{
    $project: {{
      number: 1,
      magnet: 1,
      title: 1,
      post_time: 1,
      version_type: 1,
      version_priority: 1,
      source_collection: 1,
      _id: 0
    }}
  }},

  // 8. 最终按番号和版本类型排序
  {{
    $sort: {{
      number: 1,
      version_priority: 1,
      version_type: 1
    }}
  }}
], {{ allowDiskUse: true }})"""
    return query


def main():
    input_path = INPUT_FILE
    if not input_path.exists():
        print(f"❌ 找不到文件: {INPUT_FILE}")
        print(f"请把「番号 + 标题」保存到 {input_path} 后再运行。")
        return

    text = input_path.read_text(encoding="utf-8")
    numbers = extract_numbers(text)

    print(f"✅ 成功提取 {len(numbers)} 个番号：\n")
    for n in numbers:
        print(f"  {n}")

    print("\n" + "=" * 70)
    print("MongoDB 跨集合聚合查询（中文字幕 = 无码 > 破解 > 普通）")
    print("=" * 70 + "\n")

    query = generate_priority_query(numbers, COLLECTIONS)
    print(query)

    # 同时写入文件
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output_file = OUTPUT_DIR / "mongodb_priority_query.js"
    output_file.write_text(query, encoding="utf-8")
    print(f"\n✅ 查询语句已保存到: {output_file}")


if __name__ == "__main__":
    main()
