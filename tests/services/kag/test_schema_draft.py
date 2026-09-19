# -*- coding: utf-8 -*-
"""M3.5 Schema 读→写转换（services/kag/schema_draft.py）的契约测试。

fixture 取自 M3.5 live 实测（m0ProbeLive.Person 的 queryProjectSchema 读
模型 + knext ``sanitize_for_serialization`` 拦截的权威 wire 形态——重放
200、CREATE/DELETE 语义跨请求验证通过）。
"""

from __future__ import annotations

from kagweb.services.kag.schema_draft import new_relation, read_type_to_draft

#: Person 读模型裁剪版（实测形态：富化 ref + @type + visibleScope 等）
READ_TYPE = {
    "@type": "ENTITY_TYPE",
    "basicInfo": {
        "name": {
            "@type": "SPG_TYPE",
            "namespace": "m0ProbeLive",
            "nameEn": "Person",
            "identityType": "SPG_TYPE",
        },
        "nameZh": "人物",
        "desc": "",
    },
    "parentTypeInfo": {
        "uniqueId": 2026091883230305,
        "parentUniqueId": 1,
        "parentTypeIdentifier": {
            "@type": "SPG_TYPE",
            "nameEn": "Thing",
            "identityType": "SPG_TYPE",
        },
        "inheritPath": [1, 2026091883230305],
    },
    "spgTypeEnum": "ENTITY_TYPE",
    "advancedConfig": {"visibleScope": "DOMAIN"},
    "projectId": 3,
    "ontologyId": {"uniqueId": 2026091883230305, "alterId": 2026091883230305},
    "extInfo": {"commonIndex": False},
    "properties": [
        {
            "advancedConfig": {
                "encryptTypeEnum": "NONE",
                "withIndex": False,
                "subProperties": [],
                "semantics": [],
                "indexType": None,
            },
            "basicInfo": {
                "name": {"@type": "PREDICATE", "name": "id", "identityType": "PREDICATE"},
                "nameZh": "标识",
                "desc": None,
            },
            "objectTypeRef": {
                "basicInfo": {"name": {"@type": "SPG_TYPE", "nameEn": "Text", "identityType": "SPG_TYPE"}, "nameZh": "文本"},
                "spgTypeEnum": "BASIC_TYPE",
                "projectId": 0,
                "ontologyId": {"uniqueId": 1, "alterId": 1},
            },
            "extInfo": {},
            "inherited": True,
        }
    ],
    "relations": [
        {
            "isDynamic": False,
            "subjectTypeRef": {
                "basicInfo": {"name": {"@type": "SPG_TYPE", "namespace": "m0ProbeLive", "nameEn": "Person", "identityType": "SPG_TYPE"}},
                "spgTypeEnum": "ENTITY_TYPE",
                "projectId": 3,
                "ontologyId": {"uniqueId": 1, "alterId": 1},
            },
            "objectTypeRef": {
                "basicInfo": {"name": {"@type": "SPG_TYPE", "namespace": "m0ProbeLive", "nameEn": "Organization", "identityType": "SPG_TYPE"}},
                "spgTypeEnum": "ENTITY_TYPE",
                "projectId": 3,
                "ontologyId": {"uniqueId": 2, "alterId": 2},
            },
            "basicInfo": {
                "name": {"@type": "PREDICATE", "name": "workFor", "identityType": "PREDICATE"},
                "nameZh": "任职于",
                "desc": "person works for organization",
            },
            "advancedConfig": {"encryptTypeEnum": "NONE", "withIndex": False, "subProperties": [], "semantics": []},
            "extInfo": {"valueType": "ENTITY_TYPE"},
            "inherited": False,
        }
    ],
}

OBJECT_TYPE = {
    "basicInfo": {
        "name": {"@type": "SPG_TYPE", "namespace": "m0ProbeLive", "nameEn": "Organization", "identityType": "SPG_TYPE"},
        "nameZh": "组织机构",
        "desc": "",
    },
    "spgTypeEnum": "ENTITY_TYPE",
}


def test_read_type_to_draft_matches_wire_contract() -> None:
    draft = read_type_to_draft(READ_TYPE)

    # type 级：注入 alterOperation，剔除 advancedConfig（wire 无此键——显式
    # null 会 500，M3.5 实测），其余原样（@type/basicInfo/extInfo/...）
    assert draft["alterOperation"] == "UPDATE"
    assert "advancedConfig" not in draft
    assert draft["@type"] == "ENTITY_TYPE"
    assert draft["basicInfo"]["name"]["nameEn"] == "Person"
    assert draft["parentTypeInfo"]["inheritPath"] == [1, 2026091883230305]

    # property 元素：富化键剔除、ref 精简、空 extInfo 剔除
    prop = draft["properties"][0]
    adv = prop["advancedConfig"]
    assert "encryptTypeEnum" not in adv
    assert "withIndex" not in adv
    assert "indexType" in adv  # 有值键保留（knext 行为）
    assert "subProperties" in adv and "semantics" in adv
    ref = prop["objectTypeRef"]
    assert sorted(ref.keys()) == ["basicInfo", "spgTypeEnum"]
    assert "extInfo" not in prop  # 空容器键剔除

    # relation 元素：同规则；非空 extInfo 保留
    rel = draft["relations"][0]
    assert sorted(rel["objectTypeRef"].keys()) == ["basicInfo", "spgTypeEnum"]
    assert rel["extInfo"] == {"valueType": "ENTITY_TYPE"}
    assert rel["isDynamic"] is False


def test_new_relation_uses_create_operation_template() -> None:
    """新增关系（knext add_relation 的 wire 拦截形态）：元素级 CREATE、
    subjectTypeRef 骨架、objectTypeRef 全量 basicInfo。"""
    rel = new_relation(
        host_type=READ_TYPE,
        object_type=OBJECT_TYPE,
        name="mentorOf",
        name_zh="指导",
        desc="",
    )
    assert rel["alterOperation"] == "CREATE"
    assert rel["basicInfo"]["name"] == {
        "@type": "PREDICATE",
        "name": "mentorOf",
        "identityType": "PREDICATE",
    }
    # subject 骨架（knext 实发形态），object 全量
    assert rel["subjectTypeRef"]["basicInfo"]["name"] == {"identityType": "SPG_TYPE", "@type": "SPG_TYPE"}
    assert rel["objectTypeRef"]["basicInfo"]["name"]["nameEn"] == "Organization"
    assert rel["objectTypeRef"]["spgTypeEnum"] == "ENTITY_TYPE"
    assert rel["advancedConfig"]["constraint"] == {"constraintItems": []}
    assert rel["isDynamic"] is False
