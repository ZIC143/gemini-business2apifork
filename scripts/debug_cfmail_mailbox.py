"""调试 CFMail 邮箱内容并复现项目中的验证码提取流程。

用法示例：
    python scripts/debug_cfmail_mailbox.py ^
      --base-url https://apimail.zicw.me ^
      --jwt eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJhZGRyZXNzIjoicXdla2NzcjdycGl6b3N5NWZzQHppY3cubWUiLCJhZGRyZXNzX2lkIjoiMTE0In0.5Vqk9DZVaS5BiYi6dAmf0amu7l-xtTiZDHhiEDrv_c8 ^
      --api-key odsajsjd.kasl ^
      --limit 5 ^
      --verify-ssl false

也支持环境变量：
    CFMAIL_BASE_URL
    CFMAIL_JWT
    CFMAIL_API_KEY
    CFMAIL_VERIFY_SSL
"""

from __future__ import annotations

import argparse
import email
import json
import os
import re
from typing import Any, Optional

import requests


def extract_verification_code(text: str) -> Optional[str]:
    """完全复用项目当前的验证码提取规则。"""
    if not text:
        return None

    context_pattern = r"(?:验证码|code|verification|passcode|pin).*?[:：]\s*([A-Za-z0-9]{4,8})\b"
    match = re.search(context_pattern, text, re.IGNORECASE)
    if match:
        candidate = match.group(1)
        if not re.match(r"^\d+(?:px|pt|em|rem|vh|vw|%)$", candidate, re.IGNORECASE):
            return candidate

    match = re.search(r"[A-Z0-9]{6}", text)
    if match:
        return match.group(0)

    digits = re.findall(r"\b\d{6}\b", text)
    if digits:
        return digits[0]

    return None


def extract_body_from_raw(raw: str) -> str:
    """完全复用项目当前的 raw 邮件解析逻辑。"""
    if not raw:
        return ""
    try:
        msg = email.message_from_string(raw)
        parts: list[str] = []
        if msg.is_multipart():
            for part in msg.walk():
                ct = part.get_content_type()
                if ct in ("text/plain", "text/html"):
                    payload = part.get_payload(decode=True)
                    if payload:
                        charset = part.get_content_charset() or "utf-8"
                        parts.append(payload.decode(charset, errors="replace"))
        else:
            payload = msg.get_payload(decode=True)
            if payload:
                charset = msg.get_content_charset() or "utf-8"
                parts.append(payload.decode(charset, errors="replace"))
        return "".join(parts)
    except Exception as exc:
        return f"<RAW_PARSE_ERROR: {exc}>"


def parse_bool(value: Any, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    return default


def build_session(jwt: str, api_key: str) -> requests.Session:
    session = requests.Session()
    session.headers.update({"Authorization": f"Bearer {jwt}"})
    if api_key:
        session.headers["x-admin-auth"] = api_key
    return session


def print_json(title: str, data: Any) -> None:
    print(f"\n===== {title} =====")
    print(json.dumps(data, ensure_ascii=False, indent=2, default=str))


def trim_text(text: str, limit: int = 3000) -> str:
    text = text or ""
    return text if len(text) <= limit else text[:limit] + "\n...<TRUNCATED>"


def normalize_messages_order(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按新到旧排序。"""
    try:
        return sorted(messages, key=lambda m: int(m.get("id") or 0), reverse=True)
    except Exception:
        return messages


def main() -> int:
    parser = argparse.ArgumentParser(description="调试 CFMail 邮件拉取与验证码提取")
    parser.add_argument("--base-url", default=os.getenv("CFMAIL_BASE_URL", ""))
    parser.add_argument("--jwt", default=os.getenv("CFMAIL_JWT", ""))
    parser.add_argument("--api-key", default=os.getenv("CFMAIL_API_KEY", ""))
    parser.add_argument("--verify-ssl", default=os.getenv("CFMAIL_VERIFY_SSL", "true"))
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--mail-id", default="", help="只调试指定邮件 ID")
    parser.add_argument("--show-list-json", action="store_true", help="打印完整列表 JSON")
    args = parser.parse_args()

    if not args.base_url or not args.jwt:
        print("缺少参数：--base-url 和 --jwt 必填")
        return 2

    verify_ssl = parse_bool(args.verify_ssl, True)
    base_url = args.base_url.rstrip("/")
    session = build_session(args.jwt, args.api_key)

    print("===== 请求参数 =====")
    print(f"base_url   : {base_url}")
    print(f"verify_ssl : {verify_ssl}")
    print(f"limit      : {args.limit}")
    print(f"offset     : {args.offset}")
    print(f"mail_id    : {args.mail_id or '<auto>'}")

    list_resp = session.get(
        f"{base_url}/api/mails",
        params={"limit": args.limit, "offset": args.offset},
        timeout=30,
        verify=verify_ssl,
    )
    print(f"\n/api/mails?limit={args.limit}&offset={args.offset} -> HTTP {list_resp.status_code}")
    list_resp.raise_for_status()

    list_data = list_resp.json() if list_resp.content else {}
    messages = list_data.get("results", []) if isinstance(list_data, dict) else []
    total = int(list_data.get("total", len(messages)) or len(messages)) if isinstance(list_data, dict) else len(messages)
    if not isinstance(messages, list):
        messages = []

    if args.show_list_json:
        print_json("列表完整 JSON", list_data)

    print(f"收到 {len(messages)} 封邮件，总数 {total}")
    if not messages:
        return 0

    messages = normalize_messages_order(messages)

    fetched_ids = [int(m.get("id") or 0) for m in messages if str(m.get("id") or "").isdigit()]
    if fetched_ids and max(fetched_ids) < total and total > len(messages):
        latest_offset = max(total - args.limit, 0)
        print(f"\n检测到当前页可能不是最新邮件，自动再请求最后一页: offset={latest_offset}")
        latest_resp = session.get(
            f"{base_url}/api/mails",
            params={"limit": args.limit, "offset": latest_offset},
            timeout=30,
            verify=verify_ssl,
        )
        print(f"/api/mails?limit={args.limit}&offset={latest_offset} -> HTTP {latest_resp.status_code}")
        latest_resp.raise_for_status()
        latest_data = latest_resp.json() if latest_resp.content else {}
        latest_messages = latest_data.get("results", []) if isinstance(latest_data, dict) else []
        if isinstance(latest_messages, list) and latest_messages:
            messages = normalize_messages_order(latest_messages)
            print(f"已切换到最后一页，共 {len(messages)} 封，按新到旧处理")

    if args.mail_id:
        selected = [m for m in messages if str(m.get("id")) == str(args.mail_id)]
        if not selected:
            print(f"未在列表中找到 mail_id={args.mail_id}")
            return 1
        messages = selected

    for idx, msg in enumerate(messages, 1):
        mail_id = msg.get("id")
        print(f"\n{'=' * 20} 邮件 {idx}/{len(messages)} | id={mail_id} {'=' * 20}")
        summary = {
            "id": msg.get("id"),
            "created_at": msg.get("created_at") or msg.get("createdAt"),
            "from": msg.get("from"),
            "to": msg.get("to"),
            "subject": msg.get("subject"),
            "has_raw": bool(msg.get("raw")),
            "text_len": len(msg.get("text") or ""),
            "html_len": len(msg.get("html") or ""),
        }
        print_json("列表摘要", summary)

        list_raw = msg.get("raw") or ""
        if list_raw:
            body = extract_body_from_raw(list_raw)
            code = extract_verification_code(body)
            print("\n[列表 raw 解析正文]")
            print(trim_text(body))
            print(f"\n[列表 raw 提取验证码] {code}")
        else:
            print("\n[列表 raw 解析正文] <empty>")

        summary_text = (msg.get("subject") or "") + (msg.get("text") or "") + (msg.get("html") or "")
        summary_code = extract_verification_code(summary_text)
        print(f"\n[subject+text+html 提取验证码] {summary_code}")

        if not mail_id:
            continue

        detail_resp = session.get(
            f"{base_url}/api/mail/{mail_id}",
            timeout=30,
            verify=verify_ssl,
        )
        print(f"/api/mail/{mail_id} -> HTTP {detail_resp.status_code}")
        if detail_resp.status_code != 200:
            print(trim_text(detail_resp.text, 1000))
            continue

        detail = detail_resp.json() if detail_resp.content else {}
        detail_summary = {
            "id": detail.get("id"),
            "subject": detail.get("subject"),
            "has_raw": bool(detail.get("raw")),
            "keys": sorted(detail.keys()) if isinstance(detail, dict) else [],
        }
        print_json("详情摘要", detail_summary)

        detail_raw = detail.get("raw") or ""
        detail_body = extract_body_from_raw(detail_raw)
        detail_code = extract_verification_code(detail_body)
        print("\n[详情 raw 解析正文]")
        print(trim_text(detail_body))
        print(f"\n[详情 raw 提取验证码] {detail_code}")

        if detail_code or summary_code:
            print("\n>>> 此邮件按当前项目规则可以提取到验证码")
        else:
            print("\n>>> 此邮件按当前项目规则提取失败，优先检查正文格式 / 编码 / 验证码样式")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
