# wave-1 - Valkey 9.1 vs Redis 8.10

Definitive attempts: 90 / 90. Error records (not scored): 0. Spend, all records: $3.74.

## Pass matrix (n per cell = 3)

| model | cache valkey | cache redis | vector valkey | vector redis | migrate valkey | migrate redis | total |
|---|---|---|---|---|---|---|---|
| claude-opus-5-5 | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 | 18/18 |
| openai/gpt-6-sol | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 | 18/18 |
| deepseek/deepseek-v4.1-flash | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 | 18/18 |
| z-ai/glm-5.3-flash | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 | 18/18 |
| xiaomi/mimo-v2.6-flash | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 | 18/18 |
| **all** | **15/15** | **15/15** | **15/15** | **15/15** | **15/15** | **15/15** | |

## Effort per job and product (medians over all models)

| job | product | pass | tool calls | tokens | cost $ |
|---|---|---|---|---|---|
| cache | valkey | 15/15 | 7 | 22029 | 0.0328 |
| cache | redis | 15/15 | 8 | 34888 | 0.0188 |
| vector | valkey | 15/15 | 18 | 89567 | 0.0155 |
| vector | redis | 15/15 | 17 | 54837 | 0.0194 |
| migrate | valkey | 15/15 | 18 | 146817 | 0.0518 |
| migrate | redis | 15/15 | 21 | 108918 | 0.0263 |

## Stop reasons

```
{"completed": 90}
```

## Failure reasons

