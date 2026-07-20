# evidence-walkthrough（证据走查）

一次可作为参考的完整研究流程：固定的数据库查询 → 本地分析 → 带 lineage 的
版本化产物 → 一个能在干净环境里校验的证据包。

适用于首次运行的示范、作为基准用例（输入固定，两次运行才可比），或者当一个
结果需要交给当时不在场的人时。

用接收方的方式校验导出的包，不需要 daemon：

```
openai4s verify-package <session>.openai4s-session.zip
```

通过表示这个包是**完整未被篡改**的，而不是"来源可信"——具体校验了什么、没有
校验什么，见 [`openai4s/evidence.py`](../../openai4s/evidence.py)。
