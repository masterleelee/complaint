"""飞书多维表格服务 - 投诉记录登记"""
import requests
from datetime import datetime
from typing import Optional
from config import load_config


class FeishuService:
    """飞书 Bitable API 服务"""

    TOKEN_URL = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
    BITABLE_BASE = "https://open.feishu.cn/open-apis/bitable/v1/apps"

    def __init__(self):
        cfg = load_config().get("feishu", {})
        self.app_id = cfg.get("app_id", "")
        self.app_secret = cfg.get("app_secret", "")
        self.app_token = cfg.get("bitable_app_token", "")
        self.table_id = cfg.get("bitable_table_id", "")
        self._token: Optional[str] = None
        self._token_expires_at: Optional[float] = None  # Token 过期时间戳

    def _get_token(self) -> str:
        """获取 tenant_access_token（带过期检查，飞书 token 有效期 2 小时）"""
        import time
        if self._token and self._token_expires_at and time.time() < self._token_expires_at - 60:
            return self._token  # 还有 1 分钟以上余量，直接复用
        resp = requests.post(self.TOKEN_URL, json={
            "app_id": self.app_id,
            "app_secret": self.app_secret,
        }, timeout=10)
        data = resp.json()
        if data.get("code") == 0:
            self._token = data["tenant_access_token"]
            self._token_expires_at = time.time() + 7200  # 2 小时有效期
            return self._token
        raise Exception(f"飞书Token获取失败: {data.get('msg')}")

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._get_token()}",
            "Content-Type": "application/json",
        }

    def get_next_serial(self) -> int:
        """获取下一个序号"""
        try:
            url = f"{self.BITABLE_BASE}/{self.app_token}/tables/{self.table_id}/records"
            resp = requests.get(url, headers=self._headers(), params={
                "page_size": 1,
                "sort": '[{"field_name":"序号","desc":true}]',
            }, timeout=10)
            data = resp.json()
            if data.get("code") == 0:
                items = data.get("data", {}).get("items", [])
                if items:
                    last_serial = items[0].get("fields", {}).get("序号", 0)
                    return int(last_serial) + 1
        except Exception:
            pass
        return 1

    def add_record(self, complaint_data: dict) -> dict:
        """写入投诉记录到多维表格"""
        serial = self.get_next_serial()

        # 投诉时间转毫秒时间戳
        complaint_date_ms = None
        date_str = complaint_data.get("complaint_date", "")
        if date_str:
            try:
                dt = datetime.strptime(date_str, "%Y-%m-%d")
                complaint_date_ms = int(dt.timestamp() * 1000)
            except ValueError:
                pass

        # 生成处理编号: TS-{代号}-{日期}-{姓名}-{序号}
        school_short = complaint_data.get("school_short", "XX")
        date_part = datetime.now().strftime("%Y%m%d")
        name = complaint_data.get("name", "")
        handle_no = f"TS-{school_short}-{date_part}-{name}-{serial:03d}"

        registration_fee = float(
            complaint_data.get("registration_fee")
            or complaint_data.get("total_fee")
            or 0
        )
        deduction = float(
            complaint_data.get("deduction")
            or complaint_data.get("deduction_fee")
            or complaint_data.get("total_deduction")
            or 0
        )
        if "refund" in complaint_data and complaint_data["refund"] is not None:
            refund_amount = float(complaint_data["refund"] or 0)
        else:
            actual_paid = float(complaint_data.get("actual_paid") or registration_fee)
            refund_amount = max(0, actual_paid - deduction)

        # 构建字段映射（与飞书表格字段名一致）
        fields = {
            "处理编号": handle_no,
            "代号": school_short,
            "姓名": name,
            "身份证": complaint_data.get("id_card", ""),
            "手机号": complaint_data.get("phone", ""),
            "车型": complaint_data.get("license_type", ""),
            "考试阶段": complaint_data.get("exam_stage", ""),
            "报名费": registration_fee,
            "扣费": deduction,
            "应退金额": refund_amount,
            "处理状态": complaint_data.get("handle_status", "待处理"),
            "投诉渠道": complaint_data.get("complaint_channel", "交通局"),
            "备注": complaint_data.get("remarks", ""),
        }

        if complaint_date_ms:
            fields["投诉时间"] = complaint_date_ms

        complaint_type = complaint_data.get("complaint_type", "")
        if complaint_type:
            fields["投诉类型"] = complaint_type

        url = f"{self.BITABLE_BASE}/{self.app_token}/tables/{self.table_id}/records"
        resp = requests.post(url, headers=self._headers(), json={"fields": fields}, timeout=15)
        data = resp.json()

        if data.get("code") == 0:
            record_id = data.get("data", {}).get("record", {}).get("record_id", "")
            return {
                "success": True,
                "record_id": record_id,
                "serial": serial,
                "handle_no": handle_no,
            }
        else:
            return {"success": False, "error": data.get("msg", "未知错误")}

    def test_connection(self) -> dict:
        """测试飞书连接"""
        try:
            if not self.app_id or not self.app_secret:
                return {"success": False, "msg": "请先配置飞书App ID和App Secret"}
            self._token = None
            self._get_token()
            return {"success": True, "msg": "飞书连接成功"}
        except Exception as e:
            return {"success": False, "msg": str(e)}
