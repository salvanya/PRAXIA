from langchain_text_splitters import RecursiveCharacterTextSplitter

from app.config import get_settings
from app.ingest.parse import ParsedDoc
from app.models import Chunk


def chunk(parsed: ParsedDoc, document_id: str, title: str, doc_type: str) -> list[Chunk]:
    s = get_settings()
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=s.chunk_size, chunk_overlap=s.chunk_overlap
    )
    out: list[Chunk] = []
    idx = 0
    for page_no, text in parsed["pages"]:
        for piece in splitter.split_text(text):
            if not piece.strip():
                continue
            out.append(
                Chunk(
                    text=piece,
                    page=page_no,
                    chunk_index=idx,
                    document_id=document_id,
                    title=title,
                    doc_type=doc_type,
                )
            )
            idx += 1
    return out
