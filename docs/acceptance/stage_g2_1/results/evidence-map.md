# 已审核论断与原始证据映射

论断正文来自本次模型 Worker；引用身份、范围与报告结构由程序核验。
Reviewer passed 是模型语义判断。独立人工事实审核：尚未完成。

## blip-2-2301.12597v1 / blip2_qformer_mechanism

Claim ID: `claim_cbf63baaf4c7f9cb7b3e06fe9d9d005b` / Requirement: `req_e19ea943240204a26cb56cbb87c94ce0`

Q-Former is a Querying Transformer designed as an information bottleneck interface between a frozen image encoder and the LLM, pre-trained in two stages (vision-language representation learning with a frozen image encoder, then vision-to-language generative learning with a frozen LLM). It uses 32 learnable queries of dimension 768 (Z = 32×768, much smaller than frozen image features e.g. 257×1024 for ViT-L/14), and the bottleneck architecture together with pre-training objectives forces the queries to extract visual information most relevant/informative to the text.

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

## blip-2-2301.12597v1 / blip2_language_transfer_path

Claim ID: `claim_2d0a653fc263fa8c0eccc38f35c8afe4` / Requirement: `req_64aab1b7787c2b237ec6d8cc2f3c1eb0`

In the generative pre-training stage the LLM receives the Q-Former output query embeddings Z; an FC layer linearly projects Z to the LLM text-embedding dimension, and the projected query embeddings are prepended to the input text embeddings as soft visual prompts conditioning the LLM on the visual representation extracted by the Q-Former (serving as an information bottleneck).

### `ev_e4829335e46c87be4cada6e262bdd856` · PDF Page 4

原始 Prepared Evidence（逐字摘录）：

```text
In the generative pre-training stage, we connect Q- Former (with the frozen image encoder attached) to a frozen LLM to harvest the LLM’s generative language capability. As shown in Figure 3, we use a fully-connected (FC) layer to linearly project the output query embeddings Z into the same dimension as the text embedding of the LLM. The projected query embeddings are then prepended to the input text embeddings. They function as soft visual prompts that condition the LLM on visual representation extracted by the Q-Former. Since the Q-Former has been pre-trained to extract language-informative visual representation, it ef- fectively functions as an information bottleneck that feeds the most useful information to the LLM while removing irrelevant visual information. This reduces the burden of the LLM to learn vision-language alignment, thus mitigating the catastrophic forgetting problem.
```

## blip-2-2301.12597v1 / blip2_training_objectives

Claim ID: `claim_1b2e06d00bad094258a4285c9a509e4e` / Requirement: `req_d92d50b90f7369f8728705f60572f512`

In the representation learning stage, Q-Former is connected to a frozen image encoder and pre-trained on image-text pairs with three jointly optimized pre-training objectives (inspired by BLIP) that share input format and model parameters, each using a different attention masking strategy between queries and text, so that queries learn to extract visual representation most informative of the text. In the generative pre-training stage, Q-Former is connected to a frozen LLM to harvest its generative language capability, with queries acting as soft visual prompts / an information bottleneck.

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

### `ev_525a1f73be26c523e5fb57a5a02a3ecc` · PDF Page 2

原始 Prepared Evidence（逐字摘录）：

```text
We propose BLIP-2, a new vision-language pre-training method that bootstraps from frozen pre-trained unimodal models. In order to bridge the modality gap, we propose a Querying Transformer (Q-Former) pre-trained in two stages: (1) vision-language representation learning stage with a frozen image encoder and (2) vision-to-language genera- tive learning stage with a frozen LLM. This section ﬁrst introduces the model architecture of Q-Former, and then delineates the two-stage pre-training procedures.
```

## flamingo-2204.14198v1 / flamingo_perceiver_resampler

Claim ID: `claim_87e210ba8ea56f9ae47c01bd83886fe3` / Requirement: `req_2f68888fa1d7cc279e07413a66bb28c1`

The Perceiver Resampler takes a variable number of image or video features from the vision encoder and produces a fixed number of visual outputs (in practice 64) by learning a predefined number of latent input queries: after adding learned temporal position encodings, the flattened (and concatenated) visual features form keys/values, and the latent queries are fed to a transformer stack that cross-attends to these visual features. The number of output tokens equals the number of learned latent queries. Unlike DETR/Perceiver, the keys and values computed from the learned latents are concatenated to the keys and values from the visual features. The motivation for resampling to a fixed, small number of outputs is to reduce the computational complexity of vision-text cross attention, particularly for multiple long videos.

### `ev_9262472c69770e9a098422d6b46534f9` · PDF Page 11

原始 Prepared Evidence（逐字摘录）：

```text
architecture in Figure 3. It takes as input a variable number of image or video features from the vision encoder and produces a ﬁxed number of visual outputs as illustrated in Figure 4 (hence the name Resampler). The motivation for re-sampling the visual input to a ﬁxed and small number (in practice 64) of outputs is to signiﬁcantly reduce the computational complexity of vision-text cross attention, particularly important when dealing with multiple long videos. In similar spirit to Perceiver (Jaegle et al., 2021) and DETR (Carion et al., 2020), we learn a predeﬁned number of latent input queries. These latent queries are fed to a transformer stack and cross attend to the ﬂattened visual features
```

### `ev_1863b35f4e6e9086964ec47670f5ea7b` · PDF Page 11

原始 Prepared Evidence（逐字摘录）：

```text
𝑋𝑓. These visual features are obtained by ﬁrst adding a learnt temporal position encoding to each spatial grid of features corresponding to a given frame of the video (an image being considered as a single-frame video). Note that we only use temporal encodings and no spatial grid position encodings; we did not observe improvements from the latter, potentially because CNNs implicitly encode space information channel-wise (Islam et al., 2021). The visual features are then ﬂattened and concatenated as illustrated in Figure 4. The number of output tokens of the Resampler is thus equal to the number of learnt latent queries. Unlike in DETR and Perceiver, the keys and values computed from the learnt latents are concatenated to the keys and values obtained from 𝑋𝑓, which we found to perform slightly better. We show later in the ablation studies (Section 4.4), that using such a vision-language resampler module outperforms a plain transformer and an MLP. More architectural details are provided in Table 13.
```

### `ev_1f60e62600f7a79eae799d5ed285f571` · PDF Page 9

原始 Prepared Evidence（逐字摘录）：

```text
The architectural components shown in Figure 3 are key to the performance of Flamingo models, and these are chosen according to two objectives. The ﬁrst objective is to leverage pretrained models without having to spend compute training them from scratch. On the vision side, we pretrain a vision encoder with a contrastive text-image approach, à la CLIP (Radford et al., 2021). The role of this model is to extract semantic spatial features that describe attributes that one would want to query about a visual datum: color, shape, nature, positions of objects, etc. On the language side, we start from an existing autoregressive language model (LM) trained on a large and diverse text corpus (Hoﬀmann et al., 2022). By doing so, Flamingo models gain strong generative language abilities and access to a large amount of knowledge stored in the LM weights. The second objective is to bridge these pretrained models harmoniously. To do so, we freeze the weights of these models so that their initial capacity remains unchanged. We then link them via two learnable architecture components. First, the Perceiver Resampler (Section 3.1.1) receives spatio-temporal features from the Vision Encoder (obtained from either image or video) and outputs a ﬁxed-size set of visual tokens.
```

### `ev_feb34e007ade9b3e9d28cbb6e7deb391` · PDF Page 10

原始 Prepared Evidence（逐字摘录）：

```text
is then ﬂattened to 1D as shown in Figure 4. Our model also handles video inputs. In this setting, frames are sampled at 1 FPS, encoded independently to obtain a sequence of 𝑇feature maps 𝑋𝑓which are then concatenated before being fed to downstream components (Figure 4).
```

## 保留的缺失单元

- blip-2-2301.12597v1 / flamingo_perceiver_resampler: `insufficient_evidence`
- blip-2-2301.12597v1 / flamingo_gated_cross_attention: `insufficient_evidence`
- blip-2-2301.12597v1 / flamingo_architecture_freeze: `insufficient_evidence`
- blip-2-2301.12597v1 / interaction_difference: `insufficient_evidence`
- flamingo-2204.14198v1 / blip2_qformer_mechanism: `insufficient_evidence`
- flamingo-2204.14198v1 / blip2_language_transfer_path: `insufficient_evidence`
- flamingo-2204.14198v1 / blip2_training_objectives: `insufficient_evidence`
- flamingo-2204.14198v1 / flamingo_gated_cross_attention: `insufficient_evidence`
- flamingo-2204.14198v1 / flamingo_architecture_freeze: `insufficient_evidence`
- flamingo-2204.14198v1 / interaction_difference: `insufficient_evidence`
