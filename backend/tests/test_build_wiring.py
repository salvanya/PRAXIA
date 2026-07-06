from app.graph.build import build_graph


def test_memory_nodes_are_wired() -> None:
    graph = build_graph(checkpointer=None)
    nodes = set(graph.get_graph().nodes)
    assert {"recall", "consolidate", "memory_command"} <= nodes


def test_memory_command_goes_to_end_not_consolidate() -> None:
    graph = build_graph(checkpointer=None)
    g = graph.get_graph()
    targets = {e.target for e in g.edges if e.source == "memory_command"}
    assert "__end__" in targets and "consolidate" not in targets


def test_graph_compiles_without_checkpointer() -> None:
    assert build_graph(checkpointer=None) is not None
