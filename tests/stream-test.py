from typing import TypedDict
from langgraph.graph import StateGraph, START, END
from langgraph.config import get_stream_writer
import time



class State(TypedDict):
    topic: str
    joke: str


def generate_joke(state: State):
    writer = get_stream_writer()
    time.sleep(0.5)
    writer({"status": "writer写入...20%"})
    time.sleep(0.5)
    writer({"status": "writer写入1...50%"})
    time.sleep(0.5)
    writer({"status": "writer写入2...99%"})
    return {"joke": f"Why did the {state['topic']} go to school? To get a sundae education!return 写入"}

graph = (
    StateGraph(State)
    .add_node(generate_joke)
    .add_edge(START, "generate_joke")
    .add_edge("generate_joke", END)
    .compile()
)

for chunk in graph.stream(
    {"topic": "ice cream"},
    stream_mode=["updates", "custom"],
    version="v2",
):
    if chunk["type"] == "updates":
        for node_name, state in chunk["data"].items():

            print(f"Node {node_name} updated: {state}")
    elif chunk["type"] == "custom":

        print(f"Status: {chunk['data']['status']}")