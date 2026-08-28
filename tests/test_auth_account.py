"""账号体系端到端验收（阶段 7）

覆盖原计划"65 题验收"中所有可由后端 + 前端直接验证的项目：

- 默认 admin/admin 自动创建
- 未登录访问 / → 跳登录页（返回 login.html）
- 未登录访问 /api/* → 401
- 错密 → 401
- 正确登录 → 注入 current_user
- /me / 退出 / 退出后访问 401
- 同账号异地登录 → 旧 session 失效
- /api/auth/login（三系统爬虫）跟 session 解耦
- 停用账号 → 403
- viewer GET 200 / POST 403
- admin 增删改 / 重置密码 / 启停用
- 改自己密码要原密码（且原密码错就 400）
- 改自己密码后 session 失效（防 hijack）
- handler 改他人 / 改自己 role 403
- admin 自停用 400
- 工单 handler_user_id 写入
- 列表 / 详情返回 handler_user_id
- 别名映射 + 立即回写工单
- 别名删除 + 工单回滚 handler_user_id=NULL

不依赖真实三系统爬虫，跑得快（<5s）。
"""
import io
import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

# 测试用临时 DB，避免污染真实数据
_TMP_DIR = Path(tempfile.mkdtemp(prefix="auth-tests-"))

import database  # noqa: E402
database.DB_PATH = _TMP_DIR / "test.db"
database._local.clear()

import app as app_module  # noqa: E402
from werkzeug.security import generate_password_hash
from services import user_service
from database import save_ticket, list_tickets, migrate_handler_names_to_user


# ═══════════════════════════════════════
#  Fixture
# ═══════════════════════════════════════

@pytest.fixture(autouse=True)
def _isolate_db(tmp_path, monkeypatch):
    """每个测试一个全新 DB + 重置 admin 密码。"""
    test_db = tmp_path / "test.db"
    database.DB_PATH = test_db  # 保持 Path 对象
    database._local.clear()
    database.init_db()
    # 关键：模块级的 _ensure_default_admin 只在 import 时跑一次，
    # 测试里动态切 DB_PATH 后必须手动建默认 admin
    database._ensure_default_admin()
    # 重置 admin 密码为 admin（默认初始化已建）
    import sqlite3
    conn = sqlite3.connect(str(test_db))
    conn.execute(
        "UPDATE users SET password_hash=?, session_version=0, status='启用' WHERE username='admin'",
        (generate_password_hash('admin'),),
    )
    conn.commit()
    conn.close()
    app_module.app.config["TESTING"] = True
    yield
    database._local.clear()


@pytest.fixture()
def client():
    return app_module.app.test_client()


@pytest.fixture()
def admin_client(client):
    """admin 登录后的 client。"""
    client.post('/api/session/login', json={'username': 'admin', 'password': 'admin'})
    return client


def _ensure_zhang():
    """测试用张三（handler）"""
    try:
        return user_service.create_user('zhangsan', '123456', '张三', 'handler')['user']
    except Exception:
        return next(u for u in user_service.list_users() if u['username'] == 'zhangsan')


def _ensure_lisi():
    try:
        return user_service.create_user('lisi', '123456', '李四', 'handler')['user']
    except Exception:
        return next(u for u in user_service.list_users() if u['username'] == 'lisi')


# ═══════════════════════════════════════
#  默认 admin + 登录闭环
# ═══════════════════════════════════════

class TestDefaultAdmin:
    def test_default_admin_exists(self):
        """首次启动自动建 admin/admin"""
        user = user_service.get_user_by_username('admin')
        assert user is not None
        assert user['role'] == 'admin'
        assert user['real_name'] == '系统管理员'
        assert user['status'] == '启用'

    def test_unauth_api_returns_401(self, client):
        r = client.get('/api/tickets')
        assert r.status_code == 401
        assert r.get_json()['code'] == 'unauthorized'

    def test_unauth_root_renders_login_page(self, client):
        r = client.get('/')
        body = r.data.decode('utf-8')
        assert '投诉处理系统' in body
        assert '登录' in body  # 登录按钮/标题
        # 不是主框架
        assert '新增投诉' not in body

    def test_wrong_password_401(self, client):
        r = client.post('/api/session/login', json={'username': 'admin', 'password': 'wrong'})
        assert r.status_code == 401

    def test_login_success_and_user_info(self, client):
        r = client.post('/api/session/login', json={'username': 'admin', 'password': 'admin'})
        data = r.get_json()
        assert r.status_code == 200
        assert data['data']['real_name'] == '系统管理员'
        assert data['data']['role'] == 'admin'

    def test_me_endpoint(self, client):
        client.post('/api/session/login', json={'username': 'admin', 'password': 'admin'})
        r = client.get('/api/session/me')
        assert r.get_json()['data']['username'] == 'admin'

    def test_logout_then_401(self, client):
        client.post('/api/session/login', json={'username': 'admin', 'password': 'admin'})
        client.post('/api/session/logout')
        r = client.get('/api/tickets')
        assert r.status_code == 401

    def test_logged_in_root_injects_user(self, client):
        client.post('/api/session/login', json={'username': 'admin', 'password': 'admin'})
        r = client.get('/')
        body = r.data.decode('utf-8')
        assert '__CURRENT_USER__' in body
        # 注入的 JSON 反 escape 后含 admin 用户名
        import re
        m = re.search(r'__CURRENT_USER__\s*=\s*(\{.*?\});', body)
        assert m, '未找到 __CURRENT_USER__ 注入'
        u = json.loads(m.group(1))
        assert u['username'] == 'admin'
        assert u['role'] == 'admin'

    def test_logged_in_root_renders_main(self, client):
        client.post('/api/session/login', json={'username': 'admin', 'password': 'admin'})
        r = client.get('/')
        body = r.data.decode('utf-8')
        # 主框架特征
        assert '新增投诉' in body
        assert '处理人' in body


# ═══════════════════════════════════════
#  踢人
# ═══════════════════════════════════════

class TestSessionEviction:
    def test_b_login_kicks_a(self, _isolate_db):
        s1 = app_module.app.test_client()
        s2 = app_module.app.test_client()
        s1.post('/api/session/login', json={'username': 'admin', 'password': 'admin'})
        s2.post('/api/session/login', json={'username': 'admin', 'password': 'admin'})
        # s1 session 因 session_version bump 失效
        r = s1.get('/api/session/me')
        assert r.status_code == 401


# ═══════════════════════════════════════
#  三系统爬虫登录与 session 解耦
# ═══════════════════════════════════════

class TestCrawlerLoginDecoupled:
    def test_crawler_login_unaffected_by_account_session(self, client):
        client.post('/api/session/login', json={'username': 'admin', 'password': 'admin'})
        client.post('/api/session/logout')
        # session 失效后，/api/auth/login（爬虫登录）不应因没 session 报 401
        r = client.post('/api/auth/login', json={'background': True})
        assert r.status_code != 401


# ═══════════════════════════════════════
#  停用 / 角色权限
# ═══════════════════════════════════════

class TestStatusAndRoles:
    def test_disabled_account_cannot_login(self, client):
        _ensure_zhang()
        # 停用
        user_service.set_user_status(_ensure_zhang()['id'], '停用')
        r = client.post('/api/session/login', json={'username': 'zhangsan', 'password': '123456'})
        assert r.status_code == 403

    def test_viewer_get_ok_post_403(self, client):
        user_service.create_user('reader01', '123456', '审计员', 'viewer')
        client.post('/api/session/login', json={'username': 'reader01', 'password': '123456'})
        assert client.get('/api/tickets').status_code == 200
        assert client.post('/api/intake/parse', json={'text': 'x' * 20}).status_code == 403
        assert client.put('/api/tickets/xxx', json={}).status_code == 403
        assert client.delete('/api/tickets/xxx').status_code == 403

    def test_handler_can_access_business_api(self, client):
        user_service.create_user('h01', '123456', '受理员', 'handler')
        client.post('/api/session/login', json={'username': 'h01', 'password': '123456'})
        assert client.get('/api/tickets').status_code == 200

    def test_admin_cannot_disable_self(self, admin_client):
        me = admin_client.get('/api/session/me').get_json()['data']
        r = admin_client.put(f'/api/users/{me["id"]}/status', json={'status': '停用'})
        assert r.status_code == 400
        assert '不能停用自己' in r.get_json()['error']


# ═══════════════════════════════════════
#  用户管理 API
# ═══════════════════════════════════════

class TestUserAPI:
    def test_list_users_admin_ok_handler_403(self, admin_client, client):
        r = admin_client.get('/api/users')
        assert r.status_code == 200
        assert isinstance(r.get_json()['data'], list)
        user_service.create_user('h01', '123456', '受理员', 'handler')
        s = app_module.app.test_client()
        s.post('/api/session/login', json={'username': 'h01', 'password': '123456'})
        assert s.get('/api/users').status_code == 403

    def test_create_user_duplicate_rejected(self, admin_client):
        admin_client.post('/api/users', json={
            'username': 'newone', 'password': '123456', 'real_name': '新人', 'role': 'handler'
        })
        r = admin_client.post('/api/users', json={
            'username': 'newone', 'password': 'x', 'real_name': 'x', 'role': 'handler'
        })
        assert r.status_code == 400
        assert '用户名已存在' in r.get_json()['error']

    def test_handler_cannot_create_user(self, client):
        user_service.create_user('h01', '123456', '受理员', 'handler')
        client.post('/api/session/login', json={'username': 'h01', 'password': '123456'})
        r = client.post('/api/users', json={
            'username': 'x', 'password': 'x', 'real_name': 'x', 'role': 'handler'
        })
        assert r.status_code == 403

    def test_admin_change_role(self, admin_client):
        r = admin_client.post('/api/users', json={
            'username': 'newone', 'password': '123456', 'real_name': '新人', 'role': 'handler'
        })
        nid = r.get_json()['data']['id']
        r2 = admin_client.put(f'/api/users/{nid}', json={'role': 'viewer', 'real_name': '改名'})
        assert r2.status_code == 200
        assert r2.get_json()['data']['role'] == 'viewer'

    def test_handler_cannot_change_others(self):
        """handler 不能改其他人的资料（用李四的 id 测试，登录张三）"""
        _ensure_zhang()
        lisi = _ensure_lisi()  # 李四
        s = app_module.app.test_client()
        s.post('/api/session/login', json={'username': 'zhangsan', 'password': '123456'})
        r = s.put(f'/api/users/{lisi["id"]}', json={'real_name': 'X'})
        assert r.status_code == 403

    def test_handler_cannot_change_own_role(self, client):
        _ensure_zhang()
        client.post('/api/session/login', json={'username': 'zhangsan', 'password': '123456'})
        me = client.get('/api/session/me').get_json()['data']
        r = client.put(f'/api/users/{me["id"]}', json={'role': 'admin'})
        assert r.status_code == 403

    def test_admin_reset_password_then_login(self, admin_client):
        r = admin_client.post('/api/users', json={
            'username': 'newone', 'password': '123456', 'real_name': '新人', 'role': 'handler'
        })
        nid = r.get_json()['data']['id']
        admin_client.post(f'/api/users/{nid}/reset-password', json={'new_password': '654321'})
        s = app_module.app.test_client()
        r2 = s.post('/api/session/login', json={'username': 'newone', 'password': '654321'})
        assert r2.status_code == 200

    def test_change_own_password_wrong_old(self, client):
        _ensure_zhang()
        client.post('/api/session/login', json={'username': 'zhangsan', 'password': '123456'})
        r = client.post('/api/users/me/password', json={'old_password': 'wrong', 'new_password': '000'})
        assert r.status_code == 400

    def test_change_own_password_invalidates_session(self, client):
        _ensure_zhang()
        client.post('/api/session/login', json={'username': 'zhangsan', 'password': '123456'})
        r = client.post('/api/users/me/password', json={'old_password': '123456', 'new_password': '000'})
        assert r.status_code == 200
        # session 立即失效
        r2 = client.get('/api/session/me')
        assert r2.status_code == 401


# ═══════════════════════════════════════
#  处理人字段（handler_user_id）
# ═══════════════════════════════════════

class TestHandlerUserId:
    def test_ticket_handler_user_id_written(self, admin_client):
        z = _ensure_zhang()
        tid = save_ticket({
            'student_name': '测试A', 'id_card': '110101199003070011', 'phone': '13800000000',
            'source_channel': '12345', 'complaint_date': '2026-08-28', 'complaint_type': 'A',
            'handler_name': '张三', 'handler_user_id': z['id'],
            'school_short': 'DC-04', 'registration_date': '2025-01-01',
            'license_type': 'C1', 'exam_stage': '科一', 'student_status': '在培', 'training_hours': {},
        })
        records, _ = list_tickets(limit=10)
        t = next(x for x in records if x['id'] == tid)
        assert t['handler_name'] == '张三'
        assert t['handler_user_id'] == z['id']

    def test_ticket_transfer_via_user_id(self, admin_client):
        z = _ensure_zhang()
        l = _ensure_lisi()
        tid = save_ticket({
            'student_name': '测试B', 'id_card': '110101199003070011', 'phone': '13800000000',
            'source_channel': '12345', 'complaint_date': '2026-08-28', 'complaint_type': 'A',
            'handler_name': '张三', 'handler_user_id': z['id'],
            'school_short': 'DC-04', 'registration_date': '2025-01-01',
            'license_type': 'C1', 'exam_stage': '科一', 'student_status': '在培', 'training_hours': {},
        })
        r = admin_client.put(f'/api/tickets/{tid}', json={
            'handler_user_id': l['id'], 'handler_name': '李四',
        })
        assert r.status_code == 200
        records, _ = list_tickets(limit=10)
        t = next(x for x in records if x['id'] == tid)
        assert t['handler_name'] == '李四'
        assert t['handler_user_id'] == l['id']

    def test_viewer_cannot_transfer(self, client):
        z = _ensure_zhang()
        l = _ensure_lisi()
        tid = save_ticket({
            'student_name': '测试C', 'id_card': '110101199003070011', 'phone': '13800000000',
            'source_channel': '12345', 'complaint_date': '2026-08-28', 'complaint_type': 'A',
            'handler_name': '张三', 'handler_user_id': z['id'],
            'school_short': 'DC-04', 'registration_date': '2025-01-01',
            'license_type': 'C1', 'exam_stage': '科一', 'student_status': '在培', 'training_hours': {},
        })
        user_service.create_user('viewer01', '123456', '审计员', 'viewer')
        client.post('/api/session/login', json={'username': 'viewer01', 'password': '123456'})
        r = client.put(f'/api/tickets/{tid}', json={'handler_user_id': l['id']})
        assert r.status_code == 403

    def test_assignable_excludes_viewer_and_disabled(self, admin_client):
        _ensure_zhang()
        _ensure_lisi()
        user_service.create_user('viewer01', '123456', '审计员', 'viewer')
        items = admin_client.get('/api/users/assignable').get_json()['data']
        usernames = {u['username'] for u in items}
        assert 'zhangsan' in usernames
        assert 'lisi' in usernames
        assert 'viewer01' not in usernames
        # 停用
        lisi = _ensure_lisi()
        user_service.set_user_status(lisi['id'], '停用')
        items = admin_client.get('/api/users/assignable').get_json()['data']
        usernames = {u['username'] for u in items}
        assert 'lisi' not in usernames

    def test_api_tickets_includes_handler_user_id(self, admin_client):
        z = _ensure_zhang()
        save_ticket({
            'student_name': '测试D', 'id_card': '110101199003070011', 'phone': '13800000000',
            'source_channel': '12345', 'complaint_date': '2026-08-28', 'complaint_type': 'A',
            'handler_name': '张三', 'handler_user_id': z['id'],
            'school_short': 'DC-04', 'registration_date': '2025-01-01',
            'license_type': 'C1', 'exam_stage': '科一', 'student_status': '在培', 'training_hours': {},
        })
        items = admin_client.get('/api/tickets').get_json()['data']['records']
        assert items
        assert 'handler_user_id' in items[0]
        assert items[0]['handler_user_id'] == z['id']


# ═══════════════════════════════════════
#  别名映射
# ═══════════════════════════════════════

class TestAliasAPI:
    def test_create_alias_writes_back(self, admin_client):
        z = _ensure_zhang()
        tid = save_ticket({
            'student_name': '历史学员', 'id_card': '110101199003070011', 'phone': '13800000000',
            'source_channel': '12345', 'complaint_date': '2025-08-28', 'complaint_type': 'A',
            'handler_name': '李老师', 'handler_user_id': None,
            'school_short': 'DC-04', 'registration_date': '2024-01-01',
            'license_type': 'C1', 'exam_stage': '科一', 'student_status': '在培', 'training_hours': {},
        })
        # 创建映射
        r = admin_client.post('/api/users/aliases', json={'old_name': '李老师', 'user_id': z['id']})
        assert r.status_code == 200
        assert r.get_json()['data']['rewritten'] == 1
        # 验证回写
        records, _ = list_tickets(limit=10)
        t = next(x for x in records if x['id'] == tid)
        assert t['handler_user_id'] == z['id']
        assert t['handler_name'] == '李老师'  # 原名保留

    def test_list_aliases(self, admin_client):
        """先建一个未映射的工单，再调列表接口验证 unmapped 包含它。"""
        save_ticket({
            'student_name': '历史3', 'id_card': '110101199003070011', 'phone': '13800000000',
            'source_channel': '12345', 'complaint_date': '2025-10-01', 'complaint_type': 'A',
            'handler_name': '李老师', 'handler_user_id': None,
            'school_short': 'DC-04', 'registration_date': '2024-01-01',
            'license_type': 'C1', 'exam_stage': '科一', 'student_status': '在培', 'training_hours': {},
        })
        r = admin_client.get('/api/users/aliases')
        data = r.get_json()['data']
        assert 'aliases' in data
        assert 'unmapped' in data
        assert any(u['old_name'] == '李老师' for u in data['unmapped'])

    def test_handler_cannot_access_aliases(self, client):
        _ensure_zhang()
        client.post('/api/session/login', json={'username': 'zhangsan', 'password': '123456'})
        assert client.get('/api/users/aliases').status_code == 403
        z = _ensure_zhang()
        assert client.post('/api/users/aliases', json={'old_name': 'X', 'user_id': z['id']}).status_code == 403

    def test_delete_alias_rolls_back(self, admin_client):
        z = _ensure_zhang()
        save_ticket({
            'student_name': '历史2', 'id_card': '110101199003070011', 'phone': '13800000000',
            'source_channel': '12345', 'complaint_date': '2025-09-01', 'complaint_type': 'A',
            'handler_name': '王老师', 'handler_user_id': None,
            'school_short': 'DC-04', 'registration_date': '2024-01-01',
            'license_type': 'C1', 'exam_stage': '科一', 'student_status': '在培', 'training_hours': {},
        })
        admin_client.post('/api/users/aliases', json={'old_name': '王老师', 'user_id': z['id']})
        alias_id = admin_client.get('/api/users/aliases').get_json()['data']['aliases'][0]['id']
        r = admin_client.delete(f'/api/users/aliases/{alias_id}')
        assert r.status_code == 200
        # 工单回滚
        records, _ = list_tickets(limit=10)
        t = next(x for x in records if x['handler_name'] == '王老师')
        assert t['handler_user_id'] is None

    def test_duplicate_alias_rejected(self, admin_client):
        z = _ensure_zhang()
        admin_client.post('/api/users/aliases', json={'old_name': '李老师', 'user_id': z['id']})
        r = admin_client.post('/api/users/aliases', json={'old_name': '李老师', 'user_id': z['id']})
        assert r.status_code == 400
