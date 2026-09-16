from __future__ import annotations

import argparse
import csv
import os
import sqlite3
import subprocess
from collections import defaultdict
from pathlib import Path


PENDING = "PENDING"


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_text(path: Path, text: str, *, force: bool) -> None:
    if path.exists() and not force:
        raise FileExistsError(f"Refusing to overwrite an existing review packet: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.rstrip() + "\n", encoding="utf-8")


def source_link(url: str) -> str:
    return f"[查看原始来源]({url})" if url else "原始来源暂缺"


def yaml_block(lines: list[str]) -> str:
    return "```yaml\n" + "\n".join(lines) + "\n```"


def batched(rows: list[dict[str, str]], size: int) -> list[list[dict[str, str]]]:
    return [rows[index : index + size] for index in range(0, len(rows), size)]


def category_packets(audit_dir: Path, output_dir: Path, *, force: bool) -> list[Path]:
    rows = read_csv(audit_dir / "earphone-category-review.csv")
    paths: list[Path] = []
    for batch_index, batch in enumerate(batched(rows, 25), start=1):
        path = output_dir / "01-earphone-categories" / f"category-review-{batch_index:02d}.md"
        body = [
            f"# 耳机细分类审核 · 第 {batch_index} 批",
            "",
            "只修改每条记录 YAML 中的 `decision`、`final_category` 和 `note`。",
            "`decision` 可填写：`ACCEPT_SUGGESTION`、`KEEP_PENDING`、`OVERRIDE`。",
            "",
        ]
        for offset, row in enumerate(batch, start=(batch_index - 1) * 25 + 1):
            item_id = f"CAT-{offset:04d}"
            body.extend(
                [
                    f"## {item_id} · {row.get('brand') or '未知品牌'} {row.get('model') or '未知型号'}",
                    "",
                    f"- 产品 ID：`{row.get('product_id', '')}`",
                    f"- 当前分类：{row.get('current_category') or '待细分耳机'}",
                    f"- 建议分类：{row.get('proposed_category') or '无明确建议'}",
                    f"- 待确认原因：{row.get('review_reason') or row.get('basis') or '证据不足'}",
                    f"- 证据说明：{row.get('basis') or '—'}",
                    f"- 来源：{source_link(row.get('source_url', ''))}",
                    "",
                    yaml_block(
                        [
                            f"item_id: {item_id}",
                            f"decision: {PENDING}",
                            'final_category: ""',
                            'note: ""',
                        ]
                    ),
                    "",
                ]
            )
        write_text(path, "\n".join(body), force=force)
        paths.append(path)
    return paths


def alias_packet(
    audit_dir: Path,
    output_dir: Path,
    *,
    csv_name: str,
    output_name: str,
    title: str,
    item_prefix: str,
    force: bool,
) -> Path:
    rows = read_csv(audit_dir / csv_name)
    groups: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        groups[row.get("group_id", "")].append(row)
    body = [
        f"# {title}",
        "",
        "只修改 YAML。`decision` 可填写：`MERGE_ALIAS`、`KEEP_SEPARATE`、`DISPLAY_GROUP_ONLY`、`NEEDS_MORE_EVIDENCE`。",
        "只有确认是同一主体时才使用 `MERGE_ALIAS`；母子公司或集团关系应保持独立。",
        "",
    ]
    for index, group_id in enumerate(sorted(groups, key=lambda value: int(value or 0)), start=1):
        item_id = f"{item_prefix}-{index:03d}"
        members = groups[group_id]
        body.extend([f"## {item_id}", ""])
        for member in members:
            body.append(
                f"- **{member.get('name', '')}**：{member.get('unique_products', '0')} 款产品；"
                f"{source_link(member.get('sample_source_url', ''))}"
            )
        body.extend(
            [
                f"- 系统提示：{members[0].get('reason') or members[0].get('status') or '疑似别名'}",
                "",
                yaml_block(
                    [
                        f"item_id: {item_id}",
                        f"decision: {PENDING}",
                        'canonical_name: ""',
                        'note: ""',
                    ]
                ),
                "",
            ]
        )
    path = output_dir / output_name
    write_text(path, "\n".join(body), force=force)
    return path


def identity_packets(audit_dir: Path, output_dir: Path, *, force: bool) -> list[Path]:
    rows = read_csv(audit_dir / "product-identity-review.csv")
    paths: list[Path] = []
    for batch_index, batch in enumerate(batched(rows, 20), start=1):
        path = output_dir / "04-product-identities" / f"identity-review-{batch_index:02d}.md"
        body = [
            f"# 产品身份审核 · 第 {batch_index} 批",
            "",
            "只修改 YAML。`decision` 可填写：`CONFIRM`、`OVERRIDE`、`KEEP_UNRESOLVED`。",
            "",
        ]
        for offset, row in enumerate(batch, start=(batch_index - 1) * 20 + 1):
            item_id = f"IDENTITY-{offset:04d}"
            body.extend(
                [
                    f"## {item_id} · {row.get('brand') or '未知品牌'} {row.get('model') or '未知型号'}",
                    "",
                    f"- 产品 ID：`{row.get('product_id', '')}`",
                    f"- 当前分类：{row.get('category') or '—'}",
                    f"- 问题：{row.get('reason') or '品牌或型号未解析'}",
                    f"- 来源：{source_link(row.get('source_url', ''))}",
                    "",
                    yaml_block(
                        [
                            f"item_id: {item_id}",
                            f"decision: {PENDING}",
                            'final_brand: ""',
                            'final_model: ""',
                            'note: ""',
                        ]
                    ),
                    "",
                ]
            )
        write_text(path, "\n".join(body), force=force)
        paths.append(path)
    return paths


def sqlite_path(value: str) -> Path:
    prefix = "sqlite:///"
    if value.startswith(prefix):
        return Path(value[len(prefix) :])
    return Path(value)


def deleted_image_packet(root: Path, database: Path, output_dir: Path, *, force: bool) -> Path:
    result = subprocess.run(
        ["git", "diff", "--name-only", "--diff-filter=D", "--", "web/public/images"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    filenames = [Path(line).name for line in result.stdout.splitlines() if line.strip()]
    groups: dict[str, dict[str, object]] = {}
    if filenames:
        connection = sqlite3.connect(database)
        placeholders = ",".join("?" for _ in filenames)
        rows = connection.execute(
            "SELECT ia.owner_id, ia.object_key, r.title, r.url "
            "FROM image_assets ia LEFT JOIN reports r ON r.id = ia.owner_id "
            f"WHERE ia.owner_type = 'report' AND ia.object_key IN ({placeholders})",
            [f"images/{name}" for name in filenames],
        ).fetchall()
        connection.close()
        for report_id, object_key, title, url in rows:
            item = groups.setdefault(
                str(report_id),
                {"title": title or "未知来源报告", "url": url or "", "files": set()},
            )
            item["files"].add(Path(object_key).name)  # type: ignore[index,union-attr]
    mapped = {name for group in groups.values() for name in group["files"]}  # type: ignore[index]
    orphaned = sorted(set(filenames) - mapped)
    if orphaned:
        groups["UNMAPPED"] = {"title": "无法映射到单一来源报告", "url": "", "files": set(orphaned)}

    body = [
        "# 历史图片删除审核",
        "",
        "不要逐张判断；按来源报告确认该组图片是否仍属于耳机情报范围。",
        "`decision` 可填写：`KEEP_AND_MIGRATE`、`EXCLUDE_FROM_SCOPE`、`KEEP_AS_RELATED_INTELLIGENCE`、`NEEDS_MORE_EVIDENCE`。",
        "",
    ]
    for index, (report_id, group) in enumerate(
        sorted(groups.items(), key=lambda item: (-len(item[1]["files"]), item[0])), start=1  # type: ignore[arg-type]
    ):
        files = sorted(group["files"])  # type: ignore[arg-type]
        item_id = f"IMAGE-GROUP-{index:02d}"
        title = str(group["title"])
        lowered = title.lower()
        if any(token in title for token in ("音箱", "转接器", "适配器", "放大器")):
            recommendation = "EXCLUDE_FROM_SCOPE"
            rationale = "当前项目范围是耳机产品；该来源属于音箱、转接器或耳机放大器。"
        elif "获" in title and "采用" in title:
            recommendation = "KEEP_AS_RELATED_INTELLIGENCE"
            rationale = "属于器件采用文章，不是完整拆解报告，但可能作为供应链旁证。"
        elif "耳机" in title or "accentum" in lowered:
            recommendation = "KEEP_AND_MIGRATE"
            rationale = "属于耳机产品来源；应先迁移到正确产品档案，再清理旧路径。"
        else:
            recommendation = "NEEDS_MORE_EVIDENCE"
            rationale = "无法仅根据来源标题确定范围。"
        body.extend(
            [
                f"## {item_id} · {title}",
                "",
                f"- 报告 ID：`{report_id}`",
                f"- 涉及图片：{len(files)} 张",
                f"- 来源：{source_link(str(group['url']))}",
                f"- 系统建议：`{recommendation}`",
                f"- 建议原因：{rationale}",
                f"- 文件：{', '.join(f'`{name}`' for name in files)}",
                "",
                yaml_block(
                    [
                        f"item_id: {item_id}",
                        f"decision: {PENDING}",
                        'target_product_id: ""',
                        'note: ""',
                    ]
                ),
                "",
            ]
        )
    path = output_dir / "05-deleted-images.md"
    write_text(path, "\n".join(body), force=force)
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate editable Markdown packets for human review.")
    parser.add_argument("--audit-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--database", default=os.getenv("DATABASE_URL", ""))
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    output_dir = args.output_dir.resolve()
    generated: list[Path] = []
    generated.extend(category_packets(args.audit_dir, output_dir, force=args.force))
    generated.append(
        alias_packet(
            args.audit_dir,
            output_dir,
            csv_name="supplier-alias-review.csv",
            output_name="02-supplier-aliases.md",
            title="供应商别名与企业关系审核",
            item_prefix="SUPPLIER",
            force=args.force,
        )
    )
    generated.append(
        alias_packet(
            args.audit_dir,
            output_dir,
            csv_name="brand-alias-review.csv",
            output_name="03-brand-aliases.md",
            title="耳机品牌别名审核",
            item_prefix="BRAND",
            force=args.force,
        )
    )
    generated.extend(identity_packets(args.audit_dir, output_dir, force=args.force))
    if args.database:
        generated.append(
            deleted_image_packet(root, sqlite_path(args.database), output_dir, force=args.force)
        )

    readme = "\n".join(
        [
            "# 人工审核入口",
            "",
            "这里的 Markdown 是人工决策输入；`reports/data-quality` 下的 CSV 是只读审计输出，请不要修改 CSV。",
            "",
            "## 操作方法",
            "",
            "1. 打开各 Markdown，只修改 YAML 代码块中的字段。",
            "2. 不确定的条目继续保留 `PENDING`，不需要勉强判断。",
            "3. 修改完成后告诉 Codex：‘我已经完成 Markdown 审核，请读取并生成变更预览’。",
            "4. Codex 先输出变更预览；再次确认后才写入分类覆盖表、别名注册表或产品身份覆盖表。",
            "5. 审核文件不会自动改数据库，也不会自动删除图片。",
            "",
            "## 本批文件",
            "",
            *[f"- `{path.relative_to(output_dir).as_posix()}`" for path in generated],
        ]
    )
    write_text(output_dir / "README.md", readme, force=args.force)
    print(f"Generated {len(generated) + 1} review files under {output_dir}")


if __name__ == "__main__":
    main()
