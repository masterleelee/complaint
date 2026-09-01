"""经营单位字典与归属规范化。

数据来源：车辆信息表.xlsx 的「现存分校分店」「已注销」两个单元簿（2026-08-31 校准）。
覆盖此前仅 38 条、且与车辆台账命名不一致的旧字典。

约定：
- id 为稳定主键，已写入 complaint_tickets.organization_unit_id，只增不改，避免历史工单失联。
- name 以车辆台账为准；已注销网点保留在字典内，保证历史工单仍可解析到归属。
- 代号「全」（原「邓柳全」）经确认为「发」（李发良分店）的旧代号，同一分店；
  车辆数只记在「发」上，避免重复计入分母。
"""

ORGANIZATION_UNITS = [
    # ── 分校（现存，按车辆台账 41 个）──
    {"id": "branch-dongkeng-main", "type": "分校", "name": "总校", "code": "总", "active": True},
    {"id": "branch-changping-hengjiangxia", "type": "分校", "name": "常平横江厦分校", "code": "常", "active": True},
    {"id": "branch-dalang-songbolang", "type": "分校", "name": "大朗松柏朗分校", "code": "成", "active": True},
    {"id": "branch-dalingshan", "type": "分校", "name": "大岭山分校", "code": "李", "active": True},
    {"id": "branch-east-industrial-park", "type": "分校", "name": "常平东部分校", "code": "东", "active": True},
    {"id": "branch-dongcheng-zhushan", "type": "分校", "name": "东城主山分校", "code": "主", "active": True},
    {"id": "branch-gaobu-huancheng", "type": "分校", "name": "高埗分校", "code": "环", "active": True},
    {"id": "branch-hengli-hepan", "type": "分校", "name": "横沥河畔分校", "code": "沥", "active": True},
    {"id": "branch-houjie-hetian", "type": "分校", "name": "厚街河田分校", "code": "田", "active": True},
    {"id": "branch-liaobu-lingxia", "type": "分校", "name": "寮步岭厦分校", "code": "步A", "active": True},
    {"id": "branch-qishi-zhenhua", "type": "分校", "name": "企石分校", "code": "企", "active": True},
    {"id": "branch-qiaotou-shaogangtou", "type": "分校", "name": "桥头邵岗头分校", "code": "栅E", "active": True},
    {"id": "branch-shatian", "type": "分校", "name": "沙田分校", "code": "栅D", "active": True},
    {"id": "branch-songshanhu", "type": "分校", "name": "松山湖分校", "code": "坑", "active": True},
    {"id": "branch-tangxia-lincun", "type": "分校", "name": "塘厦青溪分校", "code": "青C", "active": True},
    {"id": "branch-xiegang", "type": "分校", "name": "谢岗分校", "code": "岗", "active": True},
    {"id": "branch-changan", "type": "分校", "name": "长安分校", "code": "长", "active": True},
    {"id": "branch-shijie", "type": "分校", "name": "石碣分校", "code": "碣", "active": True},
    {"id": "branch-machong", "type": "分校", "name": "麻涌分校", "code": "麻", "active": True},
    # ── 分店（现存）──
    {"id": "store-yin-huanquan", "type": "分店", "name": "殷焕权分店", "code": "权", "active": True},
    {"id": "store-chen-weiliang", "type": "分店", "name": "李植棠分店", "code": "伟良", "active": True},
    {"id": "store-zhou-yaopei", "type": "分店", "name": "伦金平分店", "code": "培", "active": True},
    {"id": "store-zhou-lijiao", "type": "分店", "name": "陈建明分店", "code": "明仔", "active": True},
    {"id": "store-li-shuixiang", "type": "分店", "name": "李水祥分店", "code": "祥", "active": True},
    {"id": "store-li-jinhua", "type": "分店", "name": "黎锦华分店", "code": "华", "active": True},
    {"id": "store-yuan-jintao", "type": "分店", "name": "袁进滔分店", "code": "进", "active": True},
    {"id": "store-li-yongjiang", "type": "分店", "name": "黎永江分店", "code": "江", "active": True},
    {"id": "store-wu-weiguo", "type": "分店", "name": "吴卫国分店", "code": "国", "active": True},
    {"id": "store-deng-liuquan", "type": "分店", "name": "李发良分店（旧代号 全·邓柳全）", "code": "全", "active": False},
    {"id": "store-yuan-xiaokun", "type": "分店", "name": "袁效坤分店", "code": "坤", "active": True},
    {"id": "store-ding-dexiang", "type": "分店", "name": "丁德祥分店", "code": "丁", "active": True},
    {"id": "store-yin-yaohui", "type": "分店", "name": "尹耀辉分店", "code": "辉", "active": True},
    {"id": "store-hu-xianhan", "type": "分店", "name": "胡先汗分店", "code": "汗", "active": True},
    {"id": "store-ou-tuxiong", "type": "分店", "name": "欧土雄分店", "code": "欧", "active": True},
    {"id": "store-yin-jinwang", "type": "分店", "name": "殷金旺分店", "code": "妹", "active": True},
    {"id": "store-lu-kunhe", "type": "分店", "name": "卢昆合分店", "code": "昆", "active": True},
    {"id": "store-jiao-she", "type": "分店", "name": "东坑角社分店", "code": "健", "active": True},
    {"id": "store-huang-weiqiu", "type": "分店", "name": "黄伟秋分店", "code": "秋", "active": True},
    {"id": "store-lu-weiqiu", "type": "分店", "name": "卢伟秋分店", "code": "伟", "active": True},
    {"id": "store-li-faliang", "type": "分店", "name": "李发良分店", "code": "发", "active": True},
    {"id": "store-huangjiang-longjiantian", "type": "分店", "name": "黄江龙见田分店", "code": "见", "active": True},
    {"id": "store-yao-weifeng", "type": "分店", "name": "姚伟锋分店", "code": "姚", "active": True},
    # ── 历史工单中出现的注销网点（车辆数 0，仅从工单名称补录）──
    {"id": "branch-dongcheng-wentang", "type": "分校", "name": "东城温塘分校", "code": "塘", "active": False},
    {"id": "branch-changping-luwu", "type": "分校", "name": "常平卢屋分校", "code": "屋", "active": False},
    {"id": "branch-daojiao-daluo", "type": "分校", "name": "道滘大罗沙招生点", "code": "滘", "active": False},
    {"id": "branch-shengtai", "type": "分校", "name": "生态园分校", "code": "态", "active": False},
    {"id": "branch-wangniudun", "type": "分校", "name": "望牛墩分校", "code": "望", "active": False},
    {"id": "store-jianqing", "type": "分店", "name": "健青分店", "code": "青D", "active": False},
    {"id": "branch-changping-sima", "type": "分校", "name": "常平司马分校", "code": "马", "active": False},
    # ── 已注销（车辆数 0，保留字典以便历史工单解析）──
    {"id": "branch-liaobu-shida", "type": "分校", "name": "寮步石大路分校", "code": "丽", "active": False},
    {"id": "branch-humen-nanzha", "type": "分校", "name": "虎门南栅分校", "code": "栅C", "active": False},
    {"id": "branch-qiaotou-dongjiang", "type": "分校", "name": "桥头东江分校", "code": "栅B", "active": False},
    {"id": "branch-humen-beizha", "type": "分校", "name": "虎门北栅分校", "code": "栅A", "active": False},
    {"id": "branch-zhongtang", "type": "分校", "name": "中堂分校", "code": "中", "active": False},
    {"id": "branch-nancheng", "type": "分校", "name": "南城分校", "code": "南", "active": False},
    {"id": "branch-chashan", "type": "分校", "name": "茶山分校", "code": "茶", "active": False},
    {"id": "branch-dalang-meijing", "type": "分校", "name": "大朗美景分校", "code": "美", "active": False},
    {"id": "branch-shipai", "type": "分校", "name": "石排分校", "code": "排", "active": False},
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
