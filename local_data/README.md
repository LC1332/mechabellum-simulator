# local_data/

这里存放**不入库**的本地大文件，目录整体被 `.gitignore` 忽略：

- `rounds_new11.json`（约 40MB，含原始属性的完整官方回放语料）
- `rounds.json`（约 4.7MB，全量语料）
- `exp_regen/`（`benchmarks/run.py --regen-exp` 重算臂的输出）

web 服务器启动时按以下顺序回落加载回放语料：

1. `local_data/rounds_new11.json`
2. `local_data/rounds.json`
3. `data/samples/rounds.json`（仓库内小样例，约 170 单位）

三者任一存在即可在首页 `/` 使用「回放导入」功能；全部缺失时服务器仍能启动，
只是回放列表为空（布阵沙盘与 `/bench` 播放器不受影响）。
