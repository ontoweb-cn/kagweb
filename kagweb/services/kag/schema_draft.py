# -*- coding: utf-8 -*-
"""Schema 读→写模型转换（M3.5）。

OpenSPG 的读模型（``GET /public/v1/schema/queryProjectSchema``）是服务端富化
形态，直接回传 ``POST /public/v1/schema/alterSchema`` 会 400（Spring 反序列
化失败）。写模型（wire）权威形态取自 M3.5 实测：拦截 knext
``schema_alter_schema_post`` 抓 ``sanitize_for_serialization`` 产物并重放
200。核心差异（实测对照）：

- type 级注入 ``alterOperation: "UPDATE"``，``advancedConfig`` 置 ``None``
  （读模型的 ``visibleScope`` 等富化字段不进写模型）；
- property/relation 元素的 ``advancedConfig`` 收敛为
  ``{"subProperties": [], "semantics": []}``（读模型的
  ``encryptTypeEnum``/``withIndex`` 不进写模型）；
- ``subjectTypeRef``/``objectTypeRef`` 精简为 ``{basicInfo, spgTypeEnum}``
  （读模型携带 ``projectId``/``ontologyId`` 富化字段）；
- 其余键（``@type``/``basicInfo``/``extInfo``/``ontologyId``/
  ``parentTypeInfo``/``projectId``/``spgTypeEnum``/``inherited``/``isDynamic``）
  原样透传——整型覆写语义（knext ``update_type`` 的整只提交）。
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


def read_type_to_draft(spg_type: dict[str, Any], *, operation: str = "UPDATE") -> dict[str, Any]:
    """queryProjectSchema 读模型 → alterSchema 写模型（整型覆写）。"""
    draft = deepcopy(spg_type)
    if not isinstance(draft, dict):
        raise ValueError("spg_type 必须为 dict")
    draft["alterOperation"] = operation
    # wire 无 advancedConfig 键（knext 对 None 字段不序列化——显式置 null 会 500）
    draft.pop("advancedConfig", None)
    for prop in draft.get("properties") or []:
        _slim_item(prop)
    for rel in draft.get("relations") or []:
        _slim_item(rel)
    return draft


def new_relation(
    *,
    host_type: dict[str, Any],
    object_type: dict[str, Any],
    name: str,
    name_zh: str = "",
    desc: str = "",
) -> dict[str, Any]:
    """组装一条新 relation 的写模型元素（M3.5 实测：knext add_relation 的
    wire 拦截形态）。

    host_type/object_type 为读模型中的 SPG type；元素级
    ``alterOperation: "CREATE"``，subjectTypeRef 只需骨架（knext 实发形态），
    objectTypeRef 带目标类型全量 basicInfo；无 ontologyId/projectId 键
    （服务端分配）。
    """
    return {
        "alterOperation": "CREATE",
        "isDynamic": False,
        "basicInfo": {
            "name": {"@type": "PREDICATE", "name": name, "identityType": "PREDICATE"},
            "nameZh": name_zh,
            "desc": desc,
        },
        "subjectTypeRef": {
            "basicInfo": {"name": {"identityType": "SPG_TYPE", "@type": "SPG_TYPE"}}
        },
        "objectTypeRef": _ref(object_type),
        "advancedConfig": {
            "constraint": {"constraintItems": []},
            "subProperties": [],
            "semantics": [],
        },
    }


def _ref(spg_type: dict[str, Any]) -> dict[str, Any]:
    """``subjectTypeRef``/``objectTypeRef`` 的 wire 精简形态。"""
    return {
        "basicInfo": deepcopy(spg_type.get("basicInfo") or {}),
        "spgTypeEnum": spg_type.get("spgTypeEnum"),
    }


def _slim_item(item: dict[str, Any]) -> None:
    """property/relation 元素按 wire 形态收敛（就地）。

    advancedConfig 保留原键集（如 indexType——有值时 knext 保留），只剔除
    读模型富化键（encryptTypeEnum/withIndex）；``subjectTypeRef``/
    ``objectTypeRef`` 精简；空 extInfo 剔除（knext 不序列化空容器键）。
    """
    old_adv = item.get("advancedConfig")
    old_adv = old_adv if isinstance(old_adv, dict) else {}
    slim_adv = {
        k: v for k, v in old_adv.items() if k not in ("encryptTypeEnum", "withIndex")
    }
    item["advancedConfig"] = slim_adv
    for key in ("objectTypeRef", "subjectTypeRef"):
        ref = item.get(key)
        if isinstance(ref, dict):
            item[key] = {
                "basicInfo": ref.get("basicInfo"),
                "spgTypeEnum": ref.get("spgTypeEnum"),
            }
    if item.get("extInfo") == {}:
        item.pop("extInfo", None)
