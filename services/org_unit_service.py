"""经营单位字典与归属规范化。"""

ORGANIZATION_UNITS = [
    {"id": "branch-dongkeng-main", "type": "分校", "name": "东坑总校", "code": "总", "active": True},
    {"id": "branch-changping-hengjiangxia", "type": "分校", "name": "常平横江厦分校", "code": "常", "active": True},
    {"id": "branch-dalang-songbolang", "type": "分校", "name": "大朗松柏朗分校", "code": "成", "active": True},
    {"id": "branch-dalingshan", "type": "分校", "name": "大岭山分校", "code": "李", "active": True},
    {"id": "branch-east-industrial-park", "type": "分校", "name": "东部工业园", "code": "东", "active": True},
    {"id": "branch-dongcheng-zhushan", "type": "分校", "name": "东城主山分校", "code": "主", "active": True},
    {"id": "branch-gaobu-huancheng", "type": "分校", "name": "高埗环城分校", "code": "环", "active": True},
    {"id": "branch-hengli-hepan", "type": "分校", "name": "横沥河畔分校", "code": "沥", "active": True},
    {"id": "branch-houjie-hetian", "type": "分校", "name": "厚街河田分校", "code": "田", "active": True},
    {"id": "branch-liaobu-lingxia", "type": "分校", "name": "寮步岭厦分校", "code": "步A", "active": True},
    {"id": "branch-qishi-zhenhua", "type": "分校", "name": "企石振华分校", "code": "企", "active": True},
    {"id": "branch-qiaotou-shaogangtou", "type": "分校", "name": "桥头邵岗头分校", "code": "栅E", "active": True},
    {"id": "branch-shatian", "type": "分校", "name": "沙田分校", "code": "栅D", "active": True},
    {"id": "branch-songshanhu", "type": "分校", "name": "松山湖分校", "code": "坑", "active": True},
    {"id": "branch-tangxia-lincun", "type": "分校", "name": "塘厦林村分校", "code": "青C", "active": True},
    {"id": "branch-xiegang", "type": "分校", "name": "谢岗分校", "code": "岗", "active": True},
    {"id": "branch-changan", "type": "分校", "name": "长安分校", "code": "长", "active": True},
    {"id": "branch-liaobu-shida", "type": "分校", "name": "寮步石大分校", "code": "丽", "active": True},
    {"id": "store-yin-huanquan", "type": "分店", "name": "殷焕权", "code": "权", "active": True},
    {"id": "store-chen-weiliang", "type": "分店", "name": "陈伟良", "code": "伟良", "active": True},
    {"id": "store-zhou-yaopei", "type": "分店", "name": "周耀培", "code": "培", "active": True},
    {"id": "store-zhou-lijiao", "type": "分店", "name": "周丽娇", "code": "明仔", "active": True},
    {"id": "store-li-shuixiang", "type": "分店", "name": "李水祥", "code": "祥", "active": True},
    {"id": "store-li-jinhua", "type": "分店", "name": "黎锦华", "code": "华", "active": True},
    {"id": "store-yuan-jintao", "type": "分店", "name": "袁进滔", "code": "进", "active": True},
    {"id": "store-li-yongjiang", "type": "分店", "name": "黎永江", "code": "江", "active": True},
    {"id": "store-wu-weiguo", "type": "分店", "name": "吴卫国", "code": "国", "active": True},
    {"id": "store-deng-liuquan", "type": "分店", "name": "邓柳全", "code": "全", "active": True},
    {"id": "store-yuan-xiaokun", "type": "分店", "name": "袁效坤", "code": "坤", "active": True},
    {"id": "store-ding-dexiang", "type": "分店", "name": "丁德祥", "code": "丁", "active": True},
    {"id": "store-yin-yaohui", "type": "分店", "name": "尹耀辉", "code": "辉", "active": True},
    {"id": "store-hu-xianhan", "type": "分店", "name": "胡先汗", "code": "汗", "active": True},
    {"id": "store-ou-tuxiong", "type": "分店", "name": "欧土雄", "code": "欧", "active": True},
    {"id": "store-yin-jinwang", "type": "分店", "name": "殷金旺", "code": "妹", "active": True},
    {"id": "store-lu-kunhe", "type": "分店", "name": "卢昆合", "code": "昆", "active": True},
    {"id": "store-jiao-she", "type": "分店", "name": "角社", "code": "健", "active": True},
    {"id": "store-huang-weiqiu", "type": "分店", "name": "黄伟秋", "code": "秋", "active": True},
    {"id": "store-lu-weiqiu", "type": "分店", "name": "卢伟秋", "code": "伟", "active": True},
]


def resolve_org_unit(code: str = "", name: str = "") -> dict | None:
    code = (code or "").strip()
    name = (name or "").strip()
    for unit in ORGANIZATION_UNITS:
        if code and unit["code"] == code:
            return dict(unit)
    for unit in ORGANIZATION_UNITS:
        if name and (unit["name"] == name or name in unit["name"] or unit["name"] in name):
            return dict(unit)
    return None


def normalize_ticket_org_fields(data: dict) -> dict:
    unit = resolve_org_unit(
        data.get("organization_unit_code") or data.get("school_short", ""),
        data.get("organization_unit_name") or data.get("school_name", ""),
    )
    if unit:
        data["organization_unit_id"] = unit["id"]
        data["organization_unit_type"] = unit["type"]
        data["organization_unit_name"] = unit["name"]
        data["organization_unit_code"] = unit["code"]
        data.setdefault("school_short", unit["code"])
        return data

    code = (data.get("organization_unit_code") or data.get("school_short") or "").strip()
    name = (data.get("organization_unit_name") or data.get("school_name") or "").strip()
    if code or name:
        data["organization_unit_id"] = ""
        data["organization_unit_type"] = data.get("organization_unit_type", "") or "未入字典"
        data["organization_unit_name"] = name or code
        data["organization_unit_code"] = code
    return data
