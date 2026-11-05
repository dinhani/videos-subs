from pathlib import Path

from models import LlamaModel, NmtFamily, NmtModel, ScoringModel
from runs import PipelineTranslator, Run, Source

PROMPTS_DIR = Path(__file__).parent / "prompts"

SOURCES = (
    Source(library_english=Path("Z:/midias/filmes/Fall - 1 - Fall (2022)/_subs/Fall (2022, Web-DL, 1080p, SDR, h264, 5.1, EAC3).embed.3.eng.sdh.srt")),
    Source(
        library_english=Path("Z:/midias/filmes/Hannibal Lecter - 1 - Silence of the Lambs, The (1991)/_subs/The Silence of the Lambs (1991, Web-DL, 2160p, SDR, h265, 5.1, AAC).embed.2.eng.srt"),
        library_official=Path("Z:/midias/filmes/Hannibal Lecter - 1 - Silence of the Lambs, The (1991)/_subs/The Silence of the Lambs (1991, Web-DL, 2160p, SDR, h265, 5.1, AAC).embed.21.pob.srt"),
    ),
    Source(
        library_english=Path("Z:/midias/filmes/F1 - 1 - F1 (2025)/_subs/F1 (2025, Web-DL, 2160p, HDR, h265, 5.1, EAC3 Atmos).embed.2.eng.srt"),
        library_official=Path("Z:/midias/filmes/F1 - 1 - F1 (2025)/_subs/F1 (2025, Web-DL, 2160p, HDR, h265, 5.1, EAC3 Atmos).embed.32.pob.srt"),
    ),
)

FLAGS_PIPELINE = ("--jinja", "--ctx-size", "16384", "--parallel", "1")
FLAGS_PIPELINE_8K = ("--jinja", "--ctx-size", "8192", "--parallel", "1")
FLAGS_THINKING_OFF = ("--reasoning-budget", "0")
FLAGS_THINKING_FULL = ("--reasoning-budget", "-1")
FLAGS_TEMPLATE_THINKING_OFF = ("--chat-template-kwargs", '{"enable_thinking": false}')
FLAGS_REASONING_LOW = ("--chat-template-kwargs", '{"reasoning_effort": "low"}')
FLAGS_GPU_ONLY = ("--n-gpu-layers", "all", "--fit", "off")
FLAGS_CACHE_Q8 = ("--cache-type-k", "q8_0", "--cache-type-v", "q8_0")
FLAGS_GEMMA = ("--no-mmproj", "--top-p", "0.95", "--top-k", "64")
FLAGS_COHERE = ("--no-jinja",)
FLAGS_TRANSLATEGEMMA = ("--no-jinja", "--chat-template", "gemma")
FLAGS_QWEN = ("--no-mmproj", "--top-p", "0.8", "--top-k", "20", "--min-p", "0")

GEMMA_4_26B = LlamaModel(name="gemma-4-26b", repo="lmstudio-community/gemma-4-26B-A4B-it-QAT-GGUF", file="gemma-4-26B-A4B-it-QAT-Q4_0.gguf", flags=(*FLAGS_PIPELINE, *FLAGS_THINKING_OFF, *FLAGS_GPU_ONLY, *FLAGS_GEMMA))
GEMMA_4_12B = LlamaModel(name="gemma-4-12b", repo="lmstudio-community/gemma-4-12B-it-QAT-GGUF", file="gemma-4-12B-it-QAT-Q4_0.gguf", flags=(*FLAGS_PIPELINE, *FLAGS_THINKING_OFF, *FLAGS_GPU_ONLY, *FLAGS_GEMMA))
MISTRAL_SMALL_3_2_24B = LlamaModel(name="mistral-small-3.2-24b", repo="bartowski/mistralai_Mistral-Small-3.2-24B-Instruct-2506-GGUF", file="mistralai_Mistral-Small-3.2-24B-Instruct-2506-IQ4_XS.gguf", flags=(*FLAGS_PIPELINE, *FLAGS_GPU_ONLY, *FLAGS_CACHE_Q8))
MINISTRAL_3_14B = LlamaModel(name="ministral-3-14b", repo="bartowski/mistralai_Ministral-3-14B-Instruct-2512-GGUF", file="mistralai_Ministral-3-14B-Instruct-2512-Q6_K.gguf", flags=(*FLAGS_PIPELINE, *FLAGS_GPU_ONLY))
QWEN_3_14B = LlamaModel(name="qwen-3-14b", repo="bartowski/Qwen_Qwen3-14B-GGUF", file="Qwen_Qwen3-14B-Q6_K.gguf", flags=(*FLAGS_PIPELINE, *FLAGS_THINKING_OFF, *FLAGS_TEMPLATE_THINKING_OFF, *FLAGS_GPU_ONLY, *FLAGS_CACHE_Q8, *FLAGS_QWEN))
GPT_OSS_20B = LlamaModel(name="gpt-oss-20b", repo="lmstudio-community/gpt-oss-20b-GGUF", file="gpt-oss-20b-MXFP4.gguf", flags=(*FLAGS_PIPELINE, *FLAGS_REASONING_LOW, *FLAGS_GPU_ONLY))
ERNIE_4_5_21B = LlamaModel(name="ernie-4.5-21b", repo="lmstudio-community/ERNIE-4.5-21B-A3B-PT-GGUF", file="ERNIE-4.5-21B-A3B-PT-Q4_K_M.gguf", flags=(*FLAGS_PIPELINE, *FLAGS_GPU_ONLY, *FLAGS_CACHE_Q8))
COMMAND_R7B = LlamaModel(name="command-r7b", repo="bartowski/c4ai-command-r7b-12-2024-GGUF", file="c4ai-command-r7b-12-2024-Q8_0.gguf", flags=(*FLAGS_PIPELINE, *FLAGS_GPU_ONLY, *FLAGS_COHERE))
AYA_EXPANSE_8B = LlamaModel(name="aya-expanse-8b", repo="bartowski/aya-expanse-8b-GGUF", file="aya-expanse-8b-Q8_0.gguf", flags=(*FLAGS_PIPELINE, *FLAGS_GPU_ONLY, *FLAGS_COHERE))
MAGISTRAL_SMALL_24B = LlamaModel(name="magistral-small-24b", repo="bartowski/mistralai_Magistral-Small-2509-GGUF", file="mistralai_Magistral-Small-2509-IQ4_XS.gguf", flags=(*FLAGS_PIPELINE, *FLAGS_THINKING_FULL, *FLAGS_GPU_ONLY, *FLAGS_CACHE_Q8))
GEMMA_3_12B = LlamaModel(name="gemma-3-12b", repo="lmstudio-community/gemma-3-12b-it-GGUF", file="gemma-3-12b-it-Q4_K_M.gguf", flags=(*FLAGS_PIPELINE, *FLAGS_GPU_ONLY, *FLAGS_GEMMA))
GEMMA_4_31B = LlamaModel(name="gemma-4-31b-iq3", repo="bartowski/google_gemma-4-31B-it-GGUF", file="google_gemma-4-31B-it-IQ3_XS.gguf", flags=(*FLAGS_PIPELINE_8K, *FLAGS_THINKING_OFF, *FLAGS_GPU_ONLY, *FLAGS_CACHE_Q8, *FLAGS_GEMMA))

HY_MT2_7B = LlamaModel(name="hy-mt2-7b", repo="tencent/Hy-MT2-7B-GGUF", file="HY-MT2-7B-Q8_0.gguf", flags=(*FLAGS_PIPELINE, *FLAGS_GPU_ONLY))
TOWER_PLUS_9B = LlamaModel(name="tower-plus-9b", repo="mradermacher/Tower-Plus-9B-GGUF", file="Tower-Plus-9B.Q8_0.gguf", flags=(*FLAGS_PIPELINE, *FLAGS_GPU_ONLY))
TRANSLATEGEMMA_12B = LlamaModel(name="translategemma-12b", repo="mradermacher/translategemma-12b-it-GGUF", file="translategemma-12b-it.Q6_K.gguf", flags=(*FLAGS_PIPELINE, *FLAGS_GPU_ONLY, *FLAGS_TRANSLATEGEMMA))
HY_MT2_30B = LlamaModel(name="hy-mt2-30b-iq3", repo="mradermacher/Hy-MT2-30B-A3B-i1-GGUF", file="Hy-MT2-30B-A3B.i1-IQ3_XS.gguf", flags=(*FLAGS_PIPELINE_8K, *FLAGS_GPU_ONLY))

MADLAD_400_10B = NmtModel(name="madlad-400-10b", repo="SoybeanMilk/madlad400-10b-mt-ct2-int8_float16", family=NmtFamily.MADLAD)
MADLAD_400_7B = NmtModel(name="madlad-400-7b", repo="avans06/madlad400-7b-mt-bt-ct2-int8_float16", family=NmtFamily.MADLAD)
MADLAD_400_3B = NmtModel(name="madlad-400-3b", repo="SoybeanMilk/madlad400-3b-mt-ct2-int8_float16", family=NmtFamily.MADLAD)
NLLB_200_3_3B = NmtModel(name="nllb-200-3.3b", repo="OpenNMT/nllb-200-3.3B-ct2-int8", family=NmtFamily.NLLB)
NLLB_200_1_3B = NmtModel(name="nllb-200-1.3b", repo="OpenNMT/nllb-200-distilled-1.3B-ct2-int8", family=NmtFamily.NLLB)
NLLB_200_600M = NmtModel(name="nllb-200-600m", repo="JustFrederik/nllb-200-distilled-600M-ct2-int8", family=NmtFamily.NLLB)
OPUS_MT_EN_PT = NmtModel(name="opus-mt-en-pt", repo="ooeoeo/opus-mt-tc-big-en-pt-ct2-float16", family=NmtFamily.OPUS)
NMT_MODELS = (MADLAD_400_10B, MADLAD_400_7B, MADLAD_400_3B, NLLB_200_3_3B, NLLB_200_1_3B, NLLB_200_600M, OPUS_MT_EN_PT)

MODELS = (*NMT_MODELS, GEMMA_4_26B, GEMMA_4_12B, MISTRAL_SMALL_3_2_24B, MINISTRAL_3_14B, QWEN_3_14B, GPT_OSS_20B, ERNIE_4_5_21B, COMMAND_R7B, AYA_EXPANSE_8B, MAGISTRAL_SMALL_24B, GEMMA_3_12B, GEMMA_4_31B, HY_MT2_7B, TOWER_PLUS_9B, TRANSLATEGEMMA_12B, HY_MT2_30B)

COMET_WMT22 = ScoringModel(name="comet-wmt22", repo="Unbabel/wmt22-comet-da", files=("checkpoints/model.ckpt", "hparams.yaml"))
XLM_ROBERTA_LARGE = ScoringModel(name="xlm-roberta-large", repo="xlm-roberta-large", files=("config.json", "sentencepiece.bpe.model", "tokenizer.json", "tokenizer_config.json"))
SCORING_MODELS = (COMET_WMT22, XLM_ROBERTA_LARGE)

RUNS_ACTIVE = (
    Run.from_model(GEMMA_4_26B),
    Run.from_fixes(Run.from_model(GEMMA_4_26B)),
    *(Run.from_prompt(GEMMA_4_26B, prompt) for prompt in sorted(PROMPTS_DIR.glob("*.txt"))),
)
RUNS_DISABLED = (
    *(Run.from_model(model) for model in NMT_MODELS),
    Run.from_model(HY_MT2_7B),
    Run.from_model(COMMAND_R7B),
    Run.from_model(AYA_EXPANSE_8B),
    Run.from_model(TOWER_PLUS_9B),
    Run.from_model(GEMMA_4_12B),
    Run.from_model(GEMMA_3_12B),
    Run.from_model(TRANSLATEGEMMA_12B),
    Run.from_model(MINISTRAL_3_14B),
    Run.from_model(QWEN_3_14B),
    Run.from_model(GPT_OSS_20B),
    Run.from_model(ERNIE_4_5_21B),
    Run.from_model(MISTRAL_SMALL_3_2_24B),
    Run.from_model(MAGISTRAL_SMALL_24B),
    Run.from_model(HY_MT2_30B),
    Run.from_model(GEMMA_4_31B),
    Run.from_fixes(Run.from_model(GEMMA_4_31B)),
)
RUNS = (*RUNS_ACTIVE, *RUNS_DISABLED)
BASELINE_RUN = GEMMA_4_26B.name
