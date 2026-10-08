# Verified findings and evidence gaps

Compare the visual-language connection mechanisms in BLIP-2 and Flamingo. Explain how each paper transfers visual features into its language model, covering BLIP-2's Q-Former and visual-language interface, and Flamingo's Perceiver Resampler and gated cross-attention. Identify the main differences in visual-language interaction, and cite prepared paper evidence for each finding. Mark unsupported details as evidence gaps.

Status: `completed`

5 of 12 planned requirements have reviewed evidence; 7 remain missing.

## Visual-language interface architecture · Verified findings

### blip-2-2301.12597v1

Requirement: `req_90c92d87bb8f5870b8117fafa4b6816a` · Status: `verified`

The visual-language interface is a lightweight Querying Transformer (Q-Former) bridging frozen unimodal models, pre-trained in two stages (representation learning with a frozen image encoder, and generative learning with a frozen LLM). It contains a set of 32 learnable queries (dim 768, matching the Q-Former hidden dimension) that interact with the frozen image encoder output through the Q-Former, whose output query representation Z is a compact 32x768 bottleneck versus the frozen image features (e.g., 257x1024 for ViT-L/14). In the generative stage, a fully-connected layer projects Z into the LLM text-embedding dimension and the projected queries are prepended to the text embeddings as soft visual prompts.

Claim: `claim_db583d10d3072ff4009e0222120d56e0`

Sources:

- `ev_525a1f73be26c523e5fb57a5a02a3ecc` · blip-2-2301.12597v1 · Page 2
- `ev_65bb1fa95a078ec3cacc188e7d033199` · blip-2-2301.12597v1 · Page 3
- `ev_e4829335e46c87be4cada6e262bdd856` · blip-2-2301.12597v1 · Page 4

### flamingo-2204.14198v1

Requirement: `req_f873dd200f90c6967e1c8659ffa392e8` · Status: `execution_failed`

没有该单元的已审核主张；不补充论文技术事实。

## Visual feature transfer flow · Verified findings

### blip-2-2301.12597v1

Requirement: `req_f3827455511f3141ce0df473c90a9e11` · Status: `verified`

Visual features flow from the frozen image encoder into the Q-Former queries, which compress them into the output query representation Z (32x768); in the generative stage a fully-connected layer linearly projects Z to the LLM text-embedding dimension, and the projected query embeddings are prepended to the input text embeddings as soft visual prompts conditioning the LLM. So visual information is injected at the LLM input (prepended visual representation), for both decoder-based LLMs (language modeling loss) and encoder-decoder LLMs (prefix text concatenated with visual representation for the encoder).

Claim: `claim_7f5288692c48f73b2c1f717e4868345d`

Sources:

- `ev_e4829335e46c87be4cada6e262bdd856` · blip-2-2301.12597v1 · Page 4
- `ev_65bb1fa95a078ec3cacc188e7d033199` · blip-2-2301.12597v1 · Page 3
- `ev_234984c983953494ce28027dc2716acc` · blip-2-2301.12597v1 · Page 4

### flamingo-2204.14198v1

Requirement: `req_3685132e9920050ac285bc19e3c8022b` · Status: `execution_failed`

没有该单元的已审核主张；不补充论文技术事实。

## Query-based visual token extraction · Verified findings

### blip-2-2301.12597v1

Requirement: `req_7d4ab5fd29c8cf742c60581bc6ac8269` · Status: `verified`

Yes: the Q-Former uses 32 learnable queries (each dim 768) whose output representation Z is a bottleneck much smaller than the frozen image features (e.g., 257x1024 for ViT-L/14). The queries interact with the frozen image encoder output and, combined with the pre-training objectives, are forced to extract visual information most relevant to the text.

Claim: `claim_f9a0e85d5bcbdead422d5c1b4b710822`

Sources:

- `ev_65bb1fa95a078ec3cacc188e7d033199` · blip-2-2301.12597v1 · Page 3
- `ev_242192f04216b43089535562ea0dcec9` · blip-2-2301.12597v1 · Page 3

### flamingo-2204.14198v1

Requirement: `req_ad94bf03060e6edbf329354449110179` · Status: `execution_failed`

没有该单元的已审核主张；不补充论文技术事实。

## Cross-attention and gating design · Evidence coverage

### blip-2-2301.12597v1

Requirement: `req_f4d4ec133b74c437e54d69fd4d026668` · Status: `insufficient_evidence`

没有该单元的已审核主张；不补充论文技术事实。

### flamingo-2204.14198v1

Requirement: `req_c08c6273ff813c3d49b13e33814e7ca6` · Status: `execution_failed`

没有该单元的已审核主张；不补充论文技术事实。

## Training stages and module freezing · Verified findings

### blip-2-2301.12597v1

Requirement: `req_7a9f570ddbb50fac0aa637150d815c30` · Status: `verified`

Two-stage pre-training: (1) vision-language representation learning stage, connecting Q-Former to a frozen image encoder with joint optimization of three pre-training objectives sharing the same input format and model parameters (attention masking between queries and text controls interaction); (2) vision-to-language generative learning stage, connecting the Q-Former (with frozen image encoder attached) to a frozen LLM, training a fully-connected projection of query embeddings. Image encoder and LLM are frozen across stages; the Q-Former and projection are the trainable components.

Claim: `claim_e31c6c05e3313043f4c491e3422a012f`

Sources:

- `ev_525a1f73be26c523e5fb57a5a02a3ecc` · blip-2-2301.12597v1 · Page 2
- `ev_242192f04216b43089535562ea0dcec9` · blip-2-2301.12597v1 · Page 3
- `ev_e4829335e46c87be4cada6e262bdd856` · blip-2-2301.12597v1 · Page 4

### flamingo-2204.14198v1

Requirement: `req_2275fe81f4144501b28c90a4c75e1d12` · Status: `execution_failed`

没有该单元的已审核主张；不补充论文技术事实。

## Visual-language interaction granularity · Verified findings

### blip-2-2301.12597v1

Requirement: `req_b2d4b03e9643cf174601e7e3f4c0e73d` · Status: `verified`

Interaction is mediated at a coarse, output-level granularity via a bottleneck: 32 query embeddings (Z of size 32x768, much smaller than frozen image features) summarize the visual input, and only these passed-through query embeddings are coupled to the language model as prepended soft visual prompts. The Q-Former acts as an information bottleneck feeding only the most useful visual information to the LLM, avoiding dense token-level visual-language interaction inside the LLM.

Claim: `claim_5940237715b073186da63b03151a3e8f`

Sources:

- `ev_65bb1fa95a078ec3cacc188e7d033199` · blip-2-2301.12597v1 · Page 3
- `ev_e4829335e46c87be4cada6e262bdd856` · blip-2-2301.12597v1 · Page 4

### flamingo-2204.14198v1

Requirement: `req_3ca4a0edcaf1f1fe73f150aa2d3fbe09` · Status: `execution_failed`

没有该单元的已审核主张；不补充论文技术事实。

## Metrics

```json
{
  "total_requirements": 12,
  "verified_requirements": 5,
  "missing_requirements": 7,
  "rejected_requirements": 0,
  "resolved_requirements": 6,
  "reviewed_requirements": 5,
  "citation_validation_failures": 0,
  "synthesis_model_calls": 1,
  "protocol_corrections": 0,
  "provider_retries": 0,
  "synthesis_latency_ms": 4643.971012003021,
  "resolution_rate": 0.5,
  "evidence_coverage": 0.4166666666666667,
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
