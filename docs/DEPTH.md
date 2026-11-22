# 业主表决簿业务与测试登记

- 规模：中型
- 使用者：住宅小区业委会计票员
- 核心规则：同一所有权主体跨房合并为一票，按共有比例计算面积；参与和赞成均同时满足面积及人数门槛。

## 功能模块

| 模块 | 使用者动作 | 入口 | 核心文件 | 测试 |
|---|---|---|---|---|
| M1 送审票册 | 检查编码、字段宽度和来源行 | `POST /api/intake` | `app/modules/intake/service.py` | `tests/test_intake.py` |
| M2 产权字段 | 归一修订、业务键和偏移时间 | `POST /api/contract` | `app/modules/contract/service.py` | `tests/test_contract.py` |
| M3 冲突票夹 | 定位重复、被替代与矛盾来源 | `POST /api/quality` | `app/modules/quality/versions.py` | `tests/test_quality.py` |
| M4 份额计票 | 按规则复算并追溯证据 | `POST /api/domain` | `app/modules/domain/rule.py` | `tests/test_domain.py` |
| M5 公示勾稽 | 核对独立清单和各类差异 | `POST /api/reconcile` | `app/modules/reconcile/service.py` | `tests/test_reconcile.py` |
| M6 议案记录 | 打开历史批次并对原文件复跑 | `POST /api/runs` | `app/modules/runs/service.py` | `tests/test_runs.py` |
| M7 计票底稿 | 登记复核结论并下载证据 | `POST /api/review/{run_id}` | `app/modules/review/decisions.py` | `tests/test_review.py` |
| M8 请款分账 | 合格合同按楼栋面积分摊到户、校验分栋余额并整批发布 | `POST /api/disburse/preview` | `app/modules/disburse/service.py` | `tests/test_disburse.py`、`tests/test_disburse_api.py` |

## 陷阱登记

| TRAP | 现象 | 样例 | 处理位置 | 测试 |
|---|---|---|---|---|
| TRAP-01 | GB18030 来源文件 | legacy.csv | `app/modules/intake/reader.py` | `tests/test_intake.py::test_gb18030` |
| TRAP-02 | BOM 前缀 | shift.csv | `app/modules/intake/reader.py` | `tests/test_intake.py::test_bom` |
| TRAP-03 | 备注含引号内换行 | shift.csv | `app/modules/intake/service.py` | `tests/test_intake.py::test_multiline` |
| TRAP-04 | 分段重复表头 | shift.csv | `app/modules/intake/service.py` | `tests/test_intake.py::test_repeated` |
| TRAP-05 | 列数不足 | shift.csv | `app/modules/intake/service.py` | `tests/test_intake.py::test_width` |
| TRAP-07 | 缺测哨兵 | shift.csv | `app/modules/contract/service.py` | `tests/test_contract.py::test_missing` |
| TRAP-08 | 前导零的单号 | shift.csv | `app/modules/contract/service.py` | `tests/test_contract.py::test_leading_zero` |
| TRAP-11 | 偏移时刻 UTC 归一 | shift.csv | `app/modules/domain/values.py` | `tests/test_contract.py::test_offsets` |
| TRAP-14 | 同版业务字段冲突 | shift.csv | `app/modules/quality/versions.py` | `tests/test_quality.py::test_conflicting` |
| TRAP-16 | 末行引号截断 | truncated.csv | `app/modules/intake/reader.py` | `tests/test_intake.py::test_truncated_rejected` |
| TRAP-17 | 旧版列名映射 | legacy.csv | `app/modules/contract/schema.py` | `tests/test_contract.py::test_legacy_aliased` |
| TRAP-18 | 空文件、空白文件与仅表头 | 测试构造 | `app/modules/intake/service.py` | `tests/test_intake.py::test_empty_states` |
| TRAP-26 | 陈旧复核版本拒绝 | 测试构造 | `app/modules/review/decisions.py` | `tests/test_review.py::test_stale` |
| TRAP-106 | only area | cases.csv | `app/modules/domain/rule.py` | `tests/test_domain.py::test_only_area` |

## 对账

输入 = 计入 + 隔离 + 重传及版本替换 + 空白记录 + 重复表头。按业务键全外连接，缺失一侧与数值为零分别登记。逐笔差值与总差一致。

## 请款分账（M8）

- 资格门：只有双门槛「通过」且对账结论为 `MATCH` 的议案进入请款；隔离/被替代记「待核实」，未达门槛或不在计票结果记「未通过」，二者均不出账单。
- 公共部分合同由全小区按楼栋建筑面积分担；专有部分合同仅由「受益范围」内楼栋分担，未受益楼栋分摊为零。
- 楼栋再按户内建筑面积分到各户；同一业主跨楼栋在业主汇总里分楼栋列示并合计。
- 定点到分：每层先按权重求理论值向下取整，尾差按「理论值减取整值」从大到小、并列按稳定次序逐分找补，保证各档之和与合同总价分厘不差。
- 受益楼栋任一分栋余额不足，或没有合格议案，或总额无法对平时，`ready=false` 且发布返回 `DISBURSE_BLOCKED`，整批退回。
- 发布为单事务（`BEGIN IMMEDIATE`）：同指纹重跑返回已存在批次、不新增请款；失败整体回滚，历史批次账单原样保留。
