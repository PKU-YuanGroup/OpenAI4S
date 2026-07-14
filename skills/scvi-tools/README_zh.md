# scvi-tools Skill

这个渐进披露 recipe 覆盖外部 scVI/scANVI 的 count-based 单细胞 latent space、label transfer 与 Bayesian differential expression 工作流。加载 Skill 可挂入一个小型兼容 sidecar；scvi-tools、PyTorch、数据、训练模型与 GPU 资源均未捆绑。

## 直属文件

| 文件 | 职责 |
| --- | --- |
| [`SKILL.md`](SKILL.md) | 记录 raw count 保留、`setup_anndata`、scVI/scANVI training、embedding/normalized expression、differential expression、输出、remote-compute pattern 及常见 count/batch/device 陷阱。 |
| [`kernel.py`](kernel.py) | 可选 sidecar，暴露 `h5ad_safe_obs`：复制 observation table，并将 index 与 string-like column 转成 HDF5-safe 表示后再写 `.h5ad`。 |

## 直属子目录

无。

Convergence、batch correction、label 与 differential-expression 结论必须在实际数据上检查；存在 recipe 不代表已安装兼容 scvi-tools。
