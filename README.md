# 跨款尺码、试穿证据与换货权益联动

项目使用 Python 3.11 标准库，把以下资料连到同一个柜台/售后结论中：

- `scales.json`：按款、标注版本发布的手围、指长、掌宽边界；
- `reference.json`：同一受试者同次取得的手围/指长对应；
- `fittings.json`：按人聚类的试穿等级与适用区间；
- `provider_reports.json`：测量服务商实际上报的部位、区间、场景和到件周；
- `orders.json` / `exchanges.json`：原发货规格、凭证限额和换货状态；
- `store_handover.json`：跨店接收、版次回执、拒绝和单据引用链；
- `compensation_receipts.json`：已到账/待认可的运费、差额支付事实；
- `commercial.json`：标注争议、偏好变更、适配不明的费用分担规则。

## 运行

```sh
python3 scripts/inspect_sources.py
python3 -m unittest discover -s tests
```

柜台 JSON：

```sh
python3 -m sizing_exchange.cli advice --sku B2 --version V3 --person U014 --as-of W10
python3 -m sizing_exchange.cli application O1 B1 V3 --exchange E1 --as-of W13
python3 -m sizing_exchange.cli ledger O1
python3 -m sizing_exchange.cli routing E1
python3 -m sizing_exchange.cli impact
python3 -m sizing_exchange.cli dashboard   # 完整资料场景，输出较大
```

## 程序化接口

```python
from sizing_exchange import CounterService, Interval

service = CounterService()

# 1. 尺码建议：会列出使用了哪些测量、量表连接路径、客群分布和可信度。
advice = service.fit.advise("B2", "V3", person="U014", as_of="W10")
assert advice["status"] == "fit_supported"
assert advice["store_guarantee_allowed"] is True

# 2. 实际发货版次不明：并列 V3/V4，只给跨版交集作为保守可承诺范围。
ambiguous = service.size_advice("B1", person="U014", as_of="W12")
assert ambiguous["version_state"] == "actual_version_unknown"
assert ambiguous["committable_range_across_versions"] == {
    "lo_mm": 67.0, "hi_mm": 72.0, "axis": "指长", "source": None
}

# 3. 新换货申请：适配、原因、费用、凭证余额、左右配对、跨店确认一起返回。
decision = service.assess_application(
    "O1", "B1", "V3", exchange_id="E1", as_of="W13",
    freight_cny=12, gap_cny=10, quote_provided=True,
)
```

## 核心建模规则

### 1. 测量来源不互相覆盖

`ScaleService.measurement_sets(person, as_of)` 逐条保留：

- `reference` 的同次共同实测；
- 服务商报告的 `measured_axis`、`scope`、样本周和到件周；
- 单点测量表示为 `[x, x]`。

`as_of` 按 `W01` 这样的周次过滤。U014 在 `W10` 只能看到 W02 共同实测和 W06 的 R1 手围报告；W13 才能看到 R2 指长、R4 手围复测。R2 是指长测量，不会补写过去的手围结论；不同场景/周次也不会被静默合并成一次测量。

柜台自己的简式单据由 `counter.CounterSlip` 表示，只允许备选款、测量方式、是否成副试穿；传入数值区间或试穿等级会直接报错，防止把柜台便条当成服务商测量。

### 2. 量表图只沿共同测量或匹配试穿连通

`ScaleService` 构建 `手围 — 指长 — 掌宽` 图：

- `reference.json` 提供 `common_measurement` 边，可做经验映射；
- 同一受试者在多款上的试穿提供 `matched_fitting` 边，只能证明等级可配对比较，不提供可靠毫米换算；
- 掌宽当前只有 C-sole/C1 V5，和手围、指长不连通，因此 U017 的 C1 建议是 `scale_disconnected`，不能推断。

同一受试者试多款始终按 person 聚类。`FitAdvisor.evidence_summary()` 的可信度以独立人数为分母，并明确输出依赖警告，不把多款记录重复当成独立人群。

### 3. 建议按款、标注版本、客群给出

`FitAdvisor.advise()` 返回：

- 该 `(sku, version)` 的官方标注区间；
- 客群手部尺寸分布；
- 该客群与全部客群的独立人数、结果分布、Wilson 可信度下界；
- 适合区间并集/包络；
- 每个客户测量来源转换后的区间及关系；
- 同一人对该款该版的直接试穿；
- 确定性留出受试者（编号模 5 余 4，整人留出）的共同实测校验；
- 状态、柜台话术和是否允许承诺。

状态包括：

- `fit_supported` / `table_supported`；
- `intersection_only`：仅区间相交，只能呈现交集；
- `one_sided_signal`：只有偏紧/偏松等单边信号；
- `unknown_only`；
- `conflicting_measurements` / `conflicting_evidence`；
- `scale_disconnected` / `insufficient_evidence`。

除强证据外，`store_guarantee_allowed` 均为 `false`。

### 4. 三类原因和费用分担不混用

`classify_cause()` 输出稳定的中文类别：

| 类别 | 商业规则 issue | 运费 | 差额 |
| --- | --- | --- | --- |
| 原说明有误 | `标注争议`，须证据达标 | 商家 | 品牌 |
| 试穿数据不足 | `适配不明` | 待复核 | 待复核 |
| 客户单纯改变偏好 | `偏好变更` | 买方 | 买方 |

仅有“标注争议”申请、区间部分相交或留出数据噪声，不直接认定原标注错误。`label_error_proof()` 要求官方 `replace` 更正、同一测量下适配判断翻转，并有直接试穿或系统性留出偏差佐证；调用方持有可核验证明时可传 `explicit_label_error_proof=True`。

### 5. 凭证限额、已用权益和可追加金额

O1/O2 的 `partner` 互指，`BenefitService` 将其识别为同一凭证 `凭证:O1+O2`。两行的 `limits=35` 不重复计算。

当前资料的结果是：

- 凭证共用限额：35 元；
- 已到账可验证支付：T1 商家运费 8 元 + T2 品牌差额 10 元 = 18 元；
- 现在尚可追加：`35 - 18 = 17` 元；
- T3 商家运费 12 元是 `待认可`，只作为或有占用；
- 若 T3 之后被认可，剩余：`17 - 12 = 5` 元。

`exchange.cost` 是换货总费用，只用于和回执核对，不能与回执金额再次相加。已签收的 B1 V3、已使用的补偿和已有换货成本会继续保留；新款更合适不会把这些权益重置为未使用。

### 6. 左右配对只换一件

`WorkflowService.pair_check(order_id, candidate_sku, candidate_version)`：

- 找到 partner 当前实际款式（优先已签收替换款，否则原发货规格）；
- 计算两只标注范围的交集；
- 检查正宽度/零宽度边界点；
- 附带原店认可/替换确认承诺。

例如 O1 申请 B1 V4，而 O2 已签收 B1 V3：两版指长范围交集为 `[67, 72] mm`，单据上允许部分换货；但这只证明配合范围非空，不证明 U014 适合，仍需适配证据。若候选是 C1 V5 这类不连通量表，则不能计算配合范围。

### 7. 跨店确认门槛

`exchange_routing(exchange_id, as_of)` 固定检查三道门：

1. **寄入店接收**：有效 `柜台接收/签收` 且 `units > 0`；`拒绝收件`、`units=0` 不算；
2. **寄出店确认替换品/版次**：`原店认可`、`替换品确认`、`版次回执` 或完整“接收→签收”链；空版本必须有后续版次回执；
3. **财务确认差额/费用**：只认可已到账回执，待认可不算完成。

E2 已有 H2 柜台接收、H3 签收和 T1/T2 到账，流程关闭。E1 中 H5 拒绝 V4/零数量，T3 待认可，因此南柜接收门和财务门未过；北柜/H1 的原承诺仍可经 `H4→H1`、`H5→H4` 引用链追踪，旧取货权益不被被拒绝的 V4 覆盖。

`voucher_fulfillment()` 表达“先补一只、另一只等退回有效接收后才有发新件资格”的顺序。

### 8. 版次不明、回执和新表影响

- `version_candidates()` 保留改款后两周内旧网页组套/库存仍可发出的双版本可能；
- 实际版本优先取 `版次回执`，空 `version` 不会被替换成最新版；
- `resolve_version_receipt()` 在回执到齐后比较哪些旧状态/边界关系被推翻，哪些粗粒度结论仍可维持；
- `chart_impact()` 分别检查待发货、在途/已受理、已签收订单：
  - 待发货 O4 可 hold 并按新版复核；
  - 在途 O3/E3 需版次回执和跨店确认；
  - 已签收 O1/O2/E2 仍按实际发货 V1/V3 解释历史，更正只有在证据成立时产生追加责任，不溯及既往抹掉已用权益。

新表到站后可用同一套规则处理：

```python
next_service = service.with_scale_rows([
    {"version": "V6", "class": "雪手套", "sku": "S9", "method": "手围",
     "mark": "大", "lower_mm": 198, "upper_mm": 216,
     "publish_week": "W14", "replace": None}
])
```

## 资料中的关键柜台结论

- **U014 / O1+O2**：W10 证据下，B1 V3 只有“指长偏高 + 本人偏紧”的单边信号，不能保证；B2 V3 有 W02 指长 79 和本人“适合”记录，状态为 `fit_supported`。W13 的 R2/R4 复测与 W02 测点冲突后，不再作无条件保证。
- **E1**：当前候选 B1 V3 不适配且跨店/财务未齐，不能给出确定取件承诺；需列出适配替代、退回接收、T3 认可和凭证余额等待协商项。
- **E2**：B1 V3 已签收且 18 元回执到账，流程关闭，已用权益继续有效。
- **U016 / O3 / E3**：偏好变更，费用由买方承担；H6 空版本已由 W13 的 H7 回执确认为 S1 V1。S1 V1 只有区间相交/偏紧信号，不能因“偏好变更”而改称为适合。
- **U017 / O4 / E4**：C1 V5 掌宽与现有手围/指长图不连通，不能推断；其本人 S1 V1 有直接“适合”记录，是可复核的候选替代。O4 待发货，仍可在出库前改规格。

## 测试覆盖

`tests/test_sizing_exchange.py` 覆盖测量时效、量表不连通、人员聚类、双版边界、凭证余额、费用分担、左右配对、跨店门槛、版次回执、柜台简式单据限制和内存新表接入。原有 `tests/test_data_contract.py` 继续保留。
