# 本体建模与应用指南（Ontology Guide）

从概念体系到形式化模型，再到数据与应用的中文本体建模指南。内容覆盖总览、语义网与本体历史、标准与语言（RDF/OWL/SKOS/SHACL/SPARQL）、建模方法论与本体元素、实现架构与技术栈、应用场景、落地路线图与端到端案例，共 44 页。

本目录是一个独立的 VitePress 站点，可单独构建与部署，也可作为 ontoPlantform 平台的文档子项目。

## 目录结构

```
ontology-guide/
├─ docs/
│  ├─ index.md                  # 站点首页
│  ├─ main.md                   # 总览（FAQ）
│  ├─ history.md                # 本体与语义网历史
│  ├─ ontology-model.md         # 本体模型：是什么、不是什么
│  ├─ spec/                     # 标准与语言（RDF、OWL 2、SKOS、SHACL、SPARQL）
│  ├─ modeling/                 # 建模：方法论、本体元素、设计模式
│  ├─ impl/                     # 实现：架构模式、本体对象、JVM/Python/JS 技术栈
│  ├─ application/             # 应用：知识图谱、语义检索与 RAG、数据治理等
│  ├─ adoption/index.md         # 落地路线图
│  ├─ examples/index.md         # 端到端案例（电商本体）
│  └─ .vitepress/config.mts     # 站点配置（导航、侧边栏、语言）
├─ package.json
└─ README.md
```

## 本地开发

```bash
pnpm install
pnpm docs:dev      # 开发服务器
pnpm docs:build    # 生产构建，输出到 docs/.vitepress/dist
pnpm docs:preview  # 预览构建结果
```

## 部署

配置中的 `base` 目前是 `"/"`，即部署在域名根路径。若发布到 GitHub Pages 的仓库子路径（例如 `https://<org>.github.io/ontology-guide/`），请把 `docs/.vitepress/config.mts` 中的 `base` 改为 `"/ontology-guide/"` 后重新构建。

## 与 DDD 指南的关系

本指南是 [DDD 指南](https://ddd-fans.github.io/ddd-guideline/) 的姊妹篇：DDD 管理业务边界与行为，本体管理共享语义与数据互操作。正文中指向 DDD 指南章节的链接使用已发布站点的绝对地址；其余站内链接均指向本指南自身页面。

## 许可

沿用 [CC BY-SA 4.0](LICENSE) 许可协议。
