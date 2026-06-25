import pytest

from app import embeddings


@pytest.mark.integration  # downloads bge-m3 on first run
async def test_embed_dim_and_normalized():
    vecs = await embeddings.embed_texts(["hola mundo", "otro texto"])
    assert len(vecs) == 2
    assert len(vecs[0]) == 1024
    norm = sum(x * x for x in vecs[0]) ** 0.5
    assert abs(norm - 1.0) < 1e-3
