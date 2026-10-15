# 构建与版本适配

## 环境与输入

写入 PE 资源需要 Windows；Python 3.10+ 标准库即可。词典和结构测试可在其他系统运行。所有命令以仓库根目录为工作目录。

1. 从 [官方页面](https://mobaxterm.mobatek.net/download-home-edition.html) 获取 Home Edition。
2. 将未修改的可执行文件保存为 `inputs/MobaXterm.exe`。
3. 打包时还需要同一发行版的 `inputs/CygUtils.plugin`、`inputs/CygUtils64.plugin`。
4. 当前仅支持 26.5 / 5542；以文件 SHA-256 判断，文件名不是判断依据。

```powershell
Get-FileHash inputs/MobaXterm.exe -Algorithm SHA256
python tools/localize.py check --source inputs/MobaXterm.exe --locale zh-CN
python tools/localize.py build --source inputs/MobaXterm.exe --locale zh-CN --output build/zh-CN/MobaXterm_26.5_zh-CN.exe
```

输出文件已存在时默认拒绝覆盖。确认只需重建生成物后，可添加 `--force`。原版路径与输出路径不能相同。

## 构建流程

1. 校验原版哈希和词典对应版本。
2. 仅加载选定词典，检查 Unicode、重复键和格式占位符。
3. 遍历 PE 资源，按精确原文或上下文替换 Delphi DFM 展示属性、Windows 字符串表。
4. 将翻译写入临时副本，以 UTF-16 保存新增文本，再次解析全部修改后的 DFM。
   对 `config/runtime-26.5.json` 中核对过的显示常量，按词典指定的代码页更新字符串长度和内容；保持原槽位容量、引用地址和相邻指令不变。两个已确认的搜索占位框允许翻译初始 `Text`，其他输入值仍保留原文。
5. 清除修改副本中已失效的原厂签名信息；不伪造或重新签署原厂签名。
6. 对比所有资源和非资源区段，确认修改范围符合计划。
7. 验证通过后将临时副本提交为输出文件，附带报告。失败时清理本次临时副本。


## 验证流程

工具栏等显示常量与指令同处 CODE 区段，因此启用运行时文字翻译后，CODE 区段的整体哈希会变化。构建报告逐项记录允许修改的字符串槽位、原始/替换字节与偏移；除这些槽位以外，非资源内容必须与原版逐字节一致。未指定修改的资源也必须一致。PE 资源目录、资源长度、文件头中的签名/校验和元数据会因资源更新而变化。

需要人工检查界面布局、字体回退、动态文案、运行时行为及实际协议连接。字符串表含有大量第三方库文本，未译条目数量不能直接换算成界面覆盖率。未纳入版本显示常量清单的动态英文及内嵌独立工具仍可能保留英文。运行时常量使用目标 Windows 的 ANSI 代码页；资源文字使用 Unicode。

## 支持新版本

建议流程：

1. 在独立测试目录取得可信的官方原版，记录版本、build、EXE 及插件哈希。
2. 对新版本资源重新提取，检查 DFM 类型、资源结构、组件名称和字符串语义变化。
   必须重新核对运行时常量偏移、字符串头、原文、静态工具栏表及对应引用；不得直接沿用旧版偏移。
3. 核对旧词典中可复用的精确原文，补充新增内容，更新或移除过时上下文覆盖。
4. 更新版本配置及词典的 `source_sha256`，完成结构测试、真实构建、界面和功能测试。
5. 记录实测范围和未解决问题后，再更新 README、CHANGELOG 和发行说明。


## 回归测试

```powershell
python -B -m unittest discover -s tests -v
```
