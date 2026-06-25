from typing import TypedDict


class Chunk(TypedDict):
    text: str
    page: int | None
    chunk_index: int
    document_id: str
    title: str
    doc_type: str


class DocumentSummary(TypedDict):
    document_id: str
    status: str
    n_chunks: int
