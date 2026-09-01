"""Infrastructure: concrete technology behind the Ports.

    persistence/   Repository / AuditLog (memory, sqlite) + CodeStore (files)
    runtime/       KernelRuntimeGateway (Ontology Runtime Kernel)
    datasource/    static / sqlite / rest connectors
    llm/           echo (scripted) / openai-compatible providers
    execution/     workflow runner + platform function executor

Only this layer may import the kernel, SQLite, urllib or vendor SDKs.
"""
