from app.graph.build import build_graph


def test_memory_nodes_are_wired() -> None:
    graph = build_graph(checkpointer=None)
    nodes = set(graph.get_graph().nodes)
    assert {"recall", "consolidate"} <= nodes


def test_graph_compiles_without_checkpointer() -> None:
    assert build_graph(checkpointer=None) is not None
