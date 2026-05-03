from __future__ import annotations


SCHEMA_MARKDOWN = """# LMIT Wiki Schema

This knowledge base is assembled from converted Markdown sources.

## Layers

- `raw/`: imported Markdown copied from the conversion output. Treat as source material.
- `wiki/sources/`: one source note per imported raw document.
- `wiki/topics/`: topic pages maintained by humans or LLM agents.
- `wiki/entities/`: people, organizations, projects, tools, and products.
- `wiki/queries/`: grounded answers, comparisons, and syntheses filed back into the wiki.
- `schema/`: rules for maintaining this knowledge base.

## Source Notes

Each source note must keep traceability:

- source path
- raw path
- content hash
- discovered URLs
- generated excerpt

Source notes may be regenerated. Do not store manual-only edits there unless they can be recreated.

## Topic And Entity Pages

Topic and entity pages may synthesize across sources, but they must link back to source notes.

Rules:

- Prefer updating an existing topic page when the concept already exists.
- Create a new topic page when the source introduces a durable concept worth revisiting.
- Mark uncertainty explicitly.
- Do not replace source text with generated conclusions.
- Keep internal links relative when possible.

## Query Pages

Query pages are durable artifacts. When a grounded answer is useful beyond one chat turn:

- save it under `wiki/queries/`
- keep the original question
- cite the wiki pages or source notes used to answer it
- prefer filing useful syntheses back into the wiki instead of leaving them in chat history

## Log

`wiki/log.md` is append-only. Record ingest runs, source counts, and notable validation warnings.
"""

