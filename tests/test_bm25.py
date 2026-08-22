from novel_kg_studio.store.bm25 import BM25Index


def test_bm25_ranks_relevant_doc_first():
    docs = [
        "the killer left through the window",
        "the gardener planted flowers in the flower bed",
        "the front door was half open",
    ]
    index = BM25Index(docs)
    scores = index.score("killer window")
    assert int(scores.argmax()) == 0

