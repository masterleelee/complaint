import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP


_STATUS_STAGES = {
    "报名": 0,
    "科目一待考": 1,
    "科一未通过": 1,
    "科二收": 2,
    "科目二待考": 2,
    "科二待考": 2,
    "科三收": 3,
    "科目三待考": 3,
    "科四收": 4,
    "科目四待考": 4,
    "已结业": 4,
    "已领证": 4,
    "已注销": None,
    "已退学": None,
    "暂停培训": None,
}

_STAGE_ALIASES = {
    "报名": 0,
    "科一": 1,
    "科目一": 1,
    "科目一待考": 1,
    "科二": 2,
    "科目二": 2,
    "科二待考": 2,
    "科目二待考": 2,
    "科三": 3,
    "科目三": 3,
    "科目三待考": 3,
    "科四": 4,
    "科目四": 4,
    "科目四待考": 4,
    "已结业": 4,
    "已领证": 4,
}

_SUBJECT_ALIASES = {
    1: 1,
    "1": 1,
    "科一": 1,
    "科目一": 1,
    2: 2,
    "2": 2,
    "科二": 2,
    "科目二": 2,
    3: 3,
    "3": 3,
    "科三": 3,
    "科目三": 3,
    4: 4,
    "4": 4,
    "科四": 4,
    "科目四": 4,
}

_SUBJECT_NAMES = {1: "科目一", 2: "科目二", 3: "科目三", 4: "科目四"}

_EXAM_FEE_STANDARDS = {
    1: {"exam_fee": Decimal("70"), "makeup_fee": Decimal("35")},
    2: {"exam_fee": Decimal("130"), "makeup_fee": Decimal("65")},
    3: {"exam_fee": Decimal("280"), "makeup_fee": Decimal("140")},
    4: {"exam_fee": Decimal("0"), "makeup_fee": Decimal("0")},
}


def _decimal(value, field, *, allow_none=False):
    if value is None and allow_none:
        return None
    if isinstance(value, bool):
        raise ValueError(f"{field} must be finite non-negative")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be finite non-negative") from exc
    if not number.is_finite() or number < 0:
        raise ValueError(f"{field} must be finite non-negative")
    return number


def _signed_decimal(value, field):
    if isinstance(value, bool):
        raise ValueError(f"{field} must be finite")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be finite") from exc
    if not number.is_finite():
        raise ValueError(f"{field} must be finite")
    return number


def _integer(value, field):
    number = _decimal(value, field)
    if number != number.to_integral_value():
        raise ValueError(f"{field} must be a non-negative integer")
    return int(number)


def _money(value):
    rounded = value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if rounded == rounded.to_integral_value():
        return int(rounded)
    return float(rounded)


def _fmt_duration(minutes):
    total = int(minutes)
    return f"{total // 60}时{total % 60:02d}分"


def _cents(value):
    rounded = value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return int(rounded * 100)


def _from_cents(value):
    return Decimal(value) / Decimal("100")


def _valid_evidence_value(value):
    if isinstance(value, bool):
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, dict):
        return any(_valid_evidence_value(item) for item in value.values())
    if isinstance(value, list):
        return any(_valid_evidence_value(item) for item in value)
    if isinstance(value, (int, float, Decimal)):
        try:
            return Decimal(str(value)).is_finite()
        except InvalidOperation:
            return False
    return False


def _valid_evidence(value):
    if not isinstance(value, (str, dict, list)) or isinstance(value, bool):
        return False
    return _valid_evidence_value(value)


def _issue(code, message, contract_id=None):
    issue = {"code": code, "message": message}
    if contract_id:
        issue["contract_id"] = contract_id
    return issue


def _add_issue(items, code, message, contract_id=None):
    key = (code, contract_id)
    if any((item["code"], item.get("contract_id")) == key for item in items):
        return
    items.append(_issue(code, message, contract_id))


def _normalise_contract_set(contract_set):
    if not isinstance(contract_set, dict):
        raise ValueError("contract_set must be an object with a contracts list")
    sources = contract_set.get("contracts")
    if not isinstance(sources, list):
        raise ValueError("contract_set.contracts must be a list")

    contracts = []
    seen_ids = set()
    for index, source in enumerate(sources):
        if not isinstance(source, dict):
            raise ValueError(f"contract at index {index} must be an object")
        contract_id = source.get("contract_id")
        if not isinstance(contract_id, str) or not contract_id.strip():
            raise ValueError(
                f"contract_id at index {index} must be a non-empty string"
            )
        if contract_id in seen_ids:
            raise ValueError(f"duplicate contract_id: {contract_id}")
        seen_ids.add(contract_id)

        if "title" in source and "contract_title" in source:
            if source["title"] != source["contract_title"]:
                raise ValueError(
                    f"contract {contract_id} has conflicting aliases "
                    "title and contract_title"
                )
        title = source.get("title") or source.get("contract_title")
        if not title:
            raise ValueError(f"contract {contract_id} requires title")
        rules = source.get("rules")
        if not isinstance(rules, list):
            raise ValueError(f"contract {contract_id} rules must be a list")
        if any(not isinstance(rule, dict) for rule in rules):
            raise ValueError(f"contract {contract_id} rules must contain objects")

        canonical_total = (
            _decimal(
                source.get("total_fee"),
                f"{contract_id}.total_fee",
                allow_none=True,
            )
            if "total_fee" in source
            else None
        )
        legacy_total = (
            _decimal(
                source.get("total_amount"),
                f"{contract_id}.total_amount",
                allow_none=True,
            )
            if "total_amount" in source
            else None
        )
        if (
            "total_fee" in source
            and "total_amount" in source
            and canonical_total != legacy_total
        ):
            raise ValueError(
                f"contract {contract_id} has conflicting aliases "
                "total_fee and total_amount"
            )
        total_value = (
            canonical_total if "total_fee" in source else legacy_total
        )
        contracts.append(
            {
                **source,
                "contract_id": contract_id,
                "title": title,
                "total_fee": total_value,
                "rules": rules,
            }
        )
    return contracts


def _normalise_issues(contract_set, field):
    values = contract_set.get(field, [])
    if not isinstance(values, list):
        raise ValueError(f"{field} must be a list")
    issues = []
    for index, value in enumerate(values):
        if not isinstance(value, dict):
            raise ValueError(f"{field}[{index}] must be an object")
        code = value.get("code")
        message = value.get("message")
        contract_id = value.get("contract_id")
        if not isinstance(code, str) or not code.strip():
            raise ValueError(f"{field}[{index}].code must be a non-empty string")
        if not isinstance(message, str) or not message.strip():
            raise ValueError(f"{field}[{index}].message must be a non-empty string")
        if contract_id is not None and (
            not isinstance(contract_id, str) or not contract_id.strip()
        ):
            raise ValueError(
                f"{field}[{index}].contract_id must be a non-empty string"
            )
        issues.append(dict(value))
    return issues


def _normalise_subject(value, field):
    subject = _SUBJECT_ALIASES.get(value)
    if subject is None:
        raise ValueError(f"{field} must identify 科目一、科目二、科目三 or 科目四")
    return subject


def _subject_value(values, subject):
    if values is None:
        return False, None
    if not isinstance(values, dict):
        raise ValueError("subject progress values must be an object")
    matches = [
        values[alias]
        for alias, number in _SUBJECT_ALIASES.items()
        if number == subject and alias in values
    ]
    legacy_key = f"subject{subject}"
    if legacy_key in values:
        matches.append(values[legacy_key])
    if not matches:
        return False, None
    first = matches[0]
    for other in matches[1:]:
        try:
            equal = Decimal(str(first)) == Decimal(str(other))
        except (InvalidOperation, TypeError, ValueError):
            equal = first == other
        if not equal:
            raise ValueError(
                f"conflicting aliases for {_SUBJECT_NAMES[subject]} progress"
            )
    return True, first


def _exam_count(progress, subject):
    values = (
        progress.get("exam_counts")
        if "exam_counts" in progress
        else progress.get("exam_attempts")
    )
    found, value = _subject_value(values, subject)
    if not found:
        return False, None
    return True, _integer(value, f"exam_counts[{_SUBJECT_NAMES[subject]}]")


def _normalise_exam_source(values, field):
    if not isinstance(values, dict):
        raise ValueError(f"{field} must be an object")
    normalised = {}
    for subject in _SUBJECT_NAMES:
        found, value = _subject_value(values, subject)
        if found:
            normalised[subject] = _integer(
                value,
                f"{field}[{_SUBJECT_NAMES[subject]}]",
            )
    return normalised


def _validate_exam_source_aliases(progress):
    if "exam_counts" not in progress or "exam_attempts" not in progress:
        return
    exam_counts = _normalise_exam_source(progress["exam_counts"], "exam_counts")
    exam_attempts = _normalise_exam_source(
        progress["exam_attempts"],
        "exam_attempts",
    )
    if exam_counts != exam_attempts:
        raise ValueError("exam_counts and exam_attempts contain conflicting values")


def _parse_training_hours(value, field):
    if isinstance(value, str):
        text = value.strip()
        match = re.fullmatch(
            r"(\d+(?:\.\d+)?)\s*(?:时|小时)\s*(?:(\d+)\s*(?:分钟|分))?",
            text,
        )
        if match:
            hours = _decimal(match.group(1), field)
            minutes = _decimal(match.group(2) or 0, field)
            if minutes >= 60:
                raise ValueError(f"{field} minutes must be below 60")
            return hours * Decimal("60") + minutes
    return _decimal(value, field) * Decimal("60")


def _training_minutes(progress, subject):
    if "training_hours" in progress:
        found, value = _subject_value(progress.get("training_hours"), subject)
        if not found:
            return False, None
        return True, _parse_training_hours(
            value,
            f"training_hours[{_SUBJECT_NAMES[subject]}]",
        )
    found, value = _subject_value(progress.get("training_minutes"), subject)
    if not found:
        return False, None
    return True, _decimal(value, f"training_minutes[{_SUBJECT_NAMES[subject]}]")


def _stage_from_value(value):
    if value is None or value == "":
        return None
    if value in _STAGE_ALIASES:
        return _STAGE_ALIASES[value]
    if isinstance(value, str) and value.strip().isdigit():
        value = int(value.strip())
    if isinstance(value, (int, float, Decimal)):
        stage = _integer(value, "exam_stage")
        if stage <= 4:
            return stage
        return None
    return None


def _current_stage(progress):
    status = str(progress.get("student_status") or "").strip()
    if status in _STATUS_STAGES and _STATUS_STAGES[status] is not None:
        return True, _STATUS_STAGES[status]
    stage = _stage_from_value(progress.get("exam_stage"))
    return (stage is not None), stage


def _parse_date(value, field):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    try:
        return date.fromisoformat(text)
    except ValueError:
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
        except ValueError as exc:
            raise ValueError(f"{field} must be an ISO date") from exc


def _add_years(value, years):
    try:
        return value.replace(year=value.year + years)
    except ValueError:
        return value.replace(month=2, day=28, year=value.year + years)


def _expiry_state(contract, progress, warnings, blockers):
    expiry_rules = [
        rule for rule in contract["rules"] if rule.get("type") == "expiry_no_refund"
    ]
    years_value = contract.get("expiry_years")
    if years_value is None and expiry_rules:
        years_value = expiry_rules[0].get("expiry_years")
    if years_value is None:
        return False, None

    contract_id = contract["contract_id"]
    years = _integer(years_value, f"{contract_id}.expiry_years")
    registration_date = progress.get("registration_date")
    as_of_date = progress.get("as_of_date") or progress.get("current_date")
    if not registration_date:
        _add_issue(
            blockers,
            "missing_registration_date",
            f"{contract['title']}缺少报名时间，无法判断合同是否到期",
            contract_id,
        )
    if not as_of_date:
        _add_issue(
            blockers,
            "missing_as_of_date",
            f"{contract['title']}缺少计算日期，无法判断合同是否到期",
            contract_id,
        )
    if not registration_date or not as_of_date:
        return False, None

    expired = _parse_date(as_of_date, "as_of_date") >= _add_years(
        _parse_date(registration_date, "registration_date"),
        years,
    )
    if not expired:
        return False, None

    valid_no_refund_rule = None
    for rule in expiry_rules:
        evidence = (
            rule.get("evidence")
            if "evidence" in rule
            else contract.get("evidence")
        )
        if rule.get("clause") and _valid_evidence(evidence):
            valid_no_refund_rule = rule
            break
    if valid_no_refund_rule:
        _add_issue(
            warnings,
            "contract_expired_no_refund",
            f"{contract['title']}已到期，合同明确约定到期不退款",
            contract_id,
        )
        return True, valid_no_refund_rule

    _add_issue(
        warnings,
        "contract_expired",
        f"{contract['title']}已超过合同有效期",
        contract_id,
    )
    return True, None


def _line(contract, rule, amount, trigger, formula, max_amount=None):
    evidence = (
        rule.get("evidence") if "evidence" in rule else contract["evidence"]
    )
    reason = f"{rule['clause']}；{trigger}" if trigger else rule["clause"]
    line = {
        "contract_id": contract["contract_id"],
        "contract_title": contract["title"],
        "item": rule["item"],
        "amount": _money(amount),
        "amount_cents": _cents(amount),
        "clause": rule["clause"],
        "trigger": trigger,
        "formula": formula,
        "evidence": evidence,
        "reason": reason,
    }
    if max_amount is not None:
        line["max_amount"] = _money(max_amount)
    return line


def _rule_is_usable(contract, rule, blockers):
    contract_id = contract["contract_id"]
    usable = True
    if "manual_override" in rule:
        raise ValueError("rule-level manual_override is not supported")
    if rule.get("type") in {"fixed", "fixed_penalty"}:
        text = f"{rule.get('item', '')} {rule.get('clause', '')}"
        has_subject = re.search(r"科目?[一二三四]", text)
        if has_subject and "补考费" in text:
            raise ValueError("subject 补考费 must use makeup_fee rule")
        if has_subject and "考试费" in text:
            raise ValueError("subject 考试费 must use exam_fee rule")
    if not rule.get("item"):
        _add_issue(
            blockers,
            "missing_rule_item",
            f"{contract['title']}存在未命名扣费规则",
            contract_id,
        )
        usable = False
    if not rule.get("clause"):
        _add_issue(
            blockers,
            "missing_rule_clause",
            f"{contract['title']}的扣费规则缺少合同条款",
            contract_id,
        )
        usable = False
    evidence = (
        rule.get("evidence")
        if "evidence" in rule
        else contract.get("evidence")
    )
    if not _valid_evidence(evidence):
        _add_issue(
            blockers,
            "missing_contract_evidence",
            f"{contract['title']}缺少可核验的合同证据",
            contract_id,
        )
        usable = False
    return usable


def _validate_exam_standard(rule, subject, amount, blockers=None, contract=None):
    """考试费/补考费金额以合同约定或人工登记为准，不再强制等于政府固定标准。

    仅当金额超过固定标准 3 倍时给出提示级 warning（不拦截），防止明显录入错误。
    """
    expected = _EXAM_FEE_STANDARDS[subject][rule["type"]]
    if expected > 0 and amount > expected * 3:
        _add_issue(
            blockers or [],
            "exam_fee_unusually_high",
            f"{rule['item']}金额 {_money(amount)} 元明显高于常见标准 {_money(expected)} 元，请核对是否录入有误",
            (contract or {}).get("contract_id"),
        )


def _calculate_rule(contract, rule, progress, stage_state, blockers):
    rule_type = rule.get("type")
    item = rule["item"]

    if rule_type == "fixed":
        amount = _decimal(rule.get("amount"), f"{item}.amount")
        return _line(contract, rule, amount, "合同约定固定扣费", f"{_money(amount)}元")

    if rule_type == "stage_fee":
        amount = _decimal(rule.get("amount"), f"{item}.amount")
        required_stage = _integer(rule.get("stage"), f"{item}.stage")
        stage_found, stage = stage_state
        if not stage_found:
            _add_issue(
                blockers,
                "missing_stage",
                f"{contract['title']}的{item}缺少学员阶段",
                contract["contract_id"],
            )
            return None
        if stage < required_stage:
            return None
        return _line(
            contract,
            rule,
            amount,
            f"当前阶段{stage}已达到合同阶段{required_stage}",
            f"{_money(amount)}元",
        )

    if rule_type in {"exam_fee", "makeup_fee"}:
        amount = _decimal(rule.get("amount"), f"{item}.amount")
        subject = _normalise_subject(rule.get("subject"), f"{item}.subject")
        _validate_exam_standard(rule, subject, amount, blockers, contract)
        found, attempts = _exam_count(progress, subject)
        if not found:
            _add_issue(
                blockers,
                "missing_exam_count",
                f"{contract['title']}的{item}缺少{_SUBJECT_NAMES[subject]}考试次数",
                contract["contract_id"],
            )
            return None
        if rule_type == "exam_fee":
            if attempts < 1:
                return None
            return _line(
                contract,
                rule,
                amount,
                f"{_SUBJECT_NAMES[subject]}考试{attempts}次",
                f"首次考试费{_money(amount)}元",
            )
        makeup_count = max(attempts - 1, 0)
        if makeup_count == 0:
            return None
        total = amount * makeup_count
        return _line(
            contract,
            rule,
            total,
            f"{_SUBJECT_NAMES[subject]}考试{attempts}次，补考{makeup_count}次",
            f"{makeup_count}次 × {_money(amount)}元 = {_money(total)}元",
        )

    if rule_type == "training_hour_fee":
        rate = _decimal(rule.get("hourly_rate"), f"{item}.hourly_rate")
        subject = _normalise_subject(rule.get("subject"), f"{item}.subject")
        found, minutes = _training_minutes(progress, subject)
        if not found:
            _add_issue(
                blockers,
                "missing_training_hours",
                f"{contract['title']}的{item}缺少{_SUBJECT_NAMES[subject]}培训学时",
                contract["contract_id"],
            )
            return None
        if minutes == 0:
            return None
        total = rate * minutes / Decimal("60")
        cap = _decimal(
            rule.get("max_amount"),
            f"{item}.max_amount",
            allow_none=True,
        ) if "max_amount" in rule else None
        capped = cap is not None and total > cap
        if capped:
            total = cap
        duration_text = _fmt_duration(minutes)
        formula = (
            f"总时长{duration_text}（{_money(minutes)}分钟）÷ 60 × "
            f"{_money(rate)}元/小时 = {_money(rate * minutes / Decimal('60'))}元"
        )
        if capped:
            formula += f"；按本项合同上限最高{_money(cap)}元，调整为{_money(total)}元"
        return _line(
            contract,
            rule,
            total,
            f"总时长：{duration_text}",
            formula,
            max_amount=cap,
        )

    if rule_type == "percentage_penalty":
        percentage = _decimal(rule.get("percentage"), f"{item}.percentage")
        if percentage > 100:
            raise ValueError("percentage must be between 0 and 100")
        total_fee = contract["total_fee"]
        if total_fee is None:
            return None
        ratio = percentage / Decimal("100")
        total = total_fee * ratio
        return _line(
            contract,
            rule,
            total,
            "学员提前解除合同",
            (
                f"{_money(total_fee)}元 × "
                f"{_money(ratio * 100)}% = {_money(total)}元"
            ),
        )

    if rule_type == "fixed_penalty":
        amount = _decimal(rule.get("amount"), f"{item}.amount")
        return _line(
            contract,
            rule,
            amount,
            "学员提前解除合同",
            f"固定违约金{_money(amount)}元",
        )

    if rule_type == "expiry_no_refund":
        return None

    raise ValueError(f"unsupported refund rule type: {rule_type}")


def _cap_contract_lines(contract, lines, warnings):
    """扣费合计允许大于合同总额（各科目仍受自身上限约束），不做总额封顶。"""
    return lines


def _empty_plan(warnings, blockers):
    return {
        "decision": "needs_input",
        "can_confirm": False,
        "total_contract_amount": None,
        "total_contract_fee": None,
        "total_fee": None,
        "actual_paid": None,
        "deductions": [],
        "total_deduction": 0,
        "total_deduction_cents": 0,
        "refund_amount": None,
        "refund": None,
        "refund_cents": None,
        "warnings": warnings,
        "blockers": blockers,
    }


def calculate_fee_plan(contract_set, progress_facts, manual_overrides=None):
    """逐合同计算，并返回可审计、可人工确认的退费方案。"""
    contracts = _normalise_contract_set(contract_set)
    input_warnings = _normalise_issues(contract_set, "warnings")
    input_blockers = _normalise_issues(contract_set, "blockers")
    if not isinstance(progress_facts, dict):
        raise ValueError("progress_facts must be an object")
    if manual_overrides is not None and not isinstance(manual_overrides, dict):
        raise ValueError("manual_overrides must be an object")
    if not contracts:
        return _empty_plan(
            input_warnings,
            input_blockers
            + [_issue("empty_contract_set", "至少需要一份合同才能计算退费")],
        )

    progress = progress_facts
    overrides = manual_overrides or {}
    warnings = input_warnings
    blockers = input_blockers
    _validate_exam_source_aliases(progress)
    stage_state = _current_stage(progress)
    deductions = []
    no_refund_contract_ids = set()

    for contract in contracts:
        contract_id = contract["contract_id"]
        if not _valid_evidence(contract.get("evidence")):
            _add_issue(
                blockers,
                "missing_contract_evidence",
                f"{contract['title']}缺少可核验的合同证据",
                contract_id,
            )
        if contract.get("missing_pages"):
            pages = "、".join(str(page) for page in contract["missing_pages"])
            _add_issue(
                warnings,
                "missing_contract_pages",
                f"{contract['title']}缺少第{pages}页，按现有清晰条款计算",
                contract_id,
            )
        for field in contract.get("missing_fields", []):
            _add_issue(
                warnings,
                "missing_contract_field",
                f"{contract['title']}缺少{field}",
                contract_id,
            )

        _, no_refund_rule = _expiry_state(contract, progress, warnings, blockers)
        if no_refund_rule and _rule_is_usable(contract, no_refund_rule, blockers):
            no_refund_contract_ids.add(contract_id)
            if contract["total_fee"] is not None:
                deductions.append(
                    _line(
                        contract,
                        no_refund_rule,
                        contract["total_fee"],
                        "合同已到期且明确约定到期不退款",
                        f"该合同可退额为0，扣除合同总额{_money(contract['total_fee'])}元",
                    )
                )
            else:
                deductions.append(
                    _line(
                        contract,
                        no_refund_rule,
                        Decimal("0"),
                        "合同已到期且明确约定到期不退款",
                        "合同总额未知，不影响该合同可退额为0的结论",
                    )
                )
            continue

        contract_lines = []
        for refund_rule in contract["rules"]:
            if not _rule_is_usable(contract, refund_rule, blockers):
                continue
            calculated = _calculate_rule(
                contract,
                refund_rule,
                progress,
                stage_state,
                blockers,
            )
            if calculated is not None:
                contract_lines.append(calculated)
        deductions.extend(_cap_contract_lines(contract, contract_lines, warnings))

    all_no_refund = len(no_refund_contract_ids) == len(contracts)
    for contract in contracts:
        if contract["total_fee"] is not None:
            continue
        if all_no_refund:
            _add_issue(
                warnings,
                "missing_contract_total",
                f"{contract['title']}缺少合同总额，但不影响到期不退款结论",
                contract["contract_id"],
            )
        else:
            _add_issue(
                blockers,
                "missing_contract_total",
                f"{contract['title']}缺少合同总额",
                contract["contract_id"],
            )

    known_totals = [contract["total_fee"] for contract in contracts]
    total_contract_amount = (
        None if any(value is None for value in known_totals) else sum(known_totals)
    )
    if "actual_paid" in overrides:
        actual_paid = _signed_decimal(overrides["actual_paid"], "actual_paid")
    else:
        actual_paid = total_contract_amount
    if actual_paid is not None and actual_paid <= 0:
        if all_no_refund:
            _add_issue(
                warnings,
                "non_positive_actual_paid",
                "实际已交金额小于或等于0，但不影响全部合同到期不退款结论",
            )
        else:
            _add_issue(
                blockers,
                "non_positive_actual_paid",
                "实际已交金额必须大于0才能确认退费方案",
            )

    total_deduction_cents = sum(line["amount_cents"] for line in deductions)
    total_deduction = _from_cents(total_deduction_cents)
    can_confirm = not blockers and (actual_paid is not None or all_no_refund)
    if all_no_refund and not blockers:
        decision = "expired_no_refund"
        refund_cents = 0
    elif can_confirm:
        decision = "calculated"
        refund_cents = max(_cents(actual_paid) - total_deduction_cents, 0)
    else:
        decision = "needs_input"
        refund_cents = None
    refund_amount = None if refund_cents is None else _from_cents(refund_cents)
    display_total_fee = (
        None if total_contract_amount is None else _money(total_contract_amount)
    )
    display_refund = None if refund_amount is None else _money(refund_amount)

    return {
        "decision": decision,
        "can_confirm": can_confirm,
        "total_contract_amount": display_total_fee,
        "total_contract_fee": display_total_fee,
        "total_fee": display_total_fee,
        "actual_paid": None if actual_paid is None else _money(actual_paid),
        "deductions": deductions,
        "total_deduction": _money(total_deduction),
        "total_deduction_cents": total_deduction_cents,
        "refund_amount": display_refund,
        "refund": display_refund,
        "refund_cents": refund_cents,
        "warnings": warnings,
        "blockers": blockers,
    }
