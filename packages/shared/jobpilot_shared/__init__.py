"""Shared schemas and the anti-hallucination whitelist gate.

This package performs no I/O: no database, no HTTP, no LLM client. It is the only
package the gate tests need, which is why the gate can be proven before the
tailoring engine that it guards exists.

It must never import from ``services/``.
"""
