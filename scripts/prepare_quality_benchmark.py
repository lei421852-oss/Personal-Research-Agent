"""从冻结 Prepared PDF 原文整理候选；不读取模型报告，不覆盖人工编辑。"""

import argparse
import hashlib
import json
import subprocess
from datetime import UTC, datetime
from itertools import combinations
from pathlib import Path

from eval.research_quality.dataset_loader import DEFAULT_DATASET, ROOT
from eval.research_quality.dataset_validator import source_index, validate_dataset
from eval.research_quality.schemas import QualityCase, QualityDataset, QualityManifest

PAPERS = {
    "clip": "clip-2103.00020v1",
    "blip": "blip-2201.12086v2",
    "blip2": "blip-2-2301.12597v1",
    "flamingo": "flamingo-2204.14198v1",
    "llava": "llava-2304.08485v1",
}
NAMES = {
    "clip": "CLIP",
    "blip": "BLIP",
    "blip2": "BLIP-2",
    "flamingo": "Flamingo",
    "llava": "LLaVA (2304.08485v1)",
}

# 答案由原始段落整理；负例是明确登记的受控错误，不是论文事实。
# key、维度、问题、候选参考答案、原证据 ID、可选错误论断。
FACTS = [
    (
        "clip-symmetric-loss",
        "clip",
        "training_objective",
        "What loss does CLIP optimize over image-text similarity scores?",
        "CLIP optimizes a symmetric cross-entropy loss over the image-text similarity scores.",
        ["ev_13217652070eda7adeb0e42abcf728eb"],
        "CLIP optimizes only an asymmetric image-to-text loss over these scores.",
    ),
    (
        "clip-batch-pairings",
        "clip",
        "training_pairings",
        "Which pairings are treated as real in a batch of N image-text pairs?",
        "Among the N squared possible pairings, N are the real image-text pairs; the remaining N squared minus N are incorrect pairings.",
        ["ev_b1dfdf019f902850d235d5e5f8f3ffeb", "ev_13217652070eda7adeb0e42abcf728eb"],
        "All N squared cross-batch image-text pairings are real positive pairs.",
    ),
    (
        "clip-two-encoders",
        "clip",
        "architecture",
        "What two encoders are jointly trained in the prepared description?",
        "CLIP jointly trains an image encoder and a text encoder to predict correct image-text pairings.",
        ["ev_7e22d464b738e57a92e8a2d4cf3b38a4"],
        None,
    ),
    (
        "clip-zero-shot-classifier",
        "clip",
        "inference",
        "How is a zero-shot classifier synthesized at test time?",
        "The learned text encoder embeds the target dataset's class names or descriptions to synthesize a zero-shot linear classifier.",
        ["ev_7e22d464b738e57a92e8a2d4cf3b38a4"],
        None,
    ),
    (
        "clip-whole-text-proxy",
        "clip",
        "objective_scope",
        "Does the explored proxy task predict the exact words or the paired text as a whole?",
        "The contrastive proxy task predicts which text as a whole is paired with an image rather than the exact accompanying words.",
        ["ev_075e49db551983dab13b66fb666c8fc9"],
        "The contrastive proxy task is to generate every exact word accompanying each image.",
    ),
    (
        "clip-reported-efficiency",
        "clip",
        "reported_experiment",
        "What efficiency change is reported when swapping the bag-of-words predictive objective for a contrastive objective?",
        "For that bag-of-words baseline comparison, the paper reports a further 4x improvement in the rate of zero-shot transfer to ImageNet; this is not a universal task-performance guarantee.",
        ["ev_075e49db551983dab13b66fb666c8fc9"],
        None,
    ),
    (
        "blip-vision-encoder",
        "blip",
        "vision_encoder",
        "How does BLIP's image encoder represent the input image?",
        "BLIP uses a visual transformer that divides an image into patches and encodes them as embeddings, with an additional CLS token for the global image feature.",
        ["ev_336da108cea409a395b7aee74def2593"],
        "BLIP uses a pretrained object detector instead of a visual transformer as the stated image encoder.",
    ),
    (
        "blip-med-functionalities",
        "blip",
        "architecture",
        "What three functionalities does MED support?",
        "MED supports a unimodal encoder, an image-grounded text encoder, and an image-grounded text decoder.",
        ["ev_197064d097092640e20dc38ce8045682"],
        "MED supports only the unimodal encoder and has no image-grounded text decoder.",
    ),
    (
        "blip-cross-attention-placement",
        "blip",
        "vision_language_bridge",
        "Where is cross-attention inserted in the image-grounded text encoder?",
        "An additional cross-attention layer is inserted between self-attention and the feed-forward network in each text-transformer block.",
        ["ev_197064d097092640e20dc38ce8045682"],
        None,
    ),
    (
        "blip-itm-negatives",
        "blip",
        "training_objective",
        "What does ITM classify and how are harder negative pairs selected?",
        "ITM classifies whether an image-text pair matches; negative pairs with higher in-batch contrastive similarity are more likely to be selected.",
        ["ev_8168c4959ec3caf6a1554f9e787a9434"],
        None,
    ),
    (
        "blip-label-smoothing",
        "blip",
        "training_settings",
        "What label-smoothing value is used for the language-modeling loss?",
        "The image-grounded decoder's language-modeling loss uses label smoothing of 0.1.",
        ["ev_6228bca0d420f2db5c71a0397f93e262"],
        "The language-modeling loss uses label smoothing of 0.7.",
    ),
    (
        "blip-capfilt-initialization",
        "blip",
        "data_preparation",
        "How are the CapFilt captioner and filter initialized and fine-tuned?",
        "Both modules are initialized from the same pretrained MED model and fine-tuned individually on COCO; the captioner generates captions and the filter removes noisy image-text pairs.",
        ["ev_03f0209e1af23ec5c3e017db94ac940e", "ev_0ef849392533fb9fe735d1fe7cd8ebf9"],
        None,
    ),
    (
        "blip2-two-stages",
        "blip2",
        "frozen_trainable_scope",
        "What two pretraining stages connect Q-Former to frozen unimodal models?",
        "Q-Former is pretrained for vision-language representation learning with a frozen image encoder, then vision-to-language generative learning with a frozen language model.",
        ["ev_525a1f73be26c523e5fb57a5a02a3ecc"],
        None,
    ),
    (
        "blip2-query-shape",
        "blip2",
        "bottleneck",
        "How many queries and what query dimension are used in the described experiments?",
        "The experiments use 32 queries of dimension 768, producing a 32 by 768 query representation.",
        ["ev_65bb1fa95a078ec3cacc188e7d033199"],
        "The experiments use 64 queries of dimension 768.",
    ),
    (
        "blip2-soft-prompts",
        "blip2",
        "vision_language_bridge",
        "How are query outputs supplied to the frozen LLM in generative pretraining?",
        "A fully connected layer projects Q-Former outputs to the LLM embedding dimension; the projected query embeddings are prepended as soft visual prompts to the input text embeddings.",
        ["ev_e4829335e46c87be4cada6e262bdd856"],
        "The projected query embeddings are appended after all text embeddings, rather than prepended.",
    ),
    (
        "blip2-image-backbones",
        "blip2",
        "vision_encoder",
        "Which frozen image encoders and output layer are explored?",
        "The paper explores CLIP ViT-L/14 and EVA-CLIP ViT-G/14; it removes the last ViT layer and uses the second-last layer's output features.",
        ["ev_9331722f1a47399ab173e624d02c6985"],
        "The paper explicitly uses the final ViT layer's output without removing that layer.",
    ),
    (
        "blip2-llm-types",
        "blip2",
        "language_model",
        "Which language-model families and corresponding generative objectives are explored?",
        "OPT is explored for decoder-based language models with a language-modeling loss; FlanT5 is explored for encoder-decoder models with a prefix language-modeling loss.",
        ["ev_9331722f1a47399ab173e624d02c6985", "ev_234984c983953494ce28027dc2716acc"],
        None,
    ),
    (
        "blip2-training-steps",
        "blip2",
        "training_settings",
        "How many pretraining steps are used in each of the two stages?",
        "The first stage runs for 250k steps and the second for 80k steps.",
        ["ev_4825dfc97130014274b3be0682b0bffe"],
        None,
    ),
    (
        "flamingo-vision-backbone",
        "flamingo",
        "vision_encoder",
        "What vision backbone is named in the prepared source?",
        "Flamingo's stated vision encoder is a pretrained Normalizer-Free ResNet, NFNet F6.",
        ["ev_03da035280d198641cd7bcb081b6f845"],
        "Flamingo's stated vision encoder is CLIP ViT-L/14 instead of NFNet F6.",
    ),
    (
        "flamingo-resampler-outputs",
        "flamingo",
        "bottleneck",
        "Why are visual features resampled and how many outputs are used in practice?",
        "The Perceiver Resampler maps a variable number of visual features to a fixed small set of outputs, 64 in practice, reducing vision-text cross-attention complexity.",
        ["ev_9262472c69770e9a098422d6b46534f9"],
        "The Resampler output count varies with the input feature count and is not fixed.",
    ),
    (
        "flamingo-frozen-backbones",
        "flamingo",
        "frozen_trainable_scope",
        "Which pretrained components are frozen and what learnable components connect them?",
        "The pretrained vision encoder and language model are frozen; they are connected through a learnable Perceiver Resampler and freshly initialized cross-attention layers.",
        ["ev_1f60e62600f7a79eae799d5ed285f571", "ev_a802881b56aa396b45e4431996b61ae5"],
        None,
    ),
    (
        "flamingo-interleaved-attention",
        "flamingo",
        "vision_language_bridge",
        "How are cross-attention and gating used with the pretrained language model?",
        "Cross-attention layers are interleaved with frozen language-only layers; gating mechanisms minimize the initial impact of the new layers and improve stability.",
        ["ev_218bd889fec22dc20b36a380b0e0fb62", "ev_a802881b56aa396b45e4431996b61ae5"],
        None,
    ),
    (
        "flamingo-video-sampling",
        "flamingo",
        "video_processing",
        "What frame sampling rate is described for video inputs?",
        "Video frames are sampled at 1 FPS, encoded independently, and their feature maps concatenated for downstream components.",
        ["ev_feb34e007ade9b3e9d28cbb6e7deb391"],
        "Video frames are sampled at 16 FPS in the described procedure.",
    ),
    (
        "flamingo-pretrained-lm",
        "flamingo",
        "language_model",
        "What language-model starting point and prediction task are described?",
        "Flamingo starts with an existing autoregressive language model pretrained on a large diverse text corpus and conditions its next-token prediction on visual tokens through cross-attention.",
        ["ev_1f60e62600f7a79eae799d5ed285f571", "ev_a802881b56aa396b45e4431996b61ae5"],
        None,
    ),
    (
        "llava-language-model",
        "llava",
        "language_model",
        "Which language model is explicitly chosen in this fixed v1 paper?",
        "The fixed 2304.08485v1 paper explicitly chooses LLaMA as its language model.",
        ["ev_ebe5ff3e83181708c609e82e314f4f59"],
        "The fixed v1 paper explicitly chooses OPT as its language model.",
    ),
    (
        "llava-vision-backbone",
        "llava",
        "vision_encoder",
        "Which visual encoder and features are considered?",
        "LLaVA considers the pretrained CLIP ViT-L/14 visual encoder and grid features before and after the last Transformer layer.",
        ["ev_5af59f6012398940516f736562f5800e"],
        None,
    ),
    (
        "llava-linear-projection",
        "llava",
        "vision_language_bridge",
        "How are image features mapped into the language embedding space?",
        "A trainable linear projection matrix W maps image features into tokens with the language model's word-embedding dimensionality.",
        ["ev_5af59f6012398940516f736562f5800e"],
        None,
    ),
    (
        "llava-stage-one",
        "llava",
        "frozen_trainable_scope",
        "Which parameters are updated in feature-alignment pretraining?",
        "In stage 1 the visual encoder and LLM are frozen; only projection matrix W is trained.",
        ["ev_c445999410b5ee326425346ce4b2377b"],
        "In stage 1 the LLM weights are updated jointly with W rather than kept frozen.",
    ),
    (
        "llava-stage-two",
        "llava",
        "frozen_trainable_scope",
        "Which parameters are updated in end-to-end fine-tuning?",
        "In stage 2 the visual encoder stays frozen, while the projection layer and LLM weights are updated.",
        ["ev_84b236a4049e358dd2a7dda5bddb4464"],
        "In stage 2 both the LLM and the projection layer stay frozen.",
    ),
    (
        "llava-instruction-counts",
        "llava",
        "training_data",
        "How are the 158K generated instruction-following samples divided?",
        "The reported 158K samples comprise 58K conversations, 23K detailed descriptions, and 77K complex-reasoning samples.",
        ["ev_bb12c44657449eb3224236e0a430a850"],
        None,
    ),
]


def locations(ids, records):
    values = []
    for identity in ids:
        row, source = records[identity]
        values.append(
            {
                "evidence_id": identity,
                "paper_id": row.paper_id,
                "pdf_page": row.page_number,
                "printed_page": source.page_mapping[str(row.page_number)],
                "section_title": row.section_title,
                "source_url": source.source_url,
                "source_version": source.source_version,
                "source_document_sha256": source.source_sha256,
                "source_span": [s.model_dump(mode="json") for s in row.source_span],
                "evidence_field": "caption"
                if row.evidence_type == "figure"
                else "text",
                "quote": row.caption if row.evidence_type == "figure" else row.content,
            }
        )
    return values


def build_candidates(records, snapshot, created_at):
    provenance = {
        "reference_origin": "original_prepared_pdf",
        "author": "coding_agent",
        "created_at": created_at,
        "method": "coding_agent_source_curation",
        "source_notes": "Candidate reference manually curated from the cited original Prepared PDF paragraphs/captions, not Stage E model claims. Human verification is still required.",
        "human_reviews": [],
    }
    cases = []

    def add(
        task,
        key,
        paper_ids,
        dimension,
        question,
        reference,
        ids,
        groups,
        behavior,
        **extra,
    ):
        value = {
            "case_id": "rqv1-" + task + "-" + key,
            "task_type": task,
            "paper_ids": paper_ids,
            "dimension": dimension,
            "question": question,
            "reference_answer": reference,
            "gold_evidence_ids": ids,
            "source_locations": locations(ids, records),
            "expected_behavior": behavior,
            "annotation_status": "pending_review",
            "annotation_provenance": provenance,
            "fact_group_ids": groups,
            "split": "unassigned",
            "usage_partition": "benchmark_only",
            **extra,
        }
        cases.append(QualityCase.model_validate(value))

    facts = {}
    for key, paper, dim, question, answer, ids, negative in FACTS:
        facts[key] = (answer, ids)
        add(
            "claim",
            key,
            [PAPERS[paper]],
            dim,
            NAMES[paper] + ": " + question,
            answer,
            ids,
            [key],
            "answer",
        )
        if negative:
            for label, claim, origin in [
                ("passed", answer, "source_paraphrase"),
                ("rejected", negative, "controlled_contradiction"),
            ]:
                add(
                    "reviewer",
                    key + "-" + label,
                    [PAPERS[paper]],
                    dim,
                    "Assess whether the stated claim is fully supported by the supplied original evidence for "
                    + NAMES[paper]
                    + ".",
                    answer,
                    ids,
                    [key],
                    "review_pass" if label == "passed" else "review_reject",
                    reviewer={
                        "claim": claim,
                        "cited_evidence_ids": ids,
                        "expected_label": label,
                        "rationale": "The original source supports: "
                        + answer
                        + (
                            " The example paraphrases that fact without broadening its scope."
                            if label == "passed"
                            else " The deliberately altered claim contradicts this source fact; reject the complete claim."
                        ),
                        "example_origin": origin,
                    },
                )

    # 五个共同维度，每一项比较均明确列出逐论文事实，避免从模型报告抄答案。
    comparison_facts = {
        "vision_encoder": {
            "blip2": (
                "CLIP ViT-L/14 and EVA-CLIP ViT-G/14 are explored, using second-last-layer features.",
                ["ev_9331722f1a47399ab173e624d02c6985"],
                ["blip2-image-backbones"],
            ),
            "flamingo": (
                "The stated vision encoder is NFNet F6.",
                ["ev_03da035280d198641cd7bcb081b6f845"],
                ["flamingo-vision-backbone"],
            ),
            "llava": (
                "The visual encoder considered is CLIP ViT-L/14, with grid features before/after the last Transformer layer.",
                ["ev_5af59f6012398940516f736562f5800e"],
                ["llava-vision-backbone"],
            ),
        },
        "vision_language_bridge": {
            "blip2": (
                "FC-projected Q-Former outputs are prepended as soft visual prompts to text embeddings.",
                ["ev_e4829335e46c87be4cada6e262bdd856"],
                ["blip2-soft-prompts"],
            ),
            "flamingo": (
                "A Resampler produces visual tokens and new cross-attention layers interleaved with frozen LM layers condition next-token prediction.",
                [
                    "ev_1f60e62600f7a79eae799d5ed285f571",
                    "ev_a802881b56aa396b45e4431996b61ae5",
                ],
                ["flamingo-frozen-backbones", "flamingo-interleaved-attention"],
            ),
            "llava": (
                "A trainable linear projection maps image features to the word-embedding space.",
                ["ev_5af59f6012398940516f736562f5800e"],
                ["llava-linear-projection"],
            ),
        },
        "frozen_trainable_scope": {
            "blip2": (
                "Q-Former is pretrained in two stages with a frozen image encoder and a frozen LLM.",
                ["ev_525a1f73be26c523e5fb57a5a02a3ecc"],
                ["blip2-two-stages"],
            ),
            "flamingo": (
                "The vision and language backbones are frozen; the Resampler and cross-attention links are learnable.",
                [
                    "ev_1f60e62600f7a79eae799d5ed285f571",
                    "ev_a802881b56aa396b45e4431996b61ae5",
                ],
                ["flamingo-frozen-backbones"],
            ),
            "llava": (
                "Stage 1 trains only W with vision/LLM frozen; stage 2 updates W and LLM while vision remains frozen.",
                [
                    "ev_c445999410b5ee326425346ce4b2377b",
                    "ev_84b236a4049e358dd2a7dda5bddb4464",
                ],
                ["llava-stage-one", "llava-stage-two"],
            ),
        },
        "training_objective": {
            "blip2": (
                "Generative pretraining uses language-modeling loss for decoder-based LLMs and prefix language-modeling loss for encoder-decoder LLMs.",
                ["ev_234984c983953494ce28027dc2716acc"],
                ["blip2-llm-types"],
            ),
            "flamingo": (
                "Visual cross-attention conditions the LM for next-token prediction; the vision encoder is left frozen in the main text-generation training phase.",
                [
                    "ev_a802881b56aa396b45e4431996b61ae5",
                    "ev_e86416c4f3b10aeabf3180b5f807d7be",
                ],
                ["flamingo-pretrained-lm", "flamingo-frozen-backbones"],
            ),
            "llava": (
                "Instruction tuning uses the LLM's original autoregressive training objective on prediction tokens.",
                ["ev_3c29482998c95fdc372eebddf41d6060"],
                ["llava-autoregressive-objective"],
            ),
        },
        "language_model": {
            "blip2": (
                "OPT decoder models and FlanT5 encoder-decoder models are explored.",
                ["ev_9331722f1a47399ab173e624d02c6985"],
                ["blip2-llm-types"],
            ),
            "flamingo": (
                "The prepared text describes an existing autoregressive LM pretrained on a large diverse text corpus; it does not supply a specific model-family name here.",
                ["ev_1f60e62600f7a79eae799d5ed285f571"],
                ["flamingo-pretrained-lm"],
            ),
            "llava": (
                "The fixed v1 paper chooses LLaMA.",
                ["ev_ebe5ff3e83181708c609e82e314f4f59"],
                ["llava-language-model"],
            ),
        },
    }
    for left, right in combinations(["blip2", "flamingo", "llava"], 2):
        for dim, values in comparison_facts.items():
            independent = [
                {
                    "paper_id": PAPERS[p],
                    "fact": values[p][0],
                    "evidence_ids": values[p][1],
                    "fact_group_ids": values[p][2],
                }
                for p in [left, right]
            ]
            ids = sorted({i for f in independent for i in f["evidence_ids"]})
            groups = sorted({g for f in independent for g in f["fact_group_ids"]})
            difference = (
                NAMES[left]
                + ": "
                + values[left][0]
                + " In comparison, "
                + NAMES[right]
                + ": "
                + values[right][0]
                + " Limit the comparison to these documented choices; do not infer overall performance superiority."
            )
            add(
                "comparison",
                left + "-" + right + "-" + dim.replace("_", "-"),
                [PAPERS[left], PAPERS[right]],
                dim,
                "Compare "
                + NAMES[left]
                + " and "
                + NAMES[right]
                + " on "
                + dim.replace("_", " ")
                + " using only the prepared corpus.",
                difference,
                ids,
                groups,
                "compare",
                comparison={
                    "dimension": dim,
                    "paper_facts": independent,
                    "expected_differences": [
                        {
                            "paper_ids": [PAPERS[left], PAPERS[right]],
                            "statement": difference,
                            "evidence_ids": ids,
                        }
                    ],
                },
            )

    def boundary(
        key,
        paper,
        question,
        reference,
        ids,
        kind,
        scope,
        group,
        abstain=True,
        page=None,
    ):
        add(
            "abstention",
            key,
            [PAPERS[paper]],
            "evidence_boundary",
            question,
            reference,
            ids,
            [group],
            "abstain" if abstain else "answer_with_limits",
            abstention={
                "expected_abstention": abstain,
                "boundary_kind": kind,
                "assessment_scope": scope,
                "checked_corpus_snapshot_id": snapshot["snapshot_id"],
                "rationale": reference,
                "requested_page": page,
            },
        )

    for paper in PAPERS:
        boundary(
            paper + "-private-manifest",
            paper,
            "Using only the prepared corpus, give the exact SHA-256 of the private training-dataset manifest for "
            + NAMES[paper]
            + ". Do not substitute the public paper PDF hash.",
            "The prepared corpus does not supply that private dataset-manifest hash. Abstain from the exact value; the public PDF hash is a different object.",
            [],
            "private_information",
            "prepared_corpus_only",
            "boundary-private-manifest",
        )
    for paper, page in [
        ("clip", 1),
        ("blip", 1),
        ("blip2", 5),
        ("flamingo", 1),
        ("llava", 2),
    ]:
        boundary(
            paper + "-unprepared-page",
            paper,
            f"Transcribe the exact first full paragraph on PDF page {page} of {NAMES[paper]}, using only already prepared page evidence; do not parse the PDF on demand.",
            f"PDF page {page} is not in this paper's prepared evidence pages. State that the requested exact paragraph is unavailable in the prepared corpus; do not reconstruct it from memory.",
            [],
            "unprepared_page",
            "specified_prepared_page",
            "boundary-unprepared-page",
            page=page,
        )
    figures = {
        "blip": "ev_e86cf697f7388bf7dca291a648f630b1",
        "blip2": "ev_b950abaa495e057a05fcb9617b5f37b8",
        "llava": "ev_2e61f65a38847f02450511bbee1ccef0",
    }
    for paper, identity in figures.items():
        boundary(
            paper + "-caption-rgb",
            paper,
            "Using only the supplied figure caption of "
            + NAMES[paper]
            + ", give the exact RGB hexadecimal color of the main module boxes. Do not inspect image pixels or call a vision tool.",
            "The supplied caption does not specify exact RGB values. Abstain from the pixel-specific value; caption text alone is not image-pixel analysis.",
            [identity],
            "caption_only",
            "provided_caption_only",
            "boundary-caption-rgb",
        )
    for key, paper in [
        ("clip-symmetric-loss", "clip"),
        ("flamingo-resampler-outputs", "flamingo"),
    ]:
        fact = next(f for f in FACTS if f[0] == key)
        boundary(
            key + "-answerable",
            paper,
            "Using only the cited prepared evidence, " + fact[3],
            fact[4],
            fact[5],
            "answerable_control",
            "prepared_corpus_only",
            key,
            abstain=False,
        )
    return cases


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_DATASET)
    args = parser.parse_args()
    records, snapshot = source_index(ROOT)
    manifest_path = args.output_dir / "manifest.json"
    created_at = (
        QualityManifest.model_validate_json(manifest_path.read_text()).prepared_at
        if manifest_path.exists()
        else datetime.now(UTC)
    )
    cases = build_candidates(records, snapshot, created_at)
    files, contents = {}, {}
    for task in ["claim", "reviewer", "comparison", "abstention"]:
        rows = [c for c in cases if c.task_type == task]
        filename = task + "_cases.jsonl"
        # 旧候选版本不写新AI字段，保持历史JSONL与清单字节不变。
        content = "".join(c.model_dump_json(exclude={"ai_review"}) + "\n" for c in rows)
        contents[filename] = content
        files[task] = {
            "filename": filename,
            "sha256": hashlib.sha256(content.encode()).hexdigest(),
            "count": len(rows),
        }
    commit = subprocess.check_output(
        ["git", "rev-parse", "stage-e-freeze-v1^{}"], cwd=ROOT, text=True
    ).strip()
    manifest = QualityManifest(
        dataset_version="research_quality_v1",
        prepared_at=created_at,
        stage_e_tag="stage-e-freeze-v1",
        stage_e_commit_sha=commit,
        corpus_snapshot_id=snapshot["snapshot_id"],
        registered_paper_ids=sorted(PAPERS.values()),
        purpose="research_quality_evaluation_only",
        forbidden_uses=["training", "prompt_optimization", "threshold_tuning"],
        target_counts={"claim": 30, "reviewer": 30, "comparison": 15, "abstention": 15},
        case_files=files,
        notes=[
            "All references are source-curated candidates, pending human original-PDF review; no approved Gold is created.",
            "Reviewer negatives are controlled contradictions with explicit source-based correction, not alleged paper facts.",
            "Related cases share fact_group_ids across tasks; all candidate splits are unassigned.",
            "Prepared-corpus absence is not a claim about absence from the entire PDF or external world.",
            "No Stage E model-generated Claim or abstract-only unsupported detail is used as a reference answer.",
        ],
    )
    validation = validate_dataset(
        QualityDataset(manifest=manifest, cases=cases), records, snapshot
    )
    contents["manifest.json"] = (
        manifest.model_dump_json(indent=2, exclude={"reference_kind"}) + "\n"
    )
    # 先检查所有目标，再写文件；任何人工编辑都不会被静默覆盖。
    for filename, text in contents.items():
        path = args.output_dir / filename
        if path.exists() and path.read_text() != text:
            raise ValueError(
                "Existing benchmark was edited; prepare into a new version directory instead"
            )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for filename, text in contents.items():
        path = args.output_dir / filename
        if not path.exists():
            path.write_text(text)
    print(json.dumps(validation, ensure_ascii=False))


if __name__ == "__main__":
    main()
