# SatMend

[English](README.md) · [快速开始](#快速开始) · [MIT 许可证](LICENSE)

![SatMend 场景图，展示星地联系、星上软件版本和经网关恢复的过程](assets/background.jpg)

*地面维护中心利用计划内的联系窗口，经网关卫星对多颗卫星上的应用开展维护。*

同一星座中，多颗协作卫星可能运行同一组件的不同版本。故障导致服务中断时，维护方需要准备兼容的修复、利用有限的联系机会完成交付，并在截止时间前验证目标是否恢复。一个本身健康的应用，也可能因为上游依赖故障而无法提供服务。

SatMend 提供离散事件仿真器和参考维护流程。读者可以用自己定义的场景，对比联系日历、链路容量、部署布局、候选提交时机和恢复策略。仿真器记录应用何时恢复、依赖服务何时恢复，以及地面何时收到确认。

## 方法概览

![SatMend 框架图，展示跨版本会话、逐版本验证和及时交付](assets/method.jpg)

*SatMend 将相关版本组织到地面修复会话中，对各版本分别验证，并在候选就绪时提交。*

[参考规划器](satmend/method.py)按组件组织受影响版本，将每个组件组分配给一个修复工作槽。它模拟诊断结果的跨版本复用，对每个代码版本单独评估，并输出带就绪时间的代码交付或状态恢复动作。[仿真器](satmend/simulator.py)通过统一的上行、网关暂存、星间转发、激活、验证和回执模型执行这些动作。读者可以通过 `Assessment` 回调提供各版本的验证结果和成本，也可以导入自己的完整动作序列。

## 快速开始

克隆仓库后可用 Python 3.9 或更新版本运行示例，仿真器的运行依赖为 Python 标准库。

```bash
git clone https://github.com/luopeng69131/SatMend.git
cd SatMend
mkdir -p outputs
python3 -m satmend prepare examples/scenario.json \
  --contacts examples/ground_contacts.csv --output outputs/scenario.json
python3 -m satmend run outputs/scenario.json --output outputs/result.json
python3 -c 'import json; print(json.load(open("outputs/result.json"))["summary"])'
```

示例包含三颗卫星和两个地面联系窗口。网关首先恢复，两个副本在断联期间完成星上验证，回执在下一次联系时到达地面。后续版本的上行在下一个窗口从剩余 20 字节处续传。

```text
{'deadline_s': 22.0, 'faulty_applications': 4, 'recovered_applications': 4, 'affected_services': 5, 'restored_services': 5, 'mean_service_outage_s': 10.14}
```

如需安装 `satmend` 命令和 Python 包，可在虚拟环境中执行：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install .
satmend --help
```

## 配置自己的实验

复制[场景 JSON](examples/scenario.json)并修改下表字段。时间单位为秒，链路速率为字节每秒。

| 输入 | 作用 |
| --- | --- |
| `deadline_s`、`ground_contacts` | 维护期限和星地联系窗口 |
| `gateway_satellite`、`gateway_app`、`crosslink_contacts` | 网关位置、转发健康状态和各目标卫星可选的可见窗口 |
| `*_bytes_per_s`、`command_bytes`、`receipt_bytes` | 星地上下行容量、共享星间链路容量和控制消息大小 |
| `applications` | 应用所在卫星、组件、版本、初始故障、上游依赖、包大小、激活与验证耗时 |
| `--slots`、`--diagnosis-s`、`--reuse-diagnosis-s`、`--validation-s`、`--state-recovery-s` | 参考规划器的并发数和各阶段耗时 |
| `--actions` | 导入 JSON 动作数组，可用结果文件中的 `planned_actions` 作为模板 |

[数据加载器](satmend/scenario.py)校验应用编号、依赖关系、时间和容量，并对联系窗口排序、合并。通过 `prepare` 可以输出规范化后的场景文件。包含 `start_s,end_s` 的可选 CSV 可替换 JSON 中的地面联系日历。星间联系中，未配置的目标在网关正常时持续可见，显式空列表表示该目标不可见。代码故障的 `package_bytes` 设为正整数。

星地传输在联系窗口内进行，窗口关闭后保留续传进度。完整到达网关的包可在断联期间继续向目标交付。网关缓存兼容修复包，并在转发应用健康时使用共享星间链路逐目标传输。激活与验证使用设定的时间和结果，恰好在截止时通过验证的目标计入恢复。

结果 JSON 包含 `summary` 汇总、逐目标 `applications`、逐项 `jobs`、未完成的 `pending_transfers`、带时间戳的 `events` 和输入动作 `planned_actions`。应用恢复时刻、地面确认时刻和依赖服务可用时刻分别记录。平均服务中断时长基于初始受影响服务计算，未恢复服务的中断时长记为实验总时长。

## 在 Python 中调用

```python
from satmend import Assessment, load_scenario, plan_sessions, simulate

scenario = load_scenario("examples/scenario.json")

def assess(component, version, targets):
    return Assessment(validated=True, verification_passes=True, duration_s=1.5)

actions = plan_sessions(scenario, slots=2, assess=assess)
report = simulate(scenario, actions)
print(report["summary"])
```

可在回调里设置每个代码版本的验证结果和耗时，也可直接构造 `Action` 对比维护策略。代码动作指定就绪时间、目标、组件、版本、候选编号、包大小和验证结果。状态恢复动作指定就绪时间和目标。默认值见[动作和规划器定义](satmend/method.py)。

代码、文档、合成示例和项目配图采用 [MIT 许可证](LICENSE)。[资产来源](ASSET_SOURCES.md)
