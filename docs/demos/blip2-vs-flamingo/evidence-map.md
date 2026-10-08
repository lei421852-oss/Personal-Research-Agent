# 已审核论断与原始证据映射

论断正文来自本次模型 Worker；引用身份、范围与报告结构由程序核验。
Reviewer passed 是模型语义判断。独立人工事实审核：尚未完成。

## blip-2-2301.12597v1 / vl_interface_architecture

Claim ID: `claim_db583d10d3072ff4009e0222120d56e0` / Requirement: `req_90c92d87bb8f5870b8117fafa4b6816a`

The visual-language interface is a lightweight Querying Transformer (Q-Former) bridging frozen unimodal models, pre-trained in two stages (representation learning with a frozen image encoder, and generative learning with a frozen LLM). It contains a set of 32 learnable queries (dim 768, matching the Q-Former hidden dimension) that interact with the frozen image encoder output through the Q-Former, whose output query representation Z is a compact 32x768 bottleneck versus the frozen image features (e.g., 257x1024 for ViT-L/14). In the generative stage, a fully-connected layer projects Z into the LLM text-embedding dimension and the projected queries are prepended to the text embeddings as soft visual prompts.

### `ev_525a1f73be26c523e5fb57a5a02a3ecc` · PDF Page 2

原始 Prepared Evidence（逐字摘录）：

```text
We propose BLIP-2, a new vision-language pre-training method that bootstraps from frozen pre-trained unimodal models. In order to bridge the modality gap, we propose a Querying Transformer (Q-Former) pre-trained in two stages: (1) vision-language representation learning stage with a frozen image encoder and (2) vision-to-language genera- tive learning stage with a frozen LLM. This section ﬁrst introduces the model architecture of Q-Former, and then delineates the two-stage pre-training procedures.
```

### `ev_65bb1fa95a078ec3cacc188e7d033199` · PDF Page 3

原始 Prepared Evidence（逐字摘录）：

```text
In our experiments, we use 32 queries where each query has a dimension of 768 (same as the hidden dimension of the Q-Former). We use Z to denote the output query representation. The size of Z (32 × 768) is much smaller than the size of frozen image features (e.g. 257 × 1024 for ViT-L/14). This bottleneck architecture works together with our pre-training objectives into forcing the queries to extract visual information that is most relevant to the text.
```

### `ev_e4829335e46c87be4cada6e262bdd856` · PDF Page 4

原始 Prepared Evidence（逐字摘录）：

```text
In the generative pre-training stage, we connect Q- Former (with the frozen image encoder attached) to a frozen LLM to harvest the LLM’s generative language capability. As shown in Figure 3, we use a fully-connected (FC) layer to linearly project the output query embeddings Z into the same dimension as the text embedding of the LLM. The projected query embeddings are then prepended to the input text embeddings. They function as soft visual prompts that condition the LLM on visual representation extracted by the Q-Former. Since the Q-Former has been pre-trained to extract language-informative visual representation, it ef- fectively functions as an information bottleneck that feeds the most useful information to the LLM while removing irrelevant visual information. This reduces the burden of the LLM to learn vision-language alignment, thus mitigating the catastrophic forgetting problem.
```

## blip-2-2301.12597v1 / visual_feature_transfer_flow

Claim ID: `claim_7f5288692c48f73b2c1f717e4868345d` / Requirement: `req_f3827455511f3141ce0df473c90a9e11`

Visual features flow from the frozen image encoder into the Q-Former queries, which compress them into the output query representation Z (32x768); in the generative stage a fully-connected layer linearly projects Z to the LLM text-embedding dimension, and the projected query embeddings are prepended to the input text embeddings as soft visual prompts conditioning the LLM. So visual information is injected at the LLM input (prepended visual representation), for both decoder-based LLMs (language modeling loss) and encoder-decoder LLMs (prefix text concatenated with visual representation for the encoder).

### `ev_e4829335e46c87be4cada6e262bdd856` · PDF Page 4

原始 Prepared Evidence（逐字摘录）：

```text
In the generative pre-training stage, we connect Q- Former (with the frozen image encoder attached) to a frozen LLM to harvest the LLM’s generative language capability. As shown in Figure 3, we use a fully-connected (FC) layer to linearly project the output query embeddings Z into the same dimension as the text embedding of the LLM. The projected query embeddings are then prepended to the input text embeddings. They function as soft visual prompts that condition the LLM on visual representation extracted by the Q-Former. Since the Q-Former has been pre-trained to extract language-informative visual representation, it ef- fectively functions as an information bottleneck that feeds the most useful information to the LLM while removing irrelevant visual information. This reduces the burden of the LLM to learn vision-language alignment, thus mitigating the catastrophic forgetting problem.
```

### `ev_65bb1fa95a078ec3cacc188e7d033199` · PDF Page 3

原始 Prepared Evidence（逐字摘录）：

```text
In our experiments, we use 32 queries where each query has a dimension of 768 (same as the hidden dimension of the Q-Former). We use Z to denote the output query representation. The size of Z (32 × 768) is much smaller than the size of frozen image features (e.g. 257 × 1024 for ViT-L/14). This bottleneck architecture works together with our pre-training objectives into forcing the queries to extract visual information that is most relevant to the text.
```

### `ev_234984c983953494ce28027dc2716acc` · PDF Page 4

原始 Prepared Evidence（逐字摘录）：

```text
We experiment with two types of LLMs: decoder-based LLMs and encoder-decoder-based LLMs. For decoder- based LLMs, we pre-train with the language modeling loss, where the frozen LLM is tasked to generate the text con- ditioned on the visual representation from Q-Former. For encoder-decoder-based LLMs, we pre-train with the preﬁx language modeling loss, where we split a text into two parts. The preﬁx text is concatenated with the visual representation as input to the LLM’s encoder. The sufﬁx text is used as the generation target for the LLM’s decoder.
```

## blip-2-2301.12597v1 / visual_token_query_mechanism

Claim ID: `claim_f9a0e85d5bcbdead422d5c1b4b710822` / Requirement: `req_7d4ab5fd29c8cf742c60581bc6ac8269`

Yes: the Q-Former uses 32 learnable queries (each dim 768) whose output representation Z is a bottleneck much smaller than the frozen image features (e.g., 257x1024 for ViT-L/14). The queries interact with the frozen image encoder output and, combined with the pre-training objectives, are forced to extract visual information most relevant to the text.

### `ev_65bb1fa95a078ec3cacc188e7d033199` · PDF Page 3

原始 Prepared Evidence（逐字摘录）：

```text
In our experiments, we use 32 queries where each query has a dimension of 768 (same as the hidden dimension of the Q-Former). We use Z to denote the output query representation. The size of Z (32 × 768) is much smaller than the size of frozen image features (e.g. 257 × 1024 for ViT-L/14). This bottleneck architecture works together with our pre-training objectives into forcing the queries to extract visual information that is most relevant to the text.
```

### `ev_242192f04216b43089535562ea0dcec9` · PDF Page 3

原始 Prepared Evidence（逐字摘录）：

```text
In the representation learning stage, we connect Q-Former to a frozen image encoder and perform pre-training using image-text pairs. We aim to train the Q-Former such that the queries can learn to extract visual representation that is most informative of the text. Inspired by BLIP (Li et al., 2022), we jointly optimize three pre-training objectives that share the same input format and model parameters. Each objec- tive employs a different attention masking strategy between queries and text to control their interaction (see Figure 2).
```

## blip-2-2301.12597v1 / training_stage_design

Claim ID: `claim_e31c6c05e3313043f4c491e3422a012f` / Requirement: `req_7a9f570ddbb50fac0aa637150d815c30`

Two-stage pre-training: (1) vision-language representation learning stage, connecting Q-Former to a frozen image encoder with joint optimization of three pre-training objectives sharing the same input format and model parameters (attention masking between queries and text controls interaction); (2) vision-to-language generative learning stage, connecting the Q-Former (with frozen image encoder attached) to a frozen LLM, training a fully-connected projection of query embeddings. Image encoder and LLM are frozen across stages; the Q-Former and projection are the trainable components.

### `ev_525a1f73be26c523e5fb57a5a02a3ecc` · PDF Page 2

原始 Prepared Evidence（逐字摘录）：

```text
We propose BLIP-2, a new vision-language pre-training method that bootstraps from frozen pre-trained unimodal models. In order to bridge the modality gap, we propose a Querying Transformer (Q-Former) pre-trained in two stages: (1) vision-language representation learning stage with a frozen image encoder and (2) vision-to-language genera- tive learning stage with a frozen LLM. This section ﬁrst introduces the model architecture of Q-Former, and then delineates the two-stage pre-training procedures.
```

### `ev_242192f04216b43089535562ea0dcec9` · PDF Page 3

原始 Prepared Evidence（逐字摘录）：

```text
In the representation learning stage, we connect Q-Former to a frozen image encoder and perform pre-training using image-text pairs. We aim to train the Q-Former such that the queries can learn to extract visual representation that is most informative of the text. Inspired by BLIP (Li et al., 2022), we jointly optimize three pre-training objectives that share the same input format and model parameters. Each objec- tive employs a different attention masking strategy between queries and text to control their interaction (see Figure 2).
```

### `ev_e4829335e46c87be4cada6e262bdd856` · PDF Page 4

原始 Prepared Evidence（逐字摘录）：

```text
In the generative pre-training stage, we connect Q- Former (with the frozen image encoder attached) to a frozen LLM to harvest the LLM’s generative language capability. As shown in Figure 3, we use a fully-connected (FC) layer to linearly project the output query embeddings Z into the same dimension as the text embedding of the LLM. The projected query embeddings are then prepended to the input text embeddings. They function as soft visual prompts that condition the LLM on visual representation extracted by the Q-Former. Since the Q-Former has been pre-trained to extract language-informative visual representation, it ef- fectively functions as an information bottleneck that feeds the most useful information to the LLM while removing irrelevant visual information. This reduces the burden of the LLM to learn vision-language alignment, thus mitigating the catastrophic forgetting problem.
```

## blip-2-2301.12597v1 / interaction_granularity

Claim ID: `claim_5940237715b073186da63b03151a3e8f` / Requirement: `req_b2d4b03e9643cf174601e7e3f4c0e73d`

Interaction is mediated at a coarse, output-level granularity via a bottleneck: 32 query embeddings (Z of size 32x768, much smaller than frozen image features) summarize the visual input, and only these passed-through query embeddings are coupled to the language model as prepended soft visual prompts. The Q-Former acts as an information bottleneck feeding only the most useful visual information to the LLM, avoiding dense token-level visual-language interaction inside the LLM.

### `ev_65bb1fa95a078ec3cacc188e7d033199` · PDF Page 3

原始 Prepared Evidence（逐字摘录）：

```text
In our experiments, we use 32 queries where each query has a dimension of 768 (same as the hidden dimension of the Q-Former). We use Z to denote the output query representation. The size of Z (32 × 768) is much smaller than the size of frozen image features (e.g. 257 × 1024 for ViT-L/14). This bottleneck architecture works together with our pre-training objectives into forcing the queries to extract visual information that is most relevant to the text.
```

### `ev_e4829335e46c87be4cada6e262bdd856` · PDF Page 4

原始 Prepared Evidence（逐字摘录）：

```text
In the generative pre-training stage, we connect Q- Former (with the frozen image encoder attached) to a frozen LLM to harvest the LLM’s generative language capability. As shown in Figure 3, we use a fully-connected (FC) layer to linearly project the output query embeddings Z into the same dimension as the text embedding of the LLM. The projected query embeddings are then prepended to the input text embeddings. They function as soft visual prompts that condition the LLM on visual representation extracted by the Q-Former. Since the Q-Former has been pre-trained to extract language-informative visual representation, it ef- fectively functions as an information bottleneck that feeds the most useful information to the LLM while removing irrelevant visual information. This reduces the burden of the LLM to learn vision-language alignment, thus mitigating the catastrophic forgetting problem.
```

## 保留的缺失单元

- blip-2-2301.12597v1 / cross_attention_gating: `insufficient_evidence`
- flamingo-2204.14198v1 / vl_interface_architecture: `execution_failed`
- flamingo-2204.14198v1 / visual_feature_transfer_flow: `execution_failed`
- flamingo-2204.14198v1 / visual_token_query_mechanism: `execution_failed`
- flamingo-2204.14198v1 / cross_attention_gating: `execution_failed`
- flamingo-2204.14198v1 / training_stage_design: `execution_failed`
- flamingo-2204.14198v1 / interaction_granularity: `execution_failed`
