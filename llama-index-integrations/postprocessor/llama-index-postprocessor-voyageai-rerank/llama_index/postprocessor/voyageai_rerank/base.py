from typing import Any, List, Optional

from llama_index.core.bridge.pydantic import Field, PrivateAttr
from llama_index.core.callbacks import CBEventType, EventPayload
from llama_index.core.instrumentation import get_dispatcher
from llama_index.core.instrumentation.events.rerank import (
    ReRankEndEvent,
    ReRankStartEvent,
)
from llama_index.core.postprocessor.types import BaseNodePostprocessor
from llama_index.core.schema import MetadataMode, NodeWithScore, QueryBundle

dispatcher = get_dispatcher(__name__)


class VoyageAIRerank(BaseNodePostprocessor):
    model: str = Field(description="Name of the model to use.")
    top_n: int = Field(
        description="The number of most relevant documents to return. If not specified, the reranking results of all documents will be returned."
    )
    truncation: bool = Field(
        description="Whether to truncate the input to satisfy the 'context length limit' on the query and the documents."
    )

    _client: Any = PrivateAttr()

    def __init__(
        self,
        api_key: str,
        model: str,
        top_n: Optional[int] = None,
        truncation: Optional[bool] = None,
    ):
        try:
            from voyageai import Client
        except ImportError:
            raise ImportError(
                "Cannot import voyageai package, please `pip install voyageai`."
            )

        self._client = Client(api_key=api_key)
        super().__init__(top_n=top_n, model=model, truncation=truncation)

    @classmethod
    def class_name(cls) -> str:
        return "VoyageAIRerank"

    def _postprocess_nodes(
        self,
        nodes: List[NodeWithScore],
        query_bundle: Optional[QueryBundle] = None,
    ) -> List[NodeWithScore]:
        dispatch_event = dispatcher.get_dispatch_event()
        dispatch_event(
            ReRankStartEvent(
                query=query_bundle, nodes=nodes, top_n=self.top_n, model_name=self.model
            )
        )

        if query_bundle is None:
            raise ValueError("Missing query bundle in extra info.")
        if len(nodes) == 0:
            return []

        with self.callback_manager.event(
            CBEventType.RERANKING,
            payload={
                EventPayload.NODES: nodes,
                EventPayload.MODEL_NAME: self.model,
                EventPayload.QUERY_STR: query_bundle.query_str,
                EventPayload.TOP_K: self.top_n,
            },
        ) as event:
            texts = [
                node.node.get_content(metadata_mode=MetadataMode.EMBED)
                for node in nodes
            ]
            results = self._client.rerank(
                model=self.model,
                top_k=self.top_n,
                query=query_bundle.query_str,
                documents=texts,
                truncation=self.truncation,
            ).results

            new_nodes = []
            for result in results:
                new_node_with_score = NodeWithScore(
                    node=nodes[result.index].node, score=result.relevance_score
                )
                new_nodes.append(new_node_with_score)
            event.on_end(payload={EventPayload.NODES: new_nodes})

        dispatch_event(ReRankEndEvent(nodes=new_nodes))
        return new_nodes
