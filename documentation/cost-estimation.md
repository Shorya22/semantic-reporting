# Cost Estimation — Agentic AI Semantic Reporting

**Baseline:** 1,000 active users · Medium usage = 750K queries/month  
**Updated:** 2026-04-28

---

## Agent Pipeline (per query)

| Agent | Tokens (in + out) | Complexity |
|---|---|---|
| Router / Intent Classifier | ~500 | Low — smallest model viable |
| SQL Generator | ~2,000 | High — needs strong code/SQL reasoning |
| SQL Validator | ~500 | Low — pattern matching |
| Result Interpreter | ~1,500 | Medium — needs data reasoning |
| Chart Spec Generator | ~1,000 | Medium — structured JSON output |
| Report Composer | ~3,000 | Medium — fluent long-form writing |

**Per-query totals:** ~8,500 tokens total · ~5,500 input · ~3,000 output

---

## Usage Tiers

| Tier | Queries/user/day | Queries/month | Input tokens/month | Output tokens/month |
|---|---|---|---|---|
| Light | 10 | 300K | 1.65B | 900M |
| Medium | 25 | 750K | 4.1B | 2.25B |
| Heavy | 50 | 1.5M | 8.25B | 4.5B |

---

## External API Providers

### Groq (LPU — Fastest Inference)

| Model | Input $/1M | Output $/1M | Speed (tok/s) |
|---|---|---|---|
| Llama 3.3 70B | $0.59 | $0.79 | ~330 |
| Llama 3.1 8B | $0.05 | $0.08 | ~750 |
| Llama 3.2 3B | $0.06 | $0.06 | ~900 |
| Llama 3.2 1B | $0.04 | $0.04 | ~1,200 |
| Mixtral 8x7B | $0.24 | $0.24 | ~480 |
| DeepSeek R1 Distill 70B | $0.75 | $0.99 | ~200 |

- **Best for:** Router + Validator + Chart agents (ultra-low latency, very cheap)
- **TTFT:** <200ms — fastest commercially available
- **Limits:** 1,000 RPM (paid). Rate-limiting at scale is the key risk.
- **Optimal mix cost (Medium):** ~$2,100/month (blended)
- **Cons:** Open-source models only, no fine-tuning, SLA less mature than hyperscalers

---

### Google Gemini

| Model | Input $/1M | Output $/1M | Context | Free Tier |
|---|---|---|---|---|
| Gemini 2.5 Pro | $1.25–$2.50 | $10–$15 | 1M | 5 RPM (AI Studio) |
| Gemini 2.0 Flash | $0.10 | $0.40 | 1M | 15 RPM |
| Gemini 2.0 Flash-Lite | $0.075 | $0.30 | 1M | 30 RPM |
| Gemini 1.5 Flash | $0.075 | $0.30 | 1M | 15 RPM |
| Gemini 1.5 Flash-8B | $0.0375 | $0.15 | 1M | 15 RPM |

- **Best for:** Result Interpreter + Report Composer (great value, 1M context)
- **Optimal mix cost (Medium):** ~$850/month (blended)
- **Free tier** useful for dev/staging (AI Studio)
- **Cons:** SQL generation slightly behind Claude/GPT-4o on complex joins/CTEs

---

### Anthropic (Direct API)

| Model | Input $/1M | Output $/1M | Cached Input $/1M | Context |
|---|---|---|---|---|
| Claude Sonnet 4.6 | $3.00 | $15.00 | $0.30 (read) | 200K |
| Claude 3.5 Haiku | $0.80 | $4.00 | $0.08 (read) | 200K |
| Claude 3 Haiku | $0.25 | $1.25 | $0.03 (read) | 200K |

- **Best for:** SQL Generator (best-in-class SQL + structured output)
- **Prompt caching** on system prompts (90% cache hit) cuts effective input cost ~80%
- **With caching, Sonnet effective input:** ~$0.57/1M
- **Optimal mix cost (Medium, with caching):** ~$3,100/month (Sonnet SQL + Haiku rest)
- **Cons:** No fine-tuning, fewer models in lineup

---

### OpenAI

| Model | Input $/1M | Output $/1M | Speed (tok/s) |
|---|---|---|---|
| GPT-4o | $2.50 | $10.00 | ~100 |
| GPT-4o-mini | $0.15 | $0.60 | ~150 |
| o3-mini | $1.10 | $4.40 | ~60 |

- **Best for:** SQL Generator (GPT-4o), all others (GPT-4o-mini)
- **Batch API:** 50% off for async workloads (scheduled reports)
- **Optimal mix cost (Medium):** ~$3,400/month
- **Cons:** Expensive at scale, no self-hosting, data privacy concerns

---

### Mistral AI

| Model | Input $/1M | Output $/1M | Notes |
|---|---|---|---|
| Codestral (22B) | $0.20 | $0.60 | Code/SQL optimized |
| Mistral Small (22B) | $0.20 | $0.60 | General |
| Mistral Nemo (12B) | $0.15 | $0.15 | Fast, cheap |
| Mistral Large 2 | $2.00 | $6.00 | High quality |

- **Best for:** SQL Generator via Codestral (purpose-built code model)
- **Optimal mix cost (Medium):** ~$1,800/month
- **Cons:** Codestral SQL quality below GPT-4o/Claude on complex schemas — benchmark first

---

### DeepInfra (Budget API)

| Model | Input $/1M | Output $/1M |
|---|---|---|
| Llama 3.3 70B | $0.35 | $0.40 |
| Llama 3.1 8B | $0.06 | $0.06 |
| Qwen 2.5 72B | $0.35 | $0.40 |

- **Cheapest 70B inference** via API (~60% cheaper than Groq for 70B)
- **Optimal mix cost (Medium):** ~$1,200/month
- **Cons:** Slower than Groq, smaller ecosystem, less SLA certainty

---

### Together.ai / Fireworks.ai

| Provider | Model | Input+Output $/1M | Speed |
|---|---|---|---|
| Together | Llama 3.3 70B | $0.88 | ~100 tok/s |
| Together | DeepSeek V3 | $0.90 | ~60 tok/s |
| Together | DeepSeek R1 | $3.00 | ~30 tok/s |
| Fireworks | Llama 3.3 70B | $0.90 | ~140 tok/s |
| Fireworks | FireFunction V2 | $0.90 | ~100 tok/s |

- Together: good for fine-tuning + dedicated endpoints
- Fireworks: excellent for structured JSON output (grammar-guided generation)
- **Optimal mix cost (Medium):** ~$2,600–$2,800/month

---

### AWS Bedrock

- Same models as Anthropic/OpenAI/Mistral + Llama + Cohere, 10–20% premium
- **Enterprise add-ons:** VPC endpoints, CloudWatch, guardrails API, SOC2/HIPAA BAA
- **Best for:** Enterprise compliance, unified AWS billing, no infrastructure management
- **Provisioned Throughput:** 30–50% savings for sustained high volume (monthly commitment)
- **Optimal mix cost (Medium, Claude 3.5 Sonnet SQL + Haiku rest):** ~$6,200/month

---

## Self-Hosted Options

### Recommended Models for Self-Hosting

| Model | Q4 Size | Min VRAM | Quality |
|---|---|---|---|
| Llama 3.3 70B | ~40GB | 48GB (1x GPU) | High |
| Qwen 2.5 72B | ~42GB | 48GB | High |
| DeepSeek V3 (671B MoE) | ~130GB | 4×80GB | Very High |
| Mistral Nemo 12B | ~8GB | 16GB | Medium |
| Llama 3.1 8B | ~5GB | 8GB | Medium |
| Llama 3.2 3B | ~2GB | 4GB | Low-Medium |
| Llama 3.2 1B | ~0.7GB | 2GB | Low (routing only) |
| CodeLlama 34B | ~20GB | 24GB | High (SQL focus) |

---

### Self-Hosted GPU Cloud (Inference Servers)

| Provider | GPU | $/hr | Monthly (24/7) | Notes |
|---|---|---|---|---|
| Lambda Labs | A100 80GB | $1.80 | $1,296 | ML-focused, often sold out |
| Lambda Labs | H100 SXM | $3.45 | $2,484 | Best perf/$, best option |
| RunPod (Secure) | A100 80GB | $2.21 | $1,591 | Reliable, on-demand |
| RunPod (Community) | RTX 4090 | $0.44 | $317 | Dev/test only |
| Vast.ai | A100 80GB | $0.90–$1.80 | $648–$1,296 | Marketplace, variable |
| CoreWeave | H100 SXM | $4.76 | $3,427 | Enterprise SLA |

**Inference stack:** vLLM (recommended for production — PagedAttention, continuous batching, OpenAI-compatible API) or Ollama (dev/simple setups)

**70B Q4 on A100 80GB:** ~50 tok/s, ~12 concurrent requests — handles Medium usage with 1 GPU
**Full self-hosted cost (Medium, 1×A100):** ~$1,300–$1,600/month (all agents)

---

### Self-Hosted CPU (Budget / Air-Gapped)

| Platform | CPU | RAM | $/month | 70B Speed | 8B Speed |
|---|---|---|---|---|---|
| Hetzner AX162 | EPYC 9554P 64c | 256GB | ~$280 | ~4 tok/s | ~25 tok/s |
| Hetzner AX102 | EPYC 9454P 48c | 128GB | ~$180 | N/A | ~15 tok/s |
| OVHcloud Adv-6 | EPYC 9354P 32c | 256GB | ~$350 | ~3 tok/s | ~20 tok/s |

- **Only viable for 8B and smaller models** in real-time pipeline (70B is too slow: 45–250s/response)
- **Best use:** Router (1B/3B) + Validator + Chart agents on CPU; GPU handles SQL/Interpreter/Report
- **Hybrid (Hetzner CPU + RunPod A100):** ~$1,870/month for Medium usage

---

### Local Deployment (Developer / Small Team)

| Machine | 70B Speed | 8B Speed | One-Time Cost |
|---|---|---|---|
| Mac Studio M4 Max 128GB | ~12–15 tok/s | ~40 tok/s | $4,999 |
| Mac Studio M3 Ultra 192GB | ~18–22 tok/s | ~50 tok/s | ~$7,000 |
| Desktop RTX 4090 + 64GB RAM | ~25–35 tok/s | ~80 tok/s | ~$2,500 |
| Desktop RTX 3090 + 32GB RAM | ~15–20 tok/s | ~50 tok/s | ~$1,500 |

- **Zero marginal cost** after hardware purchase
- **Mac (Apple Silicon):** Unified memory = run 70B without multi-GPU complexity
- **RTX 4090:** Best bang-for-buck local GPU, 70B Q4 fits in 24GB VRAM
- **Not production viable** for 1,000 users — dev/testing/POC only

---

### On-Premise Server (Regulated / Air-Gapped)

| Config | Hardware | One-Time Cost | Monthly TCO |
|---|---|---|---|
| 1× A100 80GB | EPYC + 256GB RAM | ~$18,000 | ~$2,144 |
| 1× H100 80GB | EPYC + 512GB RAM | ~$40,000 | ~$3,800 |
| CPU-only server | EPYC 9754 128c + 384GB | ~$18,000 | ~$900 |

- **Break-even vs cloud:** ~18–24 months for single GPU server
- **3-year TCO (1× A100):** ~$77,000 total vs $46,000 on Lambda Labs cloud
- **Best for:** HIPAA / PCI-DSS / classified environments where data cannot leave premises
- **Gap:** Fine-tune open-source models on your SQL dialect to close quality gap vs Claude/GPT-4o

---

## Quantization Quick Reference

| Format | 70B Size | Quality Loss | Recommendation |
|---|---|---|---|
| FP16 | 140GB | Baseline | Multi-GPU only |
| INT8 AWQ | 70GB | <1% | A100 production |
| INT4 AWQ (Q4_K_M) | 40GB | 2–3% | Sweet spot — single A100/L40S |
| GGUF Q5_K_M | 48GB | 1–2% | llama.cpp balance |
| GGUF Q4_K_M | 40GB | 2–3% | Most popular |

**For SQL Generator:** Use INT8 or Q5 minimum — precision matters for complex SQL  
**For Router/Validator/Chart:** Q4 or smaller is fine

---

## Recommended Configurations

### Config 1 — Budget (< $700/month, Medium usage)

| Agent | Provider | Model |
|---|---|---|
| Router | Groq | Llama 3.2 1B |
| SQL Generator | DeepInfra | Llama 3.3 70B |
| SQL Validator | Groq | Llama 3.1 8B |
| Result Interpreter | DeepInfra | Llama 3.3 70B |
| Chart Spec | Groq | Llama 3.1 8B |
| Report Composer | DeepInfra | Llama 3.3 70B |

**~$220/month (Light) · ~$657/month (Medium)**  
Tradeoff: SQL quality ~70–80% of Claude/GPT-4o. Requires quality monitoring.

---

### Config 2 — Best Value (Startup · $500–$1,500/month)

| Agent | Provider | Model |
|---|---|---|
| Router | Groq | Llama 3.2 1B |
| SQL Generator | Anthropic | Claude 3.5 Haiku (prompt cached) |
| SQL Validator | Groq | Llama 3.1 8B |
| Result Interpreter | Gemini | 2.0 Flash |
| Chart Spec | Groq | Llama 3.1 8B |
| Report Composer | Gemini | 2.0 Flash |

**~$500/month (Light) · ~$1,282/month (Medium) · ~$2,564/month (Heavy)**  
Best quality-per-dollar. Claude Haiku prompt caching cuts 90% of input cost. Groq handles fast/cheap tasks.

---

### Config 3 — Enterprise ($3,000–$8,000/month)

| Agent | Provider | Model |
|---|---|---|
| Router | AWS Bedrock | Claude 3 Haiku |
| SQL Generator | AWS Bedrock | Claude Sonnet 4.6 |
| SQL Validator | AWS Bedrock | Claude 3 Haiku |
| Result Interpreter | AWS Bedrock | Claude 3.5 Haiku |
| Chart Spec | AWS Bedrock | Claude 3 Haiku |
| Report Composer | AWS Bedrock | Claude 3.5 Haiku |

**~$2,500/month (Light) · ~$6,240/month (Medium) · ~$12,480/month (Heavy)**  
Single-provider, SLA, VPC, CloudWatch, HIPAA-eligible. Zero infra management.

---

### Config 4 — Maximum Speed (Sub-5s pipeline)

| Agent | Provider | Model | TTFT |
|---|---|---|---|
| Router | Groq | Llama 3.2 1B | <50ms |
| SQL Generator | Groq | Llama 3.3 70B | <200ms |
| SQL Validator | Groq | Llama 3.1 8B | <100ms |
| Result Interpreter | Groq | Llama 3.3 70B | <200ms |
| Chart Spec | Groq | Llama 3.1 8B | <100ms |
| Report Composer | Groq | Llama 3.3 70B | <200ms |

**~$3,572/month (Medium)**  
End-to-end pipeline: ~3.5s (with parallelization). Risk: 1,000 RPM rate limit at scale.

---

### Config 5 — Air-Gapped / On-Premise

| Agent | Infra | Model |
|---|---|---|
| Router + Validator + Chart | CPU (EPYC server) | Llama 3.2 3B / 8B Q4 |
| SQL Generator + Interpreter + Report | GPU (A100 80GB) | Llama 3.3 70B Q4 |

**~$2,944/month TCO (amortized) · ~$65,000 Year 1 (hardware + colo + ops)**  
No data leaves premises. Fine-tune on your SQL schema to close quality gap.

---

## Cost Optimization Levers

| Strategy | Savings | Complexity |
|---|---|---|
| Prompt caching (Anthropic) | 60–90% on input tokens | Zero — built-in |
| Exact query cache (Redis) | 10–30% | Low |
| Semantic cache (embedding similarity) | 20–40% | Medium |
| SQL result cache (skip downstream agents) | 30–50% on cache hits | Low |
| Batch API (OpenAI/Anthropic/Bedrock) | 50% off | Low — async only |
| Small model for router/validator | 80–95% vs using 70B everywhere | Low |
| Groq for latency-insensitive small tasks | Near-zero cost | Low |

**Combined realistic savings: 30–45% off raw API cost.**  
Config 2 baseline $1,282 → with caching: **~$770–$900/month**

---

## Provider Comparison Summary

| Provider | Best For | Monthly (Medium, optimal mix) | SQL Quality | Self-Host | Fine-tune |
|---|---|---|---|---|---|
| Groq | Speed + cheap simple tasks | ~$2,100 (all-Groq) | Good | No | No |
| Gemini Flash | Low-cost general reasoning | ~$850 | Good | No | No (Vertex) |
| Anthropic | SQL + structured output | ~$3,100 | Excellent | No | No |
| OpenAI | SQL + ecosystem | ~$3,400 | Excellent | No | Yes |
| Mistral | Code/SQL via Codestral | ~$1,800 | Very Good | Yes (weights) | Yes |
| DeepInfra | Cheapest 70B API | ~$1,200 | Good | No | No |
| Together.ai | Fine-tuning + flex | ~$2,800 | Good | Dedicated EP | Yes |
| AWS Bedrock | Enterprise compliance | ~$6,200 | Excellent | No | No |
| Self-hosted GPU | Privacy + volume scale | ~$1,300–$1,600 | Good (open LLM) | Yes | Yes |
| Self-hosted CPU | Air-gapped / ultra-budget | ~$280–$550 | Good (8B only RT) | Yes | Yes |
| On-premise | Regulated / no cloud | ~$2,944/mo TCO | Good (open LLM) | Yes | Yes |

---

## Recommended Starting Point

**Start with Config 2** (Best Value):
- Groq Llama 1B for routing (~$15/month)
- Claude 3.5 Haiku with prompt caching for SQL (~$750/month)
- Gemini 2.0 Flash for interpretation and reports (~$480/month)

Add prompt + result caching from day one. Upgrade SQL agent to Claude Sonnet only when query complexity demands it.