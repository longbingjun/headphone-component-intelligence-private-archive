#!/usr/bin/env python3
"""Audit every published supplier label without mutating the alias registry.

The model is a review assistant only.  It may surface candidates, but this
script never writes ``data/config/manufacturer_aliases.json`` and never merges
companies automatically.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from openai import OpenAI


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MANIFEST = ROOT / "web" / "public" / "data" / "supplier-sourcing" / "manifest.json"
DEFAULT_OUTPUT = ROOT.parent / "pilot-results" / "supplier-alias-audit"

SYSTEM_PROMPT = """你是拆解数据的供应商名称质量审计员。输入是当前网站全部供应商显示名称。
只依据输入字符串本身做数据清洗候选，不使用外部知识，不自动替业务决定企业法律关系。

只报告存在问题的名称，status 只能取：
- exact_alias：明显是同一采购供应商名称的大小写、前缀、简称或公司后缀变体；
- group_relation：字符串提示母子公司、集团或关联关系，不能直接合并；
- invalid_extraction：名称明显混入型号、参数、器件说明或不是公司名称；
- uncertain：疑似相关但字符串证据不足。

规则：
1. members 必须逐字取自输入 supplier 字段，不得创造名称；
2. exact_alias 的 canonical 必须从 members 中选择；
3. 母子公司不得标为 exact_alias；
4. 不确定时返回 uncertain；
5. 没有问题的单一名称不要输出。

只返回 JSON：
{"issues":[{"status":"exact_alias|group_relation|invalid_extraction|uncertain","members":["输入名称"],"canonical":"仅 exact_alias 填写，否则空字符串","reason":"简短理由"}]}"""


def load_env(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def parse_payload(content: str) -> dict[str, Any]:
    clean = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip(), flags=re.I)
    payload = json.loads(clean)
    return payload if isinstance(payload, dict) else {}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    parser.add_argument("--env-file", default=str(ROOT.parent / "secrets" / ".env.production"))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--batch-size", type=int, default=25)
    args = parser.parse_args()

    manifest_path = Path(args.manifest).resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    suppliers = [
        {
            "supplier": str(item.get("supplier") or ""),
            "products": int(item.get("products") or 0),
            "components": int(item.get("components") or 0),
            "brands": int(item.get("brands") or 0),
        }
        for item in manifest.get("suppliers") or []
        if str(item.get("supplier") or "").strip()
    ]
    supplier_names = {item["supplier"] for item in suppliers}

    load_env(Path(args.env_file).resolve())
    api_key = os.environ.get("APP_TEXT_MODEL_TOKEN", "").strip()
    api_url = os.environ.get("APP_TEXT_MODEL_BASE_URL", "").strip()
    model = os.environ.get("APP_TEXT_MODEL_NAME", "deepseek-v4-flash").strip() or "deepseek-v4-flash"
    if not api_key or not api_url:
        raise RuntimeError("APP_TEXT_MODEL_TOKEN / APP_TEXT_MODEL_BASE_URL not configured")

    client = OpenAI(api_key=api_key, base_url=api_url, timeout=90.0, max_retries=1)
    raw_issues: list[dict[str, Any]] = []
    failed_batches: list[dict[str, Any]] = []
    size = max(5, min(args.batch_size, 30))
    for start in range(0, len(suppliers), size):
        batch = suppliers[start:start + size]
        try:
            response = client.chat.completions.create(
                model=model,
                temperature=0.0,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": json.dumps({"suppliers": batch}, ensure_ascii=False)},
                ],
            )
            raw = parse_payload(response.choices[0].message.content or "{}")
            raw_issues.extend(item for item in raw.get("issues") or [] if isinstance(item, dict))
            print(f"[supplier alias audit] {min(start + size, len(suppliers))}/{len(suppliers)}", flush=True)
        except Exception as exc:
            failed_batches.append({
                "start": start,
                "supplier_names": [item["supplier"] for item in batch],
                "error": f"{type(exc).__name__}: {exc}",
            })
    accepted: list[dict[str, Any]] = []
    rejected = 0
    for item in raw_issues:
        if not isinstance(item, dict):
            rejected += 1
            continue
        status = str(item.get("status") or "")
        members = [str(value) for value in item.get("members") or []]
        canonical = str(item.get("canonical") or "")
        if (
            status not in {"exact_alias", "group_relation", "invalid_extraction", "uncertain"}
            or not members
            or any(member not in supplier_names for member in members)
            or (status == "exact_alias" and canonical not in members)
        ):
            rejected += 1
            continue
        accepted.append(
            {
                "status": status,
                "members": members,
                "canonical": canonical if status == "exact_alias" else "",
                "reason": str(item.get("reason") or "").strip(),
            }
        )

    run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    output_dir = Path(args.output_dir).resolve() / run_id
    output_dir.mkdir(parents=True, exist_ok=True)
    result = {
        "run": {
            "model": model,
            "manifest": str(manifest_path),
            "supplier_count": len(suppliers),
            "writes_registry": False,
            "failed_batches": failed_batches,
        },
        "summary": {
            "accepted_issue_groups": len(accepted),
            "rejected_model_items": rejected,
            "failed_batch_count": len(failed_batches),
            "by_status": {
                status: sum(item["status"] == status for item in accepted)
                for status in ("exact_alias", "group_relation", "invalid_extraction", "uncertain")
            },
        },
        "issues": accepted,
    }
    (output_dir / "result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({"output": str(output_dir), **result["summary"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
