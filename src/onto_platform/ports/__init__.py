"""Ports: what the platform needs from the outside world.

    runtime.py      RuntimeGateway  - the ONLY door to the Kernel
    datasource.py   DataSourceConnector - reality -> rows
    llm.py          LLMProvider     - understanding / generation
    storage.py      Repository / CodeStore / AuditLog
    execution.py    WorkflowRunner  - capability orchestration

Domain and Application layers depend on these Protocols only; every
concrete technology (Kernel, SQLite, REST, OpenAI, files) lives in
infrastructure/.
"""
