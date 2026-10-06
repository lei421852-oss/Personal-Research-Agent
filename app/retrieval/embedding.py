"""适配自 Personal RAG 的 src/rag/embedding.py；重型依赖仅按需导入。"""


class QwenEmbeddingModel:
    def __init__(
        self,
        model_name: str = "Qwen/Qwen3-Embedding-0.6B",
        *,
        device: str = "cpu",
        batch_size: int = 8,
    ):
        if batch_size <= 0:
            raise ValueError("batch_size must be positive")
        import torch
        from transformers import AutoModel, AutoTokenizer

        self.torch = torch
        self.tokenizer = AutoTokenizer.from_pretrained(model_name)
        self.model = AutoModel.from_pretrained(model_name).to(device).eval()
        self.batch_size = batch_size

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._embed(texts)

    def embed_query(self, query: str) -> list[float]:
        instruction = "Instruct: Given a user question, retrieve relevant passages that answer the question.\n"
        return self._embed([instruction + "Query: " + query])[0]

    def _embed(self, texts: list[str]) -> list[list[float]]:
        vectors = []
        for start in range(0, len(texts), self.batch_size):
            inputs = self.tokenizer(
                texts[start : start + self.batch_size],
                padding=True,
                truncation=True,
                max_length=8192,
                return_tensors="pt",
            )
            inputs = {key: value.to(self.model.device) for key, value in inputs.items()}
            with self.torch.inference_mode():
                states = self.model(**inputs).last_hidden_state
                mask = inputs["attention_mask"]
                if bool((mask[:, -1] == 1).all()):
                    pooled = states[:, -1]
                else:
                    lengths = mask.sum(dim=1) - 1
                    pooled = states[
                        self.torch.arange(states.shape[0], device=states.device),
                        lengths,
                    ]
                vectors.extend(
                    self.torch.nn.functional.normalize(pooled, p=2, dim=1)
                    .cpu()
                    .tolist()
                )
        return vectors
