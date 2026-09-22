# Headphone Component Intelligence

一个面向竞品研究与成本工程场景的耳机器件情报作品集项目。它将公开拆解资料加工为可追溯的产品、BOM、供应商与品牌供应链视图。

> 这是固定、脱敏的小规模个人作品集演示。仓库仅包含 8 个结构化真实案例，不执行定时采集，也不包含公司私有配置、原始图片、全文、字幕、Cookie、API Key 或内部部署数据。原始信息请通过每条记录中的公开来源链接核对。

## 核心能力

- 产品档案：将拆解报告与视频线索对齐到统一产品模型。
- 器件—供应商溯源：按器件类型、耳机类型、使用位置、供应商和品牌筛选。
- 品牌供应链：查看品牌的器件组合、供应商关系与历史演变。
- 产品对比：支持同类与跨类产品的 BOM 与关键参数对比。
- 证据追溯：结构化事实保留来源链接与报告证据层。
- 生产架构适配：FastAPI/SQLAlchemy/Alembic + PostgreSQL + MinIO，静态前端由 Astro/React 生成。

## 架构

```text
公开报告 / 视频字幕
          |
          v
采集与证据对齐 -> 规则提取 + 可选 LLM 语义补全
          |
          v
规范化实体（产品 / BOM / 品牌 / 供应商 / 证据）
          |
          +--> PostgreSQL（结构化数据）
          +--> MinIO（图片与对象）
          |
          v
FastAPI 运行时 / Astro 静态导出 -> 工作台、溯源、供应链与对比页
```

更详细的数据边界和部署说明见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)。

## 本地运行作品集演示

环境要求：Python 3.11+ 与 Node.js 22.12+。

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

python scripts\build_matrix.py
python scripts\prepare_web_data.py

cd web
npm ci
npm run dev
```

打开 `http://127.0.0.1:4321/`。静态构建：

```powershell
cd web
npm run build:minio
```

本地演示数据可直接生成页面，不需要 API Key、Cookie、PostgreSQL 或 MinIO。发布前可执行 `python scripts/validate_public_demo.py` 检查公开数据边界。

## 生产能力验证

服务端通过环境变量连接 PostgreSQL 与 MinIO，并使用 Alembic 管理 schema。不要把真实凭据写入仓库；可从 `.env.example` 复制本地配置。

```powershell
python -m server.cli migrate
python -m server.cli import-data
python -m server.cli validate-release-data
python -m server.cli rebuild-site
```

## 数据与版权边界

- 公开信息来源之一为[我爱音频网拆解](https://www.52audio.com/archives/category/teardowns)；来源内容权利归原权利人所有。
- 仓库不再分发原始报告图片和全文，只保留少量脱敏结构化演示记录与来源 URL。
- 产品卡片和详情页使用本项目原创的类型化 SVG 示意封面，不冒充产品实拍或品牌官方素材。
- 统计结果只反映收录样本，不代表市场份额、实际采购量或供应商出货排名。
- 本仓库当前未授予开源许可；如需使用或合作，请先联系作者。

详细说明见 [数据来源与加工链路](docs/DATA_PROVENANCE.md)、[公开演示数据政策](docs/PUBLIC_DEMO_DATA_POLICY.md)、[第三方权利说明](docs/THIRD_PARTY_NOTICES.md)与[纠错/下架流程](docs/TAKEDOWN.md)。

## English summary

This portfolio project turns public headphone teardown evidence into traceable product, BOM, supplier, and brand supply-chain views. The repository contains a small sanitized dataset and excludes company credentials, private deployment details, source images, and full article text.
