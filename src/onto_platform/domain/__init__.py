"""Domain: pure business semantics, zero technology dependencies.

    workspace/     平台容器
    ontology/      世界有什么 (ObjectType / Relation / Vocabulary + Compiler)
    data/          现实数据如何进入世界 (DataSource / Mapping / Projection)
    capability/    世界可以做什么 (Capability / Workflow)
    application/   面向最终用户的产品 (View / Query)
    intelligence/  Semantic / Agent 模型
    governance/    Access / Lifecycle / Audit

Domain 不允许知道 SQLite / HTTP / OpenAI / Kernel 的存在 (design.txt §37)。
"""
