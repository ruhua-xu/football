# ADR-0002：使用版本化 FusionPolicy

## 状态

Accepted

## 背景

如果上层流程直接写死 `P_final=P_quant`，以后加入市场融合和 LLM 时会修改应用流程，也无法准确回放历史预测。

## 决策

上层只依赖：

```text
FusionInputs -> FusionPolicy -> FinalPrediction
```

MVP 定义并测试：

```text
QUANT_ONLY_V1
MARKET_QUANT_BLEND_V1
```

第二个策略使用：

```text
P_final = w * P_quant + (1 - w) * P_market
```

`w` 来自运行配置且满足 `0 <= w <= 1`。每次 AnalysisRun 保存 policy ID、版本和完整配置快照。

## 结果

- MVP 默认 baseline 仍然简单、确定和可解释。
- 第二策略验证 FusionPolicy 接口确实可替换。
- 加入 LLM 时只增加新实现，不改变调用方。
- 输入缺失时不得隐式猜测，必须执行显式 fallback 并保存原因码。
