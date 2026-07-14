# 生态适配器

[English](./README.md)

**状态：扩展边界已实现。** 本包保存将外部生态适配到现有 OpenAI4S 运行时契约的可选集成。导入本包不会给标准库核心增加第三方依赖。

## 架构位置

适配器位于生态边界，不拥有任何一层循环。它们可以驱动现有 outer-loop 或 kernel 接口，但不能复制编排逻辑、绕过 Host 策略，或默默扩大完成语义。可选 import 应放在真正启动适配器的位置。

## 本目录直属文件

| 文件 | 职责 |
| --- | --- |
| [`__init__.py`](./__init__.py) | 标记可选适配器命名空间，并有意不急切导出任何集成。 |

## 直属子目录

| 目录 | 在架构中的位置 |
| --- | --- |
| [`jupyter/`](./jupyter/) | 围绕现有 Python/R Worker manager 的可选 Jupyter wire bridge 和纯标准库 KernelSpec 生成。 |

## 扩展契约

- 复用核心 port 或 manager，不引入第二套执行引擎。
- 保持第三方 import 延迟加载，使普通 `openai4s` 导入仍仅依赖标准库。
- 明确写出不支持的集成语义；适配器不会自动等同于 Web workbench。
