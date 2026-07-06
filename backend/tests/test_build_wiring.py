from app.graph.build import build_graph


def test_memory_nodes_are_wired() -> None:
    graph = build_graph(checkpointer=None)
    nodes = set(graph.get_graph().nodes)
    assert {"recall", "consolidate", "memory_command"} <= nodes


def test_memory_command_conditional_reflect_wiring() -> None:
    graph = build_graph(checkpointer=None)
    g = graph.get_graph()
    targets = {e.target for e in g.edges if e.source == "memory_command"}
    assert "consolidate" in targets and "__end__" in targets  # condicional: reflect salvo forget


def test_graph_compiles_without_checkpointer() -> None:
    assert build_graph(checkpointer=None) is not None
