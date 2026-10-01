# IIS 可计算事件表达规范 V3

情报结构、事件核、事件限定、事件关系与提取标准

## 1. 目标与边界

本规范用于从一条情报中提取一项或少量具有独立分析价值的事件，并形成稳定、可检查、可计算的结构。

本规范只描述一条情报内部的事件表达，不负责：

- 判断不同情报是否描述同一个现实事件；
- 评价来源可靠性或情报可信度；
- 保存文章中的全部动作、背景和修辞；
- 结构化所有具体方式、技术手段和低频领域术语。

一条事件记录分为三个语义部分：

1. **事件核**：事件是什么；
2. **事件限定**：事件处于什么状态，或被如何计划、批准、命令、预测、否定；
3. **事件关系**：当前事件与本情报中其他事件有什么明确关系。

事件核的存在只表示该事件被本情报提及并被选为分析事件。事件是否已经发生，必须结合事件限定判断。

## 2. 总体结构

```text
情报
├── primary_event_id
└── events
    ├── 事件1
    │   ├── core
    │   │   ├── frame
    │   │   │   ├── dynamics
    │   │   │   ├── topology
    │   │   │   └── agency
    │   │   ├── predicate
    │   │   ├── roles
    │   │   ├── time
    │   │   ├── context
    │   │   └── attributes
    │   ├── qualifiers
    │   └── relations
    └── 事件2
```

最小结构：

```yaml
schema_version: "3.0"
primary_event_id: E1
events:
  - id: E1
    core:
      frame:
        dynamics: change
        topology: transfer
        agency: agentive
      predicate:
        id: acquire
        surface: 收购
      roles:
        acquirer: [ENT1]
        asset: [ENT2]
```

## 3. 顶层字段

| 字段               | 类型     | 基数 | 限定                          |
| ------------------ | -------- | ---- | ----------------------------- |
| `schema_version`   | string   | 1    | 固定为 `3.0`                  |
| `primary_event_id` | event_id | 1    | 必须引用 `events` 中的事件    |
| `events`           | event[]  | 1..5 | 按分析重要性排序，ID 不得重复 |

默认提取一项事件；确有独立事件时可以增加，通常不超过三项。超过五项时，只保留最重要且相互独立的五项。

事件 ID 只在当前情报内有效，格式为 `E1`、`E2`、`E3`。它不表示跨情报的现实事件身份。

roles、context 和 qualifier 中的实体引用指向情报已有的实体表。V3 不重复定义人物、组织、地点等实体结构，只规定事件如何引用实体。

## 4. 事件结构

| 字段         | 类型        | 基数 | 限定                   |
| ------------ | ----------- | ---- | ---------------------- |
| `id`         | event_id    | 1    | 当前情报内唯一         |
| `core`       | event_core  | 1    | 事件核                 |
| `qualifiers` | qualifier[] | 0..8 | 不得出现语义重复项     |
| `relations`  | relation[]  | 0..8 | 关系起点默认为当前事件 |

`qualifiers` 或 `relations` 为空时省略字段，不输出空数组。

## 5. 事件核 core

事件核定义被持续跟踪的事件身份。它由 FRAME、predicate、roles、time、context 和 attributes 组成。

| 字段         | 类型          | 基数 | 开放性           |
| ------------ | ------------- | ---- | ---------------- |
| `frame`      | frame         | 1    | 封闭             |
| `predicate`  | predicate     | 1    | 核心 ID 封闭     |
| `roles`      | role map      | 1    | 按核心谓词封闭   |
| `time`       | time map      | 0..1 | 字段封闭         |
| `context`    | context       | 0..1 | 封闭             |
| `attributes` | attribute map | 0..1 | 字段与类型均封闭 |

`roles` 至少包含一个核心角色。其他可选块没有内容时必须省略。

## 6. FRAME

FRAME 是事件核的结构轮廓，由三个必填维度组成。三个维度均为封闭枚举，不允许新增临时值。

### 6.1 dynamics：动态形态

| 值        | 定义                                 | 示例                   |
| --------- | ------------------------------------ | ---------------------- |
| `state`   | 某种状态、属性或关系在一段时间内成立 | 拥有、位于、控制、依赖 |
| `process` | 活动持续展开，不以状态跳变为核心     | 生产、巡逻、谈判、研发 |
| `change`  | 对象在可识别维度上发生前后变化       | 上涨、停产、任命、收购 |

判定顺序：

1. predicate 本义要求前态到后态发生改变，选择 `change`；
2. 否则，核心是持续活动，选择 `process`；
3. 否则选择 `state`。

按 predicate 本义判定，不按句中附带的数量或结果判定。“生产1000台设备”的 predicate 是“生产”，选择 `process`；“产量增加到1000台”的 predicate 是“增加”，选择 `change`。短时完成不自动等于 `change`，只有事件身份本身包含状态转换时才选择 `change`。

### 6.2 topology：参与结构

| 值           | 定义                                   | 最小回退角色                                     |
| ------------ | -------------------------------------- | ------------------------------------------------ |
| `intrinsic`  | 主要描述一个主体自身的状态、活动或变化 | `subject`                                        |
| `relational` | 描述两个或多个参与者之间的关系或互动   | `subject`、`counterpart`                         |
| `targeted`   | 行动者对目标实施作用                   | `actor`、`target`                                |
| `transfer`   | 实体、权利、资源或位置在端点间转移     | `theme`，以及 `source` 或 `destination` 至少一项 |

判定顺序：

1. 核心是对象在来源端和目标端之间转移，选择 `transfer`；
2. 核心是行动者作用于目标，选择 `targeted`；
3. 核心是两个或多个参与者之间的关系或对等互动，选择 `relational`；
4. 否则选择 `intrinsic`。

`transfer` 要求同一 theme 的位置、持有、控制或归属发生端点变化；仅产生新对象不属于 transfer。“签署合同”是 `targeted`，“转让合同权利”是 `transfer`。关系的建立或解除属于 `change + relational`，持续成立的关系属于 `state + relational`。

### 6.3 agency：施动方式

| 值             | 定义                             |
| -------------- | -------------------------------- |
| `agentive`     | 存在主动发起或控制事件过程的主体 |
| `non_agentive` | 事件表现为自发状态或非意志性变化 |
| `unknown`      | 原文不足以判断是否存在施动者     |

`unknown` 只用于证据不足，不得作为难以分类时的默认值。

agency 根据事件过程是否由有意志主体控制判定，不根据自愿程度判定。被命令或被迫撤退仍是 `agentive`；价格上涨、设备自然损坏为 `non_agentive`；原文未说明变化是主动调整还是自然发生时使用 `unknown`。

### 6.4 FRAME 约束

- FRAME 不表达具体事件身份；
- FRAME 不表达计划、批准、否定、可能性或生命周期；
- 已归类谓词的 FRAME 由本规范的核心谓词表确定，提取时不得改写；
- 未归类事件必须根据上述规则填写三个维度；
- FRAME 维度不能决定具体角色名称，角色由 predicate 优先定义。

## 7. predicate

predicate 表示可被独立跟踪的核心事件家族。核心谓词表是随 V3 完整发布的封闭词表，必须全部提供给提取模型，不依赖运行时预检索。

```yaml
predicate:
  id: acquire
  surface: 拿下
```

| 字段      | 类型      | 基数 | 限定                                         |
| --------- | --------- | ---- | -------------------------------------------- |
| `id`      | enum/null | 1    | 使用第 7.3 节核心 ID；无法归类时为 null      |
| `surface` | string    | 1    | 原文中的核心谓语，不得改写                   |
| `gloss`   | string    | 0..1 | 仅 `id: null` 时必填，一句释义，不得增添事实 |

### 7.1 谓词准入条件

一个语义类别只有同时满足以下条件，才有资格进入核心谓词表：

1. **事件性**：表示可定位的状态、过程或变化；
2. **独立性**：不能由已有谓词加限定、关系或 `surface` 细节表达；
3. **角色稳定性**：具有相对稳定的核心参与者结构；
4. **分析价值**：区分该谓词会改变检索、统计或后续跟踪。

新增核心谓词还必须至少满足一项：

- 形成用户需要单独查询的事件类别；
- 具有不同的必填角色结构；
- 判断同一事件时必须与现有谓词区分；
- 生命周期或事件尺度与现有谓词不同。

以下内容默认不作为事件核谓词：

- 计划、考虑、批准、命令、允许、禁止；
- 可能、预计、认为、怀疑、否认；
- 开始、持续、暂停、完成、失败；
- 导致、促进、阻止、先于、组成；
- 宣布、报道、透露、指出等传播外壳。

它们分别进入 qualifier、relation，或被省略。只有当这些行为本身是文章持续跟踪的现实对象时，才允许成为事件谓词。

### 7.2 粒度规则

核心谓词描述分析所需的最小事件家族，不描述所有具体方式：

- “空袭”“炮击”“导弹袭击”通常归入 `attack`，具体词保存在 `surface`；
- “撤退”“增援”“疏散”通常归入 `move`；
- “购买”“出售”归入同一个 `trade` 事件，通过 buyer 和 seller 区分视角；
- “并购”“买下控制权”归入 `acquire`；
- “战争”归入 `armed_conflict`，不能与战争中的单次 `attack` 合并。

高层次不等于最大抽象。若两个表达具有不同事件尺度、核心角色或生命周期，必须保留不同谓词。方法细节只有在成为稳定查询需求后，才通过规范升级提升为新的核心谓词。

`surface` 永久保留具体说法，可用于全文和向量检索，但不参与核心谓词精确统计。无法归入任何核心谓词时使用 `id: null`，不得就近强选或现场创建 ID。

### 7.3 核心谓词表

FRAME 缩写依次为 `dynamics/topology/agency`。角色栏中 `+` 表示必填角色，`?` 表示可选角色；身份角色用于发现不同情报是否描述同一次现实事件。

#### 状态与关系

| ID             | 标准名称与边界                       | FRAME                         | 角色                     | 身份角色               |
| -------------- | ------------------------------------ | ----------------------------- | ------------------------ | ---------------------- |
| `exist`        | 存在、处于某种整体状态；仅作状态兜底 | state/intrinsic/non_agentive  | subject+                 | subject                |
| `possess`      | 持有实体、资产或权利                 | state/relational/agentive     | holder+、asset+          | holder、asset          |
| `control`      | 对实体、组织、区域或资源具有支配权   | state/relational/agentive     | controller+、controlled+ | controller、controlled |
| `member_of`    | 属于、隶属或加入某组织体系           | state/relational/non_agentive | member+、organization+   | member、organization   |
| `located_at`   | 实体位于或部署于某位置               | state/relational/non_agentive | subject+、location+      | subject、location      |
| `depend_on`    | 主体持续依赖对象或条件               | state/relational/non_agentive | dependent+、dependency+  | dependent、dependency  |
| `connected_to` | 参与者之间存在通信、运输或结构连接   | state/relational/non_agentive | participant+             | participant            |
| `capable`      | 主体具备某项能力或资格               | state/intrinsic/non_agentive  | subject+、capability+    | subject、capability    |
| `valid`        | 政策、协议、许可或资格当前有效       | state/intrinsic/non_agentive  | subject+                 | subject                |

#### 状态变化

| ID            | 标准名称与边界                         | FRAME                      | 角色                           | 身份角色           |
| ------------- | -------------------------------------- | -------------------------- | ------------------------------ | ------------------ |
| `increase`    | 数量、规模、价格或程度增加             | change/intrinsic/unknown   | subject+、agent?               | subject            |
| `decrease`    | 数量、规模、价格或程度减少             | change/intrinsic/unknown   | subject+、agent?               | subject            |
| `improve`     | 质量、能力或表现向有利方向变化         | change/intrinsic/unknown   | subject+、agent?               | subject            |
| `deteriorate` | 质量、能力或表现向不利方向变化         | change/intrinsic/unknown   | subject+、agent?               | subject            |
| `create`      | 实体、组织、制度或关系从无到有         | change/intrinsic/agentive  | actor+、object+                | object             |
| `terminate`   | 实体、关系、资格或活动永久结束         | change/intrinsic/unknown   | subject+、agent?               | subject            |
| `gain`        | 主体取得能力、资格、领土或非交易性控制 | change/relational/unknown  | subject+、object+、agent?      | subject、object    |
| `lose`        | 主体失去能力、资格、领土或控制         | change/relational/unknown  | subject+、object+、agent?      | subject、object    |
| `damage`      | 人员、设施或对象受到损伤、损毁         | change/intrinsic/unknown   | affected+、actor?              | affected           |
| `restore`     | 对象恢复功能、状态或关系               | change/intrinsic/unknown   | affected+、actor?              | affected           |
| `appoint`     | 人员取得职务或制度角色                 | change/relational/agentive | authority+、person+、position+ | person、position   |
| `remove`      | 人员失去职务或被解除角色               | change/relational/agentive | authority?、person+、position+ | person、position   |
| `discover`    | 原本未知的对象或事实被识别             | change/targeted/agentive   | discoverer+、object+           | object             |
| `default`     | 债务人进入未按约履行偿付义务的状态     | change/relational/unknown  | debtor+、obligation+           | debtor、obligation |

#### 转移与交换

| ID            | 标准名称与边界                         | FRAME                     | 角色                                  | 身份角色                   |
| ------------- | -------------------------------------- | ------------------------- | ------------------------------------- | -------------------------- |
| `move`        | 人员、装备或实体改变空间位置           | change/transfer/unknown   | theme+、source?、destination?、agent? | theme、source、destination |
| `transfer`    | 权利、责任、订单或控制关系转移         | change/transfer/agentive  | theme+、source?、destination?、agent? | theme、source、destination |
| `trade`       | 商品或服务通过买卖在双方间交换         | change/transfer/agentive  | goods+、buyer?、seller?               | goods、buyer、seller       |
| `acquire`     | 通过交易取得企业、资产、权益或控制权   | change/transfer/agentive  | acquirer+、asset+、seller?            | acquirer、asset            |
| `invest`      | 为取得预期权益或回报而投入资金或资源   | process/transfer/agentive | investor+、recipient+、resource?      | investor、recipient        |
| `fund`        | 向目标主体或活动提供资金               | process/transfer/agentive | provider+、recipient+、resource?      | provider、recipient        |
| `pay`         | 付款方向收款方支付资金                 | change/transfer/agentive  | payer+、payee+                        | payer、payee               |
| `lend`        | 贷款方向借款人提供需偿还资金           | change/transfer/agentive  | lender+、borrower+                    | lender、borrower           |
| `repay`       | 债务人向债权人偿还债务                 | change/transfer/agentive  | debtor+、creditor+                    | debtor、creditor           |
| `supply`      | 向接收方持续或批量提供产品、物资或能力 | process/transfer/agentive | supplier+、recipient+、goods+         | supplier、recipient、goods |
| `communicate` | 信息被发布、披露、分享或传递           | process/transfer/agentive | sender+、information+、recipient?     | sender、information        |

#### 建设、运行与分析活动

| ID            | 标准名称与边界                     | FRAME                       | 角色                            | 身份角色                |
| ------------- | ---------------------------------- | --------------------------- | ------------------------------- | ----------------------- |
| `operate`     | 设施、系统或组织开展其常规活动     | process/intrinsic/unknown   | subject+、operator?             | subject                 |
| `produce`     | 制造或形成产品、材料或产出         | process/targeted/agentive   | producer+、product+             | producer、product       |
| `construct`   | 建造或扩建实体设施                 | process/targeted/agentive   | builder+、object+               | object                  |
| `develop`     | 研究、设计或开发技术、产品与能力   | process/targeted/agentive   | developer+、object+             | developer、object       |
| `test`        | 通过规定方法检验对象               | process/targeted/agentive   | tester+、object+                | tester、object          |
| `deploy`      | 将设备、软件或能力配置到目标环境   | change/targeted/agentive    | deployer+、object+、location?   | object、location        |
| `maintain`    | 维护、维修或保障对象正常状态       | process/targeted/agentive   | actor+、object+                 | actor、object           |
| `inspect`     | 检查、审计或验证对象状态与合规性   | process/targeted/agentive   | inspector+、object+             | inspector、object       |
| `investigate` | 围绕事件、人员或问题收集核验信息   | process/targeted/agentive   | investigator+、object+          | investigator、object    |
| `negotiate`   | 多方围绕议题进行协商               | process/relational/agentive | party+、topic?                  | party、topic            |
| `agree`       | 多方形成协议、合同或共同决定       | change/relational/agentive  | party+、agreement+              | party、agreement        |
| `regulate`    | 主管主体制定或调整规则、政策与标准 | change/targeted/agentive    | authority+、target+、rule?      | authority、target、rule |
| `restrict`    | 主体对目标施加制裁、封锁或行为限制 | process/targeted/agentive   | authority+、target+             | authority、target       |
| `elect`       | 通过选举使人员取得职位             | change/relational/agentive  | electorate+、person+、position+ | person、position        |
| `adjudicate`  | 有权主体对案件或争议作出裁决       | change/targeted/agentive    | authority+、case+、party?       | case                    |
| `detain`      | 有权或实际控制方拘捕、拘留人员     | change/targeted/agentive    | authority+、person+             | person                  |
| `protest`     | 群体公开表达反对、诉求或抵制       | process/targeted/agentive   | participant+、target?           | participant、target     |
| `cooperate`   | 多方持续开展合作或联合行动         | process/relational/agentive | party+、topic?                  | party、topic            |
| `release`     | 产品、软件、文件或成果被正式发布   | change/transfer/agentive    | releaser+、object+、recipient?  | releaser、object        |

#### 冲突、安全与灾害

| ID               | 标准名称与边界                                     | FRAME                          | 角色                         | 身份角色                  |
| ---------------- | -------------------------------------------------- | ------------------------------ | ---------------------------- | ------------------------- |
| `armed_conflict` | 多方在一定时期和战区内持续武装对抗；不表示单次攻击 | process/relational/agentive    | belligerent+、theater+       | belligerent、theater      |
| `attack`         | 行动方对目标实施一次或一组紧密连续的敌对行动       | process/targeted/agentive      | actor+、target+、instrument? | actor、target             |
| `defend`         | 行动方保护目标并抵抗攻击                           | process/targeted/agentive      | actor+、target+、instrument? | actor、target             |
| `intercept`      | 对移动对象实施阻断、接触或摧毁                     | process/targeted/agentive      | actor+、target+、instrument? | actor、target             |
| `observe`        | 搜索、监视、巡逻或侦察目标                         | process/targeted/agentive      | actor+、target+、instrument? | actor、target             |
| `disrupt`        | 主动干扰系统、行动、供应或通信正常运行             | process/targeted/agentive      | actor+、target+、instrument? | actor、target             |
| `accident`       | 非故意事故造成异常、损失或中断                     | change/intrinsic/non_agentive  | affected+                    | affected                  |
| `natural_hazard` | 地震、风暴、洪水等自然灾害发生                     | process/intrinsic/non_agentive | phenomenon+、affected_area?  | phenomenon、affected_area |
| `outbreak`       | 疾病或有害现象在群体或地域中暴发传播               | process/intrinsic/non_agentive | phenomenon+、affected_area+  | phenomenon、affected_area |

### 7.4 归类规则

- 先按语义定义归类，不按字面动词匹配；
- 同一句可以因事件尺度不同拆出不同 predicate，例如战争中的攻击；
- 具体方式不改变核心角色和事件尺度时，仅保留在 `surface`；
- 一个候选同时符合多个谓词时，选择角色结构和事件尺度更精确者；
- 仍无法唯一归类时使用 `id: null`，并进入规范评估清单；
- 核心谓词只能通过 V3.x 规范修订增加、删除或改变定义，不允许运行时扩展。

### 7.5 Prompt 与预检索

核心谓词表规模受本规范约束，必须与 FRAME、qualifier 和 relation 一起完整提供给 AI。单次 AI 调用即可完成事件拆分、谓词归类和字段提取。

系统可以根据原文词语或向量相似度预先标亮可能的谓词，但预检索只能改变候选展示顺序，不得删减 AI 可见的核心谓词表，也不得强制选择检索结果。这样预检索不准确只影响提示效率，不影响表达能力；没有合适谓词时仍使用 `id: null`。

## 8. roles

roles 保存事件的参与实体。角色值必须是实体引用数组，即使只有一个实体也使用数组。

```yaml
roles:
  acquirer: [ENT1]
  asset: [ENT2]
```

### 8.1 角色名称

- 已归类事件只能使用第 7.3 节对应谓词声明的角色名；
- `+` 角色必须出现，`?` 角色按原文填写；
- `id: null` 的事件只能使用所属 topology 的回退角色；
- 不允许模型临时创建角色名；
- 同一事实不得同时使用专用角色和意义相同的回退角色。

回退角色表：

| topology     | 允许角色                                  | 最小要求                                       |
| ------------ | ----------------------------------------- | ---------------------------------------------- |
| `intrinsic`  | `subject`                                 | `subject` 必填                                 |
| `relational` | `subject`、`counterpart`                  | 两项必填                                       |
| `targeted`   | `actor`、`target`、`instrument`           | `actor`、`target` 必填                         |
| `transfer`   | `theme`、`source`、`destination`、`agent` | `theme` 必填；`source`、`destination` 至少一项 |

特殊基数约束：

- `connected_to.participant`、`negotiate.party`、`agree.party`、`cooperate.party` 和 `armed_conflict.belligerent` 至少包含两个实体；
- `move` 的 `source`、`destination` 至少出现一项；
- `trade` 的 `buyer`、`seller` 至少出现一项；
- 实体确实存在但名称未知时使用匿名实体引用，以满足必要角色，不得凭空补全身份。

### 8.2 角色值

- 每个值必须引用本情报实体表中的实体；
- 同一实体可以承担多个语义不同的角色；
- 数量、金额、时间、状态和完整命题不得作为角色值；
- 并列参与者放入同一角色数组，不复制事件；
- 无法识别的必填实体使用匿名实体引用，不使用字符串 `unknown`。

## 9. time

time 只描述事件核的时间，不描述文章发布时间、报道时间或限定行为的时间。

允许字段为：

| 字段                  | 定义                                         |
| --------------------- | -------------------------------------------- |
| `event_time`          | 无需区分起止节点的事件发生时间或状态成立时间 |
| `start_time`          | 事件开始或状态开始成立的时间                 |
| `end_time`            | 事件完成、终止或状态结束的时间               |
| `effective_time`      | 政策、协议、许可或权利义务开始产生效力的时间 |
| `deadline`            | 必须完成事件或满足条件的最迟时间             |
| `expected_start_time` | 尚未发生的预计开始时间                       |
| `expected_end_time`   | 尚未完成的预计结束时间                       |

`event_time` 用于不区分起止节点的单点事件或整体时间范围；`start_time`、`end_time` 用于原文明示起止节点的事件。一个事件中 `event_time` 与 `start_time`/`end_time` 禁止共存。只有年份等低精度时间仍使用 `event_time`，不得人为扩展成全年起止区间。

### 9.1 标准时间表达

```yaml
expected_end_time:
  normalized: "2026-12"
  precision: month
  approximate: true
  surface: 2026年底
```

| 字段          | 类型                 | 基数 | 限定                                     |
| ------------- | -------------------- | ---- | ---------------------------------------- |
| `normalized`  | ISO 8601 string/null | 1    | 可可靠解析时填写，否则为 null            |
| `precision`   | enum                 | 1    | `year`、`month`、`day`、`hour`、`minute` |
| `approximate` | boolean              | 1    | 原文是否明确为约数                       |
| `surface`     | string               | 1    | 原始时间表达                             |

时间规则：

- 只提取原文或可靠元数据明确支持的时间；
- 相对时间可依据情报发布时间解析，但必须保留 `surface`；
- 不能可靠解析时，`normalized` 为 null，不得猜测；
- 时间范围使用两个语义字段或 ISO 8601 interval，不创建临时时间字段；
- 限定行为自身的时间放入对应 qualifier，不放入事件核 time。

## 10. context

V3 的 context 只允许一个字段：

```yaml
context:
  event_location: [ENT3]
```

`event_location` 表示事件发生环境中的地点。若地点决定事件对象、起点、终点或目标，必须进入 roles，不得同时写入 context。

具体规则：

- transfer 的出发地和目的地分别进入 `source`、`destination`；
- 地点本身是攻击、控制、占领等事件的对象时进入对应 predicate role；
- 仅说明事件在哪里发生时进入 `context.event_location`；
- “美国工厂”中的美国若只用于识别该实体，属于实体信息，不重复进入事件；
- 同一地点不得同时出现在 roles 和 context 中表达同一语义。

除 `event_location` 外不允许创建其他 context 字段。无法归入该字段的信息保留在原文，不进入 V3 核心结构。

## 11. attributes

attributes 保存金额、数量、比例、等级等既不属于实体角色、时间或地点的信息。

```yaml
attributes:
  amount:
    type: money
    value: 20
    unit: 亿元
    currency: CNY
    surface: 20亿元
```

### 11.1 字段限定

- 所有事件只能使用下述通用 attribute，不允许谓词自行扩展字段；
- 不提供 `custom_fields`、`other` 或自由键入口；
- 能表示为实体角色、时间或关系的信息不得重复写入 attributes。

通用 attribute 名称为封闭集合：

| 名称           | 数据类型   | 用途                       |
| -------------- | ---------- | -------------------------- |
| `amount`       | `money`    | 交易、投资、融资或支付金额 |
| `quantity`     | `number`   | 对象数量，例如100台设备    |
| `ratio`        | `ratio`    | 比例、份额或变化率         |
| `value_before` | `number`   | 变化前数值                 |
| `value_after`  | `number`   | 变化后数值                 |
| `delta`        | `number`   | 明确给出的增减量           |
| `duration`     | `duration` | 事件持续时长               |
| `level`        | `number`   | 有明确数值尺度的等级或强度 |

例如“A向B转移100台设备”中，设备实体进入 `theme`，`100台`进入 `attributes.quantity`。

### 11.2 允许的数据类型

| type       | 必需字段            | 可选字段          |
| ---------- | ------------------- | ----------------- |
| `number`   | `value`             | `unit`、`surface` |
| `money`    | `value`、`currency` | `unit`、`surface` |
| `ratio`    | `value`             | `unit`、`surface` |
| `duration` | `value`、`unit`     | `surface`         |

`currency` 必须使用 ISO 4217 代码。`unit` 应使用系统单位表中的标准单位；无法可靠标准化时不生成该 attribute，只保留原文。`ratio.value` 使用十进制数，例如 20% 保存为 `0.2`。attributes 不接受自由文本或开放枚举；若核心意义长期依赖此类值，应判定谓词表或总体结构需要修订。

## 12. qualifiers

qualifier 描述事件核的状态或作用于事件的限定。限定类型和值均为封闭枚举。

```yaml
qualifiers:
  - id: Q1
    type: intention
    value: planned
    by: [ENT1]
    scope: event
```

| 字段      | 类型            | 基数 | 限定                                      |
| --------- | --------------- | ---- | ----------------------------------------- |
| `id`      | qualifier_id    | 1    | 当前事件内唯一，格式 `Q1`、`Q2`           |
| `type`    | enum            | 1    | 使用下表限定类型                          |
| `value`   | enum            | 1    | 必须属于对应类型                          |
| `by`      | entity_ref[]    | 0..1 | 态度持有者、意向主体、批准方或命令方      |
| `scope`   | string          | 1    | `event` 或当前事件中先出现的 qualifier ID |
| `time`    | time expression | 0..1 | 限定本身成立的时间                        |
| `surface` | string          | 0..1 | 原文限定表达                              |

### 12.1 限定类型和值

| type            | 允许 value                                                                           | `by` 规则              |
| --------------- | ------------------------------------------------------------------------------------ | ---------------------- |
| `phase`         | `not_started`、`ongoing`、`suspended`、`completed`、`cancelled`、`blocked`、`failed` | 禁止                   |
| `intention`     | `considering`、`planned`、`committed`                                                | 原文给出意向主体时必填 |
| `authorization` | `required`、`pending`、`approved`、`rejected`、`revoked`                             | 原文给出权力主体时必填 |
| `directive`     | `requested`、`ordered`、`required`、`prohibited`                                     | 原文给出发出者时必填   |
| `epistemic`     | `asserted`、`estimated`、`doubted`、`denied`                                         | 必填                   |
| `modality`      | `possible`、`probable`、`conditional`                                                | 可选                   |
| `polarity`      | `negated`                                                                            | 禁止                   |

### 12.2 限定规则

- 每个类型默认最多一项；存在不同 `by` 且原文明确区分时可以重复；
- `scope: event` 表示限定整个事件核；
- qualifier 可以限定另一个 qualifier，但嵌套深度最多一层；
- 不得形成循环引用；
- 未被其他 qualifier 引用的 qualifier 是本情报直接提出的限定；被引用的 qualifier 只是外层限定的内容，不独立断言为真；
- `epistemic` 指向另一 qualifier 时只表达 `by` 对该内容的立场，不推出目标 qualifier 为真或为假；`denied` 表示发生了否认，不等于系统确认被否认内容为假；
- 未出现限定不等于 `unknown`，而是原文没有提供该维度；
- 没有现实性限定的直接陈述，按情报直接呈现该事件处理，但不得据此推断具体 phase；
- 若计划、批准、命令、预测或否认发生在原文明示的时间，必须把该时间写入相应 qualifier；未填写 qualifier time 只表示该时间未提供，不得假定它与事件核时间相同；
- 来源可靠性、分析置信度和事件重要性不属于 qualifier。

示例：“国防部否认部队已经撤退”：

```yaml
qualifiers:
  - id: Q1
    type: phase
    value: completed
    scope: event
  - id: Q2
    type: epistemic
    value: denied
    by: [ENT1]
    scope: Q1
```

该结构断言国防部否认“撤退已经完成”，不表示撤退已经完成。

## 13. relations

relation 表示当前事件与本情报中另一事件的明确关系。当前事件是关系起点，`target_event_id` 是终点。

```yaml
relations:
  - predicate: causes
    target_event_id: E2
    surface: 导致
```

| 字段              | 类型     | 基数 | 限定                       |
| ----------------- | -------- | ---- | -------------------------- |
| `predicate`       | enum     | 1    | 使用下表关系谓词           |
| `target_event_id` | event_id | 1    | 必须引用本情报中的另一事件 |
| `surface`         | string   | 0..1 | 原文关系表达               |

### 13.1 关系谓词

| 分组 | predicate       | 定义                                     |
| ---- | --------------- | ---------------------------------------- |
| 因果 | `causes`        | 当前事件使目标事件发生或形成             |
| 因果 | `promotes`      | 当前事件提高目标事件发生概率、速度或程度 |
| 因果 | `prevents`      | 当前事件使目标事件未发生或无法继续       |
| 因果 | `aggravates`    | 当前事件提高目标负面状态的程度           |
| 因果 | `mitigates`     | 当前事件降低目标负面状态的程度           |
| 时序 | `precedes`      | 当前事件先于目标事件                     |
| 时序 | `follows`       | 当前事件后于目标事件                     |
| 时序 | `overlaps`      | 两个事件在相关时间范围内重叠             |
| 条件 | `condition_for` | 当前事件成立是目标事件成立的明确条件     |
| 结构 | `part_of`       | 当前事件是目标事件的组成部分             |

### 13.2 关系规则

- 只提取原文明示或由句法直接蕴含的关系；
- 不因两个事件共同出现、时间接近或主题相似而创建关系；
- `precedes`、`follows` 仅在时序本身具有分析价值时提取；
- 同一起点、终点和 predicate 不得重复；
- 关系谓词表不允许运行时扩展；无法表达的关系保留在原文，并进入规范评估清单；
- 分析人员后续推断的关系不属于本情报提取结果。

## 14. 事件识别标准

一个文本表达只有同时满足以下条件，才建立独立事件：

1. 具有可归纳为状态、过程或变化的事件核；
2. 具有自己的 predicate 和至少一个核心参与实体；
3. 可以在时间上成立、发生、持续或变化；
4. 属于文章相对于其自身背景和回顾内容所提供的新信息，或文章明确更新了它的状态。

标题、导语和正文必须合并理解。标题中的概括不得覆盖正文中的明确限制。

## 15. 事件拆分标准

### 15.1 必须拆分

两个表达分别满足事件识别标准，并满足以下任一条件时，拆为两个事件：

- 原文可以承诺其中一个成立而不承诺另一个成立，且两者具有不同的事件身份；
- 两者具有可分别更新的时间或生命周期；
- 两者具有不同的必填角色结构；
- 一个是另一个的原因、结果、条件或组成事件。

例：“部队攻击大桥并将其摧毁。”

```text
E1：部队攻击大桥
E2：大桥损毁
E1 causes E2
```

攻击可以发生而未造成损毁，损毁也可以被独立确认，因此必须拆分。

### 15.2 不得拆分

以下内容默认不建立独立事件：

- 事件的阶段、意向、授权、指令、可能性、否定和认知立场；
- 仅用于引出消息的宣布、报道、透露和指出；
- 事件的工具、方式、地点、数量和普通结果描述；
- 同一事件的同义复述；
- 不具有独立分析价值的背景事实。

例：“公司宣布计划收购公司B。”只建立“收购”事件；“计划”进入 qualifier，“宣布”省略。

计划、批准、命令等行为只有在其自身具有独立参与者、时间和后续状态，并且文章主要跟踪该行为本身时，才提升为独立事件；否则始终作为 qualifier。

### 15.3 合并重复描述

同一情报中的多个表达满足以下条件时，合并为一个事件：

- predicate 身份相同；
- 所有必填角色指向相同实体；
- 时间不冲突；
- 后一表达只是补充阶段、地点、数量或同义信息。

若两个动作共享参与者但可分别发生，不得因角色相同而合并。

## 16. 事件选择与排序

语义拆分先于重要性筛选。不得为了满足事件数量限制，把两个独立事件压缩成一个复合谓词。

事件按以下优先级保留：

1. 构成文章最新信息增量的事件；
2. 实际发生的行动或变化；
3. 已作出的决定、命令或明确计划所指向的事件；
4. 具有明确原因、结果或关键门槛的事件；
5. 预测、评价和一般状态。

`primary_event_id` 指向删除后最能改变文章核心意义的事件。关系中的原因或结果可以不是主事件，但必须本身满足事件识别标准。

## 17. 提取流程

1. 阅读标题、导语和正文，写出文章的信息增量；
2. 标记所有候选状态、过程和变化；
3. 使用事件识别与拆分标准确定独立事件；
4. 选择主事件和最多四个必要事件；
5. 根据事件尺度、语义定义和角色结构，从完整核心谓词表选择 predicate；
6. 按谓词表复制 FRAME，并提取其必填和可选角色；
7. 无法唯一归类时使用 `id: null`，按 FRAME 回退角色表达；
8. 提取封闭类型的 qualifiers；
9. 提取本情报事件之间的明确 relations；
10. 提取通用数值 attributes、时间和 context；
11. 校验角色完整性、引用有效性、时间一致性和语义重复；
12. 根据结构生成标准文本，与原文进行含义核对。

禁止先根据标题选择 predicate，再强行把正文信息填入该结构。

## 18. 完整示例

原文：

> 公司A计划以20亿元收购公司B控股权，交易仍待监管机构批准。公司A还计划扩建公司B的上海工厂。

```yaml
schema_version: "3.0"
primary_event_id: E1
events:
  - id: E1
    core:
      frame:
        dynamics: change
        topology: transfer
        agency: agentive
      predicate:
        id: acquire
        surface: 收购
      roles:
        acquirer: [ENT_COMPANY_A]
        asset: [ENT_COMPANY_B_CONTROL]
      attributes:
        amount:
          type: money
          value: 20
          unit: 亿元
          currency: CNY
          surface: 20亿元
    qualifiers:
      - id: Q1
        type: intention
        value: planned
        by: [ENT_COMPANY_A]
        scope: event
      - id: Q2
        type: authorization
        value: pending
        by: [ENT_REGULATOR]
        scope: event
  - id: E2
    core:
      frame:
        dynamics: process
        topology: targeted
        agency: agentive
      predicate:
        id: construct
        surface: 扩建
      roles:
        builder: [ENT_COMPANY_A]
        object: [ENT_FACTORY_B_SHANGHAI]
    qualifiers:
      - id: Q1
        type: intention
        value: planned
        by: [ENT_COMPANY_A]
        scope: event
```

这里建立两个事件，因为收购与扩建具有不同事件身份和可分别更新的生命周期。两个计划共同出现不构成事件关系。

### 18.1 正在进行战争的地域

```yaml
id: E1
core:
  frame:
    dynamics: process
    topology: relational
    agency: agentive
  predicate:
    id: armed_conflict
    surface: 战争仍在持续
  roles:
    belligerent: [ENT_COUNTRY_A, ENT_COUNTRY_B]
    theater: [ENT_REGION_X]
  time:
    start_time:
      normalized: "2025-04"
      precision: month
      approximate: false
      surface: 2025年4月
qualifiers:
  - id: Q1
    type: phase
    value: ongoing
    scope: event
```

查询当前战争地域时，筛选 `predicate.id = armed_conflict` 且最新状态为 `phase = ongoing`，再聚合 `roles.theater`。战争中的单次空袭使用 `attack`，不得以 `armed_conflict` 表达。

### 18.2 相同事件发现与归类

核心谓词表中的身份角色用于生成同事件候选。不同情报中的事件若 predicate 相同，并且身份角色、时间和结构性地点兼容，可以判为同一次现实事件的候选；qualifiers 通常用于更新事件状态，不参与事件身份。

```text
收购事件身份：acquire + acquirer + asset
攻击事件身份：attack + actor + target + time/location
战争事件身份：armed_conflict + belligerent + theater
```

事件匹配必须按事件记录进行，不能按整篇情报进行。一条情报中的不同事件可以分别匹配到不同现实事件。`id: null` 的事件仍可使用 FRAME、回退角色、时间、地点和原文向量召回候选，但不得自动归入精确谓词类别。

## 19. 验收规则

每条输出必须满足：

- `events` 数量为 1 到 5，且主事件引用有效；
- 每个事件具有完整 FRAME、predicate 和最小角色；
- FRAME、qualifier 和 relation 只使用本文定义的封闭值；
- 非空 predicate ID 必须属于核心谓词表，FRAME 和 roles 必须符合对应定义；
- `id: null` 的事件不得包含自造谓词 ID 或专用角色；
- attributes 只使用通用字段和规定的数据类型；
- 时间值符合语义字段和标准表达要求；
- context 只包含 `event_location`；
- qualifier 作用域有效且嵌套不超过一层；
- relation 只引用本情报中的其他事件；
- 没有把限定、关系或传播外壳错误注册为复合事件谓词；
- 没有把两个可独立成立的事件压缩成一项；
- 没有根据常识补充原文不存在的事实。

## 20. 方案有效性检查

本方案只有在真实语料中保持以下性质时才成立：

### 20.1 应保持封闭的部分

- 顶层字段名；
- 事件与事件核字段名；
- FRAME 三个维度及其值；
- time 字段名及时间精度；
- context 字段名；
- attribute 字段名和数据类型；
- qualifier 类型及其值；
- relation 谓词；
- 核心 predicate ID、FRAME、角色和身份角色。

### 20.2 允许开放的部分

- 实体、时间、数值和原文 surface；
- `id: null` 事件的 surface 与 gloss。

### 20.3 失败信号

在代表性语料评估中出现以下情况，说明方案需要修改，而不是继续增加临时字段：

- 大量事件无法稳定选择 dynamics、topology 或 agency；
- qualifier 或 relation 经常需要使用列表外的新值；
- 大量事件只有依赖自由文本字段才能表达核心意义；
- 不同分析者频繁对事件拆分结果产生根本分歧；
- 为表达常见句子，经常需要超过一层限定嵌套；
- `id: null` 长期占较高比例且无法归入已有核心谓词；
- 单篇普通情报经常需要超过五个事件才能保留核心意义；
- 标准文本无法从结构中还原原文的关键事实限制。

V3 不提供 `other`、`custom_fields`、专用 attributes 或任意 qualifier/relation。无法表达的真实样本必须进入评估清单，用于判断是核心谓词粒度不当，还是总体模型失效。