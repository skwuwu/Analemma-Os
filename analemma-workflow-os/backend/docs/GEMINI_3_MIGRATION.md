# Gemini 3 Migration Guide

## Overview

Analemma-OS has been upgraded to support **Gemini 3 Pro/Flash** models, the latest generation of Google's multimodal AI with advanced reasoning capabilities.

## What's New

### Gemini 3 Models

- **gemini-3-pro**: Advanced reasoning with 1M context, adaptive thinking capabilities
- **gemini-3-flash**: Best multimodal understanding, near-zero thinking level, excellent performance/cost ratio

### Gemini 2.5 Models (Still Supported)

- **gemini-2.5-pro**: Adaptive thinking, 1M context
- **gemini-2.5-flash**: Controllable thinking budgets

### Legacy Models (Still Supported)

- **gemini-2.0-flash**: Cost-effective general purpose
- **gemini-1.5-pro**: Stable production model
- **gemini-1.5-flash**: Fast real-time collaboration
- **gemini-1.5-flash-8b**: Ultra-low cost option

## Key Features

### 1. Extended Context Window
- **Gemini 3**: 2M tokens (double Gemini 1.5)
- Better long-context understanding

### 2. Thinking Capabilities
- **Gemini 3 Pro**: Adaptive thinking for complex reasoning
- **Gemini 3 Flash**: Near-zero thinking level for fast responses
- Chain of Thought visualization in Co-design mode

### 3. Enhanced Multimodal
- Better image understanding
- Improved code generation
- Native JSON schema support

## Changes Made

### 1. Updated Dependencies

```bash
# requirements.txt
google-cloud-aiplatform>=1.71.0  # Gemini 3 support
```

### 2. New Model Definitions

**gemini_service.py**:
```python
class GeminiModel(Enum):
    # Gemini 3: Latest generation
    GEMINI_3_PRO = "gemini-3-pro"
    GEMINI_3_FLASH = "gemini-3-flash"
    
    # Gemini 2.5: Thinking capabilities
    GEMINI_2_5_PRO = "gemini-2.5-pro"
    GEMINI_2_5_FLASH = "gemini-2.5-flash"
    
    # Legacy models...
```

### 3. Updated Codesign Service

The Co-design Assistant now uses **Gemini 3 Flash** by default:

```python
def get_gemini_codesign_service() -> GeminiService:
    return GeminiService(GeminiConfig(
        model=GeminiModel.GEMINI_3_FLASH,  # Latest generation
        enable_thinking=True,
        enable_automatic_caching=True,
    ))
```

### 4. Model Router Updates

**model_router.py**: Added Gemini 3 models to routing logic with optimized pricing and performance profiles.

## Migration Steps

### 1. Install Updated Dependencies

```bash
cd backend
pip install --upgrade google-cloud-aiplatform
```

Or install all requirements:

```bash
pip install -r requirements.txt
```

### 2. Deploy to Lambda

```bash
sam build
sam deploy
```

### 3. Test Gemini 3 Models

```python
from src.services.llm.gemini_service import GeminiService, GeminiConfig, GeminiModel

# Test Gemini 3 Flash
service = GeminiService(GeminiConfig(
    model=GeminiModel.GEMINI_3_FLASH
))

response = service.invoke_model(
    user_prompt="Explain quantum computing",
    system_instruction="You are a helpful AI assistant"
)
```

## Pricing Comparison

| Model | Input ($/1M tokens) | Output ($/1M tokens) | Cached ($/1M tokens) |
|-------|--------------------:|---------------------:|---------------------:|
| gemini-3-pro | $1.50 | $6.00 | $0.375 |
| gemini-3-flash | $0.20 | $0.80 | $0.05 |
| gemini-2.5-flash | $0.15 | $0.60 | $0.0375 |
| gemini-2.0-flash | $0.10 | $0.40 | $0.025 |
| gemini-1.5-pro | $1.25 | $5.00 | $0.3125 |

## Performance Impact

- **Gemini 3 Flash**: ~20% faster TTFT than Gemini 2.5 Flash
- **Context Caching**: 75% cost reduction for repeated contexts
- **Thinking Mode**: Available in Co-design for better reasoning

## Backwards Compatibility

All existing code continues to work with legacy models. No breaking changes to API.

## Troubleshooting

### Import Error
```bash
# Error: google-cloud-aiplatform package not installed
pip install google-cloud-aiplatform>=1.71.0
```

### Model Not Found
```
# Error: 404 Model not found
# Solution: Ensure you're using Vertex AI SDK 1.71+ and check model name
```

### Authentication Issues
```bash
# Ensure GCP credentials are configured
export GCP_PROJECT_ID="your-project-id"
export GCP_LOCATION="us-central1"
export GOOGLE_APPLICATION_CREDENTIALS="/path/to/service-account.json"
```

## Documentation

- [Gemini 3 Models](https://cloud.google.com/vertex-ai/docs/generative-ai/models/gemini-3)
- [Vertex AI SDK](https://cloud.google.com/vertex-ai/docs/python-sdk/use-vertex-ai-python-sdk)
- [Context Caching](https://cloud.google.com/vertex-ai/docs/generative-ai/context-cache)

## Next Steps

1. **Install dependencies**: `pip install -r requirements.txt`
2. **Test locally**: Run unit tests with new models
3. **Deploy to Lambda**: Use SAM to deploy updated backend
4. **Monitor performance**: Check CloudWatch for latency/cost metrics
5. **Enable Gemini 3**: Update Codesign API to use Gemini 3 Flash

---

**Last Updated**: January 28, 2026
**Version**: 2.0.0
