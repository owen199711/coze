"""Atlas: a small, local-first coding agent core.

The package intentionally keeps the durable core small:
- Session / turn / event / checkpoint storage
- One knowledge store (facts, preferences, decisions, lessons, docs, commands)
- A tool registry and workspace guard
- A thin agent loop that can be driven by any chat-completions compatible model

The AgentScope adapter lives behind the same AgentRuntime boundary in a later
milestone; the M0 core is usable without it.
"""

__version__ = "0.1.0"
