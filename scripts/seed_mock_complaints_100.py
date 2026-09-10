#!/usr/bin/env python3
"""向投诉数据库插入 100 条模拟工单，用于测试列表/看板/统计效果。

运行：
    ./venv/bin/python scripts/seed_mock_complaints_100.py

说明：
- 不覆盖已有数据。
- 生成不同时间（过去 90 天）、不同状态、不同网点、不同处理人的记录。
- 故意制造一些超时、已归档、已撤诉、三系统无信息的样本，方便测试过滤。
"""
import json
import os
import random
import sqlite3
import sys
import uuid
from datetime import datetime, timedelta

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "complaints.db")

LAST_NAMES = list("赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨朱秦尤许何吕施张孔曹严华")
FIRST_NAMES = ["伟", "芳", "娜", "敏", "静", "丽", "强", "磊", "军", "洋",
               "勇", "艳", "杰", "涛", "明", "超", "秀", "英", "华", "鹏",
               "飞", "婷", "刚", "强", "平", "辉", "刚", "桂", "芳", "娟"]
SCHOOLS = ["东", "常", "大", "岭", "高", "横", "厚", "寮", "企", "桥",
           "沙", "松", "塘", "谢", "长", "石", "殷", "权", "伟", "培", "娇", "滔", "祥", "华"]
CHANNELS = ["交通部门", "12345", "电话来访", "信访", "邮件投诉", "驾培协会", "其他途径"]
TYPES = ["A", "B", "C", "D", "E"]
HANDLERS = [
    {"name": "系统管理员", "user_id": 1},
    {"name": "谢绍祺", "user_id": 29},
    {"name": "陈伟良", "user_id": 0},
    {"name": "周耀培", "user_id": 0},
    {"name": "", "user_id": 0},
]


def random_id_card():
    # 生成 18 位假身份证号（仅作测试展示，校验位不保证真实）
    prefix = random.choice(["440881", "441900", "440101", "440303", "330106", "510107"])
    birth = (datetime.now() - timedelta(days=random.randint(18 * 365, 60 * 365))).strftime("%Y%m%d")
    seq = str(random.randint(0, 999)).zfill(3)
    base = prefix + birth + seq
    # 简易加权求模（第 18 位校验）
    weights = [7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2]
    check_map = "10X98765432"
    s = sum(int(base[i]) * weights[i] for i in range(17)) % 11
    return base + check_map[s]


def random_name():
    return random.choice(LAST_NAMES) + random.choice(FIRST_NAMES) + (random.random() > 0.7 and random.choice(FIRST_NAMES) or "")


def build_query_result(name, id_card, phone, school_short, complaint_type):
    return {
        "name": name,
        "id_card": id_card,
        "phone": phone,
        "school_short": school_short,
        "license_type": random.choice(["C1", "C2", "C1", "C1", "D"]),
        "complaint_type": complaint_type,
        "sources": {"internal": "success", "third": random.choice(["success", "not_found"]), "driving": "success"},
        "merged_into_existing": False,
    }


def main():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    before = cur.execute("SELECT COUNT(*) FROM complaint_tickets").fetchone()[0]

    now = datetime.now()
    today_str = now.strftime("%Y-%m-%d")
    rows = []
    for i in range(100):
        name = random_name()
        id_card = random_id_card()
        phone = "1" + random.choice("3456789") + "".join(str(random.randint(0, 9)) for _ in range(9))
        complaint_date = (now - timedelta(days=random.randint(0, 89))).strftime("%Y-%m-%d")
        created_at = (datetime.strptime(complaint_date, "%Y-%m-%d") + timedelta(hours=random.randint(8, 22), minutes=random.randint(0, 59))).strftime("%Y-%m-%d %H:%M:%S")
        c_type = random.choice(TYPES)
        channel = random.choice(CHANNELS)
        school_short = random.choice(SCHOOLS)

        # 状态分布：在途占多数，归档/撤诉占一部分
        status_roll = random.random()
        if status_roll < 0.35:
            handle_status = "待处理"
        elif status_roll < 0.70:
            handle_status = "处理中"
        elif status_roll < 0.85:
            handle_status = "已完结"
        else:
            handle_status = random.choice(["待处理", "处理中"])

        archive_status = "已归档" if handle_status == "已完结" else ""
        withdraw_status = "已撤诉" if (status_roll >= 0.85 and handle_status != "已完结") else "未撤诉"
        if withdraw_status == "已撤诉":
            handle_status = "处理中"
            archive_status = ""

        handler = random.choice(HANDLERS)
        intake_type = random.random() < 0.08 and "人工录入" or ""
        total_fee = random.choice([2680, 2980, 3280, 3980, 4280, 4980])
        actual_paid = random.choice([total_fee, total_fee - random.choice([0, 200, 500, 800])])
        refund_fee = max(0, round(actual_paid * random.uniform(0.1, 0.45), 2))
        deduction_fee = round(actual_paid - refund_fee, 2)

        ticket_id = str(uuid.uuid4())
        rows.append({
            "id": ticket_id,
            "ticket_no": f"TS{created_at.replace('-', '').replace(' ', '').replace(':', '')[:12]}{i+1:04d}",
            "student_name": name,
            "id_card": id_card,
            "phone": phone,
            "complaint_content": f"模拟投诉：学员对{random.choice(['退费金额', '培训安排', '教练服务', '合同条款', '考试预约'])}有异议。",
            "complaint_demands": random.choice(["要求全额退费", "要求退还部分费用", "要求继续培训", "要求更换教练", "要求道歉"]),
            "source_channel": channel,
            "complaint_date": complaint_date,
            "complaint_type": c_type,
            "priority": random.random() < 0.15 and "high" or "normal",
            "query_result": json.dumps(build_query_result(name, id_card, phone, school_short, c_type), ensure_ascii=False),
            "handle_status": handle_status,
            "archive_status": archive_status,
            "withdraw_status": withdraw_status,
            "handler_name": handler["name"],
            "handler_user_id": handler["user_id"] or None,
            "school_short": school_short,
            "school_name": f"{school_short}训练场",
            "total_fee": total_fee,
            "actual_paid": actual_paid,
            "deduction_fee": deduction_fee,
            "refund_fee": refund_fee,
            "deduction_detail": "[]",
            "created_at": created_at,
            "updated_at": created_at,
            "intake_type": intake_type,
            "final_outcome": archive_status == "已归档" and random.choice(["协商退费", "正常归档", "撤诉转归档"]) or "",
        })

    columns = [
        "id", "ticket_no", "student_name", "id_card", "phone", "complaint_content",
        "complaint_demands", "source_channel", "complaint_date", "complaint_type",
        "priority", "query_result", "handle_status", "archive_status", "withdraw_status",
        "handler_name", "handler_user_id", "school_short", "school_name", "total_fee",
        "actual_paid", "deduction_fee", "refund_fee", "deduction_detail", "created_at",
        "updated_at", "intake_type", "final_outcome"
    ]
    placeholders = ",".join("?" * len(columns))
    sql = f"INSERT INTO complaint_tickets ({','.join(columns)}) VALUES ({placeholders})"
    cur.executemany(sql, [tuple(r[c] for c in columns) for r in rows])
    conn.commit()
    after = cur.execute("SELECT COUNT(*) FROM complaint_tickets").fetchone()[0]
    conn.close()
    print(f"插入前: {before} 条, 插入后: {after} 条, 新增: {after - before} 条")


if __name__ == "__main__":
    main()
