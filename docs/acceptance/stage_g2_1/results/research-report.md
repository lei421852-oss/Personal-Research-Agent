# Verified findings and evidence gaps

Compare the visual-language connection mechanisms in BLIP-2 and Flamingo. Explain how each paper transfers visual features into its language model, covering BLIP-2's Q-Former and visual-language interface, and Flamingo's Perceiver Resampler and gated cross-attention. Identify the main differences in visual-language interaction, and cite prepared paper evidence for each finding. Mark unsupported details as evidence gaps.

Status: `completed`

4 of 14 planned requirements have reviewed evidence; 10 remain missing.

## BLIP-2 Q-Former mechanism and visual-language interface · Verified findings

### blip-2-2301.12597v1

Requirement: `req_e19ea943240204a26cb56cbb87c94ce0` · Status: `verified`

Q-Former is a Querying Transformer designed as an information bottleneck interface between a frozen image encoder and the LLM, pre-trained in two stages (vision-language representation learning with a frozen image encoder, then vision-to-language generative learning with a frozen LLM). It uses 32 learnable queries of dimension 768 (Z = 32×768, much smaller than frozen image features e.g. 257×1024 for ViT-L/14), and the bottleneck architecture together with pre-training objectives forces the queries to extract visual information most relevant/informative to the text.

Claim: `claim_cbf63baaf4c7f9cb7b3e06fe9d9d005b`

Sources:

- `ev_525a1f73be26c523e5fb57a5a02a3ecc` · blip-2-2301.12597v1 · Page 2
- `ev_65bb1fa95a078ec3cacc188e7d033199` · blip-2-2301.12597v1 · Page 3
- `ev_242192f04216b43089535562ea0dcec9` · blip-2-2301.12597v1 · Page 3
- `ev_e4829335e46c87be4cada6e262bdd856` · blip-2-2301.12597v1 · Page 4

### flamingo-2204.14198v1

Requirement: `req_472304b624f534754f88a7a1ce8df1ca` · Status: `insufficient_evidence`

没有该单元的已审核主张；不补充论文技术事实。

## BLIP-2 path from visual features into the language model · Verified findings

### blip-2-2301.12597v1

Requirement: `req_64aab1b7787c2b237ec6d8cc2f3c1eb0` · Status: `verified`

In the generative pre-training stage the LLM receives the Q-Former output query embeddings Z; an FC layer linearly projects Z to the LLM text-embedding dimension, and the projected query embeddings are prepended to the input text embeddings as soft visual prompts conditioning the LLM on the visual representation extracted by the Q-Former (serving as an information bottleneck).

Claim: `claim_2d0a653fc263fa8c0eccc38f35c8afe4`

Sources:

- `ev_e4829335e46c87be4cada6e262bdd856` · blip-2-2301.12597v1 · Page 4

### flamingo-2204.14198v1

Requirement: `req_9640a28c422eede971e8bb1150400a35` · Status: `insufficient_evidence`

没有该单元的已审核主张；不补充论文技术事实。

## BLIP-2 training-stage objectives of the interface · Verified findings

### blip-2-2301.12597v1

Requirement: `req_d92d50b90f7369f8728705f60572f512` · Status: `verified`

In the representation learning stage, Q-Former is connected to a frozen image encoder and pre-trained on image-text pairs with three jointly optimized pre-training objectives (inspired by BLIP) that share input format and model parameters, each using a different attention masking strategy between queries and text, so that queries learn to extract visual representation most informative of the text. In the generative pre-training stage, Q-Former is connected to a frozen LLM to harvest its generative language capability, with queries acting as soft visual prompts / an information bottleneck.

Claim: `claim_1b2e06d00bad094258a4285c9a509e4e`

Sources:

- `ev_242192f04216b43089535562ea0dcec9` · blip-2-2301.12597v1 · Page 3
- `ev_e4829335e46c87be4cada6e262bdd856` · blip-2-2301.12597v1 · Page 4
- `ev_525a1f73be26c523e5fb57a5a02a3ecc` · blip-2-2301.12597v1 · Page 2

### flamingo-2204.14198v1

Requirement: `req_06063a5ddc0e2564ae49b3ba7bb8292f` · Status: `insufficient_evidence`

没有该单元的已审核主张；不补充论文技术事实。

## Flamingo Perceiver Resampler and visual feature aggregation · Verified findings

### blip-2-2301.12597v1

Requirement: `req_0f10028e54f726d1be066c1f6b805b92` · Status: `insufficient_evidence`

没有该单元的已审核主张；不补充论文技术事实。

### flamingo-2204.14198v1

Requirement: `req_2f68888fa1d7cc279e07413a66bb28c1` · Status: `verified`

The Perceiver Resampler takes a variable number of image or video features from the vision encoder and produces a fixed number of visual outputs (in practice 64) by learning a predefined number of latent input queries: after adding learned temporal position encodings, the flattened (and concatenated) visual features form keys/values, and the latent queries are fed to a transformer stack that cross-attends to these visual features. The number of output tokens equals the number of learned latent queries. Unlike DETR/Perceiver, the keys and values computed from the learned latents are concatenated to the keys and values from the visual features. The motivation for resampling to a fixed, small number of outputs is to reduce the computational complexity of vision-text cross attention, particularly for multiple long videos.

Claim: `claim_87e210ba8ea56f9ae47c01bd83886fe3`

Sources:

- `ev_9262472c69770e9a098422d6b46534f9` · flamingo-2204.14198v1 · Page 11
- `ev_1863b35f4e6e9086964ec47670f5ea7b` · flamingo-2204.14198v1 · Page 11
- `ev_1f60e62600f7a79eae799d5ed285f571` · flamingo-2204.14198v1 · Page 9
- `ev_feb34e007ade9b3e9d28cbb6e7deb391` · flamingo-2204.14198v1 · Page 10

## Flamingo gated cross-attention insertion into the language model · Verified findings

### blip-2-2301.12597v1

Requirement: `req_488d6ef7f53c8b23b39b348b7ae81a72` · Status: `insufficient_evidence`

没有该单元的已审核主张；不补充论文技术事实。

### flamingo-2204.14198v1

Requirement: `req_a67a1d796abb3f49bd724aa2f7acfe32` · Status: `insufficient_evidence`

没有该单元的已审核主张；不补充论文技术事实。

## Flamingo component training and freezing arrangement · Verified findings

### blip-2-2301.12597v1

Requirement: `req_5ea4dbb5caf92b23846abfcba93c13cd` · Status: `insufficient_evidence`

没有该单元的已审核主张；不补充论文技术事实。

### flamingo-2204.14198v1

Requirement: `req_f10c07f2388998f46e381d5bfbf19ef9` · Status: `insufficient_evidence`

没有该单元的已审核主张；不补充论文技术事实。

## Differences in visual-language interaction behavior · Verified findings

### blip-2-2301.12597v1

Requirement: `req_fdf49c34db1de272c3a0807c6de25405` · Status: `insufficient_evidence`

没有该单元的已审核主张；不补充论文技术事实。

### flamingo-2204.14198v1

Requirement: `req_c58007dd9e860a4a249c61b2e1764eee` · Status: `insufficient_evidence`

没有该单元的已审核主张；不补充论文技术事实。

## Metrics

```json
{
  "total_requirements": 14,
  "verified_requirements": 4,
  "missing_requirements": 10,
  "rejected_requirements": 0,
  "resolved_requirements": 14,
  "reviewed_requirements": 4,
  "citation_validation_failures": 1,
  "synthesis_model_calls": 2,
  "protocol_corrections": 1,
  "provider_retries": 0,
  "synthesis_latency_ms": 11401.851623028051,
  "resolution_rate": 1.0,
  "evidence_coverage": 0.2857142857142857,
  "review_pass_rate": 1.0,
  "report_generation_attempts": 1,
  "report_generation_successes": 1,
  "report_generation_success_rate": 1.0,
  "report_status": "completed",
  "degraded_generation": false
}
```

## Limitations

- Only reviewed claims and prepared evidence are factual report content.
- Structural citation validation does not prove semantic entailment or model-review correctness.
- Missing cells are not completed from paper profiles or model knowledge.
