# 三系统 API 接口细节文档

> 编写日期：2026-05-11
> 本文档记录投诉处理系统中对接的三个外部驾培系统的 API 接口细节，包括鉴权方式、接口路径、入参出参、字段映射等。

---

## 目录

1. [公共机制：登录态管理](#1-公共机制登录态管理)
2. [内部系统（jxywxt）](#2-内部系统jxywxt)
3. [第三系统（jppt - 计时培训管理平台）](#3-第三系统jppt---计时培训管理平台)
4. [驾培系统（guanjiaxie - 电子合同）](#4-驾培系统guanjiaxie---电子合同)

---

## 1. 公共机制：登录态管理

### 1.1 架构

```
BaseCrawler (core/auth_manager.py)
├── HTTPClient (utils/http_client.py) — requests.Session 封装
├── CacheManager (utils/cache_manager.py) — 内存 + 文件持久化
└── 子类实现 _do_login() 提供各系统登录逻辑
```

### 1.2 鉴权方式

**全部三系统采用 Session-Cookie 鉴权**：
- 通过账号密码 + 图形验证码登录后，服务器返回 Session Cookie
- 后续所有请求携带该 Cookie 即可
- **无 Token 机制**，不涉及 Token 刷新

### 1.3 Cookie 缓存机制

| 特性 | 值 |
|------|-----|
| 存储位置 | `data/cache/*.json`（MD5(system:username) 作为文件名） |
| 默认有效期 | **7200 秒（2 小时）** |
| 过期处理 | 自动清除缓存文件，强制重新登录 |
| 内存缓存 | 启动时从磁盘加载有效缓存到内存，加速访问 |

### 1.4 HTTP 请求配置

| 配置项 | 默认值 |
|--------|--------|
| 超时时间 | 30 秒 |
| 最大重试次数 | 2 次（指数退避：1s / 2s） |
| SSL 验证 | 关闭（`verify=False`） |
| User-Agent | 模拟 macOS Chrome |

---

## 2. 内部系统（jxywxt）

### 2.1 基本信息

| 项目 | 内容 |
|------|------|
| 系统名称 | 驾校内部管理系统 |
| 基础 URL | `http://jxywxt.dgcheshang.cn:10003` |
| API 前缀 | `/sypro_jm` |
| 字符编码 | UTF-8 |
| 验证码类型 | 4 位数字字母 |

### 2.2 登录

**接口**: `POST /sypro_jm/userController/login.action`

| 参数 | 类型 | 说明 |
|------|------|------|
| `accounts` | string | 登录账号 |
| `pwd` | string | 密码（明文） |
| `rand` | string | OCR 识别的验证码 |

**验证码获取**: `GET /sypro_jm/authImage` → 返回 JPEG 图片二进制

**登录成功判断**: 响应 JSON 中 `obj.loginName` 字段非空

### 2.3 查询学员

**接口**: `POST /sypro_jm/xyxxController/listXyxx.action`

**入参**:

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `sfzh` | string | 是 | 身份证号 |
| `page` | int | 是 | 页码（固定 1） |
| `rows` | int | 是 | 每页条数（固定 10） |
| `sort` | string | 是 | 排序字段（createtime） |
| `order` | string | 是 | 排序方向（desc） |

**返回结构**（data.rows[0]）:

| JSON 字段 | 类型 | 含义 | 示例 |
|-----------|------|------|------|
| `name` | string | 学员姓名 | "陈金生" |
| `sfzh` | string | 身份证号 | "110101199003070011" |
| `sjhm` | string | 手机号 | "13800000000" |
| `pxcx` | string | 培训车型 | "C1" / "C2" |
| `orgid` | string | 分校代码 | |
| `orgname` | string | 分校全称 | "-东坑分校" |
| `orgdh` | string | **分校代号** | "明仔"/"麻"/"沥" |
| `xybh` | string | 学员编号 | |
| `xyzt` | string | 学员状态码 | "1"~"49" |
| `bmrq` | string | 报名日期 | "2025-03-15" |
| `createtime` | string | 创建时间 | "2025-03-15 10:30:00" |
| `isOwefees` | bool | 是否欠费 | |
| `id` | int | 学员 ID | |

**学员状态码映射**（xyzt → 中文）:

| 码 | 中文 | 码 | 中文 |
|----|------|----|------|
| 1 | 报名 | 9 | 已退学 |
| 2 | 科目一待考 | 10 | 暂停培训 |
| 3 | 科目二待考 | 12 | 科二待考 |
| 4 | 科目三待考 | 18 | 科一未通过 |
| 5 | 科目四待考 | 20 | 科二收 |
| 6 | 已结业 | 30 | 科三收 |
| 7 | 已领证 | 49 | 科四收 |
| 8 | 已注销 | | |

### 2.4 时间轴数据

**接口**: `GET /sypro_jm/xyxxController/queryXySjz.action`

| 参数 | 类型 | 说明 |
|------|------|------|
| `xyid` | string | 学员 ID |

**返回**: `data.obj` 为 JSON 字符串，解析后为数组：

| 字段 | 说明 | 示例 |
|------|------|------|
| `NodeTitle` | 节点标题 | "科一X年X月X日考试不合格" |
| `NodeTime` | 节点时间 | "2025-03-15" |

### 2.5 收费记录

**接口**: `GET /sypro_jm/xyxxController/queryXySfList.action`

| 参数 | 类型 | 说明 |
|------|------|------|
| `xyid` | string | 学员 ID |

**返回** `data.rows` 数组：

| 字段 | 说明 |
|------|------|
| `sfxm` | 收费项目名称 |
| `sfje` | 收费金额 |
| `sfrq` | 收费日期 |
| `bz` | 备注 |

---

## 3. 第三系统（jppt - 计时培训管理平台）

### 3.1 基本信息

| 项目 | 内容 |
|------|------|
| 系统名称 | 车尚机动车驾驶人计时培训管理平台 |
| 基础 URL | `http://jppt.dgcheshang.cn:8899` |
| 字符编码 | GBK |
| 验证码类型 | 4 位数字字母（验证码图片 URL: `/servlet/validate_image`） |

### 3.2 登录

**接口**: `POST /platform/login!login.action`

| 参数 | 类型 | 说明 |
|------|------|------|
| `loginId` | string | 登录账号 |
| `clear_password` | string | 密码（明文） |
| `password` | string | 密码 MD5 值 |
| `rand` | string | OCR 识别验证码 |
| `loginType` | string | 固定 `"3"` |
| `customstyle` | string | 固定 `"blue"` |

**登录成功判断**: 响应 HTML 中包含 `frameset` 或 `mainframe`

### 3.3 查询学员培训学时

**接口**: `POST /school/schoolOprAction!xsjdshList.action`

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `xyxshzVo.sfzmhm` | string | 是 | 身份证号 |
| `pageNumber` | int | 是 | 固定 1 |
| `pagesize` | int | 是 | 固定 10 |

**返回**: HTML 页面（GBK 编码），通过 BeautifulSoup 解析 `<tr>` 表格行

**判断学员存在**: 响应 HTML 文本中包含身份证号字符串

### 3.4 解析出的字段

| 字段名 | 说明 | 示例 |
|--------|------|------|
| `name` | 学员姓名 | "张晶晶" |
| `id_card` | 身份证号 | |
| `license_type` | 车型 | "C1" / "C2" |

**培训阶段记录**（每行对应一个科目阶段）:

| 列位置 | 字段名 | 说明 | 示例 |
|--------|--------|------|------|
| 第 5 列 | `stage_no` | 培训部分编号 | 1/2/3/4 |
| 第 6 列 | `training_time` | **平台总学时** | "15时49分" |
| 第 7 列 | `training_km` | 培训里程 | "24公里" |
| 第 8 列 | `platform_time` | **上传省平台总学时** | "13时4分" |
| 第 9 列 | `platform_km` | 平台里程 | "24公里" |
| 第 10 列 | `audit_time` | **审核有效总学时（用于扣费计算）** | "13时4分" |

**阶段编号对应科目**:

| 阶段编号 | 科目 |
|----------|------|
| 1 | 科目一 |
| 2 | 科目二 |
| 3 | 科目三 |
| 4 | 科目四 |

---

## 4. 驾培系统（guanjiaxie - 电子合同）

### 4.1 基本信息

| 项目 | 内容 |
|------|------|
| 系统名称 | 管家鞋驾培平台 |
| 基础 URL | `https://www.guanjiaxie.com:8089` |
| 验证码类型 | **算术验证码**（如 "3+5=?"） |

### 4.2 登录

**接口**: `POST /schoolLogin`

| 参数 | 类型 | 说明 |
|------|------|------|
| `schoolType` | string | 固定 `"1"` |
| `username` | string | 登录账号 |
| `password` | string | 密码 |
| `validateCode` | string | 算术验证码答案（数字） |
| `rememberMe` | string | 固定 `"false"` |

**验证码获取**: `GET /captcha/captchaImage?type=math&s={random}` → JPEG 图片

**验证码解析**: OCR 识别数字和运算符 → 计算算术结果

**登录成功判断**: `data.code == 0`

**重试机制**: 最多 20 次（每次尝试不同的可能答案）

### 4.3 查询学员

**接口**: `POST /business/student/list`

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `pageNum` | int | 是 | 固定 1 |
| `pageSize` | int | 是 | 固定 10 |
| `identity` | string | 是 | 身份证号 |

**返回结构**:

```json
{
  "code": 0,
  "rows": [
    {
      "id": "学员ID",
      "name": "学员姓名",
      "identity": "身份证号",
      "mobile": "手机号",
      "licenseType": "车型(C1/C2)",
      "createTime": "报名时间"
    }
  ]
}
```

### 4.4 检查电子合同是否存在

**接口**: `POST /business/student/checkContract`

| 参数 | 类型 | 说明 |
|------|------|------|
| `id` | string | 学员 ID |

**返回**: `{"code": 0}` 表示有合同

### 4.5 获取电子合同 PDF 路径

**接口**: `GET /business/student/viewContract/{student_id}`

**返回**: HTML 页面，从中提取 PDF 链接

**PDF 下载**: 从页面中正则提取 `.pdf` 路径，拼接完整 URL 后下载

### 4.6 完整合同下载流程

1. `query_student(id_card)` → 获取 `student_id` 和 `contract_available`
2. `_get_contract_info(student_id)` → 获取合同 PDF URL
3. 下载 PDF → 保存到本地文件系统

---

## 5. 查询流程总结

```
用户输入身份证号
    │
    ├─→ 内部系统
    │   ├─ 登录（Session Cookie）
    │   ├─ POST listXyxx.action（按 sfzh 查询）
    │   ├─ GET queryXySjz.action（时间轴）
    │   ├─ GET queryXySfList.action（收费记录）
    │   └─ 返回：姓名/手机号/分校代号/报名日期/考试进度/时间轴/收费
    │
    ├─→ 第三系统
    │   ├─ 登录（Session Cookie + MD5 密码）
    │   ├─ POST xsjdshList.action（按身份证查询）
    │   └─ 返回：姓名/车型/各科目学时（平台总学时/审核有效学时）
    │
    └─→ 驾培系统
        ├─ 登录（Session Cookie + 算术验证码）
        ├─ POST student/list（按身份证查询）
        ├─ POST checkContract（检查合同存在）
        └─ 返回：姓名/手机号/车型/报名时间/电子合同状态
```

## 6. 数据合并规则

| 字段 | 优先级 |
|------|--------|
| 学员姓名 | 内部系统 > 驾培系统 > 第三系统 |
| 手机号 | 内部系统 > 驾培系统 |
| 车型 | 内部系统 > 驾培系统 > 第三系统 |
| 报名日期 | 内部系统 > 驾培系统 |
| 分校名称/代号 | 内部系统 |
| 学员状态 | 内部系统 |
| 考试进度 | 内部系统时间轴分析 |
| 培训学时 | 第三系统（审核有效学时优先） |
| 电子合同 | 驾培系统 |
| 收费记录 | 内部系统 |
